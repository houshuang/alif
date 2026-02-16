"""Variant detection service.

Detects morphological variants (possessives, inflected forms, definite duplicates)
by combining CAMeL Tools analysis with DB-aware disambiguation.

Two detection modes:
1. Rule-based (CAMeL + gloss overlap) — fast but 34% true positive rate
2. LLM-based — uses Gemini Flash to confirm/reject ambiguous candidates

Used by:
- Import scripts (post-import variant detection)
- cleanup_lemma_variants.py (batch cleanup with optional merge)
- normalize_and_dedup.py (production cleanup)
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lemma
from app.services.morphology import find_best_db_match, CAMEL_AVAILABLE
from app.services.sentence_validator import normalize_alef

logger = logging.getLogger(__name__)


_NEVER_MERGE = {
    ("هذه", "هذا"),     # distinct demonstratives, both A1 words
    ("جدا", "جد"),      # "very" vs "grandfather"
}


def _gloss_overlap(gloss_a: str, gloss_b: str) -> bool:
    """Check if two glosses share semantic content (at least one meaningful word)."""
    if not gloss_a or not gloss_b:
        return False
    noise = {"a", "an", "the", "of", "to", "is", "my", "your", "his", "her", "its",
             "their", "our", "(m)", "(f)", "m", "f", "(masc)", "(fem)"}
    words_a = set(gloss_a.lower().replace("(", " ").replace(")", " ").split()) - noise
    words_b = set(gloss_b.lower().replace("(", " ").replace(")", " ").split()) - noise
    return bool(words_a & words_b)


def _has_enclitic(enc0: str) -> bool:
    """Check if enc0 value indicates a pronominal enclitic."""
    return bool(enc0) and enc0 not in ("0", "na")


def detect_variants(
    db: Session,
    lemma_ids: list[int] | None = None,
    verbose: bool = False,
) -> list[tuple[int, int, str, dict]]:
    """Detect morphological variants using CAMeL Tools + DB-aware disambiguation.

    Iterates all CAMeL analyses for each word and picks the one whose lex
    matches a lemma already in the database. Requires gloss overlap or
    pronominal enclitic to confirm.

    Args:
        db: Database session
        lemma_ids: If provided, only check these lemmas (for import-time use).
                   Otherwise checks all unlinked lemmas.
        verbose: Print per-word analysis details.

    Returns:
        List of (variant_id, canonical_id, vtype, details) tuples.
    """
    if not CAMEL_AVAILABLE:
        return []

    all_lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
    bare_to_lemma: dict[str, list] = {}
    for l in all_lemmas:
        bare_norm = normalize_alef(l.lemma_ar_bare or "")
        bare_to_lemma.setdefault(bare_norm, []).append(l)

    known_bare_forms = set(bare_to_lemma.keys())

    if lemma_ids is not None:
        id_set = set(lemma_ids)
        check_lemmas = [l for l in all_lemmas if l.lemma_id in id_set]
    else:
        check_lemmas = all_lemmas

    variants = []
    seen_variant_ids: set[int] = set()

    for lemma in check_lemmas:
        ar = lemma.lemma_ar or lemma.lemma_ar_bare
        if not ar:
            continue

        lemma_bare = lemma.lemma_ar_bare or ""

        match = find_best_db_match(ar, known_bare_forms, self_bare=lemma_bare)
        if not match:
            if verbose:
                print(f"  SKIP {lemma_bare} ({lemma.gloss_en}): no DB-matching analysis")
            continue

        lex_bare = match["lex_bare"]
        enc0 = match["enc0"]
        analysis = match["analysis"]

        candidates = bare_to_lemma.get(normalize_alef(lex_bare), [])
        base = None
        for c in candidates:
            if c.lemma_id == lemma.lemma_id:
                continue
            if _gloss_overlap(lemma.gloss_en, c.gloss_en):
                base = c
                break

        if not base and _has_enclitic(enc0):
            for c in candidates:
                if c.lemma_id != lemma.lemma_id:
                    base = c
                    break

        if not base:
            if verbose:
                print(f"  SKIP {lemma_bare} ({lemma.gloss_en}): lex={lex_bare} but no suitable base")
            continue

        # Reject if both lemmas have roots and they differ — prevents cross-root false variants
        if lemma.root_id and base.root_id and lemma.root_id != base.root_id:
            if verbose:
                print(f"  SKIP {lemma_bare} ({lemma.gloss_en}) → {base.lemma_ar_bare} ({base.gloss_en}): different roots")
            continue

        pair = (lemma_bare, base.lemma_ar_bare)
        if pair in _NEVER_MERGE or (pair[1], pair[0]) in _NEVER_MERGE:
            if verbose:
                print(f"  SKIP {lemma_bare} → {base.lemma_ar_bare}: never-merge")
            continue

        if lemma.lemma_id in seen_variant_ids:
            continue
        seen_variant_ids.add(lemma.lemma_id)

        vtype = "possessive" if _has_enclitic(enc0) else "inflected"
        if verbose:
            print(f"  MATCH {lemma_bare} ({lemma.gloss_en}) → {base.lemma_ar_bare} ({base.gloss_en}) [{vtype}]")
        variants.append((lemma.lemma_id, base.lemma_id, vtype, {"enc0": enc0, "lex": analysis.get("lex", "")}))

    return variants


def detect_definite_variants(
    db: Session,
    lemma_ids: list[int] | None = None,
    already_variant_ids: set[int] | None = None,
) -> list[tuple[int, int, str, dict]]:
    """Detect al-prefixed lemmas where the bare form also exists.

    Args:
        db: Database session
        lemma_ids: If provided, only check these lemmas.
        already_variant_ids: Skip lemmas already detected as variants.

    Returns:
        List of (variant_id, canonical_id, "definite", details) tuples.
    """
    all_lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
    bare_to_lemma: dict[str, list] = {}
    for l in all_lemmas:
        bare_norm = normalize_alef(l.lemma_ar_bare or "")
        bare_to_lemma.setdefault(bare_norm, []).append(l)

    if lemma_ids is not None:
        id_set = set(lemma_ids)
        check_lemmas = [l for l in all_lemmas if l.lemma_id in id_set]
    else:
        check_lemmas = all_lemmas

    already = already_variant_ids or set()
    variants = []

    for lemma in check_lemmas:
        if lemma.lemma_id in already:
            continue
        bare = lemma.lemma_ar_bare or ""
        if not bare.startswith("ال"):
            continue
        without_al = bare[2:]
        without_al_norm = normalize_alef(without_al)
        if without_al_norm in bare_to_lemma:
            for base in bare_to_lemma[without_al_norm]:
                if base.lemma_id == lemma.lemma_id:
                    continue
                if base.lemma_id in already:
                    continue
                pair = (bare, base.lemma_ar_bare or "")
                if pair in _NEVER_MERGE or (pair[1], pair[0]) in _NEVER_MERGE:
                    continue
                variants.append((lemma.lemma_id, base.lemma_id, "definite", {"stripped": without_al}))
                break

    return variants


def mark_variants(
    db: Session, variants: list[tuple[int, int, str, dict]]
) -> int:
    """Set canonical_lemma_id for detected variants. Returns count marked."""
    count = 0
    for var_id, canon_id, _vtype, _details in variants:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == var_id).first()
        if lemma and lemma.canonical_lemma_id is None:
            lemma.canonical_lemma_id = canon_id
            count += 1
    return count


# ---------------------------------------------------------------------------
# LLM-based variant detection
# ---------------------------------------------------------------------------

_LLM_VARIANT_SYSTEM_PROMPT = """\
You are an Arabic morphology expert. Your task is to determine whether \
Arabic words are morphological variants of each other (same dictionary entry) \
or distinct lemmas (different dictionary entries).

Rules:
- A verb conjugation (يَكْتُبُونَ, كَتَبَتْ) IS a variant of its base verb (كَتَبَ)
- A feminine adjective (سَعِيدَة) IS a variant of the masculine (سَعِيد)
- A broken plural (كُتُب) IS a variant of its singular (كِتَاب)
- A sound plural (مُعَلِّمُونَ) IS a variant of its singular (مُعَلِّم)
- A possessive form (كِتَابِي, كِتَابُهَا) IS a variant of the base noun (كِتَاب)
- A definite form (الكِتَاب) IS a variant of the indefinite (كِتَاب)

- A taa marbuta noun is NOT a variant of a DIFFERENT word that happens to \
share the root: جَامِعَة (university) is NOT a variant of جَامِع (mosque), \
شَاشَة (screen) is NOT a variant of شَاش (muslin), سَنَة (year) is NOT \
a variant of سِنّ (tooth). These have different dictionary meanings.
- But when the taa marbuta form IS the feminine of the same concept, it IS \
a variant: ملكة (queen) IS a variant of ملك (king), صديقة (friend f.) IS \
a variant of صديق (friend)
- A nisba adjective (مِصْرِيّ Egyptian) is NOT a variant of the base noun (مِصْر Egypt)
- A loanword (بَنْك bank) is NEVER a variant of a native Arabic word (بِنْ son)
- Words with different roots are NEVER variants of each other
- Verbal nouns / masdars (كِتَابَة writing) are NOT variants of related nouns \
(كِتَاب book) — they are distinct entries even if from the same root
- An agent noun (كَاتِب writer) is NOT a variant of the verb (كَتَبَ to write) \
or noun (كِتَاب book) — it's a separate dictionary entry
- The key test: would a learner benefit from tracking these as ONE word? \
If yes (room/rooms, king/queen, happy/happy-f) → variant. \
If no (university/mosque, writing/book, fish/poison) → distinct.

Respond with JSON only."""


def _build_llm_batch_prompt(
    candidates: list[dict[str, Any]],
) -> str:
    """Build a prompt for batch LLM variant evaluation.

    Each candidate dict has:
      - id: unique identifier for matching results back
      - word_ar: Arabic word being checked
      - word_gloss: English gloss
      - word_pos: POS tag
      - base_ar: Candidate base lemma Arabic
      - base_gloss: English gloss of base
      - base_pos: POS tag of base
    """
    lines = [
        "For each numbered pair below, determine if WORD is a morphological "
        "variant of BASE (same dictionary entry) or a distinct lemma.\n"
    ]
    for c in candidates:
        lines.append(
            f"{c['id']}. WORD: {c['word_ar']} \"{c['word_gloss']}\" ({c['word_pos']}) "
            f"→ BASE: {c['base_ar']} \"{c['base_gloss']}\" ({c['base_pos']})"
        )

    lines.append(
        '\nRespond with JSON: {"results": [{"id": 1, "is_variant": true/false, '
        '"reason": "brief explanation"}, ...]}'
    )
    return "\n".join(lines)


def _load_cache(db: Session) -> dict[tuple[str, str], dict[str, Any]]:
    """Load all cached variant decisions into a lookup dict."""
    from app.models import VariantDecision
    decisions = db.query(VariantDecision).all()
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    for d in decisions:
        key = (normalize_alef(d.word_bare), normalize_alef(d.base_bare))
        cache[key] = {
            "is_variant": d.is_variant,
            "reason": d.reason or "",
        }
    return cache


def _save_decisions(
    db: Session,
    decisions: list[dict[str, Any]],
) -> None:
    """Save LLM variant decisions to the cache table."""
    from app.models import VariantDecision
    for d in decisions:
        db.add(VariantDecision(
            word_bare=d["word_bare"],
            base_bare=d["base_bare"],
            is_variant=d["is_variant"],
            reason=d.get("reason", ""),
        ))
    db.flush()


def evaluate_variants_llm(
    candidates: list[dict[str, Any]],
    model_override: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """Use LLM to confirm or reject variant candidates, with DB cache.

    Args:
        candidates: List of candidate dicts with keys:
            id, word_ar, word_gloss, word_pos, base_ar, base_gloss, base_pos
        model_override: LLM provider to use (default: primary/Gemini)
        db: If provided, check/save cache in variant_decisions table

    Returns:
        List of result dicts with keys: id, is_variant (bool), reason (str)
    """
    from app.services.llm import generate_completion

    if not candidates:
        return []

    # Check cache for already-decided pairs
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    if db is not None:
        cache = _load_cache(db)

    cached_results: list[dict[str, Any]] = []
    uncached: list[dict[str, Any]] = []

    for cand in candidates:
        key = (normalize_alef(cand["word_ar"]), normalize_alef(cand["base_ar"]))
        if key in cache:
            cached_results.append({
                "id": cand["id"],
                "is_variant": cache[key]["is_variant"],
                "reason": cache[key]["reason"] + " (cached)",
            })
        else:
            uncached.append(cand)

    if not uncached:
        return cached_results

    # Call LLM for uncached candidates
    prompt = _build_llm_batch_prompt(uncached)
    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=_LLM_VARIANT_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.1,
            model_override=model_override,
        )
    except Exception as e:
        logger.warning("LLM variant detection failed, skipping: %s", e)
        return cached_results

    raw_results = result.get("results", [])
    if not isinstance(raw_results, list):
        logger.warning("LLM returned non-list results: %s", type(raw_results))
        return cached_results

    # Parse LLM results and save to cache
    llm_parsed: list[dict[str, Any]] = []
    to_cache: list[dict[str, Any]] = []
    uncached_by_id = {c["id"]: c for c in uncached}

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        parsed = {
            "id": item.get("id"),
            "is_variant": bool(item.get("is_variant", False)),
            "reason": str(item.get("reason", "")),
        }
        llm_parsed.append(parsed)

        cand = uncached_by_id.get(parsed["id"])
        if cand:
            to_cache.append({
                "word_bare": cand["word_ar"],
                "base_bare": cand["base_ar"],
                "is_variant": parsed["is_variant"],
                "reason": parsed["reason"],
            })

    if db is not None and to_cache:
        _save_decisions(db, to_cache)

    return cached_results + llm_parsed


def detect_variants_llm(
    db: Session,
    lemma_ids: list[int] | None = None,
    batch_size: int = 15,
    model_override: str | None = None,
    verbose: bool = False,
) -> list[tuple[int, int, str, dict]]:
    """Detect variants using CAMeL for candidates + LLM for confirmation.

    Two-phase approach:
    1. CAMeL Tools generates candidate pairs (same as detect_variants)
    2. LLM confirms or rejects each candidate (with DB cache)

    Args:
        db: Database session
        lemma_ids: If provided, only check these lemmas
        batch_size: Number of candidates per LLM call
        model_override: LLM provider override
        verbose: Print progress

    Returns:
        List of (variant_id, canonical_id, vtype, details) tuples
        where details includes 'llm_reason'.
    """
    # Phase 1: get CAMeL candidates (reuse existing logic)
    camel_candidates = detect_variants(db, lemma_ids=lemma_ids, verbose=False)

    if not camel_candidates:
        if verbose:
            print("No CAMeL candidates found")
        return []

    if verbose:
        print(f"Phase 1: {len(camel_candidates)} CAMeL candidates")

    # Build LLM evaluation batches
    id_to_candidate: dict[int, tuple] = {}
    llm_candidates: list[dict[str, Any]] = []

    for i, (var_id, canon_id, vtype, details) in enumerate(camel_candidates):
        var = db.query(Lemma).filter(Lemma.lemma_id == var_id).first()
        canon = db.query(Lemma).filter(Lemma.lemma_id == canon_id).first()
        if not var or not canon:
            continue

        cand = {
            "id": i,
            "word_ar": var.lemma_ar_bare or "",
            "word_gloss": var.gloss_en or "",
            "word_pos": var.pos or "",
            "base_ar": canon.lemma_ar_bare or "",
            "base_gloss": canon.gloss_en or "",
            "base_pos": canon.pos or "",
        }
        llm_candidates.append(cand)
        id_to_candidate[i] = (var_id, canon_id, vtype, details)

    # Phase 2: LLM evaluation in batches
    confirmed: list[tuple[int, int, str, dict]] = []

    for batch_start in range(0, len(llm_candidates), batch_size):
        batch = llm_candidates[batch_start:batch_start + batch_size]
        if verbose:
            print(f"Phase 2: evaluating batch {batch_start // batch_size + 1} "
                  f"({len(batch)} candidates)")

        results = evaluate_variants_llm(
            batch, model_override=model_override, db=db,
        )

        result_by_id = {r["id"]: r for r in results}
        for cand in batch:
            cid = cand["id"]
            llm_result = result_by_id.get(cid)
            original = id_to_candidate[cid]
            var_id, canon_id, vtype, details = original

            if llm_result and llm_result["is_variant"]:
                enriched_details = {
                    **details,
                    "llm_confirmed": True,
                    "llm_reason": llm_result["reason"],
                }
                confirmed.append((var_id, canon_id, vtype, enriched_details))
                if verbose:
                    print(f"  ✓ {cand['word_ar']} → {cand['base_ar']}: {llm_result['reason']}")
            else:
                reason = llm_result["reason"] if llm_result else "no LLM response"
                if verbose:
                    print(f"  ✗ {cand['word_ar']} → {cand['base_ar']}: {reason}")

    if verbose:
        print(f"Result: {len(confirmed)}/{len(camel_candidates)} confirmed by LLM")

    return confirmed
