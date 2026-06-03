"""Cap enforcement must never retire the last reviewable sentence covering an
FSRS word — even when that word is only *collateral* (not the target). This is
the prevention half of the due-coverage deficit fix."""
from datetime import datetime, timezone

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from scripts.update_material import step_enforce_cap

NOW = datetime.now(timezone.utc)


def _lemma(db, lemma_id, ar):
    db.add(Lemma(lemma_id=lemma_id, lemma_ar=ar, lemma_ar_bare=ar, gloss_en=ar,
                 gates_completed_at=NOW))
    db.flush()


def _known(db, lemma_id):
    db.add(UserLemmaKnowledge(lemma_id=lemma_id, knowledge_state="known", source="study",
                              fsrs_card_json={"due": NOW.isoformat()}))


def _sentence(db, sid, lemma_ids, target_id, shown_at):
    db.add(Sentence(id=sid, arabic_text=f"s{sid}", english_translation="t", is_active=True,
                    mappings_verified_at=NOW, target_lemma_id=target_id, times_shown=5,
                    last_reading_shown_at=shown_at))
    db.flush()
    for i, lid in enumerate(lemma_ids):
        db.add(SentenceWord(sentence_id=sid, position=i, surface_form=f"w{lid}",
                            lemma_id=lid, is_target_word=(lid == target_id)))
    db.flush()


def test_cap_enforcement_protects_collateral_only_sentence(db_session):
    # T is target of 3 sentences; A,B are known scaffold in all; C is known and
    # appears ONLY in S1 (as collateral). S1 is the oldest, so it would be the
    # first retirement candidate — but it is C's only reviewable sentence.
    for lid, ar in [(1, "كتاب"), (2, "قلم"), (3, "بيت"), (4, "تراث")]:
        _lemma(db_session, lid, ar)
        _known(db_session, lid)
    T, A, B, C = 1, 2, 3, 4
    _sentence(db_session, 1, [T, A, B, C], T, datetime(2020, 1, 1, tzinfo=timezone.utc))
    _sentence(db_session, 2, [T, A, B], T, datetime(2021, 1, 1, tzinfo=timezone.utc))
    _sentence(db_session, 3, [T, A, B], T, datetime(2022, 1, 1, tzinfo=timezone.utc))
    db_session.commit()

    # max_sentences=51 → retire_target=1 → must retire 2 of the 3 active sentences.
    retired = step_enforce_cap(db_session, dry_run=False, max_sentences=51, tier_lookup={})

    s1, s2, s3 = (db_session.get(Sentence, i) for i in (1, 2, 3))
    assert retired == 2
    assert s1.is_active is True, "C's only sentence must survive"
    assert s2.is_active is False and s3.is_active is False
