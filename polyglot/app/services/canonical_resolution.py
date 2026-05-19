"""Canonical lemma resolution.

Variant lemmas (`canonical_lemma_id NOT NULL`) must never get independent
acquisition/FSRS rows — the canonical lemma is the unit of scheduling.
Every code path that creates a `UserLemmaKnowledge` redirects via these
helpers first.

Alif had a separate `Variant` table; polyglot keeps it inline on `Lemma`
with the self-referential `canonical_lemma_id` FK. The chain resolution is
otherwise identical: multi-hop with cycle protection, plus a hot-path
variant that reads from a pre-loaded id-to-canonical map.
"""

from sqlalchemy.orm import Session

from app.models import Lemma


def resolve_canonical_lemma_id(db: Session, lemma_id: int) -> int:
    """Follow `canonical_lemma_id` (multi-hop) to the root canonical.

    Returns the input itself if the lemma is already canonical, missing,
    or part of a cycle.
    """
    seen: set[int] = set()
    current_id = lemma_id
    while current_id not in seen:
        seen.add(current_id)
        row = (
            db.query(Lemma.canonical_lemma_id)
            .filter(Lemma.lemma_id == current_id)
            .first()
        )
        if not row or row[0] is None:
            return current_id
        current_id = row[0]
    return current_id


def resolve_canonical_via_map(
    lemma_id: int, canonical_by_id: dict[int, int | None]
) -> int:
    """Multi-hop resolution using a pre-loaded `lemma_id → canonical_lemma_id` map.

    Useful for hot paths that resolve many lemmas at once (e.g. session
    building) and don't want a query per lemma.
    """
    seen: set[int] = set()
    current_id = lemma_id
    while current_id not in seen:
        seen.add(current_id)
        next_id = canonical_by_id.get(current_id)
        if next_id is None:
            return current_id
        current_id = next_id
    return current_id
