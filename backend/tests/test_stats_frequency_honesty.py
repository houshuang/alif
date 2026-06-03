"""Frequency-core stats must not report function words or already-known variant
forms as 'missing'/'gap' — only genuinely uncovered content words (Part D)."""
from datetime import datetime, timezone

from app.models import FrequencyCoreEntry, Lemma, UserLemmaKnowledge
from app.routers.stats import _compute_frequency_core_progress, _func_word_ids_cache
import app.routers.stats as stats_mod


def _lemma(db, lemma_id, ar, canonical=None):
    db.add(Lemma(lemma_id=lemma_id, lemma_ar=ar, lemma_ar_bare=ar, gloss_en=ar,
                 pos="noun", canonical_lemma_id=canonical))
    db.flush()


def _known(db, lemma_id):
    db.add(UserLemmaKnowledge(lemma_id=lemma_id, knowledge_state="known", source="study",
                              introduced_at=datetime.now(timezone.utc),
                              fsrs_card_json={"due": datetime.now(timezone.utc).isoformat()}))
    db.flush()


def _fce(db, rank, lemma_id, display):
    db.add(FrequencyCoreEntry(core_rank=rank, lemma_id=lemma_id, lemma_key=f"k{rank}",
                              display_form=display, score=1.0))
    db.flush()


def test_frequency_gaps_exclude_function_words_and_known_variants(db_session, monkeypatch):
    # Reset the module-level function-word cache so it rebuilds against this DB.
    monkeypatch.setattr(stats_mod, "_func_word_ids_cache", None)

    _lemma(db_session, 1, "مِن")           # function word (preposition "from")
    _lemma(db_session, 2, "كتاب"); _known(db_session, 2)   # known content word
    _lemma(db_session, 3, "يوم"); _known(db_session, 3)    # known canonical
    _lemma(db_session, 4, "اليوم", canonical=3)            # variant of known canonical, no ULK
    _lemma(db_session, 5, "جديد")          # content word, NOT introduced -> a real gap
    _fce(db_session, 1, 1, "مِن")
    _fce(db_session, 2, 2, "كتاب")
    _fce(db_session, 3, 4, "اليوم")        # entry points at the variant lemma
    _fce(db_session, 5, 5, "جديد")
    db_session.commit()

    prog = _compute_frequency_core_progress(db_session)
    assert prog is not None
    gap_forms = {g.display_form for g in prog.next_gaps}

    assert "جديد" in gap_forms                 # genuine uncovered content word IS a gap
    assert "مِن" not in gap_forms               # function word excluded
    assert "اليوم" not in gap_forms             # variant of a known canonical = covered

    band = next(b for b in prog.bands if b.top_n == 100)
    assert band.total_count == 3                # function word dropped from denominator
    assert band.learned_count == 2              # كتاب + اليوم(→يوم known)
