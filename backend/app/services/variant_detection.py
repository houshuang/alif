"""Variant detection service.

Detects morphological variants (possessives, inflected forms, definite duplicates)
by combining CAMeL Tools analysis with DB-aware disambiguation.

Used by:
- Import scripts (post-import variant detection)
- cleanup_lemma_variants.py (batch cleanup with optional merge)
"""

from sqlalchemy.orm import Session

from app.models import Lemma
from app.services.morphology import find_best_db_match, CAMEL_AVAILABLE
from app.services.sentence_validator import normalize_alef


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
