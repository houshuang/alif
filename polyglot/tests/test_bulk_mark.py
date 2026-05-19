"""Bulk-mark-remaining tests — the key UX accelerator for intermediate
learners. Patchy knowledge + 'next page presumes known' = fast bootstrap.
"""
from app.services import reading_intake
from app.models import Lemma, UserLemmaKnowledge, PageWord


def test_bulk_mark_skips_function_words(tmp_db):
    """Articles, prepositions, conjunctions should never be enrolled in the
    learner's known list."""
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db, language_code="el",
            body="ο άνθρωπος έγραφε ένα βιβλίο και σε ένα τραπέζι",
        )
        # Process the page first
        reading_intake.get_page_view(db, story.id, 1)

        # Bulk-mark
        n = reading_intake.bulk_mark_remaining_known(db, story.id, 1)
        # Function words (ο, και, σε) should NOT have a ULK
        function_words = {"ο", "και", "σε"}
        ulk_bares = {
            ulk.lemma.lemma_bare
            for ulk in db.query(UserLemmaKnowledge).all()
            if ulk.lemma  # relationship may be lazy
        }
        # Fallback if relationship not loaded
        if not ulk_bares:
            ulk_lemma_ids = [u.lemma_id for u in db.query(UserLemmaKnowledge).all()]
            lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(ulk_lemma_ids)).all()
            ulk_bares = {l.lemma_bare for l in lemmas}
        assert function_words.isdisjoint(ulk_bares), \
            f"function words should be excluded, got: {ulk_bares & function_words}"


def test_bulk_mark_skips_already_marked(tmp_db):
    """If a lemma already has any ULK state (known/unknown/encountered/ignore),
    don't overwrite it. User decisions are sacred."""
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db, language_code="el", body="βιβλίο σπίτι ταξίδι",
        )
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        target = next(t for t in tokens if t["lemma_id"])
        # Explicitly mark this lemma as unknown (now enrols into acquisition)
        reading_intake.mark_lemma(db, lemma_id=target["lemma_id"], state="unknown", fetch_gloss=False)

        # Bulk-mark the rest
        reading_intake.bulk_mark_remaining_known(db, story.id, 1)

        # The explicitly-flagged lemma should still be in the acquisition
        # pipeline, NOT overwritten to 'known' by bulk-mark.
        ulk = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == target["lemma_id"]
        ).first()
        assert ulk.knowledge_state == "acquiring"


def test_bulk_mark_creates_known_uses(tmp_db):
    """Lemmas that are content words AND have no ULK get marked 'known'."""
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι ταξίδι")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)

        content_lemma_ids = [t["lemma_id"] for t in tokens if t["lemma_id"]]
        # None marked before
        existing = db.query(UserLemmaKnowledge).count()
        assert existing == 0

        n = reading_intake.bulk_mark_remaining_known(db, story.id, 1)
        assert n == 3
        marked = db.query(UserLemmaKnowledge).all()
        assert all(m.knowledge_state == "known" for m in marked)
        assert all(m.source == "reading_intake" for m in marked)


def test_bulk_mark_returns_zero_when_all_marked(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        target = next(t["lemma_id"] for t in tokens if t["lemma_id"])
        reading_intake.mark_lemma(db, lemma_id=target, state="known", fetch_gloss=False)

        n = reading_intake.bulk_mark_remaining_known(db, story.id, 1)
        assert n == 0
