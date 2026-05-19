"""Reading-intake tests. Run without GR-NLP-TOOLKIT loading the heavy pipeline
— el provider degrades gracefully to surface-as-lemma when the model isn't
already loaded for the test process. Tests that touch the pipeline are marked
slow.
"""
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
