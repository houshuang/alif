"""Bulk-mark-remaining tests — the key UX accelerator for intermediate
learners. Patchy knowledge + 'next page presumes known' = fast bootstrap.
"""
from datetime import datetime, timezone

from app.services import reading_intake
from app.models import (
    Language, Lemma, Page, PageReviewLog, PageWord, Story, UserLemmaKnowledge,
)


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


def test_apply_page_review_greens_untapped_excludes_tapped(tmp_db):
    """Advancing a page = a green comprehension review over untapped words:
    new -> presumed known + confirmed; assumed-known -> confirmed; the word the
    user tapped unknown is excluded and keeps its red signal."""
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db, language_code="el", body="βιβλίο σπίτι ταξίδι θάλασσα",
        )
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        ids = [t["lemma_id"] for t in tokens if t["lemma_id"]]
        assert len(ids) >= 4

        tapped = ids[0]
        reading_intake.mark_lemma(db, lemma_id=tapped, state="unknown", fetch_gloss=False)

        assumed = ids[1]
        db.add(UserLemmaKnowledge(
            lemma_id=assumed, knowledge_state="known", fsrs_card_json=None,
            source="bulk", knowledge_origin="cognate_known",
        ))
        db.commit()

        res = reading_intake.apply_page_review(db, story.id, 1, tapped_lemma_ids=[tapped])

        # Tapped-unknown word excluded — still acquiring, not green-reviewed.
        u_tapped = db.query(UserLemmaKnowledge).filter_by(lemma_id=tapped).one()
        assert u_tapped.knowledge_state == "acquiring"

        # Assumed-known word confirmed by reading exposure.
        u_assumed = db.query(UserLemmaKnowledge).filter_by(lemma_id=assumed).one()
        assert u_assumed.knowledge_state == "known"
        assert u_assumed.confirmed_at is not None

        # Remaining never-seen words presumed known + confirmed.
        for lid in ids[2:]:
            u = db.query(UserLemmaKnowledge).filter_by(lemma_id=lid).one()
            assert u.knowledge_state == "known"
            assert u.confirmed_at is not None

        assert res["newly_known"] >= 1 and res["confirmed"] >= 1


# ─── Offline-queue contract (self-contained + idempotent page advance) ───────

def test_apply_page_review_applies_offline_reds_and_yellows(tmp_db):
    """Offline, the per-tap markWord calls never reached the server, so the page
    submit must apply the red/yellow taps itself: reds enrol into acquisition
    (with a recorded failure), yellows become 'encountered', and the rest are
    presumed known."""
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db, language_code="el", body="βιβλίο σπίτι ταξίδι θάλασσα",
        )
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        ids = [t["lemma_id"] for t in tokens if t["lemma_id"]]
        assert len(ids) >= 4

        red, yellow = ids[0], ids[1]
        # No prior mark_lemma calls — simulating a fully offline page.
        res = reading_intake.apply_page_review(
            db, story.id, 1,
            unknown_lemma_ids=[red],
            encountered_lemma_ids=[yellow],
            client_review_id="offline-1",
        )

        u_red = db.query(UserLemmaKnowledge).filter_by(lemma_id=red).one()
        assert u_red.knowledge_state == "acquiring"
        assert u_red.failure_count == 1
        assert u_red.acquisition_box == 1

        u_yellow = db.query(UserLemmaKnowledge).filter_by(lemma_id=yellow).one()
        assert u_yellow.knowledge_state == "encountered"

        for lid in ids[2:]:
            u = db.query(UserLemmaKnowledge).filter_by(lemma_id=lid).one()
            assert u.knowledge_state == "known"
            assert u.confirmed_at is not None

        assert res["marked_unknown"] == 1
        assert res["marked_encountered"] == 1
        assert res["newly_known"] >= 2
        assert res["duplicate"] is False


def test_apply_page_review_idempotent_on_client_review_id(tmp_db):
    """A re-flush of the same queued entry must not apply a second time."""
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db, language_code="el", body="βιβλίο σπίτι ταξίδι θάλασσα",
        )
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        ids = [t["lemma_id"] for t in tokens if t["lemma_id"]]
        red = ids[0]

        first = reading_intake.apply_page_review(
            db, story.id, 1, unknown_lemma_ids=[red], client_review_id="dup-1",
        )
        assert first["duplicate"] is False
        assert first["marked_unknown"] == 1

        fail_before = db.query(UserLemmaKnowledge).filter_by(lemma_id=red).one().failure_count
        log_rows = db.query(PageReviewLog).filter_by(client_review_id="dup-1").count()
        assert log_rows == 1

        # Replay — same client_review_id.
        second = reading_intake.apply_page_review(
            db, story.id, 1, unknown_lemma_ids=[red], client_review_id="dup-1",
        )
        assert second["duplicate"] is True
        # Stored counts are echoed back.
        assert second["marked_unknown"] == first["marked_unknown"]
        assert second["newly_known"] == first["newly_known"]

        # No double-apply: failure stays at 1, still exactly one log row.
        fail_after = db.query(UserLemmaKnowledge).filter_by(lemma_id=red).one().failure_count
        assert fail_after == fail_before == 1
        assert db.query(PageReviewLog).filter_by(client_review_id="dup-1").count() == 1


def test_apply_page_review_online_red_not_double_counted(tmp_db):
    """Online the live markWord already enrolled the red word (it's now
    'acquiring' with one failure). The authoritative page submit re-sends the
    same red inline, but must NOT record a second failure."""
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db, language_code="el", body="βιβλίο σπίτι ταξίδι",
        )
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        ids = [t["lemma_id"] for t in tokens if t["lemma_id"]]
        red = ids[0]

        # Live per-tap markWord (online).
        reading_intake.mark_lemma(db, lemma_id=red, state="unknown", fetch_gloss=False)
        u = db.query(UserLemmaKnowledge).filter_by(lemma_id=red).one()
        assert u.knowledge_state == "acquiring"
        assert u.failure_count == 1

        # Page submit carries the same red inline.
        res = reading_intake.apply_page_review(
            db, story.id, 1, unknown_lemma_ids=[red], client_review_id="online-1",
        )
        # Already-acquiring → skipped, no second failure recorded.
        assert res["marked_unknown"] == 0
        u2 = db.query(UserLemmaKnowledge).filter_by(lemma_id=red).one()
        assert u2.failure_count == 1


def test_apply_page_review_offline_lapses_assumed_known(tmp_db):
    """Offline red on an assumed-known scaffold word (no live tap reached the
    server) must lapse it into acquisition."""
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        ids = [t["lemma_id"] for t in tokens if t["lemma_id"]]
        target = ids[0]
        db.add(UserLemmaKnowledge(
            lemma_id=target, knowledge_state="known", fsrs_card_json=None,
            source="bulk", knowledge_origin="pre_known",
        ))
        db.commit()

        reading_intake.apply_page_review(
            db, story.id, 1, unknown_lemma_ids=[target], client_review_id="lapse-1",
        )
        u = db.query(UserLemmaKnowledge).filter_by(lemma_id=target).one()
        assert u.knowledge_state == "acquiring"
        assert u.failure_count == 1


def test_apply_page_review_latin(tmp_db):
    """The page-advance is language-agnostic — same path serves Latin. Built
    from manual rows so the test doesn't need the LatinCy model installed."""
    with tmp_db() as db:
        db.add(Language(code="la", name="Latin", script="latin",
                        direction="ltr", accent_display="macrons_off"))
        story = Story(language_code="la", source="paste", status="active",
                      title="Eutropius", page_count=1)
        db.add(story)
        db.flush()
        page = Page(story_id=story.id, page_number=1, body_src="consul bellum gerit",
                    processed_at=datetime.now(timezone.utc), total_words=3)
        db.add(page)
        db.flush()

        forms = ["consul", "bellum", "gero"]
        lemma_ids = []
        for i, form in enumerate(forms):
            lemma = Lemma(language_code="la", lemma_form=form, lemma_bare=form,
                          pos="noun", gloss_en=form, source="manual",
                          gates_completed_at=datetime.now(timezone.utc))
            db.add(lemma)
            db.flush()
            lemma_ids.append(lemma.lemma_id)
            db.add(PageWord(page_id=page.id, position=i, surface_form=form,
                            lemma_id=lemma.lemma_id))
        db.commit()

        red = lemma_ids[0]
        res = reading_intake.apply_page_review(
            db, story.id, 1, unknown_lemma_ids=[red], client_review_id="la-1",
        )
        assert res["marked_unknown"] == 1
        assert res["newly_known"] == 2  # the two untapped content words

        u_red = db.query(UserLemmaKnowledge).filter_by(lemma_id=red).one()
        assert u_red.knowledge_state == "acquiring"
        for lid in lemma_ids[1:]:
            u = db.query(UserLemmaKnowledge).filter_by(lemma_id=lid).one()
            assert u.knowledge_state == "known"
