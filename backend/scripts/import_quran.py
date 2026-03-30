"""Import the Quran from risan/quran-json CDN.

Phase 1: Import all 114 surahs (text + translation only, no lemmatization).
Phase 2: Lemmatize the first batch of verses.

Usage:
    python3 scripts/import_quran.py [--lemmatize N]
"""

import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import QuranicVerse

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CDN_BASE = "https://cdn.jsdelivr.net/npm/quran-json@3.1.2/dist/chapters/en"


def fetch_chapter(surah_num: int) -> dict:
    """Fetch a chapter JSON from the CDN."""
    url = f"{CDN_BASE}/{surah_num}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Alif/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def import_quran(db, skip_existing: bool = True) -> int:
    """Import all 114 surahs into the quranic_verses table."""
    total_imported = 0
    total_skipped = 0

    for surah_num in range(1, 115):
        try:
            chapter = fetch_chapter(surah_num)
        except Exception as e:
            logger.error(f"Failed to fetch surah {surah_num}: {e}")
            continue

        surah_name_ar = chapter.get("name", "")
        surah_name_en = chapter.get("transliteration", "")
        logger.info(f"Surah {surah_num}: {surah_name_en} ({surah_name_ar}) — {len(chapter.get('verses', []))} verses")

        for verse_data in chapter.get("verses", []):
            ayah = verse_data["id"]

            if skip_existing:
                existing = (
                    db.query(QuranicVerse)
                    .filter(QuranicVerse.surah == surah_num, QuranicVerse.ayah == ayah)
                    .first()
                )
                if existing:
                    total_skipped += 1
                    continue

            verse = QuranicVerse(
                surah=surah_num,
                ayah=ayah,
                surah_name_ar=surah_name_ar,
                surah_name_en=surah_name_en,
                arabic_text=verse_data["text"],
                english_translation=verse_data["translation"],
                transliteration=verse_data.get("transliteration"),
            )
            db.add(verse)
            total_imported += 1

        db.commit()

    logger.info(f"\nImport complete: {total_imported} verses imported, {total_skipped} skipped")
    return total_imported


def main():
    parser = argparse.ArgumentParser(description="Import the Quran into Alif")
    parser.add_argument("--lemmatize", type=int, default=20,
                        help="Number of verses to lemmatize after import (default: 20, 0 to skip)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total = import_quran(db)

        if args.lemmatize > 0 and total > 0:
            logger.info(f"\nLemmatizing first {args.lemmatize} verses...")
            from app.services.quran_service import lemmatize_quran_verses
            count = lemmatize_quran_verses(db, limit=args.lemmatize)
            logger.info(f"Lemmatized {count} verses")
    finally:
        db.close()


if __name__ == "__main__":
    main()
