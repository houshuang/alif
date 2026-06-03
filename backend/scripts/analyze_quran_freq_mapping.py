#!/usr/bin/env python3
"""Report how the Quranic Arabic Corpus lemma frequencies map onto Alif lemmas.

Feasibility/diagnostics pass for the Quran-frequency track. Uses the shared
mapping in app.services.quran_frequency (the same code the core builder uses),
so this report reflects exactly what the track will contain. Writes nothing.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/analyze_quran_freq_mapping.py [path-to-qac-file]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma
from app.services.quran_frequency import QAC_DEFAULT_PATH, map_quran_frequencies


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else QAC_DEFAULT_PATH
    db = SessionLocal()
    try:
        ranked, report = map_quran_frequencies(db, path=path, collect_report=True)
        lemmas_by_id = {l.lemma_id: l for l in db.query(Lemma).all()}
        s = report
        tok_total = s["mapped_tokens"] + s["unmapped_tokens"]
        print(f"QAC v0.4: {s['qac_lemmas']} distinct lemmas\n")
        print("=== MAPPING RESULTS (content lemmas only) ===")
        print(f"content lemmas: {s['content_lemmas']}")
        print(f"  mapped:   {s['mapped_qac_lemmas']:4d} QAC lemmas "
              f"({s['mapped_qac_lemmas']/s['content_lemmas']*100:.1f}%) "
              f"-> {s['distinct_alif_lemmas']} distinct Alif lemmas")
        print(f"  unmapped: {s['unmapped_qac_lemmas']:4d} QAC lemmas "
              f"({s['unmapped_qac_lemmas']/s['content_lemmas']*100:.1f}%)")
        print(f"token-weighted coverage: {s['mapped_tokens']}/{tok_total} = "
              f"{s['mapped_tokens']/tok_total*100:.1f}%")
        print(f"homograph collisions corrected by QAC POS: {s['pos_corrected']}")

        print("\n=== TOP 25 QURAN LEMMAS (ranked, mapped to Alif) ===")
        for lemma_id, rank, count in ranked[:25]:
            lem = lemmas_by_id.get(lemma_id)
            print(f"  #{rank:3d} {count:5d}  {lem.lemma_ar if lem else '?':12} "
                  f"-> #{lemma_id} ({lem.gloss_en if lem else '?'})")

        print("\n=== TOP 25 UNMAPPED BY FREQUENCY (honest gaps) ===")
        for e in sorted(s["unmapped"], key=lambda x: -x["count"])[:25]:
            print(f"  {e['count']:4d}  {e['ar']:12} ({e['bare']:10}) "
                  f"pos={e['pos']} root={e['root']}")

        out = Path("/tmp/quran_freq_mapping.json")
        out.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report written to {out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
