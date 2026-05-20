"""Variant chain resolution. The canonical lemma is the unit of scheduling;
variants must never grow their own ULK rows. These tests cover the resolver
that every acquisition / FSRS creation path goes through."""
from app.models import Lemma
from app.services.canonical_resolution import (
    resolve_canonical_lemma_id,
    resolve_canonical_via_map,
)


def _add(db, **kwargs):
    lemma = Lemma(language_code="el", source="test", **kwargs)
    db.add(lemma)
    db.flush()
    return lemma


def test_resolve_returns_self_when_already_canonical(tmp_db):
    with tmp_db() as db:
        canonical = _add(db, lemma_form="βιβλίο", lemma_bare="βιβλιο")
        assert resolve_canonical_lemma_id(db, canonical.lemma_id) == canonical.lemma_id


def test_resolve_follows_single_hop(tmp_db):
    with tmp_db() as db:
        canonical = _add(db, lemma_form="βιβλίο", lemma_bare="βιβλιο")
        variant = _add(
            db, lemma_form="βιβλίον", lemma_bare="βιβλιον",
            canonical_lemma_id=canonical.lemma_id,
        )
        db.commit()
        assert resolve_canonical_lemma_id(db, variant.lemma_id) == canonical.lemma_id


def test_resolve_follows_multi_hop_chain(tmp_db):
    with tmp_db() as db:
        # A → B → C  (C is the root canonical)
        c = _add(db, lemma_form="C", lemma_bare="c")
        b = _add(db, lemma_form="B", lemma_bare="b", canonical_lemma_id=c.lemma_id)
        a = _add(db, lemma_form="A", lemma_bare="a", canonical_lemma_id=b.lemma_id)
        db.commit()
        assert resolve_canonical_lemma_id(db, a.lemma_id) == c.lemma_id


def test_resolve_handles_cycle_safely(tmp_db):
    """A bug elsewhere could create A→B→A. The resolver must not infinite-loop."""
    with tmp_db() as db:
        a = _add(db, lemma_form="A", lemma_bare="a")
        b = _add(db, lemma_form="B", lemma_bare="b", canonical_lemma_id=a.lemma_id)
        a.canonical_lemma_id = b.lemma_id  # cycle: A→B→A
        db.commit()
        result = resolve_canonical_lemma_id(db, a.lemma_id)
        assert result in (a.lemma_id, b.lemma_id)


def test_resolve_returns_input_when_lemma_missing(tmp_db):
    """Non-existent lemma_id should fall through to the input itself rather
    than crash — defensive against stale references."""
    with tmp_db() as db:
        assert resolve_canonical_lemma_id(db, 99999) == 99999


def test_resolve_via_map_matches_db_resolver(tmp_db):
    with tmp_db() as db:
        c = _add(db, lemma_form="C", lemma_bare="c")
        b = _add(db, lemma_form="B", lemma_bare="b", canonical_lemma_id=c.lemma_id)
        a = _add(db, lemma_form="A", lemma_bare="a", canonical_lemma_id=b.lemma_id)
        db.commit()
        m = {
            a.lemma_id: b.lemma_id,
            b.lemma_id: c.lemma_id,
            c.lemma_id: None,
        }
        assert resolve_canonical_via_map(a.lemma_id, m) == c.lemma_id
        assert resolve_canonical_via_map(c.lemma_id, m) == c.lemma_id


# ─── Variant ULK redirection: end-to-end invariant ─────────────────────────
# These tests reproduce Alif's 2026-05-06 incident (36 variant ULKs accumulated
# in prod) by passing variant lemma_ids through every ULK-creation entry point
# and asserting the canonical's ULK is touched, not the variant's.

from app.models import UserLemmaKnowledge


def _ulk_count_for(db, lemma_id: int) -> int:
    return (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .count()
    )


def test_submit_review_on_variant_writes_to_canonical(tmp_db):
    from app.services.fsrs_service import submit_review

    with tmp_db() as db:
        canonical = _add(db, lemma_form="βιβλίο", lemma_bare="βιβλιο")
        variant = _add(
            db, lemma_form="βιβλίον", lemma_bare="βιβλιον",
            canonical_lemma_id=canonical.lemma_id,
        )
        db.commit()

        result = submit_review(db, lemma_id=variant.lemma_id, rating_int=3)

        assert result["lemma_id"] == canonical.lemma_id
        assert _ulk_count_for(db, variant.lemma_id) == 0
        assert _ulk_count_for(db, canonical.lemma_id) == 1


def test_submit_acquisition_review_on_variant_writes_to_canonical(tmp_db):
    from app.services.acquisition_service import (
        start_acquisition,
        submit_acquisition_review,
    )

    with tmp_db() as db:
        canonical = _add(db, lemma_form="βιβλίο", lemma_bare="βιβλιο")
        variant = _add(
            db, lemma_form="βιβλίον", lemma_bare="βιβλιον",
            canonical_lemma_id=canonical.lemma_id,
        )
        db.commit()

        # Prime: bring canonical into acquisition (passing the variant — the
        # redirect inside start_acquisition is what's exercised on the way in).
        start_acquisition(db, lemma_id=variant.lemma_id, source="test", due_immediately=True)
        db.commit()

        # Submit a review on the variant id — must land on canonical's ULK.
        submit_acquisition_review(db, lemma_id=variant.lemma_id, rating_int=3)

        assert _ulk_count_for(db, variant.lemma_id) == 0
        assert _ulk_count_for(db, canonical.lemma_id) == 1


def test_mark_lemma_known_on_variant_writes_to_canonical(tmp_db):
    from app.services.reading_intake import mark_lemma

    with tmp_db() as db:
        canonical = _add(db, lemma_form="βιβλίο", lemma_bare="βιβλιο")
        variant = _add(
            db, lemma_form="βιβλίον", lemma_bare="βιβλιον",
            canonical_lemma_id=canonical.lemma_id,
        )
        db.commit()

        ulk = mark_lemma(db, lemma_id=variant.lemma_id, state="known", fetch_gloss=False)

        assert ulk.lemma_id == canonical.lemma_id
        assert _ulk_count_for(db, variant.lemma_id) == 0
        assert _ulk_count_for(db, canonical.lemma_id) == 1


def test_propagate_known_via_cognate_writes_to_canonical(tmp_db):
    """When a Modern Greek lemma is marked known and its cognate (Ancient
    Greek) lemma is a variant of an Ancient canonical, the 'encountered' ULK
    must land on the Ancient canonical — not the Ancient variant."""
    from app.services.cognate_detector import propagate_known_via_cognate
    from app.models import Language

    with tmp_db() as db:
        # Add grc language so we can create Ancient lemmas
        if not db.query(Language).filter(Language.code == "grc").first():
            db.add(Language(code="grc", name="Ancient Greek", script="greek",
                            direction="ltr", accent_display="polytonic"))
            db.commit()

        anc_canonical = Lemma(language_code="grc", source="test",
                              lemma_form="φιλία", lemma_bare="φιλια")
        db.add(anc_canonical)
        db.flush()
        anc_variant = Lemma(language_code="grc", source="test",
                            lemma_form="φιλίη", lemma_bare="φιλιη",
                            canonical_lemma_id=anc_canonical.lemma_id)
        db.add(anc_variant)
        db.flush()

        mod = Lemma(language_code="el", source="test",
                    lemma_form="φιλία", lemma_bare="φιλια",
                    cognate_lemma_id=anc_variant.lemma_id)
        db.add(mod)
        db.commit()

        propagate_known_via_cognate(db, mod.lemma_id)

        assert _ulk_count_for(db, anc_variant.lemma_id) == 0
        assert _ulk_count_for(db, anc_canonical.lemma_id) == 1


def test_auto_mark_known_on_variant_writes_to_canonical(tmp_db):
    from app.services.cognate_detector import _auto_mark_known

    with tmp_db() as db:
        canonical = _add(db, lemma_form="φιλοσοφία", lemma_bare="φιλοσοφια")
        variant = _add(
            db, lemma_form="φιλοσοφίη", lemma_bare="φιλοσοφιη",
            canonical_lemma_id=canonical.lemma_id,
        )
        db.commit()

        _auto_mark_known(db, variant)
        db.commit()

        assert _ulk_count_for(db, variant.lemma_id) == 0
        assert _ulk_count_for(db, canonical.lemma_id) == 1
