"""Canonical lemma resolution.

Per CLAUDE.md hard invariant: variant lemmas (`canonical_lemma_id` NOT NULL)
must never get independent FSRS cards or acquisition rows. The canonical is
the unit of scheduling; variants are tracked via `variant_stats_json` on the
canonical's ULK row.

Every code path that creates a `UserLemmaKnowledge` row must redirect a
variant's `lemma_id` to the canonical first. When the redirect lands inside
`start_acquisition()` and `introduce_word()`, indirect callers
(`sentence_selector._ensure_session_words_have_intro_state`,
`quran_service`, `ocr_service.import_words`, `leech_service`,
`routers/learn.py`) are covered transitively. Direct `db.add(UserLemmaKnowledge(...))`
sites (`book_import_service`, `ocr_service` cold-encounter path) must call
`resolve_canonical_lemma_id` explicitly before constructing the row.
"""

from sqlalchemy.orm import Session

from app.models import Lemma


def resolve_canonical_lemma_id(db: Session, lemma_id: int) -> int:
    """Follow the canonical chain (multi-hop) to the root canonical.

    Returns `lemma_id` itself if it is already canonical, the lemma is
    missing, or a cycle is detected.
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

    For hot paths (e.g. `build_session`) where we don't want a query per lemma.
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
