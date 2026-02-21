from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Lemma, Root, UserLemmaKnowledge

router = APIRouter(prefix="/api/patterns", tags=["patterns"])


@router.get("")
def list_patterns(db: Session = Depends(get_db)):
    """List all wazn patterns with word counts and coverage stats."""
    rows = (
        db.query(
            Lemma.wazn,
            Lemma.wazn_meaning,
            func.count(Lemma.lemma_id).label("total"),
        )
        .filter(Lemma.wazn.isnot(None), Lemma.canonical_lemma_id.is_(None))
        .group_by(Lemma.wazn)
        .order_by(func.count(Lemma.lemma_id).desc())
        .all()
    )

    wazn_list = [r.wazn for r in rows]
    known_counts: dict[str, int] = {}
    if wazn_list:
        known_rows = (
            db.query(
                Lemma.wazn,
                func.count(Lemma.lemma_id).label("known"),
            )
            .join(UserLemmaKnowledge)
            .filter(
                Lemma.wazn.isnot(None),
                Lemma.canonical_lemma_id.is_(None),
                UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "acquiring"]),
            )
            .group_by(Lemma.wazn)
            .all()
        )
        known_counts = {r.wazn: r.known for r in known_rows}

    return [
        {
            "wazn": r.wazn,
            "wazn_meaning": r.wazn_meaning,
            "total_words": r.total,
            "known_words": known_counts.get(r.wazn, 0),
            "coverage_pct": round(known_counts.get(r.wazn, 0) / r.total * 100, 1) if r.total > 0 else 0,
        }
        for r in rows
    ]


@router.get("/{wazn}")
def get_pattern(wazn: str, db: Session = Depends(get_db)):
    """Get all words with a specific wazn pattern."""
    lemmas = (
        db.query(Lemma)
        .outerjoin(UserLemmaKnowledge)
        .filter(Lemma.wazn == wazn, Lemma.canonical_lemma_id.is_(None))
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .all()
    )

    if not lemmas:
        raise HTTPException(404, f"No words found with pattern '{wazn}'")

    wazn_meaning = next((l.wazn_meaning for l in lemmas if l.wazn_meaning), None)

    return {
        "wazn": wazn,
        "wazn_meaning": wazn_meaning,
        "words": [
            {
                "lemma_id": l.lemma_id,
                "lemma_ar": l.lemma_ar,
                "gloss_en": l.gloss_en,
                "pos": l.pos,
                "root": l.root.root if l.root else None,
                "root_meaning": l.root.core_meaning_en if l.root else None,
                "knowledge_state": l.knowledge.knowledge_state if l.knowledge else None,
                "frequency_rank": l.frequency_rank,
            }
            for l in lemmas
        ],
    }


@router.get("/roots/{root_id}/tree")
def root_tree(root_id: int, db: Session = Depends(get_db)):
    """Get full derivation tree for a root — all words grouped by pattern."""
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
            "wazn": l.wazn,
            "wazn_meaning": l.wazn_meaning,
            "knowledge_state": l.knowledge.knowledge_state if l.knowledge else None,
        })

    return {
        "root_id": root.root_id,
        "root": root.root,
        "core_meaning_en": root.core_meaning_en,
        "patterns": [
            {
                "wazn": k if k != "_unclassified" else None,
                "words": v,
            }
            for k, v in by_pattern.items()
        ],
        "total_words": len(lemmas),
    }
