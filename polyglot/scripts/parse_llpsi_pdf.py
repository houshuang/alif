"""Parse the LLPSI Familia Romana vocabulary PDF into a lemma/gloss/chapter TSV.

The PDF is a per-chapter table (Latin | English | Derivative). We parse by
column X-coordinate (Latin x0<200, English 200<=x0<360, Derivative ignored)
rather than the flattened text stream, because the optional Derivative column
makes the linear stream ambiguous. 'Chapter N' headers set the capitulum;
ligatures (ﬂ/ﬁ) are folded via NFKC; the citation form is the first token of
the Latin cell (handles principal-parts cells like 'membrum, i n.').

Output columns: lemma, gloss, chapter. The importer canonicalizes the lemma
through LatinCy at load time, so infinitives etc. are fine here.

Usage:
    .venv/bin/python scripts/parse_llpsi_pdf.py \
        --pdf ~/Downloads/Lingua-Latina-Vocabulary.pdf \
        --out data/vocab/llpsi_fr.tsv
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip()


def _cells(page) -> list[tuple[str, str]]:
    rows: dict[int, list[tuple[float, str]]] = {}
    for w in page.get_text("words"):
        x0, y0, word = w[0], w[1], _nfkc(w[4])
        rows.setdefault(round(y0 / 4.0), []).append((x0, word))
    out = []
    for key in sorted(rows):
        ws = sorted(rows[key], key=lambda t: t[0])
        latin = " ".join(w for x, w in ws if x < 200).strip()
        english = " ".join(w for x, w in ws if 200 <= x < 360).strip()
        out.append((latin, english))
    return out


def parse(pdf_path: Path) -> list[tuple[str, str, int | None]]:
    doc = fitz.open(pdf_path)
    chap_re = re.compile(r"^Chapter\s+(\d+)", re.I)
    chapter: int | None = None
    entries: list[tuple[str, str, int | None]] = []
    for pidx in range(doc.page_count):
        for latin, english in _cells(doc[pidx]):
            m = chap_re.match(latin)
            if m:
                chapter = int(m.group(1))
                continue
            if not latin or latin.lower() == "latin":
                continue
            if not english or english.lower() == "english":
                continue
            lemma = re.split(r"[\s,]+", latin)[0]
            if lemma:
                entries.append((lemma, english, chapter))
    return entries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    entries = parse(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("lemma\tgloss\tchapter\n")
        for lemma, gloss, ch in entries:
            f.write(f"{lemma}\t{gloss}\t{ch if ch else ''}\n")
    print(f"wrote {len(entries)} entries to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
