"""Tests for variant_detection service."""

from unittest.mock import patch

import pytest

from app.models import Lemma
from app.services.variant_detection import (
    detect_definite_variants,
    mark_variants,
    _gloss_overlap,
    _has_enclitic,
    _build_llm_batch_prompt,
    evaluate_variants_llm,
)


def _seed_lemma(db, lemma_id, bare, gloss, pos="noun", canonical=None):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=bare,
        lemma_ar_bare=bare,
        pos=pos,
        gloss_en=gloss,
        canonical_lemma_id=canonical,
    )
    db.add(lemma)
    db.flush()
    return lemma


class TestGlossOverlap:
    def test_shared_word(self):
        assert _gloss_overlap("big book", "my book") is True

    def test_no_overlap(self):
        assert _gloss_overlap("book", "house") is False

    def test_noise_words_ignored(self):
        assert _gloss_overlap("the book", "a book") is True

    def test_empty_gloss(self):
        assert _gloss_overlap("", "book") is False
        assert _gloss_overlap(None, "book") is False


class TestHasEnclitic:
    def test_with_enclitic(self):
        assert _has_enclitic("3ms") is True

    def test_no_enclitic_zero(self):
        assert _has_enclitic("0") is False

    def test_no_enclitic_na(self):
        assert _has_enclitic("na") is False

    def test_empty_string(self):
        assert _has_enclitic("") is False


class TestDetectDefiniteVariants:
    def test_al_prefix_detected(self, db_session):
        _seed_lemma(db_session, 1, "كتاب", "book")
        _seed_lemma(db_session, 2, "الكتاب", "the book")
        db_session.commit()

        variants = detect_definite_variants(db_session)
        assert len(variants) == 1
        var_id, canon_id, vtype, details = variants[0]
        assert var_id == 2
        assert canon_id == 1
        assert vtype == "definite"

    def test_no_false_positive(self, db_session):
        _seed_lemma(db_session, 1, "كتاب", "book")
        _seed_lemma(db_session, 2, "بيت", "house")
        db_session.commit()

        variants = detect_definite_variants(db_session)
        assert len(variants) == 0

    def test_skips_already_variant(self, db_session):
        _seed_lemma(db_session, 1, "كتاب", "book")
        _seed_lemma(db_session, 2, "الكتاب", "the book")
        db_session.commit()

        variants = detect_definite_variants(db_session, already_variant_ids={2})
        assert len(variants) == 0

    def test_empty_db(self, db_session):
        variants = detect_definite_variants(db_session)
        assert variants == []


class TestMarkVariants:
    def test_sets_canonical_lemma_id(self, db_session):
        base = _seed_lemma(db_session, 1, "كتاب", "book")
        variant = _seed_lemma(db_session, 2, "الكتاب", "the book")
        db_session.commit()

        count = mark_variants(db_session, [(2, 1, "definite", {})])
        db_session.commit()

        assert count == 1
        db_session.expire_all()
        variant = db_session.query(Lemma).filter(Lemma.lemma_id == 2).first()
        assert variant.canonical_lemma_id == 1

    def test_skips_already_linked(self, db_session):
        _seed_lemma(db_session, 1, "كتاب", "book")
        _seed_lemma(db_session, 2, "الكتاب", "the book", canonical=1)
        db_session.commit()

        count = mark_variants(db_session, [(2, 1, "definite", {})])
        assert count == 0


class TestBuildLlmBatchPrompt:
    def test_prompt_contains_candidates(self):
        candidates = [
            {"id": 0, "word_ar": "غرفة", "word_gloss": "room", "word_pos": "noun",
             "base_ar": "غرف", "base_gloss": "rooms", "base_pos": "noun"},
            {"id": 1, "word_ar": "سعيدة", "word_gloss": "happy (f.)", "word_pos": "adj",
             "base_ar": "سعيد", "base_gloss": "happy", "base_pos": "adj"},
        ]
        prompt = _build_llm_batch_prompt(candidates)
        assert "غرفة" in prompt
        assert "سعيدة" in prompt
        assert '"room"' in prompt
        assert "is_variant" in prompt

    def test_empty_candidates(self):
        prompt = _build_llm_batch_prompt([])
        assert "results" in prompt


class TestEvaluateVariantsLlm:
    def test_returns_empty_for_no_candidates(self):
        results = evaluate_variants_llm([])
        assert results == []

    def test_parses_llm_response(self):
        mock_response = {
            "results": [
                {"id": 0, "is_variant": False, "reason": "distinct noun"},
                {"id": 1, "is_variant": True, "reason": "feminine adjective"},
            ]
        }
        with patch("app.services.llm.generate_completion",
                    return_value=mock_response) as mock_llm:
            candidates = [
                {"id": 0, "word_ar": "غرفة", "word_gloss": "room", "word_pos": "noun",
                 "base_ar": "غرف", "base_gloss": "rooms", "base_pos": "noun"},
                {"id": 1, "word_ar": "سعيدة", "word_gloss": "happy (f.)", "word_pos": "adj",
                 "base_ar": "سعيد", "base_gloss": "happy", "base_pos": "adj"},
            ]
            results = evaluate_variants_llm(candidates)

        assert len(results) == 2
        assert results[0]["is_variant"] is False
        assert results[1]["is_variant"] is True
        mock_llm.assert_called_once()

    def test_handles_malformed_llm_response(self):
        with patch("app.services.llm.generate_completion",
                    return_value={"results": "not a list"}):
            results = evaluate_variants_llm([
                {"id": 0, "word_ar": "test", "word_gloss": "test", "word_pos": "noun",
                 "base_ar": "base", "base_gloss": "base", "base_pos": "noun"},
            ])
        assert results == []

    def test_handles_missing_fields(self):
        mock_response = {
            "results": [
                {"id": 0},  # missing is_variant and reason
            ]
        }
        with patch("app.services.llm.generate_completion",
                    return_value=mock_response):
            results = evaluate_variants_llm([
                {"id": 0, "word_ar": "test", "word_gloss": "test", "word_pos": "noun",
                 "base_ar": "base", "base_gloss": "base", "base_pos": "noun"},
            ])
        assert len(results) == 1
        assert results[0]["is_variant"] is False
        assert results[0]["reason"] == ""

    def test_cache_saves_and_reuses(self, db_session):
        """Decisions are cached in variant_decisions table."""
        from app.models import VariantDecision

        mock_response = {
            "results": [
                {"id": 0, "is_variant": True, "reason": "feminine adjective"},
            ]
        }
        candidates = [
            {"id": 0, "word_ar": "سعيدة", "word_gloss": "happy (f.)", "word_pos": "adj",
             "base_ar": "سعيد", "base_gloss": "happy", "base_pos": "adj"},
        ]

        # First call: LLM is called, result cached
        with patch("app.services.llm.generate_completion",
                    return_value=mock_response) as mock_llm:
            results = evaluate_variants_llm(candidates, db=db_session)
        assert mock_llm.call_count == 1
        assert len(results) == 1
        assert results[0]["is_variant"] is True
        db_session.commit()

        # Verify cache entry exists
        cached = db_session.query(VariantDecision).all()
        assert len(cached) == 1
        assert cached[0].word_bare == "سعيدة"
        assert cached[0].is_variant is True

        # Second call: cache hit, no LLM call
        with patch("app.services.llm.generate_completion") as mock_llm2:
            results2 = evaluate_variants_llm(candidates, db=db_session)
        mock_llm2.assert_not_called()
        assert len(results2) == 1
        assert results2[0]["is_variant"] is True
        assert "(cached)" in results2[0]["reason"]
