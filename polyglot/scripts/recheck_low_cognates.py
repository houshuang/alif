"""Second-pass audit on cognate-source ULKs whose best proposed cognate is
'low' transparency.

The original cognate detector at threshold=low let in two distinct kinds of
match: (a) "mitir/patir-style" — semantically clear, recognizable via Romance
reflexes or widespread Greek-derived prefixes (geo-/hydro-/photo-); and (b)
"rare-loanword" — only recognizable via medical/botanical/theological/
linguistic jargon (alopecia, picric, latria, paralipsis). Sample audit shows
~50% of low-best marks are (b).

This script re-judges each low-best lemma with a tighter prompt and deletes
the cognate-source ULK for the ones the second pass calls unrecognizable.
The Lemma row + its cognates_json stay — only the ULK is removed, so the
lemma reverts to "no knowledge state" and will be auto-introduced on next
reading like any other unknown word.

Usage:
    .venv/bin/python scripts/recheck_low_cognates.py --batch 25
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recheck_low_cognates")

MODEL = "claude-sonnet-4-5-20250929"
TIMEOUT_S = 180


PROMPT_HEADER = """For each Modern Greek lemma below, decide whether a reader fluent in English, Norwegian, German, French, Italian, and Spanish would recognize its meaning AT SIGHT via the proposed cognate.

Count as RECOGNIZABLE only if at least one of these holds:
  - A Romance reflex makes the meaning obvious (e.g. πατέρας → It. padre, Fr. père, Es. padre = "father")
  - A Germanic cognate makes the meaning obvious (e.g. νύχτα → night/Nacht)
  - A widespread Greek-derived prefix or root in everyday European vocabulary makes it obvious (geo-, hydro-, photo-, tele-, auto-, biblio-, demo-, micro-, mega-, poly-, mono-, theo-, philo-, anthropo-, neo-, paleo-)
  - A common borrowing makes the meaning obvious (e.g. λεμόνι ↔ lemon, καφές ↔ coffee)

Do NOT count as recognizable if the ONLY cognate is:
  - A rare medical/anatomical term (alopecia, picric, rhabdomyolysis, apnea)
  - A botanical/zoological technical term (didymous, phyte, alopex)
  - A theological/liturgical jargon term (latria, epithumia, hierophant)
  - A linguistic/rhetorical jargon term (paralipsis, metathesis, stich, hemistich)
  - An obscure scholarly loanword most educated readers would not know
  - A connection where the modern Greek meaning has drifted significantly from the cognate's meaning (e.g. ασφάλεια "safety/insurance" vs asphalt; νομίζω "to think" vs nominate)

Return one entry per input lemma in input order.
"""


def _call_claude(items: list[dict]) -> list[dict]:
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lemma": {"type": "string"},
                        "recognizable": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["lemma", "recognizable", "reason"],
                },
            },
        },
        "required": ["results"],
    }
    lines = []
    for it in items:
        cog = it["best_cognate"]
        lines.append(
            f"- {it['lemma']} (\"{it['gloss']}\")  proposed cognate: "
            f"{cog.get('lang','?')} \"{cog.get('form','')}\" — {cog.get('note','')[:120]}"
        )
    prompt = PROMPT_HEADER + "\n" + "\n".join(lines)
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--json-schema", json.dumps(schema),
        prompt,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed (rc={proc.returncode}): {proc.stderr[:400]}")
    wrapper = json.loads(proc.stdout)
    structured = wrapper.get("structured_output") if isinstance(wrapper, dict) else None
    if isinstance(structured, dict):
        payload = structured
    else:
        result = wrapper.get("result", proc.stdout) if isinstance(wrapper, dict) else proc.stdout
        payload = json.loads(result) if isinstance(result, str) else result
    entries = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("missing 'results' array in Claude output")
    return entries


def _best_low(cognates_json: list[dict]) -> dict | None:
    """If the strongest cognate is 'low' transparency, return it; otherwise None."""
    if not isinstance(cognates_json, list) or not cognates_json:
        return None
    order = {"high": 3, "medium": 2, "low": 1}
    best = max(cognates_json, key=lambda c: order.get(c.get("transparency", "low"), 0))
    return best if best.get("transparency") == "low" else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true",
                        help="Log verdicts but don't actually delete ULKs")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        rows = (
            db.query(Lemma, UserLemmaKnowledge)
            .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(UserLemmaKnowledge.source == "cognate",
                    Lemma.cognates_json.isnot(None))
            .all()
        )
        log.info("Loaded %d cognate-source lemmas; filtering to low-best-tier", len(rows))

        items = []
        for lemma, ulk in rows:
            best = _best_low(lemma.cognates_json)
            if best is None:
                continue
            items.append({
                "lemma_id": lemma.lemma_id,
                "lemma": lemma.lemma_form,
                "gloss": lemma.gloss_en or "",
                "best_cognate": best,
                "ulk_id": ulk.id,
            })
        log.info("%d lemmas to re-judge", len(items))
        if not items:
            return 0

        kept = dropped = errors = 0
        t0 = time.time()
        for i in range(0, len(items), args.batch):
            chunk = items[i:i + args.batch]
            try:
                verdicts = _call_claude(chunk)
            except Exception as e:
                log.warning("Batch %d failed: %s", i // args.batch, e)
                errors += len(chunk)
                continue
            by_lemma = {v["lemma"]: v for v in verdicts if isinstance(v, dict)}
            to_delete: list[int] = []
            for it in chunk:
                v = by_lemma.get(it["lemma"])
                if v is None:
                    log.warning("No verdict returned for %s; keeping", it["lemma"])
                    kept += 1
                    continue
                if v.get("recognizable"):
                    kept += 1
                    log.debug("KEEP  %s — %s", it["lemma"], v.get("reason", "")[:80])
                else:
                    dropped += 1
                    log.info("DROP  %s (\"%s\") — %s", it["lemma"], it["gloss"],
                             v.get("reason", "")[:90])
                    to_delete.append(it["ulk_id"])
            if to_delete and not args.dry_run:
                db.query(UserLemmaKnowledge).filter(
                    UserLemmaKnowledge.id.in_(to_delete)
                ).delete(synchronize_session=False)
                db.commit()
            elapsed = time.time() - t0
            log.info("  ... %d / %d judged (kept=%d, dropped=%d, errors=%d, %.0fs)",
                     i + len(chunk), len(items), kept, dropped, errors, elapsed)

        log.info("Done. kept=%d dropped=%d errors=%d", kept, dropped, errors)
        if not args.dry_run and (kept or dropped):
            log_activity(
                db,
                event_type="cognate_recheck_completed",
                summary=f"Re-judged {kept + dropped} low-tier cognate marks; "
                        f"dropped {dropped} false positives, kept {kept}",
                detail={"kept": kept, "dropped": dropped, "errors": errors},
                language_code="el",
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
