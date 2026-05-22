"""Tests for the DB-wide chimera audit (Phase 7 of warm_sentence_cache)."""
from __future__ import annotations

import pytest

from app.models import ActivityLog, Lemma
from app.services.chimera_audit import (
    ChimeraCandidate,
    check_and_alert,
    emit_chimera_alert,
    find_chimera_candidates,
    _etym_gloss_matches_derivation,
)


@pytest.mark.parametrize("gloss,derivation,expected", [
    # genuine loanwords — pre-filter should EXCLUDE (match=True)
    ("jacket", "From English 'jacket', from Middle French 'jaquette'", True),
    ("television", "From French 'télévision', combining Greek τῆλε", True),  # accent
    ("cakes", "From English 'cake'", True),  # bidirectional: cake' in cakes
    ("pizza", "From Italian 'pizza' (savory pie)", True),
    # real mismatches — pre-filter should KEEP as suspect (match=False)
    ("repentance, returning to God", "From English 'laptop' (portable computer)", False),
    ("tour", "From European languages; likely from French 'joule'", False),
])
def test_d6_prefilter_overlap(gloss, derivation, expected):
    assert _etym_gloss_matches_derivation(gloss, derivation) is expected


def test_d1_form_v_root_bare(db_session):
    lem = Lemma(
        lemma_ar="تَشَجَّعَ", lemma_ar_bare="شجع", gloss_en="to take courage",
        pos="verb",
    )
    db_session.add(lem)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert any(c.lemma_id == lem.lemma_id and c.category == "D1" for c in cands)


def test_d2_form_vii_root_bare(db_session):
    lem = Lemma(
        lemma_ar="اِنْكَسَرَ", lemma_ar_bare="كسر", gloss_en="to be broken",
        pos="verb",
    )
    db_session.add(lem)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert any(c.lemma_id == lem.lemma_id and c.category == "D2" for c in cands)


def test_d3_form_x_root_bare(db_session):
    lem = Lemma(
        lemma_ar="اِسْتَعْمَلَ", lemma_ar_bare="عمل", gloss_en="to use",
        pos="verb",
    )
    db_session.add(lem)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert any(c.lemma_id == lem.lemma_id and c.category == "D3" for c in cands)


def test_d4_defective_participle(db_session):
    lem = Lemma(
        lemma_ar="غَازٍ", lemma_ar_bare="غاز", gloss_en="raider",
    )
    db_session.add(lem)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert any(c.lemma_id == lem.lemma_id and c.category == "D4" for c in cands)


def test_d4_skips_proper_name(db_session):
    lem = Lemma(
        lemma_ar="مُنْتَشٍ", lemma_ar_bare="منتش", gloss_en="(name) Muntash",
        word_category="proper_name",
    )
    db_session.add(lem)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert not any(c.lemma_id == lem.lemma_id for c in cands)


def test_d5_cross_root_forms_json(db_session):
    lem = Lemma(
        lemma_ar="كَتَبَ", lemma_ar_bare="كتب", gloss_en="to write",
        pos="verb",
        forms_json={"plural": "طاولاتنا"},  # totally unrelated long form
    )
    db_session.add(lem)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert any(c.lemma_id == lem.lemma_id and c.category == "D5" for c in cands)


def test_skips_variants(db_session):
    canonical = Lemma(lemma_ar="جانِي", lemma_ar_bare="جاني", gloss_en="guilty")
    db_session.add(canonical)
    db_session.commit()
    variant = Lemma(
        lemma_ar="جَانٍ", lemma_ar_bare="جان", gloss_en="guilty variant",
        canonical_lemma_id=canonical.lemma_id,
    )
    db_session.add(variant)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert not any(c.lemma_id == variant.lemma_id for c in cands)


def test_regular_form_i_verb_not_flagged(db_session):
    lem = Lemma(
        lemma_ar="كَتَبَ", lemma_ar_bare="كتب", gloss_en="to write",
        pos="verb",
    )
    db_session.add(lem)
    db_session.commit()
    cands = find_chimera_candidates(db_session)
    assert not any(c.lemma_id == lem.lemma_id for c in cands)


def test_emit_alert_is_idempotent(db_session):
    lem = Lemma(
        lemma_ar="تَشَجَّعَ", lemma_ar_bare="شجع", gloss_en="x", pos="verb",
    )
    db_session.add(lem)
    db_session.commit()

    cands = find_chimera_candidates(db_session)
    row1 = emit_chimera_alert(db_session, cands)
    assert row1 is not None
    # Second emission with the same candidate set should be suppressed.
    row2 = emit_chimera_alert(db_session, cands)
    assert row2 is None
    n = db_session.query(ActivityLog).filter(
        ActivityLog.event_type == "chimera_audit_findings"
    ).count()
    assert n == 1


def test_check_and_alert_returns_candidates(db_session):
    lem = Lemma(
        lemma_ar="تَرَفَّعَ", lemma_ar_bare="رفع", gloss_en="to rise", pos="verb",
    )
    db_session.add(lem)
    db_session.commit()
    cands = check_and_alert(db_session)
    assert any(c.lemma_id == lem.lemma_id for c in cands)
