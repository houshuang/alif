"""Reading-intake tests. Run without GR-NLP-TOOLKIT loading the heavy pipeline
— el provider degrades gracefully to surface-as-lemma when the model isn't
already loaded for the test process. Tests that touch the pipeline are marked
slow.
"""
from datetime import datetime, timezone

from app.services import reading_intake
from app.models import Story, Page, PageWord, Lemma, UserLemmaKnowledge


def test_paste_creates_story_and_single_page(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db,
            language_code="el",
            body="Καλημέρα κόσμε. Είμαι εδώ.",
            title="test",
        )
        assert story.id is not None
        assert story.page_count == 1
        pages = db.query(Page).filter(Page.story_id == story.id).all()
        assert len(pages) == 1
        assert pages[0].processed_at is None  # lazy — not tokenized yet
        # No PageWord rows yet
        assert db.query(PageWord).count() == 0


def test_get_page_processes_lazily(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(
            db, language_code="el", body="βιβλίο σπίτι",
        )
        page, tokens = reading_intake.get_page_view(db, story.id, 1)
        assert page.processed_at is not None  # processed on first view
        word_tokens = [t for t in tokens if not t["is_punctuation"]]
        assert len(word_tokens) >= 2

        # Second view doesn't re-process
        first_processed = page.processed_at
        page2, _ = reading_intake.get_page_view(db, story.id, 1)
        assert page2.processed_at == first_processed


def test_get_page_retries_quality_gate_for_processed_unverified_page(tmp_db, monkeypatch):
    from app.services import sentence_harvest

    monkeypatch.setattr(reading_intake.lemma_quality, "QUALITY_GATE_ENABLED", False)
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        page, _ = reading_intake.get_page_view(db, story.id, 1)
        assert page.processed_at is not None
        assert page.mappings_verified_at is None
        story_id = story.id

    calls = {"verify": 0}

    def fake_verify(db, page):
        calls["verify"] += 1
        page.mappings_verified_at = datetime.now(timezone.utc)
        db.commit()
        return 0

    monkeypatch.setattr(reading_intake.lemma_quality, "QUALITY_GATE_ENABLED", True)
    monkeypatch.setattr(reading_intake.lemma_quality, "verify_page_mappings", fake_verify)
    monkeypatch.setattr(sentence_harvest, "harvest_page_sentences", lambda db, page: 0)

    with tmp_db() as db:
        page, _ = reading_intake.get_page_view(db, story_id, 1)
        assert calls["verify"] == 1
        assert page.mappings_verified_at is not None


def test_mark_lemma_creates_ulk(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        page, tokens = reading_intake.get_page_view(db, story.id, 1)
        target = next(t for t in tokens if t["lemma_id"] is not None)

        ulk = reading_intake.mark_lemma(
            db, lemma_id=target["lemma_id"], state="unknown", fetch_gloss=False,
        )
        # mark_lemma(state='unknown') routes through start_acquisition, so the
        # word lands in Leitner Box 1 (acquiring) rather than parking in a
        # standalone 'unknown' state. The intent — "user wants to learn this
        # word" — is identical; the new state engages the SRS engine.
        assert ulk.knowledge_state == "acquiring"
        assert ulk.acquisition_box == 1
        assert ulk.entered_acquiring_at is not None
        assert ulk.source == "reading_intake"


def test_mark_lemma_updates_existing(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        page, tokens = reading_intake.get_page_view(db, story.id, 1)
        lemma_id = next(t["lemma_id"] for t in tokens if t["lemma_id"])

        reading_intake.mark_lemma(db, lemma_id=lemma_id, state="unknown", fetch_gloss=False)
        # Marking as 'known' after enrolment is allowed — the learner is
        # signalling that they actually do know the word.
        reading_intake.mark_lemma(db, lemma_id=lemma_id, state="known")
        ulks = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lemma_id
        ).all()
        assert len(ulks) == 1
        assert ulks[0].knowledge_state == "known"


def test_mark_lemma_clear_deletes_ulk(tmp_db):
    """`clear` is the third tap in the reading screen's cycle
    (unknown → encountered → clear). It must delete the ULK so the lemma
    returns to a "no state" baseline."""
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        page, tokens = reading_intake.get_page_view(db, story.id, 1)
        lemma_id = next(t["lemma_id"] for t in tokens if t["lemma_id"])

        reading_intake.mark_lemma(db, lemma_id=lemma_id, state="unknown", fetch_gloss=False)
        assert db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lemma_id
        ).count() == 1

        result = reading_intake.mark_lemma(db, lemma_id=lemma_id, state="clear")
        assert result is None
        assert db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lemma_id
        ).count() == 0

        # Idempotent: clearing again on a lemma without a ULK is a no-op.
        result2 = reading_intake.mark_lemma(db, lemma_id=lemma_id, state="clear")
        assert result2 is None


def test_page_view_classifies_token_states(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        # Mark one known
        lemma_id = next(t["lemma_id"] for t in tokens if t["lemma_id"])
        reading_intake.mark_lemma(db, lemma_id=lemma_id, state="known")

        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        word_tokens = [t for t in tokens if not t["is_punctuation"]]
        assert any(t["is_known"] for t in word_tokens)
        assert any(t["is_new"] for t in word_tokens)


# ─── warm_pages_ahead ─────────────────────────────────────────────────────


def _seed_multi_page_story(db, n_pages: int, body_per_page: str = "βιβλίο σπίτι") -> Story:
    """Create a Story with `n_pages` raw, unprocessed pages."""
    story = Story(
        language_code="el",
        title="multi-page test",
        source="paste",
        page_count=n_pages,
        status="active",
    )
    db.add(story)
    db.flush()
    for i in range(1, n_pages + 1):
        db.add(Page(story_id=story.id, page_number=i, body_src=f"{body_per_page} (page {i})"))
    db.commit()
    return story


def _stub_quality_gate(monkeypatch):
    """Make process_page's quality gate auto-succeed without LLM calls."""
    from app.services import sentence_harvest

    def fake_verify(db, page):
        page.mappings_verified_at = datetime.now(timezone.utc)
        db.commit()
        return 0

    monkeypatch.setattr(reading_intake.lemma_quality, "QUALITY_GATE_ENABLED", True)
    monkeypatch.setattr(reading_intake.lemma_quality, "verify_page_mappings", fake_verify)
    monkeypatch.setattr(sentence_harvest, "harvest_page_sentences", lambda db, page: 0)


def test_warm_pages_ahead_empty_story_warms_first_buffer(tmp_db, monkeypatch):
    """A story the user has never opened: buffer pages should be processed
    starting from page 1."""
    _stub_quality_gate(monkeypatch)
    with tmp_db() as db:
        story = _seed_multi_page_story(db, n_pages=20)

        summary = reading_intake.warm_pages_ahead(db, story.id, buffer=5)

        assert summary["last_viewed"] == 0
        assert summary["ahead_before"] == 0
        assert summary["ahead_after"] == 5
        assert summary["pages_warmed"] == [1, 2, 3, 4, 5]
        assert summary["errors"] == []

        verified = (
            db.query(Page)
            .filter(Page.story_id == story.id, Page.mappings_verified_at.isnot(None))
            .order_by(Page.page_number)
            .all()
        )
        assert [p.page_number for p in verified] == [1, 2, 3, 4, 5]


def test_warm_pages_ahead_resumes_after_last_viewed(tmp_db, monkeypatch):
    """User has opened page 10. Cron should warm 11-15, not earlier."""
    _stub_quality_gate(monkeypatch)
    with tmp_db() as db:
        story = _seed_multi_page_story(db, n_pages=30)
        # Mark page 10 as viewed
        page10 = db.query(Page).filter_by(story_id=story.id, page_number=10).one()
        page10.viewed_at = datetime.now(timezone.utc)
        db.commit()

        summary = reading_intake.warm_pages_ahead(db, story.id, buffer=5)

        assert summary["last_viewed"] == 10
        assert summary["pages_warmed"] == [11, 12, 13, 14, 15]
        # Page 1-9 still untouched
        assert db.query(Page).filter(
            Page.story_id == story.id,
            Page.page_number < 10,
            Page.mappings_verified_at.isnot(None),
        ).count() == 0


def test_warm_pages_ahead_noop_when_buffer_already_full(tmp_db, monkeypatch):
    """If 5 pages ahead are already verified, the cron makes 0 calls."""
    _stub_quality_gate(monkeypatch)
    with tmp_db() as db:
        story = _seed_multi_page_story(db, n_pages=20)
        # Manually mark pages 1-5 as verified (simulate prior cron pass)
        now = datetime.now(timezone.utc)
        for n in range(1, 6):
            p = db.query(Page).filter_by(story_id=story.id, page_number=n).one()
            p.processed_at = now
            p.mappings_verified_at = now
        db.commit()

        # Spy on process_page so we can assert it isn't called
        process_calls = {"n": 0}
        orig_process = reading_intake.process_page

        def spy_process(db, page, *, force=False):
            process_calls["n"] += 1
            return orig_process(db, page, force=force)

        monkeypatch.setattr(reading_intake, "process_page", spy_process)

        summary = reading_intake.warm_pages_ahead(db, story.id, buffer=5)

        assert summary["ahead_before"] == 5
        assert summary["ahead_after"] == 5
        assert summary["pages_warmed"] == []
        assert process_calls["n"] == 0


def test_warm_pages_ahead_partial_buffer_fills_gap(tmp_db, monkeypatch):
    """If only 2 ahead pages are verified, fill the remaining 3 to reach buffer=5."""
    _stub_quality_gate(monkeypatch)
    with tmp_db() as db:
        story = _seed_multi_page_story(db, n_pages=20)
        now = datetime.now(timezone.utc)
        for n in [1, 2]:
            p = db.query(Page).filter_by(story_id=story.id, page_number=n).one()
            p.processed_at = now
            p.mappings_verified_at = now
        db.commit()

        summary = reading_intake.warm_pages_ahead(db, story.id, buffer=5)

        assert summary["ahead_before"] == 2
        assert summary["ahead_after"] == 5
        assert summary["pages_warmed"] == [3, 4, 5]


def test_warm_pages_ahead_handles_end_of_book(tmp_db, monkeypatch):
    """If user is near the end and fewer than buffer pages remain, process
    only what's left without error."""
    _stub_quality_gate(monkeypatch)
    with tmp_db() as db:
        story = _seed_multi_page_story(db, n_pages=10)
        # User has read up to page 8 → only 9, 10 remain
        page8 = db.query(Page).filter_by(story_id=story.id, page_number=8).one()
        page8.viewed_at = datetime.now(timezone.utc)
        db.commit()

        summary = reading_intake.warm_pages_ahead(db, story.id, buffer=5)

        assert summary["last_viewed"] == 8
        assert summary["pages_warmed"] == [9, 10]
        assert summary["ahead_after"] == 2


def test_warm_pages_ahead_max_to_warm_caps_per_run(tmp_db, monkeypatch):
    """``max_to_warm`` lets the cron bound a single pass even when more pages
    would be needed to reach the buffer — useful as a safety valve."""
    _stub_quality_gate(monkeypatch)
    with tmp_db() as db:
        story = _seed_multi_page_story(db, n_pages=20)

        summary = reading_intake.warm_pages_ahead(
            db, story.id, buffer=10, max_to_warm=3,
        )

        assert summary["pages_warmed"] == [1, 2, 3]
        assert summary["ahead_after"] == 3


def test_warm_pages_ahead_skips_pages_when_gate_fails(tmp_db, monkeypatch):
    """If the quality gate returns None (LLM failure), the page is reported
    in `errors` and not counted as warmed. Buffer stays partially filled."""
    from app.services import sentence_harvest

    monkeypatch.setattr(reading_intake.lemma_quality, "QUALITY_GATE_ENABLED", True)
    # Gate is silent — leaves mappings_verified_at NULL
    monkeypatch.setattr(reading_intake.lemma_quality, "verify_page_mappings",
                        lambda db, page: 0)
    monkeypatch.setattr(sentence_harvest, "harvest_page_sentences", lambda db, page: 0)

    with tmp_db() as db:
        story = _seed_multi_page_story(db, n_pages=10)

        summary = reading_intake.warm_pages_ahead(db, story.id, buffer=3)

        # process_page runs and stamps processed_at, but mappings_verified_at
        # stays NULL since the gate didn't set it.
        assert summary["pages_warmed"] == []
        assert summary["ahead_after"] == 0


def test_warm_all_active_stories_iterates_oldest_first(tmp_db, monkeypatch):
    """Multiple active stories: cron walks them in created_at order, so the
    earliest-imported (typically the actively-read one) is topped up first."""
    _stub_quality_gate(monkeypatch)
    with tmp_db() as db:
        s1 = _seed_multi_page_story(db, n_pages=5)
        s2 = _seed_multi_page_story(db, n_pages=5)
        # Ensure created_at ordering is deterministic
        s1.created_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
        s2.created_at = datetime(2026, 5, 5, tzinfo=timezone.utc)
        db.commit()

        summaries = reading_intake.warm_all_active_stories(
            db, language_code="el", buffer=3,
        )

        assert [s["story_id"] for s in summaries] == [s1.id, s2.id]
        assert summaries[0]["pages_warmed"] == [1, 2, 3]
        assert summaries[1]["pages_warmed"] == [1, 2, 3]
