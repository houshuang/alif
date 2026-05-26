"""Reading-intake tests. Run without GR-NLP-TOOLKIT loading the heavy pipeline
— el provider degrades gracefully to surface-as-lemma when the model isn't
already loaded for the test process. Tests that touch the pipeline are marked
slow.
"""
from datetime import datetime, timezone

from app.services import reading_intake
from app.services.acquisition_service import submit_acquisition_review
from app.models import Story, Page, PageWord, Lemma, Sentence, UserLemmaKnowledge


def test_split_into_sentences_default_greek_punctuation():
    # The cheap splitter handles . ! ? ; (Greek question mark) and ·.
    parts = reading_intake._split_into_sentences(
        "Καλημέρα. Τι κάνεις; Μια χαρά!"
    )
    assert parts == ["Καλημέρα.", "Τι κάνεις;", "Μια χαρά!"]


def test_split_into_sentences_latin_kal_does_not_break_mid_clause():
    # The 2026-05-26 Eutropius I.1 bug: "ante diem XI Kal. Maias" was cut at
    # "Kal." so the following date words became an orphan sentence.
    text = (
        "Urbs Romana condita est ante diem XI Kal. Maias, "
        "Olympiadis sextae anno tertio."
    )
    parts = reading_intake._split_into_sentences(text, language_code="la")
    assert parts == [
        "Urbs Romana condita est ante diem XI Kal. Maias, "
        "Olympiadis sextae anno tertio."
    ]


def test_split_into_sentences_latin_all_calendar_abbrevs_protected():
    # Each of the four protected abbreviations would otherwise break the
    # sentence at its own dot. Surround each with full month names so this
    # tests only the abbreviation rule, not month-name follow-on edge cases.
    text = (
        "Caesar venit a.d. III Februarias et Non. Apriles "
        "et Id. Maias et Kal. Iunias dixit."
    )
    parts = reading_intake._split_into_sentences(text, language_code="la")
    assert len(parts) == 1
    # Visible text unchanged after restore — dots come back.
    assert parts[0] == text


def test_split_into_sentences_latin_real_terminal_still_splits():
    # The Latin protection must not break real sentence boundaries.
    text = "Romulus urbem condidit. Postea bellum gessit."
    parts = reading_intake._split_into_sentences(text, language_code="la")
    assert parts == ["Romulus urbem condidit.", "Postea bellum gessit."]


def test_split_into_sentences_greek_unaffected_by_latin_protection():
    # "Kal." appears as a name in some Greek text — protection must NOT apply
    # for non-Latin languages.
    text = "Λέει Kal. Επιστρέφει."
    parts = reading_intake._split_into_sentences(text, language_code="el")
    assert parts == ["Λέει Kal.", "Επιστρέφει."]


def test_split_into_sentences_protects_terminals_inside_curly_quotes():
    # LLPSI page 3 actual prose: a quoted question with two `?` inside should
    # be one outer sentence (with the embedded dialog) — not three. The 2026-05-26
    # Reveal bug split this at every interior terminal and `;`.
    text = (
        "Aemilia venit irata et Marcum interrogat: "
        "“Cur eum verberas? Cur puer probus non es?” "
        "Marcus respondet: “Quia Quintus me videt et ridet; "
        "neque laetus sum.” Iulia, quae hic est, cantat;"
    )
    parts = reading_intake._split_into_sentences(text, language_code="la")
    assert parts == [
        "Aemilia venit irata et Marcum interrogat: "
        "“Cur eum verberas? Cur puer probus non es?”",
        "Marcus respondet: “Quia Quintus me videt et ridet; "
        "neque laetus sum.”",
        "Iulia, quae hic est, cantat;",
    ]


def test_split_into_sentences_metalinguistic_quote_does_not_split():
    # `verbum "Marcus" videt` — the bare mention has no inner terminal, so
    # the closing quote must NOT introduce a sentence break.
    text = "Iulius in pagina verbum “Marcus” videt et interrogat."
    parts = reading_intake._split_into_sentences(text, language_code="la")
    assert parts == [
        "Iulius in pagina verbum “Marcus” videt et interrogat."
    ]


def test_split_into_sentences_handles_ascii_and_guillemet_quotes():
    # Same dialog-attribution rule for ASCII "..." (older PDFs / pasted text)
    # and «...» (German/French editions).
    ascii_text = 'Dixit: "Quid agis?" Respondit: "Bene."'
    assert reading_intake._split_into_sentences(ascii_text, language_code="la") == [
        'Dixit: "Quid agis?"',
        'Respondit: "Bene."',
    ]
    guill = "Dixit: «Quid agis?» Respondit: «Bene.»"
    assert reading_intake._split_into_sentences(guill, language_code="la") == [
        "Dixit: «Quid agis?»",
        "Respondit: «Bene.»",
    ]


def test_split_into_sentences_unquoted_text_unaffected_by_dialog_rule():
    # The dialog rule must not regress plain prose — no quotes anywhere.
    text = "Marcus venit. Quintus plorat. Iulia cantat."
    parts = reading_intake._split_into_sentences(text, language_code="la")
    assert parts == ["Marcus venit.", "Quintus plorat.", "Iulia cantat."]


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


def test_mark_unknown_records_failure_lifecycle(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        lemma_id = next(t["lemma_id"] for t in tokens if t["lemma_id"])

        ulk = reading_intake.mark_lemma(
            db, lemma_id=lemma_id, state="unknown", fetch_gloss=False,
        )

        assert ulk.knowledge_state == "acquiring"
        assert ulk.knowledge_origin == "marked_unknown"
        assert ulk.first_failed_at is not None
        assert ulk.last_failed_at is not None
        assert ulk.failure_count == 1

        submit_acquisition_review(db, lemma_id=lemma_id, rating_int=3)
        db.refresh(ulk)
        assert ulk.first_correct_after_failure_at is not None


def test_mark_unknown_restarts_bulk_known_word(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        lemma_id = next(t["lemma_id"] for t in tokens if t["lemma_id"])

        known = reading_intake.mark_lemma(db, lemma_id=lemma_id, state="known")
        assert known.knowledge_state == "known"
        assert known.knowledge_origin == "pre_known"

        restarted = reading_intake.mark_lemma(
            db, lemma_id=lemma_id, state="unknown", fetch_gloss=False,
        )

        assert restarted.knowledge_state == "acquiring"
        assert restarted.acquisition_box == 1
        assert restarted.knowledge_origin == "pre_known"
        assert restarted.first_failed_at is not None
        assert restarted.failure_count == 1


def test_process_page_batch_glosses_new_lemmas(tmp_db, monkeypatch):
    """process_page should call ensure_glosses_batch for every lemma on the
    page right after Phase 2 commit, so the user sees English meanings the
    instant a tap lands."""
    monkeypatch.setattr(reading_intake, "BATCH_GLOSS_ENABLED", True)
    called_with: list[list[int]] = []

    def fake_batch(db, lemma_ids):
        called_with.append(list(lemma_ids))
        return 0

    from app.services import lemma_gloss
    monkeypatch.setattr(lemma_gloss, "ensure_glosses_batch", fake_batch)
    monkeypatch.setattr(reading_intake.lemma_quality, "QUALITY_GATE_ENABLED", False)
    monkeypatch.setattr(reading_intake.body_clean_svc, "BODY_CLEAN_ENABLED", False)

    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        page, _ = reading_intake.get_page_view(db, story.id, 1)
        assert page.processed_at is not None
        assert len(called_with) == 1
        # Both content lemmas (βιβλίο, σπίτι) should have been queued.
        assert len(called_with[0]) == 2


def test_process_page_skips_batch_gloss_when_disabled(tmp_db, monkeypatch):
    """POLYGLOT_BATCH_GLOSS=0 disables the upfront gloss call so cost-sensitive
    deployments can opt out."""
    monkeypatch.setattr(reading_intake, "BATCH_GLOSS_ENABLED", False)
    called = {"n": 0}

    def fake_batch(db, lemma_ids):
        called["n"] += 1
        return 0

    from app.services import lemma_gloss
    monkeypatch.setattr(lemma_gloss, "ensure_glosses_batch", fake_batch)
    monkeypatch.setattr(reading_intake.lemma_quality, "QUALITY_GATE_ENABLED", False)
    monkeypatch.setattr(reading_intake.body_clean_svc, "BODY_CLEAN_ENABLED", False)

    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        reading_intake.get_page_view(db, story.id, 1)
        assert called["n"] == 0


def test_process_page_normalizes_pdf_linebreak_hyphens_before_storage(tmp_db, monkeypatch):
    monkeypatch.setattr(reading_intake, "BATCH_GLOSS_ENABLED", False)
    monkeypatch.setattr(reading_intake.lemma_quality, "QUALITY_GATE_ENABLED", False)
    monkeypatch.setattr(reading_intake.body_clean_svc, "BODY_CLEAN_ENABLED", False)

    with tmp_db() as db:
        story = reading_intake.import_paste(
            db,
            language_code="el",
            body="Η δομή κατοίκη-\nσαν στην περιοχή.",
        )
        reading_intake.get_page_view(db, story.id, 1)
        surfaces = [
            w.surface_form
            for w in db.query(PageWord).order_by(PageWord.position).all()
        ]
        assert "κατοίκησαν" in surfaces
        assert "κατοίκη" not in surfaces
        assert "-" not in surfaces
        assert "σαν" not in surfaces


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


def test_page_view_treats_function_word_bare_as_noncontent(tmp_db):
    """Legacy rows may have a function-word bare form before word_category was
    backfilled. The reader should still grey them out and not show them as new
    vocabulary."""
    with tmp_db() as db:
        story = Story(language_code="el", body_src="παρά", source="paste", page_count=1)
        db.add(story)
        db.flush()
        page = Page(story_id=story.id, page_number=1, body_src="παρά")
        db.add(page)
        db.flush()
        lemma = Lemma(
            language_code="el",
            lemma_form="παρά",
            lemma_bare="παρα",
            source="test",
            word_category=None,
        )
        db.add(lemma)
        db.flush()
        db.add(PageWord(
            page_id=page.id,
            position=0,
            surface_form="παρά",
            lemma_id=lemma.lemma_id,
            sentence_index=0,
        ))
        db.commit()

        _, tokens = reading_intake._build_token_view(db, page)
        token = tokens[0]
        assert token["is_function_word"] is True
        assert token["is_new"] is False


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


# ─── Page translation (Show English reveal) ─────────────────────────────────


def test_ensure_page_translation_generates_then_caches(tmp_db, monkeypatch):
    calls = {"n": 0}

    def fake_translate(language_code, text):
        calls["n"] += 1
        assert language_code == "el"
        assert "βιβλίο" in text
        return "  book house  "  # trimmed on write

    monkeypatch.setattr(reading_intake, "_translate_page_text", fake_translate)

    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        sid = story.id

        text, generated, sentences = reading_intake.ensure_page_translation(db, sid, 1)
        assert generated is True
        assert text == "book house"
        assert sentences == []  # legacy path — no harvested Sentence rows
        assert calls["n"] == 1
        # Persisted on the Page row.
        page = db.query(Page).filter(Page.story_id == sid, Page.page_number == 1).first()
        assert page.translation_en == "book house"
        assert page.translated_at is not None

    # A fresh session serves from cache — no second LLM call.
    with tmp_db() as db:
        text2, generated2, sentences2 = reading_intake.ensure_page_translation(db, sid, 1)
        assert text2 == "book house"
        assert generated2 is False
        assert sentences2 == []
        assert calls["n"] == 1


def test_ensure_page_translation_llm_failure_leaves_null_for_retry(tmp_db, monkeypatch):
    monkeypatch.setattr(reading_intake, "_translate_page_text", lambda lc, t: None)
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        text, generated, sentences = reading_intake.ensure_page_translation(db, story.id, 1)
        assert text is None
        assert generated is False
        assert sentences == []
        page = db.query(Page).filter(Page.story_id == story.id).first()
        assert page.translation_en is None  # not cached → retried next reveal


def test_ensure_page_translation_blank_page_caches_empty_without_llm(tmp_db, monkeypatch):
    def boom(lc, t):  # must not be called for a no-alpha page
        raise AssertionError("LLM should not run for a blank page")

    monkeypatch.setattr(reading_intake, "_translate_page_text", boom)
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="123 — 456 ...")
        text, generated, sentences = reading_intake.ensure_page_translation(db, story.id, 1)
        assert text == ""
        assert generated is False
        assert sentences == []
        page = db.query(Page).filter(Page.story_id == story.id).first()
        assert page.translation_en == ""
        assert page.translated_at is not None


def test_ensure_page_translation_missing_page_returns_none(tmp_db):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        assert reading_intake.ensure_page_translation(db, story.id, 99) is None


def test_ensure_page_translation_returns_per_sentence_when_harvested(tmp_db, monkeypatch):
    """Modern path — page has harvested Sentence rows. Reveal interleaves per
    sentence, so the endpoint returns one entry per row keyed by
    sentence_index_in_page; the page-level translation_en is the concatenation."""

    def boom_page(lc, t):  # legacy whole-page LLM call must not fire
        raise AssertionError("Per-sentence path must not call the page translator")

    monkeypatch.setattr(reading_intake, "_translate_page_text", boom_page)

    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        sid = story.id
        page = db.query(Page).filter(Page.story_id == sid).first()
        page_id = page.id
        # Two harvested sentences on the page, one already translated, one NULL.
        db.add_all([
            Sentence(
                language_code="el", text="Το βιβλίο.",
                translation_en="The book.", source="textbook",
                story_id=sid, page_id=page_id, sentence_index_in_page=0,
                is_active=True,
            ),
            Sentence(
                language_code="el", text="Το σπίτι.",
                translation_en=None, source="textbook",
                story_id=sid, page_id=page_id, sentence_index_in_page=1,
                is_active=True,
            ),
        ])
        db.commit()

        # Lazy batch translator: fills the second sentence only.
        def fake_batch(language_code, items):
            assert language_code == "el"
            ids = {it["id"] for it in items}
            return {item_id: "The house." for item_id in ids}

        from app.services import material_generator
        monkeypatch.setattr(material_generator, "translate_sentences_batch", fake_batch)

        text, generated, sentences = reading_intake.ensure_page_translation(db, sid, 1)
        assert generated is True
        assert sentences == [
            {"sentence_index_in_page": 0, "translation_en": "The book."},
            {"sentence_index_in_page": 1, "translation_en": "The house."},
        ]
        assert text == "The book. The house."
        # NULL was filled in place; page-level cache mirrors the join.
        rows = (
            db.query(Sentence)
            .filter(Sentence.page_id == page_id)
            .order_by(Sentence.sentence_index_in_page)
            .all()
        )
        assert [r.translation_en for r in rows] == ["The book.", "The house."]
        fresh_page = db.query(Page).filter(Page.id == page_id).first()
        assert fresh_page.translation_en == "The book. The house."

    # Second call serves from the row cache — batch translator must not fire.
    with tmp_db() as db:
        from app.services import material_generator
        monkeypatch.setattr(
            material_generator, "translate_sentences_batch",
            lambda lc, items: (_ for _ in ()).throw(AssertionError("re-fetch should be cached")),
        )
        text2, generated2, sentences2 = reading_intake.ensure_page_translation(db, sid, 1)
        assert generated2 is False
        assert text2 == "The book. The house."
        assert [s["translation_en"] for s in sentences2] == ["The book.", "The house."]
