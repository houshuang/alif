"""Tiny-gloss tests. CLI is mocked — real CLI smoke test runs manually."""
import pytest

from app.services import lemma_gloss, reading_intake
from app.models import Lemma, UserLemmaKnowledge


def test_ensure_gloss_skips_when_present(tmp_db, monkeypatch):
    with tmp_db() as db:
        l = Lemma(language_code="el", lemma_form="βιβλίο", lemma_bare="βιβλιο",
                  gloss_en="book", source="manual")
        db.add(l); db.commit()
        called = []
        monkeypatch.setattr(lemma_gloss, "_call_claude_for_gloss",
                            lambda lemma, context: called.append(lemma) or "should-not-be-used")
        result = lemma_gloss.ensure_gloss(db, l.lemma_id)
        assert called == []
        assert result.gloss_en == "book"


def test_ensure_gloss_fetches_when_missing(tmp_db, monkeypatch):
    with tmp_db() as db:
        l = Lemma(language_code="el", lemma_form="σπίτι", lemma_bare="σπιτι", source="manual")
        db.add(l); db.commit()
        monkeypatch.setattr(lemma_gloss, "_call_claude_for_gloss",
                            lambda lemma, context: "house")
        result = lemma_gloss.ensure_gloss(db, l.lemma_id)
        assert result.gloss_en == "house"


def test_ensure_gloss_handles_cli_failure(tmp_db, monkeypatch):
    """If Claude returns None, leave gloss NULL — caller will retry later."""
    with tmp_db() as db:
        l = Lemma(language_code="el", lemma_form="οικογένεια", lemma_bare="οικογενεια", source="manual")
        db.add(l); db.commit()
        monkeypatch.setattr(lemma_gloss, "_call_claude_for_gloss", lambda lemma, context: None)
        result = lemma_gloss.ensure_gloss(db, l.lemma_id)
        assert result.gloss_en is None


def test_mark_unknown_triggers_gloss(tmp_db, monkeypatch):
    """End-to-end: mark a lemma 'unknown' → its gloss gets populated via the
    integration in reading_intake.mark_lemma.

    Disable BATCH_GLOSS so the upfront process_page gloss call doesn't preempt
    this test's mock of the single-form code path.
    """
    monkeypatch.setattr(reading_intake, "BATCH_GLOSS_ENABLED", False)
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="ταξίδι")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)
        lemma_id = next(t["lemma_id"] for t in tokens if t["lemma_id"])

        monkeypatch.setattr(lemma_gloss, "_call_claude_for_gloss",
                            lambda lemma, context: "trip, journey")
        reading_intake.mark_lemma(db, lemma_id=lemma_id, state="unknown")

        l = db.get(Lemma, lemma_id)
        assert l.gloss_en == "trip, journey"


def test_batch_gloss(tmp_db, monkeypatch):
    with tmp_db() as db:
        l1 = Lemma(language_code="el", lemma_form="βιβλίο", lemma_bare="βιβλιο", source="m")
        l2 = Lemma(language_code="el", lemma_form="σπίτι", lemma_bare="σπιτι", source="m")
        l3 = Lemma(language_code="el", lemma_form="οικογένεια", lemma_bare="οικογενεια",
                   gloss_en="family", source="m")  # already glossed; skipped
        db.add_all([l1, l2, l3]); db.commit()

        monkeypatch.setattr(lemma_gloss, "_call_claude_for_gloss_batch",
                            lambda lemmas, language_code: ["book", "house"])

        count = lemma_gloss.ensure_glosses_batch(db, [l1.lemma_id, l2.lemma_id, l3.lemma_id])
        assert count == 2
        db.refresh(l1); db.refresh(l2); db.refresh(l3)
        assert l1.gloss_en == "book"
        assert l2.gloss_en == "house"
        assert l3.gloss_en == "family"  # unchanged


def test_batch_gloss_skips_function_words_and_proper_names(tmp_db, monkeypatch):
    """Function words and proper names don't get LLM calls — the gloss is
    either uninteresting (articles, particles) or self-evident (a name)."""
    with tmp_db() as db:
        article = Lemma(language_code="el", lemma_form="ο", lemma_bare="ο",
                        word_category="function_word", source="m")
        name = Lemma(language_code="el", lemma_form="Τίγρης", lemma_bare="τιγρης",
                     word_category="proper_name", source="m")
        content = Lemma(language_code="el", lemma_form="βιβλίο", lemma_bare="βιβλιο", source="m")
        db.add_all([article, name, content]); db.commit()

        sent_to_llm = {"lemmas": None}

        def fake_call(lemmas, language_code):
            sent_to_llm["lemmas"] = [l.lemma_form for l in lemmas]
            return ["book"]

        monkeypatch.setattr(lemma_gloss, "_call_claude_for_gloss_batch", fake_call)

        count = lemma_gloss.ensure_glosses_batch(
            db, [article.lemma_id, name.lemma_id, content.lemma_id]
        )
        assert count == 1
        # The LLM never saw the article or the proper name.
        assert sent_to_llm["lemmas"] == ["βιβλίο"]


def test_batch_gloss_chunks_large_input(tmp_db, monkeypatch):
    """A batch of 120 lemmas with chunk_size=50 should produce 3 CLI calls
    (50 + 50 + 20), each chunk committing independently."""
    monkeypatch.setattr(lemma_gloss, "BATCH_CHUNK_SIZE", 50)
    with tmp_db() as db:
        lemmas = []
        for i in range(120):
            l = Lemma(language_code="el", lemma_form=f"λ{i}", lemma_bare=f"λ{i}", source="m")
            lemmas.append(l)
        db.add_all(lemmas); db.commit()

        chunk_sizes: list[int] = []

        def fake_call(chunk, language_code):
            chunk_sizes.append(len(chunk))
            return [f"gloss-{l.lemma_form}" for l in chunk]

        monkeypatch.setattr(lemma_gloss, "_call_claude_for_gloss_batch", fake_call)

        count = lemma_gloss.ensure_glosses_batch(db, [l.lemma_id for l in lemmas])
        assert count == 120
        assert chunk_sizes == [50, 50, 20]
