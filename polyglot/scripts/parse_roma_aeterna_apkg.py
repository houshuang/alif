"""Parse a Roma Aeterna (LLPSI 2) Anki .apkg into a lemma/gloss/rank TSV.

An .apkg is a zip containing a SQLite `collection.anki2`; the `notes` table
holds fields separated by \\x1f. For a Latin→English deck the fields are
[Latin, English]; the Latin field carries HTML spans, &nbsp;, and macrons.
We strip markup + entities, NFKC-normalize, and take the first token as the
citation form. Note order is preserved as the rank (≈ chapter/frequency order).

The importer canonicalizes the lemma through LatinCy and strips macrons at load,
so leaving macrons in the TSV here is harmless.

Usage:
    .venv/bin/python scripts/parse_roma_aeterna_apkg.py \
        --apkg ~/Downloads/Lingua_Latina_II_-_Roma_Aeterna_Latin_to_English.apkg \
        --out data/vocab/roma_aeterna.tsv
"""
from __future__ import annotations

import argparse
import html
import re
import sqlite3
import tempfile
import unicodedata
import zipfile
from pathlib import Path

INLINE_TAG = re.compile(r"<[^>]+>")
# Block-level tags separate distinct senses ("<div>demolition</div>
# <div>setting of the sun</div>"). Without a separator they collapse into a
# nonsense noun phrase ("demolition setting of the sun"). Emit "; " so the
# downstream TSV preserves multi-sense entries as semicolon-separated like
# the rest of the seed lists (LLPSI: "hut; cottage").
BLOCK_TAG = re.compile(r"</?(?:div|br|p|li|ol|ul|tr|td|h[1-6])\b[^>]*>", re.IGNORECASE)
WS = re.compile(r"\s+")
WORD = re.compile(r"[A-Za-zĀāĒēĪīŌōŪūȲȳÆæŒœ\-]+")


def _clean(s: str) -> str:
    s = BLOCK_TAG.sub("; ", s)
    s = INLINE_TAG.sub(" ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = unicodedata.normalize("NFKC", s)
    s = WS.sub(" ", s).strip()
    # Collapse "; ;" → "; " and strip leading/trailing separators.
    s = re.sub(r"(?:\s*;\s*)+", "; ", s).strip()
    s = s.strip("; ").strip()
    return s


def parse(apkg: Path) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(apkg) as z:
            z.extract("collection.anki2", tmp)
        con = sqlite3.connect(str(Path(tmp) / "collection.anki2"))
        rows = con.execute("SELECT flds FROM notes ORDER BY id").fetchall()
        con.close()
    out: list[tuple[str, str]] = []
    for (flds,) in rows:
        parts = flds.split("\x1f")
        if len(parts) < 2:
            continue
        latin, english = _clean(parts[0]), _clean(parts[1])
        if not latin or not english:
            continue
        lemma = re.split(r"[\s,;]+", latin)[0]
        if not lemma or not WORD.fullmatch(lemma):
            continue
        out.append((lemma, english))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apkg", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    entries = parse(args.apkg)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("lemma\tgloss\trank\n")
        for i, (lemma, gloss) in enumerate(entries, start=1):
            f.write(f"{lemma}\t{gloss}\t{i}\n")
    print(f"wrote {len(entries)} entries to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
