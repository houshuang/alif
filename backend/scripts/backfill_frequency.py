#!/usr/bin/env python3
"""Backfill frequency_rank and cefr_level on lemmas from external data sources.

Sources:
  - CAMeL Arabic Frequency Lists (MSA) — surface form counts from 12.6B tokens
    https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists
  - Kelly Project (Arabic) — ~9K lemmas with CEFR levels (A1–C2)
    http://corpus.leeds.ac.uk/serge/kelly/

Usage:
    python scripts/backfill_frequency.py [--dry-run]
"""

import argparse
import csv
import io
import os
import re
import sys
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")

from app.database import SessionLocal
from app.models import Lemma, Root

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CAMEL_URL = "https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists/releases/download/v1.0/MSA_freq_lists.tsv.zip"
CAMEL_CACHE = DATA_DIR / "MSA_freq_lists.tsv"

KELLY_URL = "http://corpus.leeds.ac.uk/serge/kelly/ar_m3.xls"
KELLY_CACHE = DATA_DIR / "kelly_ar_m3.xls"

# Fallback: HTML frequency list from Leeds (more reliable than XLS)
KELLY_HTML_URL = "http://corpus.leeds.ac.uk/frqc/arabic-m3.num.html"
KELLY_HTML_CACHE = DATA_DIR / "kelly_arabic_m3.html"


def strip_diacritics(text: str) -> str:
    """Remove Arabic diacritical marks."""
    return re.sub(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]', '', text)


def normalize_arabic(text: str) -> str:
    """Normalize Arabic text for matching: strip diacritics, normalize alefs, remove tatweel."""
    text = strip_diacritics(text)
    text = text.replace('\u0640', '')  # tatweel
    text = re.sub(r'[أإآٱ]', 'ا', text)
    return text


def download_camel_data() -> dict[str, int]:
    """Download and parse CAMeL MSA frequency list. Returns {bare_form: count}."""
    if not CAMEL_CACHE.exists():
        print(f"Downloading CAMeL MSA frequency list...")
        resp = requests.get(CAMEL_URL, timeout=120)
        resp.raise_for_status()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            tsv_name = [n for n in names if n.endswith('.tsv')][0]
            with zf.open(tsv_name) as f:
                CAMEL_CACHE.write_bytes(f.read())
        print(f"  Saved to {CAMEL_CACHE}")
    else:
        print(f"Using cached CAMeL data: {CAMEL_CACHE}")

    freq: dict[str, int] = {}
    with open(CAMEL_CACHE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) != 2:
                continue
            word, count_str = parts
            try:
                count = int(count_str)
            except ValueError:
                continue
            normalized = normalize_arabic(word)
            if normalized in freq:
                freq[normalized] += count
            else:
                freq[normalized] = count

    print(f"  Loaded {len(freq):,} unique forms")
    return freq


def download_kelly_data() -> dict[str, str]:
    """Download and parse Kelly Arabic CEFR list. Returns {bare_form: cefr_level}."""
    cefr_map: dict[str, str] = {}

    # Try HTML version first (more reliable, no openpyxl needed)
    if not KELLY_HTML_CACHE.exists():
        print(f"Downloading Kelly Arabic frequency list (HTML)...")
        try:
            resp = requests.get(KELLY_HTML_URL, timeout=60)
            resp.raise_for_status()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            KELLY_HTML_CACHE.write_bytes(resp.content)
            print(f"  Saved to {KELLY_HTML_CACHE}")
        except Exception as e:
            print(f"  HTML download failed: {e}")
    else:
        print(f"Using cached Kelly HTML data: {KELLY_HTML_CACHE}")

    if KELLY_HTML_CACHE.exists():
        cefr_map = _parse_kelly_html(KELLY_HTML_CACHE)
        if cefr_map:
            return cefr_map

    # Fallback: try XLS
    if not KELLY_CACHE.exists():
        print(f"Downloading Kelly Arabic frequency list (XLS)...")
        try:
            resp = requests.get(KELLY_URL, timeout=60)
            resp.raise_for_status()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            KELLY_CACHE.write_bytes(resp.content)
            print(f"  Saved to {KELLY_CACHE}")
        except Exception as e:
            print(f"  XLS download failed: {e}")
            return cefr_map
    else:
        print(f"Using cached Kelly XLS data: {KELLY_CACHE}")

    cefr_map = _parse_kelly_xls(KELLY_CACHE)
    return cefr_map


def _parse_kelly_html(path: Path) -> dict[str, str]:
    """Parse Kelly HTML frequency list. Format: rank word frequency."""
    cefr_map: dict[str, str] = {}
    content = path.read_text(encoding="utf-8", errors="replace")

    # The HTML list is rank-ordered. We assign CEFR based on rank:
    # 1-500: A1, 501-1200: A2, 1201-2500: B1, 2501-5000: B2, 5001-8000: C1, 8001+: C2
    lines = re.findall(r'(\d+)\s+([\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+)\s+([\d.]+)', content)
    if not lines:
        # Try extracting from table rows
        lines = re.findall(r'<tr[^>]*>.*?<td[^>]*>(\d+)</td>.*?<td[^>]*>([\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+)</td>.*?<td[^>]*>([\d.]+)</td>.*?</tr>', content, re.DOTALL)

    for rank_str, word, _ in lines:
        rank = int(rank_str)
        normalized = normalize_arabic(word)
        cefr = _rank_to_cefr(rank)
        if normalized not in cefr_map:
            cefr_map[normalized] = cefr

    print(f"  Loaded {len(cefr_map):,} Kelly entries with CEFR levels")
    return cefr_map


def _parse_kelly_xls(path: Path) -> dict[str, str]:
    """Parse Kelly XLS file."""
    cefr_map: dict[str, str] = {}
    try:
        import openpyxl
    except ImportError:
        print("  openpyxl not installed, skipping XLS parsing")
        print("  Install with: pip install openpyxl")
        return cefr_map

    wb = openpyxl.load_workbook(str(path), read_only=True)
    ws = wb.active
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header
        if len(row) < 2:
            continue
        word = str(row[0]).strip() if row[0] else ""
        if not word:
            continue
        # Check if CEFR level is in the data
        cefr = None
        for cell in row[1:]:
            val = str(cell).strip().upper() if cell else ""
            if val in ("A1", "A2", "B1", "B2", "C1", "C2"):
                cefr = val
                break
        if not cefr:
            cefr = _rank_to_cefr(i)

        normalized = normalize_arabic(word)
        if normalized not in cefr_map:
            cefr_map[normalized] = cefr

    wb.close()
    print(f"  Loaded {len(cefr_map):,} Kelly entries with CEFR levels")
    return cefr_map


def _rank_to_cefr(rank: int) -> str:
    """Map frequency rank to CEFR level based on standard thresholds."""
    if rank <= 500:
        return "A1"
    elif rank <= 1200:
        return "A2"
    elif rank <= 2500:
        return "B1"
    elif rank <= 5000:
        return "B2"
    elif rank <= 8000:
        return "C1"
    else:
        return "C2"


def main():
    parser = argparse.ArgumentParser(description="Backfill frequency and CEFR data")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without modifying DB")
    args = parser.parse_args()

    # Download data
    camel_freq = download_camel_data()
    kelly_cefr = download_kelly_data()

    # Sort by frequency to assign ranks
    sorted_forms = sorted(camel_freq.items(), key=lambda x: -x[1])
    rank_map: dict[str, int] = {}
    for rank, (form, _) in enumerate(sorted_forms, 1):
        rank_map[form] = rank

    db = SessionLocal()
    try:
        lemmas = db.query(Lemma).all()
        print(f"\nProcessing {len(lemmas)} lemmas...")

        freq_matched = 0
        cefr_matched = 0
        freq_updated = 0
        cefr_updated = 0

        for lemma in lemmas:
            bare = normalize_arabic(lemma.lemma_ar_bare) if lemma.lemma_ar_bare else None
            if not bare:
                continue

            # Try frequency match: exact bare form, then with al- prefix
            rank = rank_map.get(bare)
            if rank is None:
                # Try without al- prefix
                if bare.startswith('ال'):
                    rank = rank_map.get(bare[2:])
                else:
                    rank = rank_map.get('ال' + bare)

            if rank is not None:
                freq_matched += 1
                if lemma.frequency_rank != rank:
                    if not args.dry_run:
                        lemma.frequency_rank = rank
                    freq_updated += 1

            # Try CEFR match
            cefr = kelly_cefr.get(bare)
            if cefr is None and bare.startswith('ال'):
                cefr = kelly_cefr.get(bare[2:])
            elif cefr is None:
                cefr = kelly_cefr.get('ال' + bare)

            if cefr is not None:
                cefr_matched += 1
                if lemma.cefr_level != cefr:
                    if not args.dry_run:
                        lemma.cefr_level = cefr
                    cefr_updated += 1

        # Update root productivity scores
        roots = db.query(Root).all()
        root_updated = 0
        for root in roots:
            child_lemmas = [l for l in lemmas if l.root_id == root.root_id]
            total_freq = sum(
                camel_freq.get(normalize_arabic(l.lemma_ar_bare), 0)
                for l in child_lemmas
                if l.lemma_ar_bare
            )
            if total_freq > 0 and root.productivity_score != total_freq:
                if not args.dry_run:
                    root.productivity_score = total_freq
                root_updated += 1

        if not args.dry_run:
            db.commit()

        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"\n{prefix}Results:")
        print(f"  Frequency: {freq_matched}/{len(lemmas)} matched ({freq_matched/len(lemmas)*100:.0f}%), {freq_updated} updated")
        print(f"  CEFR:      {cefr_matched}/{len(lemmas)} matched ({cefr_matched/len(lemmas)*100:.0f}%), {cefr_updated} updated")
        print(f"  Roots:     {root_updated}/{len(roots)} productivity scores updated")

        # Show some examples
        if freq_matched > 0:
            examples = [l for l in lemmas if l.frequency_rank and l.frequency_rank <= 100][:5]
            if examples:
                print(f"\n  Top frequency examples:")
                for l in sorted(examples, key=lambda x: x.frequency_rank or 99999):
                    cefr_str = f" ({l.cefr_level})" if l.cefr_level else ""
                    print(f"    #{l.frequency_rank}: {l.lemma_ar} — {l.gloss_en}{cefr_str}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
