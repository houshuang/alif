from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Lemma, Root, UserLemmaKnowledge

router = APIRouter(prefix="/api/roots", tags=["roots"])


@router.get("")
def list_roots(db: Session = Depends(get_db)):
    """List all roots with word counts and knowledge stats."""
    rows = (
        db.query(
            Root.root_id,
            Root.root,
            Root.core_meaning_en,
            Root.enrichment_json,
            func.count(Lemma.lemma_id).label("total"),
        )
        .join(Lemma, Lemma.root_id == Root.root_id)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .group_by(Root.root_id)
        .order_by(func.count(Lemma.lemma_id).desc())
        .all()
    )

    root_ids = [r.root_id for r in rows]
    known_counts: dict[int, int] = {}
    if root_ids:
        known_rows = (
            db.query(
                Lemma.root_id,
                func.count(Lemma.lemma_id).label("known"),
            )
            .join(UserLemmaKnowledge)
            .filter(
                Lemma.root_id.in_(root_ids),
                Lemma.canonical_lemma_id.is_(None),
                UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "acquiring"]),
            )
            .group_by(Lemma.root_id)
            .all()
        )
        known_counts = {r.root_id: r.known for r in known_rows}

    result = [
        {
            "root_id": r.root_id,
            "root": r.root,
            "core_meaning_en": r.core_meaning_en,
            "total_words": r.total,
            "known_words": known_counts.get(r.root_id, 0),
            "coverage_pct": round(known_counts.get(r.root_id, 0) / r.total * 100, 1) if r.total > 0 else 0,
            "has_enrichment": r.enrichment_json is not None,
        }
        for r in rows
    ]
    return sorted(result, key=lambda r: (-r["known_words"], -r["total_words"]))


@router.get("/{root_id}")
def get_root(root_id: int, db: Session = Depends(get_db)):
    """Get root detail with enrichment and derivation tree."""
    root = db.query(Root).filter(Root.root_id == root_id).first()
    if not root:
        raise HTTPException(404, "Root not found")

    lemmas = (
        db.query(Lemma)
        .outerjoin(UserLemmaKnowledge)
        .filter(Lemma.root_id == root_id, Lemma.canonical_lemma_id.is_(None))
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .all()
    )

    by_pattern: dict[str | None, list[dict]] = {}
    for l in lemmas:
        key = l.wazn or "_unclassified"
        if key not in by_pattern:
            by_pattern[key] = []
        by_pattern[key].append({
            "lemma_id": l.lemma_id,
            "lemma_ar": l.lemma_ar,
            "gloss_en": l.gloss_en,
            "pos": l.pos,
            "knowledge_state": l.knowledge.knowledge_state if l.knowledge else None,
            "frequency_rank": l.frequency_rank,
            "transliteration": l.transliteration_ala_lc,
            "cefr_level": l.cefr_level,
        })

    return {
        "root_id": root.root_id,
        "root": root.root,
        "core_meaning_en": root.core_meaning_en,
        "enrichment": root.enrichment_json,
        "patterns": [
            {
                "wazn": k if k != "_unclassified" else None,
                "wazn_meaning": next(
                    (l.wazn_meaning for l in lemmas if l.wazn == k and l.wazn_meaning),
                    None,
                ) if k != "_unclassified" else None,
                "words": v,
            }
            for k, v in by_pattern.items()
        ],
        "total_words": len(lemmas),
    }
