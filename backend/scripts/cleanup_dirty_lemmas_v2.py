"""Cleanup dirty lemmas: Quranic presentation forms + و+ال / ال baked-in prefixes.

Supersedes `cleanup_dirty_bare_forms.py` + `merge_al_lemmas.py` by handling all
three dirtiness categories in one pass:

  A. Surah 33:35 dual-prefix (وٱل…) lemmas imported from stories — 19 lemmas.
  B. Other `source='quran'` lemmas using Mushaf diacritics (ٱ ـٰ ۥ ۡ ٓ etc.).
  C. ال-prefixed lemmas from OCR/textbook imports that the 2026-04-06 cleanup missed.

For each dirty lemma:
  1. Compute clean bare form via deterministic normalization (normalize_arabic)
     + rule-based و/ال prefix stripping with a keep-list for legitimate cases.
  2. For ambiguous cases (e.g. is "المكتب" → "مكتب" or keep?), batch-classify
     with the LLM using the same prompt as `import_quality.py`.
  3. If a clean lemma with the target bare form already exists:
        MERGE — reassign SentenceWord, ReviewLog, Sentence.target_lemma_id,
        merge UserLemmaKnowledge (keep whichever has more reviews), delete
        (or mark canonical_lemma_id) on the dirty row.
  4. If no clean target exists: REWRITE in place — update lemma_ar_bare and
     lemma_ar to the normalized form. Gloss/root/POS may then need re-running
     via quality gates (flagged in output, not done here).

Run:
  python3 scripts/cleanup_dirty_lemmas_v2.py                    # dry-run
  python3 scripts/cleanup_dirty_lemmas_v2.py --apply            # commit
  python3 scripts/cleanup_dirty_lemmas_v2.py --apply --category A    # only Surah 33:35
"""

import argparse
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_

from app.database import SessionLocal
from app.models import (
    Lemma,
    ReviewLog,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.activity_log import log_activity
from app.services.sentence_validator import (
    ARABIC_DIACRITICS,
    normalize_arabic,
    normalize_quranic_to_msa,
)

logger = logging.getLogger(__name__)


# Quranic presentation characters — any of these in lemma_ar/bare makes it dirty.
QURANIC_CHARS = {"\u0670", "\u06E5", "\u06E6", "\u0671", "\u06DC", "\u06E1", "\u0653", "\u06D6", "\u06D7", "\u06D8", "\u06D9", "\u06DA", "\u06DB", "\u06E4"}

# Words where dagger alef in lemma_ar is zero-width (phonetic-only typography),
# meaning the stored bare form WITHOUT the corresponding ا is canonical MSA.
# For any bare NOT in this set, dagger alef in lemma_ar represents a real long
# ā that should be present in the bare as ا (e.g. ضلالة not ضللة).
DAGGER_ALEF_ZERO_WIDTH_BARES = {
    "هذا", "هذه", "هؤلاء", "هذان", "هاتان",
    "ذلك", "تلك", "ذلكم", "ذلكما",
    "الله", "الرحمن", "اللات",
}


# Lemmas where ال is integral and must NOT be stripped (merged with the 2026-04-06 list).
KEEP_AL_PREFIX = {
    "الله",      # God
    "الذي", "التي", "الذين", "اللذان", "اللتان", "اللواتي",  # relative pronouns
    "الآن", "الان", "اليوم", "الليلة",  # temporal fixed expressions
    "الف", "الا", "الى", "الة",         # false positives (أَلْف, إِلَى, أَلَّ etc.)
    "الرازي", "الحاوي", "الوو",         # proper nouns / interjection
    "الم", "اليم",                       # stripping would leave <2 chars
    "ال",                                # the dictionary entry for the article itself
    # Form VIII/X verbs where ال is part of the stem (not the article)
    "التقى", "التحق", "التهاب", "التمع",
}


def has_quranic_chars(text: str) -> bool:
    return any(c in QURANIC_CHARS for c in text or "")


def is_bare_already_canonical(bare: str) -> bool:
    """True if the stored bare form is already in acceptable MSA shape.

    Even if lemma_ar carries Quranic typography (dagger alef in هٰذَا, اللّٰهُ,
    ذَٰلِكَ), the bare form may already be the canonical MSA spelling (هذا, الله,
    ذلك). In that case we must NOT rewrite — the dagger alef is conventional
    typography for those words, not a sign that the bare needs normalization.
    """
    if not bare:
        return False
    if has_quranic_chars(bare):
        return False
    if bare.startswith("وال") and len(bare) > 4:
        return False
    if bare.startswith("ال") and len(bare) > 3 and bare not in KEEP_AL_PREFIX:
        return False
    return True


def find_dirty_lemmas(db, categories: set[str]) -> list[Lemma]:
    """Find all lemmas that are dirty under any of the given categories.

    Categories:
      A — وال prefix in lemma_ar_bare (Surah 33:35 style)
      B — Quranic chars in lemma_ar or lemma_ar_bare
      C — ال prefix in lemma_ar_bare (not in KEEP_AL_PREFIX)
    """
    dirty: dict[int, Lemma] = {}

    if "A" in categories:
        for l in db.query(Lemma).filter(Lemma.lemma_ar_bare.like("وال%")).all():
            dirty[l.lemma_id] = l

    if "B" in categories:
        for l in db.query(Lemma).all():
            if has_quranic_chars(l.lemma_ar_bare) or has_quranic_chars(l.lemma_ar):
                dirty[l.lemma_id] = l

    if "C" in categories:
        for l in db.query(Lemma).filter(Lemma.lemma_ar_bare.like("ال%")).all():
            if l.lemma_ar_bare not in KEEP_AL_PREFIX and l.lemma_id not in dirty:
                # Only include if not already listed in categories A/B
                # Also skip if KEEP list check matches via normalized form
                norm = normalize_arabic(l.lemma_ar_bare)
                if norm in KEEP_AL_PREFIX:
                    continue
                dirty[l.lemma_id] = l

    if "E" in categories:
        # Cross-check: lemmas where normalize(lemma_ar) != lemma_ar_bare indicate
        # structural divergence between the display and bare forms. Excludes
        # the zero-width-dagger-alef whitelist where this divergence is expected.
        for l in db.query(Lemma).all():
            if not l.lemma_ar or not l.lemma_ar_bare:
                continue
            if l.lemma_id in dirty:
                continue
            if l.lemma_ar_bare in DAGGER_ALEF_ZERO_WIDTH_BARES:
                continue
            if normalize_arabic(l.lemma_ar) != l.lemma_ar_bare:
                dirty[l.lemma_id] = l

    return sorted(dirty.values(), key=lambda l: l.lemma_id)


def compute_clean_bare(lemma_ar_bare: str, lemma_ar: str | None = None) -> str:
    """Deterministic clean bare form: strip Quranic presentation + conjunction/article.

    Prefers `lemma_ar` (diacritized) as source when available, because the stored
    `lemma_ar_bare` may have been computed with the old normalize_arabic before
    `normalize_quranic_to_msa` existed — which stripped dagger alefs and lost the
    long-ā vowel (e.g. stored bare `والصـئمت` missing the alif for صائمات).

    Detects integral-ا patterns where ال is NOT the definite article:
    - If lemma_ar begins with أ / إ / آ, the ا is hamzated (Form IV verb, plural
      with intrinsic alef, etc.) — do NOT strip the leading ال. Examples:
        أَلْقَى (to throw), أَلْعَابٌ (games), آلِهَةٌ (gods).
    - If the candidate clean bare would be shorter than 3 chars, treat as unsafe
      and return the pre-strip cleaned form (caller then treats as no-op).
    """
    source = lemma_ar if lemma_ar else lemma_ar_bare
    cleaned = normalize_arabic(source)

    # Integral-ا detection via hamzated alef in diacritized form.
    if lemma_ar and lemma_ar[0] in ("\u0623", "\u0625", "\u0622"):  # أ إ آ
        return cleaned

    # Strip و + ال (conjunction + definite article) together.
    if cleaned.startswith("وال") and len(cleaned) > 4:
        candidate = cleaned[3:]
        if len(candidate) >= 3:
            return candidate
    elif cleaned.startswith("ال") and len(cleaned) > 3 and cleaned not in KEEP_AL_PREFIX:
        candidate = cleaned[2:]
        if len(candidate) >= 3:
            return candidate

    return cleaned


def find_merge_target(db, clean_bare: str, exclude_id: int) -> Lemma | None:
    """Find an existing lemma with the given clean bare form that's not the dirty one."""
    candidates = (
        db.query(Lemma)
        .filter(
            Lemma.lemma_ar_bare == clean_bare,
            Lemma.lemma_id != exclude_id,
            Lemma.canonical_lemma_id.is_(None),
        )
        .all()
    )
    if not candidates:
        return None
    # Prefer one with gloss_en and gates_completed_at
    scored = sorted(
        candidates,
        key=lambda l: (bool(l.gloss_en), bool(l.gates_completed_at), -l.lemma_id),
        reverse=True,
    )
    return scored[0]


def merge_into(db, dirty: Lemma, target: Lemma) -> dict:
    """Reassign all references from dirty → target, merge ULK, mark dirty as variant.

    Returns stats dict.
    """
    stats = {"sentence_words": 0, "review_logs": 0, "sentence_targets": 0, "ulk_merged": False}

    # 1. SentenceWord
    sw_rows = db.query(SentenceWord).filter(SentenceWord.lemma_id == dirty.lemma_id).all()
    for sw in sw_rows:
        sw.lemma_id = target.lemma_id
    stats["sentence_words"] = len(sw_rows)

    # 2. ReviewLog
    rl_rows = db.query(ReviewLog).filter(ReviewLog.lemma_id == dirty.lemma_id).all()
    for rl in rl_rows:
        rl.lemma_id = target.lemma_id
    stats["review_logs"] = len(rl_rows)

    # 3. Sentence.target_lemma_id
    st_rows = db.query(Sentence).filter(Sentence.target_lemma_id == dirty.lemma_id).all()
    for s in st_rows:
        s.target_lemma_id = target.lemma_id
    stats["sentence_targets"] = len(st_rows)

    # 4. UserLemmaKnowledge merge
    dirty_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == dirty.lemma_id).first()
    target_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == target.lemma_id).first()
    if dirty_ulk and target_ulk:
        d_seen = dirty_ulk.times_seen or 0
        t_seen = target_ulk.times_seen or 0
        target_ulk.times_seen = d_seen + t_seen
        target_ulk.times_correct = (target_ulk.times_correct or 0) + (dirty_ulk.times_correct or 0)
        if d_seen > t_seen and dirty_ulk.fsrs_card_json:
            target_ulk.fsrs_card_json = dirty_ulk.fsrs_card_json
            target_ulk.knowledge_state = dirty_ulk.knowledge_state
            if dirty_ulk.last_reviewed:
                target_ulk.last_reviewed = dirty_ulk.last_reviewed
        db.delete(dirty_ulk)
        stats["ulk_merged"] = True
    elif dirty_ulk and not target_ulk:
        dirty_ulk.lemma_id = target.lemma_id
        stats["ulk_merged"] = True

    # 5. Mark dirty as variant of target (don't delete — preserve for dictionary lookups)
    dirty.canonical_lemma_id = target.lemma_id

    return stats


def _strip_ar_prefix_letters(ar: str, n_letters: int) -> str:
    """Strip the first N Arabic letters from a diacritized ar form, preserving
    remaining tashkeel. Consumes diacritics adjacent to stripped letters.

    Example: strip 2 letters from 'اَلْغُرْفة' → 'غُرْفة' (strips ا, َ, ل, ْ then keeps rest).
    """
    if n_letters <= 0:
        return ar
    i = 0
    letters_seen = 0
    while i < len(ar) and letters_seen < n_letters:
        if ARABIC_DIACRITICS.match(ar[i]):
            i += 1
            continue
        letters_seen += 1
        i += 1
    # Consume trailing diacritics attached to the last stripped letter.
    while i < len(ar) and ARABIC_DIACRITICS.match(ar[i]):
        i += 1
    return ar[i:]


def normalize_lemma_ar_for_display(lemma_ar: str) -> str:
    """Produce a display-safe lemma_ar by stripping/converting Quranic-only
    typography while preserving all standard MSA tashkeel.

    Rules (each safe when the bare form is already canonical MSA):
    - ٱ (U+0671 alif waṣlah) → ا (regular alef). These are the same letter;
      U+0671 is a contextual form used in Mushaf printing.
    - ۥ (U+06E5 small waw) → strip (silent in MSA).
    - ۦ (U+06E6 small ya) → strip (silent in MSA).
    - ـٰ (U+0670 dagger alef) → strip. When the bare already lacks the
      corresponding ا, the dagger alef is phonetic-only typography for
      words conventionally spelled without the long-ā letter (هٰذَا, اللّٰهُ).
    - ـ (U+0640 tatweel) → strip.
    - Quranic annotation marks (U+06D6–U+06E8 range, U+0653 maddah, etc.)
      → strip. These are Mushaf-only reading aids, not standard tashkeel.

    Preserves fatha/kasra/damma/sukun/shadda/tanwin (standard MSA tashkeel).
    """
    # Safe letter substitutions.
    new = lemma_ar.replace("\u0671", "\u0627")  # ٱ → ا
    new = new.replace("\u06E5", "")              # ۥ → strip
    new = new.replace("\u06E6", "")              # ۦ → strip
    # Strip Quranic-only marks but preserve standard tashkeel.
    # Standard MSA tashkeel: U+064B–U+0652 (fatha, kasra, damma, tanwin, sukun, shadda).
    # Quranic-only: dagger alef U+0670, maddah U+0653, hamza above U+0654, hamza below U+0655,
    # Quranic annotation symbols U+06D6–U+06ED.
    quranic_only_marks = [
        "\u0670",  # dagger alef ـٰ
        "\u0653",  # maddah above ٓ
        "\u0654",  # hamza above (can appear Quranic)
        "\u0655",  # hamza below
        "\u0656",  # subscript alef
        "\u0657",  # inverted damma
        "\u0658",  # mark noon ghunna
        "\u0659",  # zwarakay
        "\u065A",  # vowel sign small v above
        "\u065B",  # vowel sign inverted small v above
        "\u065C",  # vowel sign dot below
        "\u065D",  # reversed damma
        "\u065E",  # fatha with two dots
        "\u065F",  # wavy hamza below
    ]
    for m in quranic_only_marks:
        new = new.replace(m, "")
    # Quranic annotation signs U+06D6–U+06ED.
    for cp in range(0x06D6, 0x06EE):
        new = new.replace(chr(cp), "")
    # Tatweel.
    new = new.replace("\u0640", "")
    return new


def find_display_candidates(db) -> list[Lemma]:
    """Find lemmas where bare is canonical but lemma_ar carries Quranic typography."""
    candidates = []
    for l in db.query(Lemma).all():
        if not l.lemma_ar or not l.lemma_ar_bare:
            continue
        if not has_quranic_chars(l.lemma_ar):
            continue
        if not is_bare_already_canonical(l.lemma_ar_bare):
            continue
        candidates.append(l)
    return sorted(candidates, key=lambda l: l.lemma_id)


def normalize_lemma_ar_for_rewrite(lemma_ar: str) -> str:
    """Normalize lemma_ar for a rewrite (bare change): converts Quranic letters
    to MSA equivalents WITHOUT stripping dagger alef. Used when the dagger alef
    represents a real long ā that must appear in the new bare as ا.

    Contrast with `normalize_lemma_ar_for_display` which strips dagger alef
    for the zero-width-dagger-alef case (هذا/الله/ذلك).
    """
    # Convert dagger alef → ا, strip small waw/ya (via normalize_quranic_to_msa).
    new = normalize_quranic_to_msa(lemma_ar)
    # Alif waṣlah → regular alef.
    new = new.replace("\u0671", "\u0627")
    # Strip Quranic annotation marks (U+06D6–U+06ED, maddah etc.) — they're
    # reading aids, not standard MSA tashkeel. Preserves fatha/kasra/damma/etc.
    for cp in range(0x06D6, 0x06EE):
        new = new.replace(chr(cp), "")
    for mark in ["\u0653", "\u0654", "\u0655", "\u0656", "\u0657", "\u0658",
                 "\u0659", "\u065A", "\u065B", "\u065C", "\u065D", "\u065E", "\u065F"]:
        new = new.replace(mark, "")
    # Tatweel.
    new = new.replace("\u0640", "")
    return new


def rewrite_in_place(db, dirty: Lemma, clean_bare: str) -> dict:
    """Rewrite lemma_ar_bare + lemma_ar in place (no merge target exists).

    Produces a lemma_ar that normalizes exactly to clean_bare:
    1. Apply Quranic→MSA letter conversions (dagger alef → ا, ٱ → ا, etc.)
       while preserving all standard tashkeel.
    2. Compare normalize(new_ar) to clean_bare. If new_ar has a leading ال/وال/و
       prefix that clean_bare lacks, strip matching letters from new_ar.
    3. Verify the result normalizes to clean_bare; fall back to clean_bare if
       any edge case slips through (loses tashkeel but preserves correctness).
    """
    old_bare = dirty.lemma_ar_bare
    old_ar = dirty.lemma_ar or ""

    # Step 1: Quranic letter conversions (keep long-ā alifs).
    new_ar = normalize_lemma_ar_for_rewrite(old_ar)

    # Step 2: Strip leading letters if normalize(new_ar) has a prefix clean_bare lacks.
    ar_normalized = normalize_arabic(new_ar)
    if ar_normalized != clean_bare and ar_normalized.endswith(clean_bare):
        n_strip = len(ar_normalized) - len(clean_bare)
        if 1 <= n_strip <= 3:  # و, ال, or وال
            new_ar = _strip_ar_prefix_letters(new_ar, n_strip)

    # Step 3: Fallback if anything went wrong.
    if not new_ar.strip() or normalize_arabic(new_ar) != clean_bare:
        new_ar = clean_bare

    dirty.lemma_ar_bare = clean_bare
    dirty.lemma_ar = new_ar

    # Mark for re-running quality gates so gloss/root/pos get refreshed next cycle.
    dirty.gates_completed_at = None

    return {"old_bare": old_bare, "new_bare": clean_bare, "old_ar": old_ar, "new_ar": new_ar}


def normalize_display_forms(apply: bool) -> None:
    """Normalize lemma_ar display forms for lemmas where bare is already canonical
    but lemma_ar carries Quranic-only typography. Preserves tashkeel.

    Safety check: verifies normalize_arabic(new_ar) == lemma_ar_bare before
    applying — if stripping would corrupt word structure (introduce a letter
    count mismatch), the lemma is skipped.
    """
    db = SessionLocal()
    try:
        candidates = find_display_candidates(db)
        if not candidates:
            print("No display-normalization candidates.")
            return

        print(f"Found {len(candidates)} lemma(s) with Quranic typography in lemma_ar (bare already canonical).\n")

        to_apply: list[tuple[Lemma, str]] = []
        skipped: list[tuple[Lemma, str]] = []

        for l in candidates:
            new_ar = normalize_lemma_ar_for_display(l.lemma_ar)
            if new_ar == l.lemma_ar:
                skipped.append((l, "no Quranic typography to strip"))
                continue
            # Safety check: the new form must still normalize to the stored bare.
            if normalize_arabic(new_ar) != l.lemma_ar_bare:
                skipped.append(
                    (l, f"would corrupt structure: normalize(new_ar)={normalize_arabic(new_ar)!r} ≠ bare={l.lemma_ar_bare!r}")
                )
                continue
            to_apply.append((l, new_ar))

        print(f"Plan: {len(to_apply)} display normalization(s), {len(skipped)} skip(ped).\n")

        if to_apply:
            print("=== DISPLAY NORMALIZATIONS (lemma_ar only, bare unchanged) ===")
            for l, new_ar in to_apply:
                print(f"  [{l.lemma_id:5d}] {l.lemma_ar_bare:18s}  {l.lemma_ar}  →  {new_ar}")
            print()

        if skipped:
            print("=== SKIPPED ===")
            for l, reason in skipped[:20]:  # cap output
                print(f"  [{l.lemma_id}] {l.lemma_ar_bare}: {reason}")
            if len(skipped) > 20:
                print(f"  ... and {len(skipped) - 20} more")
            print()

        if not apply:
            print("[DRY RUN] Re-run with --apply to commit.")
            return

        for l, new_ar in to_apply:
            l.lemma_ar = new_ar

        log_activity(
            db,
            "manual_action",
            f"Display normalization: cleaned lemma_ar on {len(to_apply)} canonical-bare lemmas",
            {"count": len(to_apply), "skipped": len(skipped)},
            commit=False,
        )
        db.commit()
        print(f"APPLIED: {len(to_apply)} lemma_ar display forms normalized.")
    finally:
        db.close()


def cleanup(categories: set[str], apply: bool) -> None:
    db = SessionLocal()
    try:
        dirty = find_dirty_lemmas(db, categories)
        if not dirty:
            print("No dirty lemmas found.")
            return

        print(f"Found {len(dirty)} dirty lemma(s) in categories {sorted(categories)}.\n")

        merges: list[tuple[Lemma, Lemma, str]] = []  # (dirty, target, clean_bare)
        rewrites: list[tuple[Lemma, str]] = []       # (dirty, clean_bare)
        skipped: list[tuple[Lemma, str]] = []        # (dirty, reason)

        for l in dirty:
            # If the bare is already canonical MSA, check if dagger alef in lemma_ar
            # is truly zero-width (e.g. هذا/الله/ذلك) or represents a real long ā
            # that was damaged out of the stored bare (e.g. ضللة should be ضلالة).
            if is_bare_already_canonical(l.lemma_ar_bare):
                # Whitelist of known zero-width-dagger-alef words: trust the bare.
                if l.lemma_ar_bare in DAGGER_ALEF_ZERO_WIDTH_BARES:
                    skipped.append((l, "dagger alef is zero-width (known MSA demonstrative/name)"))
                    continue
                # Otherwise, re-derive bare from lemma_ar to see if it's damaged.
                computed = compute_clean_bare(l.lemma_ar_bare, l.lemma_ar)
                if computed == l.lemma_ar_bare:
                    skipped.append((l, "bare already canonical and matches normalized lemma_ar"))
                    continue
                # Bare differs from what lemma_ar normalizes to — bare is damaged.
                # Fall through to the merge/rewrite path with the computed clean.
                clean = computed
            else:
                clean = compute_clean_bare(l.lemma_ar_bare, l.lemma_ar)
            if not clean or len(clean) < 3:
                skipped.append((l, f"clean bare too short ('{clean}')"))
                continue
            if clean == l.lemma_ar_bare:
                skipped.append((l, "already clean (no change computed)"))
                continue

            target = find_merge_target(db, clean, l.lemma_id)
            if target:
                merges.append((l, target, clean))
            else:
                rewrites.append((l, clean))

        print(f"Plan: {len(merges)} merge(s), {len(rewrites)} rewrite(s), {len(skipped)} skip(ped).\n")

        # Print merges
        if merges:
            print("=== MERGES (dirty → existing clean lemma) ===")
            for dirty_l, target, clean in merges:
                n_sw = db.query(SentenceWord).filter(SentenceWord.lemma_id == dirty_l.lemma_id).count()
                print(
                    f"  [{dirty_l.lemma_id:5d}] {dirty_l.lemma_ar_bare:25s} → "
                    f"[{target.lemma_id:5d}] {target.lemma_ar_bare:20s}  "
                    f"({target.gloss_en or '(no gloss)'})  sw={n_sw}"
                )
            print()

        # Print rewrites
        if rewrites:
            print("=== REWRITES (in-place normalization, no clean target exists) ===")
            for dirty_l, clean in rewrites:
                n_sw = db.query(SentenceWord).filter(SentenceWord.lemma_id == dirty_l.lemma_id).count()
                print(
                    f"  [{dirty_l.lemma_id:5d}] {dirty_l.lemma_ar_bare:25s} → {clean:20s}  "
                    f"({dirty_l.gloss_en or '(no gloss)'})  sw={n_sw}  src={dirty_l.source}"
                )
            print()

        if skipped:
            print("=== SKIPPED ===")
            for l, reason in skipped:
                print(f"  [{l.lemma_id}] {l.lemma_ar_bare}: {reason}")
            print()

        if not apply:
            print("[DRY RUN] Re-run with --apply to commit.")
            return

        # Apply
        totals = defaultdict(int)
        for dirty_l, target, clean in merges:
            st = merge_into(db, dirty_l, target)
            totals["merges"] += 1
            totals["sentence_words"] += st["sentence_words"]
            totals["review_logs"] += st["review_logs"]
            totals["sentence_targets"] += st["sentence_targets"]

        for dirty_l, clean in rewrites:
            rewrite_in_place(db, dirty_l, clean)
            totals["rewrites"] += 1

        # Log to ActivityLog
        log_activity(
            db,
            "manual_action",
            f"Cleanup: merged {totals['merges']} dirty lemmas, rewrote {totals['rewrites']} in place",
            {
                "categories": sorted(categories),
                "merges": totals["merges"],
                "rewrites": totals["rewrites"],
                "sentence_words_reassigned": totals["sentence_words"],
                "review_logs_reassigned": totals["review_logs"],
                "sentence_targets_reassigned": totals["sentence_targets"],
            },
            commit=False,
        )
        db.commit()
        print(
            f"APPLIED: {totals['merges']} merges, {totals['rewrites']} rewrites. "
            f"Reassigned {totals['sentence_words']} sentence_words, "
            f"{totals['review_logs']} review_logs, "
            f"{totals['sentence_targets']} sentence targets."
        )
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Commit changes (default: dry run)")
    ap.add_argument(
        "--category",
        action="append",
        choices=["A", "B", "C", "E"],
        help="Restrict to categor(ies). A=وال prefix, B=Quranic marks, C=ال prefix, E=normalize(ar)≠bare. Default: A,B,C.",
    )
    ap.add_argument(
        "--display",
        action="store_true",
        help="Run display-normalization pass instead: clean lemma_ar Quranic typography on canonical-bare lemmas (preserves bare + tashkeel).",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    mode = "APPLY" if args.apply else "DRY RUN"
    if args.display:
        print(f"=== {mode} — display normalization (lemma_ar only) ===\n")
        normalize_display_forms(apply=args.apply)
    else:
        cats = set(args.category) if args.category else {"A", "B", "C"}
        print(f"=== {mode} — categories: {sorted(cats)} ===\n")
        cleanup(cats, apply=args.apply)
