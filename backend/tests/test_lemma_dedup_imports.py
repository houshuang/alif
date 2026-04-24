"""Regression tests for the two import paths fixed in Phase 2 of the
lemma-decomposition audit (research/decomposition-audit-2026-04-24.md):

1. quran_service._create_unknown_quran_lemmas — was using exact-string set
   membership, would create compound lemmas (e.g. وَتَرَكَهُم) instead of
   mapping to existing canonical (تَرَكَ).
2. backfill_function_word_lemmas.backfill_function_words — same pattern,
   lower blast radius (curated input).

Both paths now call resolve_existing_lemma() before creating, matching the
reference pattern in story_service.py:305, 348, 508.
"""
from unittest.mock import patch

from app.models import Lemma
from app.services.quran_service import _create_unknown_quran_lemmas


class TestQuranLemmaCreateDedup:
    def test_dedup_via_clitic_strip(self, db_session):
        """LLM returns the compound bare (وتركهم); clitic-strip must find canonical ترك."""
        canonical = Lemma(
            lemma_ar="تَرَكَ", lemma_ar_bare="ترك",
            gloss_en="to leave", pos="verb", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()
        canonical_id = canonical.lemma_id

        all_lemmas = db_session.query(Lemma).all()

        with patch("app.services.llm.generate_completion") as mock_llm:
            mock_llm.return_value = [{
                "bare": "وتركهم",
                "gloss_en": "and left them",
                "pos": "verb",
                "is_name": False,
                "root": None,
            }]
            result_map = _create_unknown_quran_lemmas(
                db_session,
                unknown_forms={"وتركهم": "وَتَرَكَهُم"},
                all_lemmas=all_lemmas,
            )

        assert result_map.get("وتركهم") == canonical_id
        assert db_session.query(Lemma).count() == 1

    def test_dedup_via_direct_match(self, db_session):
        """LLM returns the canonical bare directly; lookup must reuse, not duplicate."""
        canonical = Lemma(
            lemma_ar="لَكِنْ", lemma_ar_bare="لكن",
            gloss_en="but", pos="particle", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()
        canonical_id = canonical.lemma_id

        all_lemmas = db_session.query(Lemma).all()

        with patch("app.services.llm.generate_completion") as mock_llm:
            mock_llm.return_value = [{
                "bare": "لكن",
                "gloss_en": "but",
                "pos": "particle",
                "is_name": False,
                "root": None,
            }]
            result_map = _create_unknown_quran_lemmas(
                db_session,
                unknown_forms={"لكن": "لَكِنْ"},
                all_lemmas=all_lemmas,
            )

        assert result_map.get("لكن") == canonical_id
        assert db_session.query(Lemma).count() == 1

    def test_creates_when_truly_new(self, db_session):
        """Sanity check: if no canonical exists and bare doesn't clitic-strip
        to anything, a new lemma is created."""
        all_lemmas = db_session.query(Lemma).all()

        with patch("app.services.llm.generate_completion") as mock_llm:
            mock_llm.return_value = [{
                "bare": "زمزم",
                "gloss_en": "Zamzam (well)",
                "pos": "noun",
                "is_name": True,
                "root": None,
            }]
            with patch("app.services.lemma_quality.run_quality_gates"):
                result_map = _create_unknown_quran_lemmas(
                    db_session,
                    unknown_forms={"زمزم": "زَمْزَم"},
                    all_lemmas=all_lemmas,
                )

        assert result_map.get("زمزم") is not None
        assert db_session.query(Lemma).count() == 1


class TestBackfillFunctionWordsDedup:
    def test_dedup_via_clitic_strip(self, db_session, monkeypatch):
        """Compound entry (و + لكن) in the glosses dict must clitic-strip to
        existing canonical, not create a new lemma."""
        canonical = Lemma(
            lemma_ar="لكن", lemma_ar_bare="لكن",
            gloss_en="but", pos="particle", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()

        from scripts import backfill_function_word_lemmas as bfl
        monkeypatch.setattr(bfl, "FUNCTION_WORD_GLOSSES", {"ولكن": "and-but"})

        created, skipped_existing, _ = bfl.backfill_function_words(db_session, verbose=False)

        assert created == 0
        assert skipped_existing == 1
        assert db_session.query(Lemma).count() == 1

    def test_dedup_via_direct_match(self, db_session, monkeypatch):
        """Direct exact-bare match must skip — basic dedup regression."""
        canonical = Lemma(
            lemma_ar="في", lemma_ar_bare="في",
            gloss_en="in", pos="particle", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()

        from scripts import backfill_function_word_lemmas as bfl
        monkeypatch.setattr(bfl, "FUNCTION_WORD_GLOSSES", {"في": "in"})

        created, skipped_existing, _ = bfl.backfill_function_words(db_session, verbose=False)

        assert created == 0
        assert skipped_existing == 1
        assert db_session.query(Lemma).count() == 1

    def test_creates_when_new(self, db_session, monkeypatch):
        """Sanity: a brand-new function word with no canonical match is created."""
        from scripts import backfill_function_word_lemmas as bfl
        monkeypatch.setattr(bfl, "FUNCTION_WORD_GLOSSES", {"غير": "other than"})

        created, _, _ = bfl.backfill_function_words(db_session, verbose=False)

        assert created == 1
        assert db_session.query(Lemma).count() == 1
