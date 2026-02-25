"""Generate rich content about Arabic morphological patterns (wazn).

Called as a background task when patterns have 2+ studied words, or via backfill script.
"""

import logging

from app.database import SessionLocal
from app.models import Lemma, PatternInfo, Root, UserLemmaKnowledge

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You generate educational content about Arabic morphological patterns (wazn/أوزان) for a language learner. The learner is intermediate and speaks English.

Given a pattern name, its meaning, and example words, produce content that helps the learner:
1. Recognize words following this pattern
2. Predict meaning from pattern + root
3. Appreciate how Arabic morphology works systematically

Return JSON with these keys:
- explanation: 2-3 sentences explaining what this pattern does — how it transforms a root's meaning. Use simple language, not linguistic jargon.
- how_to_recognize: 1-2 sentences about the phonetic/visual markers of this pattern. What should the learner look/listen for?
- semantic_fields: Array of 2-4 strings. What kinds of meanings does this pattern typically produce? E.g. "agent nouns", "professions", "places".
- register_notes: 1 sentence about formality/usage context. Is this pattern common in everyday speech, formal writing, or both? null if not notable.
- example_derivations: Array of 3-5 objects showing root→word transformation:
  {"root": "ك.ت.ب", "word": "كاتب", "gloss": "writer", "explanation": "one who writes"}
  Use the provided example words — pick the clearest demonstrations.
- fun_facts: Array of 1-2 surprising facts about this pattern. Return [] if nothing notable.
- related_patterns: Array of 1-3 strings describing related patterns. E.g. "maf'ul (the passive counterpart — the thing being acted upon)".

Be accurate. Focus on practical recognition over theoretical morphology."""


def generate_pattern_enrichment(wazn: str) -> None:
    """Background task: generate enrichment for a single pattern.

    Opens its own DB session. Creates/updates PatternInfo row.
    Idempotent — skips if enrichment exists.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()
    try:
        existing = db.query(PatternInfo).filter(PatternInfo.wazn == wazn).first()
        if existing and existing.enrichment_json:
            return

        lemmas = (
            db.query(Lemma)
            .outerjoin(Root, Lemma.root_id == Root.root_id)
            .filter(Lemma.wazn == wazn, Lemma.canonical_lemma_id.is_(None))
            .limit(15)
            .all()
        )
        if not lemmas:
            return

        wazn_meaning = next((l.wazn_meaning for l in lemmas if l.wazn_meaning), None)

        word_list = "\n".join(
            f"  - {l.lemma_ar} ({l.transliteration_ala_lc or l.lemma_ar_bare}): "
            f"{l.gloss_en or '?'} [{l.pos or '?'}] root={l.root.root if l.root else '?'}"
            for l in lemmas
        )

        prompt = f"""Generate educational content about this Arabic morphological pattern:

Pattern: {wazn}
Meaning: {wazn_meaning or "unknown"}

Example words with this pattern:
{word_list}

Return JSON with: explanation, how_to_recognize, semantic_fields, register_notes, example_derivations, fun_facts, related_patterns."""

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.7,
                model_override="claude_haiku",
                task_type="pattern_enrichment",
            )
        except AllProvidersFailed as e:
            logger.warning(f"Pattern enrichment LLM failed for wazn '{wazn}': {e}")
            return

        if result is None or not isinstance(result, dict):
            return

        if not result.get("explanation"):
            logger.warning(f"Pattern enrichment missing explanation for wazn '{wazn}'")
            return

        if existing:
            existing.enrichment_json = result
            if wazn_meaning and not existing.wazn_meaning:
                existing.wazn_meaning = wazn_meaning
        else:
            pi = PatternInfo(
                wazn=wazn,
                wazn_meaning=wazn_meaning,
                enrichment_json=result,
            )
            db.add(pi)

        db.commit()
        logger.info(f"Generated enrichment for pattern '{wazn}'")
    except Exception:
        logger.exception(f"Error generating pattern enrichment for wazn '{wazn}'")
        db.rollback()
    finally:
        db.close()


def maybe_enrich_pattern(wazn: str, db: "Session") -> None:
    """Check if a pattern qualifies for enrichment and trigger if so.

    Called when a word enters acquisition. Criteria:
    - Pattern has 2+ lemmas with knowledge state in (acquiring, learning, known)
    - Pattern has no enrichment yet
    """
    import threading

    if not wazn:
        return

    existing = db.query(PatternInfo).filter(PatternInfo.wazn == wazn).first()
    if existing and existing.enrichment_json:
        return

    studied_count = (
        db.query(Lemma.lemma_id)
        .join(UserLemmaKnowledge)
        .filter(
            Lemma.wazn == wazn,
            Lemma.canonical_lemma_id.is_(None),
            UserLemmaKnowledge.knowledge_state.in_(["acquiring", "learning", "known"]),
        )
        .count()
    )

    if studied_count >= 2:
        threading.Thread(
            target=generate_pattern_enrichment, args=(wazn,), daemon=True
        ).start()
        logger.info(f"Triggered pattern enrichment for wazn '{wazn}' ({studied_count} studied words)")
