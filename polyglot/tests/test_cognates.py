"""Cognate infrastructure tests. The external-cognate LLM path is mocked —
we test only the wiring (schema fields, auto-link, propagation).
"""
import json

from app.models import Lemma, UserLemmaKnowledge, UserProfile
from app.services import reading_intake
from app.services import cognate_detector
from app.services.cognate_detector import (
    link_intra_greek_cognates,
    propagate_known_via_cognate,
    get_user_profile,
)


def test_intra_greek_auto_link(tmp_db):
    """When a Modern Greek lemma is created and an Ancient Greek lemma with
    the same lemma_bare already exists, they should auto-link both ways."""
    with tmp_db() as db:
        # Add 'grc' as a language so the test can use it
        from app.models import Language
        db.add(Language(code="grc", name="Ancient Greek", script="greek",
                        direction="ltr", accent_display="polytonic"))
        db.commit()

        ancient = Lemma(language_code="grc", lemma_form="φιλία",
                        lemma_bare="φιλια", source="manual")
        db.add(ancient)
        db.commit()

        modern = Lemma(language_code="el", lemma_form="φιλία",
                       lemma_bare="φιλια", source="reading_intake")
        db.add(modern)
        db.flush()

        link_intra_greek_cognates(db, modern)
        db.commit()

        db.refresh(modern); db.refresh(ancient)
        assert modern.cognate_lemma_id == ancient.lemma_id
        assert ancient.cognate_lemma_id == modern.lemma_id


def test_intra_greek_no_match_no_link(tmp_db):
    """If no counterpart exists, no link should be created and no error."""
    with tmp_db() as db:
        modern = Lemma(language_code="el", lemma_form="φιλία",
                       lemma_bare="φιλια", source="reading_intake")
        db.add(modern)
        db.flush()
        result = link_intra_greek_cognates(db, modern)
        assert result is None
        assert modern.cognate_lemma_id is None


def test_mark_known_propagates_to_cognate(tmp_db):
    """Marking a Modern Greek lemma 'known' should set its Ancient cognate
    to 'encountered' (not 'known' — semantic drift)."""
    with tmp_db() as db:
        from app.models import Language
        db.add(Language(code="grc", name="Ancient Greek", script="greek",
                        direction="ltr", accent_display="polytonic"))
        db.commit()

        ancient = Lemma(language_code="grc", lemma_form="φιλία",
                        lemma_bare="φιλια", source="manual")
        modern = Lemma(language_code="el", lemma_form="φιλία",
                       lemma_bare="φιλια", source="reading_intake")
        db.add_all([ancient, modern]); db.commit()
        modern.cognate_lemma_id = ancient.lemma_id
        ancient.cognate_lemma_id = modern.lemma_id
        db.commit()

        reading_intake.mark_lemma(db, lemma_id=modern.lemma_id, state="known")

        ancient_ulk = db.query(UserLemmaKnowledge).filter_by(
            lemma_id=ancient.lemma_id
        ).first()
        assert ancient_ulk is not None
        assert ancient_ulk.knowledge_state == "encountered"  # NOT 'known'
        assert ancient_ulk.source == "cognate_propagation"


def test_propagate_does_not_overwrite_existing(tmp_db):
    """If the cognate already has a ULK in any state, don't change it."""
    with tmp_db() as db:
        from app.models import Language
        db.add(Language(code="grc", name="Ancient Greek", script="greek",
                        direction="ltr", accent_display="polytonic"))
        db.commit()

        ancient = Lemma(language_code="grc", lemma_form="ἄλογος",
                        lemma_bare="αλογος", source="manual")
        modern = Lemma(language_code="el", lemma_form="άλογο",
                       lemma_bare="αλογο", source="reading_intake")
        db.add_all([ancient, modern]); db.commit()
        modern.cognate_lemma_id = ancient.lemma_id
        # User has explicitly said they DON'T know the Ancient version
        db.add(UserLemmaKnowledge(lemma_id=ancient.lemma_id, knowledge_state="unknown"))
        db.commit()

        propagate_known_via_cognate(db, modern.lemma_id)
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=ancient.lemma_id).first()
        assert ulk.knowledge_state == "unknown"  # untouched


def test_user_profile_defaults(tmp_db):
    with tmp_db() as db:
        p = get_user_profile(db)
        assert "en" in p.known_languages
        assert "no" in p.known_languages
        assert p.cognate_auto_mark_threshold == "high"


def test_external_cognate_parser_reads_structured_output(monkeypatch):
    """The Anthropic tool-use API rejects top-level type:'array' schemas
    with HTTP 400, so the cognate detector wraps the results array in a
    {results: [...]} object. The parser must unwrap that key."""
    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = json.dumps({
            "structured_output": {
                "results": [
                    {
                        "lemma": "φιλοσοφία",
                        "cognates": [
                            {"lang": "English", "form": "philosophy", "transparency": "high"}
                        ],
                    }
                ],
            },
            "result": "",
        })

    monkeypatch.setattr(cognate_detector.subprocess, "run", lambda *args, **kwargs: FakeProc())
    lemma = Lemma(language_code="el", lemma_form="φιλοσοφία", lemma_bare="φιλοσοφια")

    result = cognate_detector._call_claude_for_cognates(
        [lemma],
        source_language="Modern Greek",
        l1_names=["English"],
    )

    assert result == [[{"lang": "English", "form": "philosophy", "transparency": "high"}]]


def test_external_cognate_parser_rejects_bare_array(monkeypatch):
    """Regression guard for the latent bug fixed in PR #107: if structured_output
    is a bare array (legacy shape), the parser must not silently accept it."""
    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = json.dumps({
            "structured_output": [
                {"lemma": "φιλοσοφία", "cognates": []},
            ],
            "result": "",
        })

    monkeypatch.setattr(cognate_detector.subprocess, "run", lambda *args, **kwargs: FakeProc())
    lemma = Lemma(language_code="el", lemma_form="φιλοσοφία", lemma_bare="φιλοσοφια")

    import pytest
    with pytest.raises(RuntimeError):
        cognate_detector._call_claude_for_cognates(
            [lemma],
            source_language="Modern Greek",
            l1_names=["English"],
        )


def test_reading_intake_auto_links_on_import(tmp_db):
    """End-to-end: when a page is processed and a new Modern Greek lemma is
    created that has an existing Ancient Greek counterpart, the link should
    be set automatically."""
    with tmp_db() as db:
        from app.models import Language
        db.add(Language(code="grc", name="Ancient Greek", script="greek",
                        direction="ltr", accent_display="polytonic"))
        db.commit()

        # Pre-seed an Ancient Greek lemma
        ancient = Lemma(language_code="grc", lemma_form="φιλία",
                        lemma_bare="φιλια", source="manual")
        db.add(ancient); db.commit()

        # Now import a Modern Greek text containing the same word
        story = reading_intake.import_paste(db, language_code="el", body="φιλία")
        _, tokens = reading_intake.get_page_view(db, story.id, 1)

        # The new Modern lemma should be linked to the existing Ancient one
        modern_lemma = db.query(Lemma).filter(
            Lemma.language_code == "el", Lemma.lemma_bare == "φιλια"
        ).first()
        assert modern_lemma is not None
        assert modern_lemma.cognate_lemma_id == ancient.lemma_id
