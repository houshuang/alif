"""Load lemmas + sentences from JSON into DB. Run once on fresh DB.

Idempotent: uses lemma_es and sentence text as natural keys to skip existing.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from .database import SessionLocal, engine, Base
from .models import Lemma, Sentence, SentenceLemma

ROOT = Path(__file__).resolve().parent.parent
LEMMAS_PATH = ROOT / "content" / "lemmas.json"
SENTENCES_PATH = ROOT / "content" / "sentences.json"


def _load_lemmas(db: Session) -> dict[str, int]:
    """Returns mapping lemma_es → lemma_id."""
    if not LEMMAS_PATH.exists():
        print(f"[content_loader] {LEMMAS_PATH} not found — skipping lemma load.")
        return {l.lemma_es: l.id for l in db.query(Lemma).all()}

    data = json.loads(LEMMAS_PATH.read_text())
    source = data.get("enriched_corrected") or data.get("enriched") or {"lemmas": []}
    entries = source["lemmas"]

    existing = {l.lemma_es: l for l in db.query(Lemma).all()}
    added = 0

    for e in entries:
        lemma_es = e["lemma_es"]
        conj_present = e.get("conjugation_present") if e.get("conjugation_applicable") else None
        agreement = e.get("agreement_forms") or {}
        # Treat all-empty agreement as None
        if agreement and not any(agreement.values()):
            agreement = None

        if lemma_es in existing:
            lem = existing[lemma_es]
        else:
            lem = Lemma(lemma_es=lemma_es)
            db.add(lem)
            added += 1

        lem.gloss_no = e.get("gloss_no", "")
        lem.pos = e.get("pos", "")
        lem.gender = e.get("gender", "none")
        lem.article_quirk = e.get("article_quirk", "") or ""
        lem.cefr_level = e.get("cefr_level", "A1")
        lem.frequency_rank = e.get("frequency_rank_estimate", 999)
        lem.memory_hook_no = e.get("memory_hook_no", "")
        lem.etymology_no = e.get("etymology_no", "")
        lem.example_es = e.get("example_es", "")
        lem.example_no = e.get("example_no", "")
        lem.conjugation_present_json = conj_present
        lem.agreement_forms_json = agreement
        lem.plural_form = e.get("plural_form", "") or ""
        lem.conjugation_applicable = bool(e.get("conjugation_applicable", False))

    db.commit()
    print(f"[content_loader] Lemmas: {added} added, {len(entries) - added} updated. Total: {len(entries)}")
    return {l.lemma_es: l.id for l in db.query(Lemma).all()}


def _load_sentences(db: Session, lemma_ids: dict[str, int]) -> None:
    if not SENTENCES_PATH.exists():
        print(f"[content_loader] {SENTENCES_PATH} not found — skipping sentence load.")
        return

    data = json.loads(SENTENCES_PATH.read_text())
    source = data.get("sentences_corrected") or data.get("sentences") or {"sentences": []}
    entries = source["sentences"]

    # Use (es, no) as natural key
    existing = {(s.es, s.no): s for s in db.query(Sentence).all()}
    added = 0
    skipped_unmapped = 0

    for e in entries:
        key = (e["es"], e["no"])
        if key in existing:
            continue

        word_mapping = e.get("word_mapping", [])
        # Validate: all referenced lemmas must exist
        missing = [w["lemma_es"] for w in word_mapping if w["lemma_es"] not in lemma_ids]
        if missing:
            skipped_unmapped += 1
            print(f"  skip (missing lemmas {missing}): {e['es']}")
            continue

        sent = Sentence(
            es=e["es"],
            no=e["no"],
            difficulty_rank=e.get("difficulty_rank", 0),
            distractors_no_json=e.get("distractors_no", []),
            word_mapping_json=word_mapping,
        )
        db.add(sent)
        db.flush()

        # Link lemmas
        seen = set()
        for w in word_mapping:
            lid = lemma_ids[w["lemma_es"]]
            if lid in seen:
                continue
            seen.add(lid)
            db.add(SentenceLemma(sentence_id=sent.id, lemma_id=lid))

        added += 1

    db.commit()
    print(f"[content_loader] Sentences: {added} added, {skipped_unmapped} skipped (unmapped lemmas)")


def load_all() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        lemma_ids = _load_lemmas(db)
        _load_sentences(db, lemma_ids)
    finally:
        db.close()


if __name__ == "__main__":
    load_all()
