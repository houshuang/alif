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
