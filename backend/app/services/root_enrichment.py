"""Generate rich cultural/linguistic content for Arabic roots.

Called as a background task when roots have 2+ studied words, or via backfill script.
"""

import logging

from app.database import SessionLocal
from app.models import Lemma, Root, UserLemmaKnowledge

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You generate rich cultural and linguistic content about Arabic trilateral roots for a language learner. The learner is intermediate and speaks English, Norwegian, and several other languages.

Given a root and its child words, produce educational content that helps the learner:
1. Understand how the root's core meaning generates all its derivatives
2. Appreciate the cultural significance of the root
3. Find memorable connections and stories

Return JSON with these keys:
- etymology_story: A 2-3 sentence narrative about this root's history and how its core meaning branches into different words. Make it engaging, not dry.
- cultural_significance: 1-2 sentences about the root's importance in Arab culture, Islam, literature, or daily life. null if nothing notable.
- literary_examples: Array of 1-3 strings. Famous quotes, Quranic references, proverbs, or poetry featuring words from this root. Include source attribution. Return [] if none notable.
- fun_facts: Array of 1-3 surprising or memorable facts about words from this root. E.g. how a word traveled to other languages, unexpected semantic connections, or historical usage.
- related_roots: Array of 1-3 strings. Related roots with their meanings, e.g. "ق.ر.أ (reading)". Only semantically related roots, not phonetically similar ones.

Be accurate. Don't fabricate quotes or attributions. If unsure about a literary example, omit it."""


def generate_root_enrichment(root_id: int) -> None:
    """Background task: generate enrichment for a single root.

    Opens its own DB session. Idempotent — skips if enrichment exists.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()
    try:
        root = db.query(Root).filter(Root.root_id == root_id).first()
        if not root:
            return
        if root.enrichment_json:
            return

        lemmas = (
            db.query(Lemma)
            .filter(Lemma.root_id == root_id, Lemma.canonical_lemma_id.is_(None))
            .all()
        )
        if not lemmas:
            return

        word_list = "\n".join(
            f"  - {l.lemma_ar} ({l.transliteration_ala_lc or l.lemma_ar_bare}): "
            f"{l.gloss_en or '?'} [{l.pos or '?'}] pattern={l.wazn or '?'}"
            for l in lemmas
        )

        prompt = f"""Generate rich cultural/linguistic content for this Arabic root:

Root: {root.root}
Core meaning: {root.core_meaning_en or "unknown"}

Words derived from this root:
{word_list}

Return JSON with: etymology_story, cultural_significance, literary_examples, fun_facts, related_roots."""

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.7,
                model_override="claude_sonnet",
                task_type="root_enrichment",
            )
        except AllProvidersFailed as e:
            logger.warning(f"Root enrichment LLM failed for root {root_id}: {e}")
            return

        if result is None or not isinstance(result, dict):
            return

        if not result.get("etymology_story"):
            logger.warning(f"Root enrichment missing etymology_story for root {root_id}")
            return

        root.enrichment_json = result
        db.commit()
        logger.info(f"Generated enrichment for root {root_id} ({root.root})")
    except Exception:
        logger.exception(f"Error generating root enrichment for root {root_id}")
        db.rollback()
    finally:
        db.close()


def maybe_enrich_root(root_id: int, db: "Session") -> None:
    """Check if a root qualifies for enrichment and trigger if so.

    Called when a word enters acquisition. Criteria:
    - Root has 2+ lemmas with knowledge state in (acquiring, learning, known)
    - Root has no enrichment yet
    """
    import threading

    root = db.query(Root).filter(Root.root_id == root_id).first()
    if not root or root.enrichment_json:
        return

    studied_count = (
        db.query(Lemma.lemma_id)
        .join(UserLemmaKnowledge)
        .filter(
            Lemma.root_id == root_id,
            Lemma.canonical_lemma_id.is_(None),
            UserLemmaKnowledge.knowledge_state.in_(["acquiring", "learning", "known"]),
        )
        .count()
    )

    if studied_count >= 2:
        threading.Thread(
            target=generate_root_enrichment, args=(root_id,), daemon=True
        ).start()
        logger.info(f"Triggered root enrichment for root {root_id} ({root.root}, {studied_count} studied words)")
