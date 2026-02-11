"""Tests for variant_detection service."""

import pytest

from app.models import Lemma
from app.services.variant_detection import (
    detect_definite_variants,
    mark_variants,
    _gloss_overlap,
    _has_enclitic,
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
