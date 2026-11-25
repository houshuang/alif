#!/usr/bin/env python3
"""Give a new participle or verbal-noun lemma the review state of its verb.

Until 2026-11-25 many participles and verbal nouns had no lemma, so their
tokens were credited to the verb through its stored forms. When such a word
gets its own lemma it would start as unknown although the learner has read it
under the verb for months. Learner decision (2026-11-25): seed it from the
verb's state.

A lemma is seeded only when all of these hold: its source is one of
`--source`; it has no review history (no row, or `encountered` with no
reviews); a verb with the same root lists it as `masdar`, `active_participle`
or `passive_participle`; and that verb is `known` or `learning` with an FSRS
card. The new row copies the verb's card (so it falls due when the verb does)
and state; counters start at zero. Every seeded pair is written to the
activity log, which is what a reversal would read.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/seed_derived_from_verb.py --source book_frequency --source textbook_list
  PYTHONPATH=. python3 scripts/seed_derived_from_verb.py --source book_frequency --apply
"""
from __future__ import annotations

import argparse
import copy
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.sentence_validator import normalize_alef, strip_diacritics  # noqa: E402

DERIVED_KEYS = ("masdar", "active_participle", "passive_participle")
SEED_STATES = ("known", "learning")


def _key(text: str) -> str:
    return normalize_alef(strip_diacritics(text or "")).replace("ى", "ي").strip()


def _stability(row: UserLemmaKnowledge) -> float:
    card = row.fsrs_card_json if isinstance(row.fsrs_card_json, dict) else {}
    return float(card.get("stability") or 0)


def plan(db, sources: list[str]) -> list[dict]:
    knowledge = {k.lemma_id: k for k in db.query(UserLemmaKnowledge).all()}
    by_root: dict[int, list[tuple[Lemma, str, str]]] = {}
    for verb in db.query(Lemma).filter(Lemma.pos == "verb", Lemma.canonical_lemma_id.is_(None)):
        if verb.root_id is None or not isinstance(verb.forms_json, dict):
            continue
        for name in DERIVED_KEYS:
            value = verb.forms_json.get(name)
            if not isinstance(value, str):
                continue
            for part in re.split(r"[/،,;]| or ", value):
                if _key(part):
                    by_root.setdefault(verb.root_id, []).append((verb, name, _key(part)))

    pairs = []
    candidates = db.query(Lemma).filter(
        Lemma.source.in_(sources), Lemma.pos != "verb",
        Lemma.canonical_lemma_id.is_(None), Lemma.gates_completed_at.isnot(None),
    )
    for lemma in candidates:
        own = knowledge.get(lemma.lemma_id)
        if own is not None and (own.knowledge_state not in (None, "new", "encountered") or own.times_seen):
            continue
        matches = [
            (verb, name) for verb, name, form in by_root.get(lemma.root_id, [])
            if form == _key(lemma.lemma_ar_bare)
            and knowledge.get(verb.lemma_id) is not None
            and knowledge[verb.lemma_id].knowledge_state in SEED_STATES
            and isinstance(knowledge[verb.lemma_id].fsrs_card_json, dict)
        ]
        if not matches:
            continue
        verb, name = max(matches, key=lambda m: _stability(knowledge[m[0].lemma_id]))
        source_row = knowledge[verb.lemma_id]
        pairs.append({
            "lemma_id": lemma.lemma_id, "lemma": lemma.lemma_ar, "gloss": lemma.gloss_en,
            "verb_id": verb.lemma_id, "verb": verb.lemma_ar, "form": name,
            "state": source_row.knowledge_state, "stability": round(_stability(source_row), 1),
        })
    return pairs


def apply(db, pairs: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    for pair in pairs:
        verb_row = db.query(UserLemmaKnowledge).filter_by(lemma_id=pair["verb_id"]).one()
        row = db.query(UserLemmaKnowledge).filter_by(lemma_id=pair["lemma_id"]).first()
        if row is None:
            lemma = db.get(Lemma, pair["lemma_id"])
            row = UserLemmaKnowledge(lemma_id=lemma.lemma_id, source=lemma.source)
            db.add(row)
        row.knowledge_state = verb_row.knowledge_state
        row.fsrs_card_json = copy.deepcopy(verb_row.fsrs_card_json)
        row.last_reviewed = verb_row.last_reviewed
        row.introduced_at = now
        row.graduated_at = now
        row.acquisition_box = None
        row.acquisition_next_due = None
        row.times_seen = 0
        row.times_correct = 0
    log_activity(
        db, "derived_seeded_from_verb",
        f"Seeded {len(pairs)} participle/verbal-noun lemmas from their verb's review state",
        {"pairs": [[p["lemma_id"], p["verb_id"], p["form"], p["state"], p["stability"]] for p in pairs]},
    )
    db.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", required=True, help="Lemma.source to consider (repeatable)")
    ap.add_argument("--apply", action="store_true", help="Write (default: dry run)")
    args = ap.parse_args()
    db = SessionLocal()
    pairs = plan(db, args.source)
    for pair in pairs:
        print(f"{pair['lemma']} ({pair['gloss']}) <- {pair['verb']} [{pair['form']}] "
              f"{pair['state']}, stability {pair['stability']} d")
    firm = sum(1 for p in pairs if p["state"] == "known" and p["stability"] >= 30)
    print(f"\n{len(pairs)} lemmas to seed ({firm} from a firm verb)")
    if args.apply and pairs:
        apply(db, pairs)
        print("APPLIED")
    db.close()


if __name__ == "__main__":
    main()
