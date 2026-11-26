"""Measure a whole-page pre-match against the logged per-tap Snap checks.

For each snapped page with logged taps: run the page through the same
discovery path a tap uses (all words, learning ones included, OOV glossed),
then one batched sense check over every vocabulary match. Compare with what
the per-tap /lookup and /lookup/sense calls returned.

Usage (from backend/, with a copy of a prod backup and the interaction events
exported to snap_events.json next to this script):
    ALIF_DB=copy.db PYTHONPATH=. python ../research/snap-prematch-2026-11/measure_prematch.py OUT.json 16 14 15 10
    ALIF_DB=copy.db PYTHONPATH=. python ../research/snap-prematch-2026-11/compare_final_word.py OUT.json
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

S = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ALIF_AUDIT_PROVIDER", "claude")
os.environ.setdefault("ALIF_CLAUDE_HAIKU_CLI_MODEL", "sonnet")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import SnapPage  # noqa: E402
from app.routers.discover import discover_words_in_text  # noqa: E402
from app.services.preread_service import check_senses, load_candidates  # noqa: E402

CHUNK = int(os.environ.get("SENSE_CHUNK", "40"))

engine = create_engine(f"sqlite:///{os.environ['ALIF_DB']}")
db = sessionmaker(bind=engine)()
events = json.load(open(os.path.join(S, "snap_events.json")))
norm = lambda s: re.sub(r"[ً-ْـ\s]", "", s or "")  # noqa: E731


def taps_for(text: str):
    t = norm(text)
    out = []
    for d in events:
        if d["event"] in ("discover_lookup", "discover_lookup_sense") and d.get("context"):
            if norm(d["context"])[:60] in t:
                out.append(d)
    return out


def run_page(pid: int, gloss_from: dict[int, str]) -> dict:
    page = db.get(SnapPage, pid)
    text = page.arabic_text
    t0 = time.time()
    words = discover_words_in_text(
        db, text, count=1000, include_oov=True, include_learning=True, max_words=1000,
    )
    t_disc = time.time() - t0
    matched = [w for w in words if w.get("lemma_id") and w.get("lemma_source") == "database"]
    items, owners = [], []
    for w in matched:
        cands = load_candidates(db, w.get("homograph_candidates"))
        for f in (w.get("surface_forms") or [w["surface"]]):
            it = {"index": len(items), "lemma_ar": w["lemma_ar"],
                  "gloss_en": gloss_from.get(w["lemma_id"], w.get("gloss_en")),
                  "pos": w.get("pos"), "forms": [f]}
            if cands:
                it["candidates"] = cands
            items.append(it)
            owners.append((w, f))
    db.rollback()
    t1 = time.time()
    chunks = [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)]
    verdicts: dict[int, dict] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for part in pool.map(lambda ch: check_senses(text, ch), chunks):
            if part is None:
                failed += 1
            else:
                verdicts.update(part)
    t_sense = time.time() - t1
    for i, (w, f) in enumerate(owners):
        w.setdefault("form_verdicts", {})[f] = verdicts.get(i, {"verdict": "fits"})
        w["batch_verdict"] = w["form_verdicts"][f]
    return {
        "page_id": pid, "tokens": len(re.findall(r"[؀-ۿ]+", text)),
        "words": words, "n_matched": len(matched), "n_oov": len(words) - len(matched),
        "t_discover_s": round(t_disc, 1), "t_sense_s": round(t_sense, 1),
        "sense_chunks": len(chunks), "sense_failed_chunks": failed,
        "taps": taps_for(text),
    }


if __name__ == "__main__":
    out_path, pids = sys.argv[1], [int(p) for p in sys.argv[2:]]
    gloss_from = {d["lemma_id"]: d["gloss_from"] for d in events
                  if d["event"] == "discover_lookup_sense" and d.get("gloss_from")}
    results = []
    for pid in pids:
        r = run_page(pid, gloss_from)
        print(pid, r["tokens"], "matched", r["n_matched"], "oov", r["n_oov"],
              "disc", r["t_discover_s"], "sense", r["t_sense_s"], "failed", r["sense_failed_chunks"],
              flush=True)
        results.append(r)
    json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=1, default=str)
