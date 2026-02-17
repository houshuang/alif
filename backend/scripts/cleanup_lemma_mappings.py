#!/usr/bin/env python3
"""Clean up lemma data quality issues and re-map sentence_words.

Handles:
  A1. Fix wrong glosses on existing lemmas
  A2. Create missing particle/noun lemmas
  A3. Fix conjugated-form lemmas (mark as variants of base form)
  A4. Fix possessive-form lemmas (mark as variants)
  A5. Fix ال-prefix lemmas (mark as variants where appropriate)
  A6. Batch re-map all active sentence_words

Usage:
    # Dry run (default)
    python3 scripts/cleanup_lemma_mappings.py

    # Apply changes
    python3 scripts/cleanup_lemma_mappings.py --apply

    # Only run specific steps
    python3 scripts/cleanup_lemma_mappings.py --apply --steps A1,A2,A6
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    map_tokens_to_lemmas,
    normalize_alef,
    strip_diacritics,
    tokenize_display,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── A1: Wrong glosses ───────────────────────────────────────────────
GLOSS_FIXES = [
    (95, {"gloss_en": "in"}),
    (11, {"gloss_en": "this (masc.)"}),
    (154, {"gloss_en": "from; than"}),
    (152, {"gloss_en": "at; to have"}),
    (115, {"gloss_en": "(question particle)"}),
    (1238, {"gloss_en": "paper, sheet; leaf"}),
    (116, {"gloss_en": "he", "lemma_ar": "هُوَ"}),  # remove shadda
]


def step_a1_fix_glosses(db, apply: bool) -> int:
    """Fix wrong glosses on existing lemmas."""
    count = 0
    for lemma_id, updates in GLOSS_FIXES:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            logger.warning(f"  Lemma {lemma_id} not found, skipping")
            continue
        changes = []
        for field, new_val in updates.items():
            old_val = getattr(lemma, field)
            if old_val != new_val:
                changes.append(f"{field}: '{old_val}' → '{new_val}'")
                if apply:
                    setattr(lemma, field, new_val)
        if changes:
            logger.info(f"  #{lemma_id} {lemma.lemma_ar}: {'; '.join(changes)}")
            count += 1
    if apply:
        db.commit()
    return count


# ── A2: Missing particles ──────────────────────────────────────────
MISSING_LEMMAS = [
    {"lemma_ar": "أَنْ", "lemma_ar_bare": "أن", "gloss_en": "that; to", "pos": "particle"},
    {"lemma_ar": "إِنْ", "lemma_ar_bare": "إن", "gloss_en": "if", "pos": "particle"},
    {"lemma_ar": "أَنَّ", "lemma_ar_bare": "أنّ", "gloss_en": "that", "pos": "particle"},
    {"lemma_ar": "إِنَّ", "lemma_ar_bare": "إنّ", "gloss_en": "indeed, that", "pos": "particle"},
    {"lemma_ar": "فَقْد", "lemma_ar_bare": "فقد", "gloss_en": "loss", "pos": "noun"},
    {"lemma_ar": "وَضْع", "lemma_ar_bare": "وضع", "gloss_en": "situation, state", "pos": "noun"},
]


def step_a2_create_missing_lemmas(db, apply: bool) -> int:
    """Create missing particle/noun lemmas."""
    count = 0
    for data in MISSING_LEMMAS:
        existing = db.query(Lemma).filter(
            Lemma.lemma_ar_bare == data["lemma_ar_bare"],
            Lemma.canonical_lemma_id.is_(None),
        ).first()
        if existing:
            logger.info(f"  {data['lemma_ar']} already exists (id={existing.lemma_id}), skipping")
            continue
        logger.info(f"  Creating: {data['lemma_ar']} ({data['gloss_en']}) [{data['pos']}]")
        if apply:
            lemma = Lemma(
                lemma_ar=data["lemma_ar"],
                lemma_ar_bare=data["lemma_ar_bare"],
                gloss_en=data["gloss_en"],
                pos=data["pos"],
                source="manual",
            )
            db.add(lemma)
        count += 1
    if apply:
        db.commit()
    return count


# ── A3: Conjugated-form verbs ──────────────────────────────────────
CONJUGATED_VERB_FIXES = [
    # (conjugated_lemma_id, correct_base_bare, correct_base_ar, correct_gloss)
    (1765, "سكن", "سَكَنَ", "to live, reside"),
    (1618, "سكن", "سَكَنَ", "to live, reside"),    # تسكن → سكن
    (1764, "سكن", "سَكَنَ", "to live, reside"),    # تسكنين → سكن
    (1731, "جلس", "جَلَسَ", "to sit"),             # نجلس → جلس
    (1609, "وضع", "وَضَعَ", "to put, place"),      # يضع → وضع
    (1743, "وقع", "وَقَعَ", "to fall; to be located"),  # يقع → وقع
    (1558, "كتب", "كَتَبَ", "to write"),           # يكتبون → كتب
    (1556, "درس", "دَرَسَ", "to study"),            # ندرس → درس
    (1828, "لبس", "لَبِسَ", "to wear"),             # يلبس → لبس
    (1736, "أكل", "أَكَلَ", "to eat"),              # نأكل → أكل
    (2073, "وجد", "وَجَدَ", "to find"),             # يوجد → وجد
]


def step_a3_fix_conjugated_verbs(db, apply: bool) -> int:
    """Fix verbs stored as conjugated forms — mark as variants or update."""
    count = 0
    for conj_id, base_bare, base_ar, base_gloss in CONJUGATED_VERB_FIXES:
        conj_lemma = db.query(Lemma).filter(Lemma.lemma_id == conj_id).first()
        if not conj_lemma:
            continue

        # Find or create the base form lemma
        base_lemma = db.query(Lemma).filter(
            Lemma.lemma_ar_bare == base_bare,
            Lemma.canonical_lemma_id.is_(None),
        ).first()

        if base_lemma and base_lemma.lemma_id != conj_id:
            # Base form exists — mark conjugated as variant
            logger.info(
                f"  #{conj_id} {conj_lemma.lemma_ar} → variant of #{base_lemma.lemma_id} {base_lemma.lemma_ar}"
            )
            if apply:
                conj_lemma.canonical_lemma_id = base_lemma.lemma_id
                # Re-map sentence_words from conjugated → base
                updated = db.query(SentenceWord).filter(
                    SentenceWord.lemma_id == conj_id
                ).update({SentenceWord.lemma_id: base_lemma.lemma_id})
                logger.info(f"    Re-mapped {updated} sentence_words")
                # Transfer ULK if needed
                _transfer_ulk(db, conj_id, base_lemma.lemma_id)
            count += 1
        elif not base_lemma:
            # No base form exists — update this lemma to be the base form
            logger.info(
                f"  #{conj_id} {conj_lemma.lemma_ar} → updating to base form {base_ar}"
            )
            if apply:
                conj_lemma.lemma_ar = base_ar
                conj_lemma.lemma_ar_bare = base_bare
                conj_lemma.gloss_en = base_gloss
            count += 1
        else:
            logger.info(f"  #{conj_id} {conj_lemma.lemma_ar} is already the base form")

    if apply:
        db.commit()
    return count


# ── A4: Possessive-form lemmas ─────────────────────────────────────
POSSESSIVE_FIXES = [
    # (possessive_lemma_id, base_lemma_bare)
    (1729, "أسرة"),   # أسرتي → أسرة
    (74, "ابن"),      # ابني → ابن
    (57, "ابن"),      # ابنك → ابن
    (1865, "عم"),     # عمي → عم
    (1690, "ملابس"), # ملابسي → ملابس
    (27, "عند"),      # عندك → عند
    (70, "أخت"),      # أختي → أخت
]


def step_a4_fix_possessive_lemmas(db, apply: bool) -> int:
    """Fix possessive forms stored as separate lemmas."""
    count = 0
    for poss_id, base_bare in POSSESSIVE_FIXES:
        poss_lemma = db.query(Lemma).filter(Lemma.lemma_id == poss_id).first()
        if not poss_lemma or poss_lemma.canonical_lemma_id is not None:
            continue

        base_lemma = db.query(Lemma).filter(
            Lemma.lemma_ar_bare == base_bare,
            Lemma.canonical_lemma_id.is_(None),
            Lemma.lemma_id != poss_id,
        ).first()

        if base_lemma:
            logger.info(
                f"  #{poss_id} {poss_lemma.lemma_ar} ({poss_lemma.gloss_en}) "
                f"→ variant of #{base_lemma.lemma_id} {base_lemma.lemma_ar}"
            )
            if apply:
                poss_lemma.canonical_lemma_id = base_lemma.lemma_id
                updated = db.query(SentenceWord).filter(
                    SentenceWord.lemma_id == poss_id
                ).update({SentenceWord.lemma_id: base_lemma.lemma_id})
                logger.info(f"    Re-mapped {updated} sentence_words")
                _transfer_ulk(db, poss_id, base_lemma.lemma_id)
            count += 1
        else:
            logger.warning(f"  No base lemma '{base_bare}' found for #{poss_id}")

    if apply:
        db.commit()
    return count


# ── A5: ال-prefix lemmas ──────────────────────────────────────────
# Lemmas where ال is genuinely part of the word — keep as-is
AL_KEEP = {
    "الله", "الآن", "الان", "اليوم", "الذي", "التي", "الذين",
    "اللذان", "اللتان", "اللواتي", "ال",
    # Country names — keep for now
    "النرويج", "الكويت", "البحرين", "الدنمارك", "السويد",
    "الاردن", "السعودية", "الجزائر", "السودان", "المغرب",
    "الصومال", "الامارات", "اليمن",
}


def step_a5_fix_al_prefix_lemmas(db, apply: bool) -> int:
    """Fix lemmas stored with ال prefix where it's not part of the word."""
    count = 0
    al_lemmas = db.query(Lemma).filter(
        Lemma.lemma_ar_bare.like("ال%"),
        Lemma.canonical_lemma_id.is_(None),
    ).all()

    for lem in al_lemmas:
        bare_norm = normalize_alef(lem.lemma_ar_bare)
        if bare_norm in AL_KEEP:
            continue

        without_al = lem.lemma_ar_bare[2:]
        if len(without_al) < 2:
            continue

        # Check if a base form already exists
        base = db.query(Lemma).filter(
            Lemma.lemma_ar_bare == without_al,
            Lemma.canonical_lemma_id.is_(None),
        ).first()

        if base:
            logger.info(
                f"  #{lem.lemma_id} {lem.lemma_ar} ({lem.gloss_en}) "
                f"→ variant of #{base.lemma_id} {base.lemma_ar}"
            )
            if apply:
                lem.canonical_lemma_id = base.lemma_id
                updated = db.query(SentenceWord).filter(
                    SentenceWord.lemma_id == lem.lemma_id
                ).update({SentenceWord.lemma_id: base.lemma_id})
                if updated:
                    logger.info(f"    Re-mapped {updated} sentence_words")
                _transfer_ulk(db, lem.lemma_id, base.lemma_id)
            count += 1
        else:
            # No base exists — strip ال from this lemma
            new_ar = lem.lemma_ar
            if new_ar.startswith("ال") or new_ar.startswith("اَلْ") or new_ar.startswith("الْ"):
                # Strip common diacritized ال patterns
                for prefix in ["اَلْ", "الْ", "ال"]:
                    if new_ar.startswith(prefix):
                        new_ar = new_ar[len(prefix):]
                        break

            gloss = lem.gloss_en or ""
            # Strip "the " from gloss if present
            if gloss.startswith("the "):
                gloss = gloss[4:]

            logger.info(
                f"  #{lem.lemma_id} {lem.lemma_ar} → {new_ar} (bare: {without_al}, gloss: '{gloss}')"
            )
            if apply:
                lem.lemma_ar = new_ar
                lem.lemma_ar_bare = without_al
                if gloss != lem.gloss_en:
                    lem.gloss_en = gloss
            count += 1

    if apply:
        db.commit()
    return count


# ── A6: Batch re-map sentence_words ────────────────────────────────
def step_a6_remap_sentence_words(db, apply: bool) -> int:
    """Re-run mapping on all active sentences with updated lookup."""
    lookup = build_comprehensive_lemma_lookup(db)
    logger.info(f"  Lookup dict has {len(lookup)} entries")

    active_sentences = db.query(Sentence).filter(Sentence.is_active == True).all()
    logger.info(f"  Processing {len(active_sentences)} active sentences")

    total_changes = 0
    for sent in active_sentences:
        old_sws = (
            db.query(SentenceWord)
            .filter(SentenceWord.sentence_id == sent.id)
            .order_by(SentenceWord.position)
            .all()
        )
        if not old_sws:
            continue

        target_lemma = db.query(Lemma).filter(Lemma.lemma_id == sent.target_lemma_id).first()
        if not target_lemma:
            continue

        target_bare = target_lemma.lemma_ar_bare
        tokens = [sw.surface_form for sw in old_sws]

        new_mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=lookup,
            target_lemma_id=sent.target_lemma_id,
            target_bare=target_bare,
        )

        for old_sw, new_m in zip(old_sws, new_mappings):
            if new_m.lemma_id and new_m.lemma_id != old_sw.lemma_id:
                old_lemma = db.query(Lemma).filter(Lemma.lemma_id == old_sw.lemma_id).first()
                new_lemma = db.query(Lemma).filter(Lemma.lemma_id == new_m.lemma_id).first()
                old_name = f"{old_lemma.lemma_ar} ({old_lemma.gloss_en})" if old_lemma else "NULL"
                new_name = f"{new_lemma.lemma_ar} ({new_lemma.gloss_en})" if new_lemma else "NULL"
                logger.info(
                    f"  Sentence #{sent.id} pos {old_sw.position}: "
                    f"'{old_sw.surface_form}' {old_name} → {new_name}"
                )
                if apply:
                    old_sw.lemma_id = new_m.lemma_id
                total_changes += 1

    if apply:
        db.commit()
    return total_changes


# ── Helpers ─────────────────────────────────────────────────────────
def _transfer_ulk(db, from_id: int, to_id: int):
    """Transfer UserLemmaKnowledge from variant to canonical if needed."""
    old_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == from_id
    ).first()
    if not old_ulk:
        return

    new_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == to_id
    ).first()

    if new_ulk:
        # Base already has ULK — keep whichever has more progress
        if (old_ulk.times_seen or 0) > (new_ulk.times_seen or 0):
            logger.info(f"    Keeping ULK from #{from_id} (more seen)")
            new_ulk.knowledge_state = old_ulk.knowledge_state
            new_ulk.times_seen = old_ulk.times_seen
            new_ulk.times_correct = old_ulk.times_correct
            new_ulk.fsrs_card_json = old_ulk.fsrs_card_json
        db.delete(old_ulk)
    else:
        # Move ULK to new lemma
        old_ulk.lemma_id = to_id


# ── Main ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Clean up lemma mapping issues")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--steps", default="A1,A2,A3,A4,A5,A6",
                        help="Comma-separated steps to run (default: all)")
    args = parser.parse_args()

    steps = set(args.steps.upper().split(","))
    mode = "APPLYING" if args.apply else "DRY RUN"
    logger.info(f"=== Lemma Mapping Cleanup ({mode}) ===\n")

    db = SessionLocal()
    try:
        if "A1" in steps:
            logger.info("Step A1: Fix wrong glosses")
            n = step_a1_fix_glosses(db, args.apply)
            logger.info(f"  → {n} glosses {'fixed' if args.apply else 'would fix'}\n")

        if "A2" in steps:
            logger.info("Step A2: Create missing particle/noun lemmas")
            n = step_a2_create_missing_lemmas(db, args.apply)
            logger.info(f"  → {n} lemmas {'created' if args.apply else 'would create'}\n")

        if "A3" in steps:
            logger.info("Step A3: Fix conjugated-form verb lemmas")
            n = step_a3_fix_conjugated_verbs(db, args.apply)
            logger.info(f"  → {n} verbs {'fixed' if args.apply else 'would fix'}\n")

        if "A4" in steps:
            logger.info("Step A4: Fix possessive-form lemmas")
            n = step_a4_fix_possessive_lemmas(db, args.apply)
            logger.info(f"  → {n} possessives {'fixed' if args.apply else 'would fix'}\n")

        if "A5" in steps:
            logger.info("Step A5: Fix ال-prefix lemmas")
            n = step_a5_fix_al_prefix_lemmas(db, args.apply)
            logger.info(f"  → {n} lemmas {'fixed' if args.apply else 'would fix'}\n")

        if "A6" in steps:
            logger.info("Step A6: Batch re-map sentence_words")
            n = step_a6_remap_sentence_words(db, args.apply)
            logger.info(f"  → {n} sentence_words {'re-mapped' if args.apply else 'would re-map'}\n")

        if not args.apply:
            logger.info("=== DRY RUN complete. Use --apply to execute. ===")
    finally:
        db.close()


if __name__ == "__main__":
    main()
