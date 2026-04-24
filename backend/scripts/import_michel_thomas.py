"""Import Michel Thomas Arabic audio course into Alif.

Pipeline:
  1. Transcribe MP3s with Soniox (Arabic + English code-switching)
  2. Extract Arabic segments from token stream
  3. Classify segments via LLM (Egyptian vs MSA, add diacritics, translate)
  4. Import words + sentences into DB

Usage:
  python3 scripts/import_michel_thomas.py --audio-dir /path/to/cd1/
  python3 scripts/import_michel_thomas.py --phase transcribe --audio-dir /path/to/cd1/
  python3 scripts/import_michel_thomas.py --phase extract
  python3 scripts/import_michel_thomas.py --phase classify
  python3 scripts/import_michel_thomas.py --phase import [--dry-run]
  python3 scripts/import_michel_thomas.py --phase verify
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import engine, Base, SessionLocal
from app.models import (
    Lemma, Root, Sentence, SentenceWord, Story, StoryWord,
    UserLemmaKnowledge,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "michel_thomas"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
SEGMENTS_FILE = DATA_DIR / "segments.json"
CLASSIFIED_FILE = DATA_DIR / "classified.json"
PROGRESS_FILE = DATA_DIR / "progress.jsonl"

MIN_ARABIC_WORDS = 3  # minimum words for a segment to be imported as a sentence


@dataclass
class ArabicSegment:
    arabic_text: str
    english_context: str
    start_ms: int
    end_ms: int
    track: str
    speaker: str | None = None


# --------------------------------------------------------------------------- #
# Phase 1: Transcribe
# --------------------------------------------------------------------------- #

def load_progress() -> dict[str, str]:
    """Load checkpoint: {filename: transcription_id}."""
    progress = {}
    if PROGRESS_FILE.exists():
        for line in PROGRESS_FILE.read_text().strip().split("\n"):
            if line.strip():
                entry = json.loads(line)
                progress[entry["file"]] = entry["txn_id"]
    return progress


def save_progress(filename: str, txn_id: str):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "a") as f:
        f.write(json.dumps({"file": filename, "txn_id": txn_id}) + "\n")


def phase_transcribe(audio_dir: Path, soniox_key: str):
    """Transcribe all MP3 files in audio_dir using Soniox."""
    from app.services.soniox_service import SonioxService

    svc = SonioxService(api_key=soniox_key)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    completed = load_progress()
    mp3_files = sorted(audio_dir.glob("*.mp3"))

    if not mp3_files:
        logger.error(f"No MP3 files found in {audio_dir}")
        return

    logger.info(f"Found {len(mp3_files)} tracks, {len(completed)} already transcribed")

    for mp3 in mp3_files:
        if mp3.name in completed:
            logger.info(f"Skipping {mp3.name} (already transcribed)")
            continue

        logger.info(f"Transcribing {mp3.name}...")
        try:
            transcript = svc.transcribe_file(mp3, language_hints=["ar", "en"])
            out_path = TRANSCRIPTS_DIR / f"{mp3.stem}.json"
            out_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
            save_progress(mp3.name, "completed")
            logger.info(f"  → {len(transcript.get('tokens', []))} tokens saved to {out_path.name}")
        except Exception as e:
            logger.error(f"  Failed: {e}")
            save_progress(mp3.name, f"error:{e}")

    logger.info("Transcription phase complete.")


# --------------------------------------------------------------------------- #
# Phase 2: Extract Arabic segments
# --------------------------------------------------------------------------- #

def phase_extract():
    """Extract Arabic segments from Soniox transcripts."""
    transcript_files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    if not transcript_files:
        logger.error(f"No transcripts found in {TRANSCRIPTS_DIR}")
        return

    all_segments: list[dict] = []

    for tf in transcript_files:
        transcript = json.loads(tf.read_text())
        tokens = transcript.get("tokens", [])
        track_name = tf.stem

        segments = _extract_segments_from_tokens(tokens, track_name)
        all_segments.extend([asdict(s) for s in segments])
        logger.info(f"  {track_name}: {len(tokens)} tokens → {len(segments)} Arabic segments")

    SEGMENTS_FILE.write_text(json.dumps(all_segments, ensure_ascii=False, indent=2))
    logger.info(f"Extracted {len(all_segments)} total segments → {SEGMENTS_FILE}")

    # Show stats
    long_segments = [s for s in all_segments if len(s["arabic_text"].split()) >= MIN_ARABIC_WORDS]
    logger.info(f"  {len(long_segments)} segments with {MIN_ARABIC_WORDS}+ Arabic words (will become sentences)")


def _extract_segments_from_tokens(tokens: list[dict], track_name: str) -> list[ArabicSegment]:
    """Group consecutive Arabic tokens into segments with English context."""
    segments: list[ArabicSegment] = []
    current_arabic: list[dict] = []
    english_before: list[str] = []
    last_english: list[str] = []

    for token in tokens:
        lang = token.get("language", "")
        text = token.get("text", "").strip()
        if not text:
            continue

        if lang == "ar":
            if not current_arabic:
                english_before = list(last_english)
            current_arabic.append(token)
        else:
            if current_arabic:
                # Flush Arabic segment
                seg = _build_segment(current_arabic, english_before, track_name)
                if seg:
                    segments.append(seg)
                current_arabic = []
                english_before = []

            last_english.append(text)
            # Keep only last ~20 English words for context
            if len(last_english) > 20:
                last_english = last_english[-20:]

    # Flush final Arabic segment
    if current_arabic:
        seg = _build_segment(current_arabic, english_before, track_name)
        if seg:
            segments.append(seg)

    return segments


def _build_segment(
    arabic_tokens: list[dict],
    english_context: list[str],
    track_name: str,
) -> ArabicSegment | None:
    """Build an ArabicSegment from a group of consecutive Arabic tokens."""
    arabic_text = " ".join(t["text"].strip() for t in arabic_tokens if t["text"].strip())
    if not arabic_text:
        return None

    return ArabicSegment(
        arabic_text=arabic_text,
        english_context=" ".join(english_context),
        start_ms=arabic_tokens[0].get("start_ms", 0),
        end_ms=arabic_tokens[-1].get("end_ms", 0),
        track=track_name,
        speaker=arabic_tokens[0].get("speaker"),
    )


# --------------------------------------------------------------------------- #
# Phase 3: LLM Classification
# --------------------------------------------------------------------------- #

def phase_classify():
    """Classify Arabic segments: Egyptian vs MSA, add diacritics, translate."""
    from app.services.llm import generate_completion

    if not SEGMENTS_FILE.exists():
        logger.error(f"No segments file at {SEGMENTS_FILE}. Run --phase extract first.")
        return

    segments = json.loads(SEGMENTS_FILE.read_text())
    logger.info(f"Classifying {len(segments)} segments...")

    # Deduplicate Arabic text for classification
    unique_texts: dict[str, dict] = {}
    for seg in segments:
        text = seg["arabic_text"].strip()
        if text not in unique_texts:
            unique_texts[text] = seg

    unique_list = list(unique_texts.values())
    logger.info(f"  {len(unique_list)} unique Arabic texts (after dedup)")

    # Process in batches of 25
    batch_size = 25
    all_classified: list[dict] = []

    for i in range(0, len(unique_list), batch_size):
        batch = unique_list[i:i + batch_size]
        logger.info(f"  Classifying batch {i // batch_size + 1} ({len(batch)} segments)...")

        entries = []
        for seg in batch:
            entries.append({
                "arabic": seg["arabic_text"],
                "english_context": seg["english_context"][:200],
            })

        prompt = f"""You are analyzing Arabic segments from a Michel Thomas Egyptian Arabic course.
For each segment, provide:

1. "arabic_cleaned": The segment with correct Arabic spelling and FULL diacritics (tashkeel on every letter)
2. "english": Concise English translation
3. "dialect_status": One of:
   - "msa" — Standard MSA word/phrase
   - "shared" — Used in both Egyptian and MSA (same form, same meaning)
   - "egyptian_with_msa_equivalent" — Egyptian form with a clear MSA equivalent
   - "egyptian_only" — Egyptian-only with no clean MSA mapping
4. "msa_equivalent": If dialect_status is "egyptian_with_msa_equivalent", the MSA form with full diacritics. Otherwise null.
5. "words": List of individual words in base/dictionary form, each with:
   - "arabic": base form with diacritics
   - "english": gloss
   - "pos": part of speech (noun/verb/adj/adv/prep/particle/conj/pron)

The English context from the lesson is provided to help with meaning.
Only extract meaningful content words (skip filler, hesitations, repetitions).

Segments:
{json.dumps(entries, ensure_ascii=False, indent=2)}

Respond with JSON: {{"classifications": [...]}}"""

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt="You are an Arabic linguistics expert. Respond with JSON only.",
                json_mode=True,
                temperature=0.3,
                model_override="claude_haiku",
            )
            classifications = result.get("classifications", [])
            for j, cls in enumerate(classifications):
                cls["original_arabic"] = batch[j]["arabic_text"]
                cls["track"] = batch[j]["track"]
                cls["english_context"] = batch[j]["english_context"]
                cls["start_ms"] = batch[j]["start_ms"]
                cls["end_ms"] = batch[j]["end_ms"]
            all_classified.extend(classifications)
        except Exception as e:
            logger.error(f"  LLM classification failed for batch: {e}")
            # Add unclassified entries
            for seg in batch:
                all_classified.append({
                    "original_arabic": seg["arabic_text"],
                    "arabic_cleaned": seg["arabic_text"],
                    "english": "",
                    "dialect_status": "unknown",
                    "msa_equivalent": None,
                    "words": [],
                    "track": seg["track"],
                    "english_context": seg["english_context"],
                })

    CLASSIFIED_FILE.write_text(json.dumps(all_classified, ensure_ascii=False, indent=2))

    # Stats
    by_status = {}
    for c in all_classified:
        status = c.get("dialect_status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    logger.info(f"Classification complete → {CLASSIFIED_FILE}")
    logger.info(f"  Status breakdown: {by_status}")

    # Count total unique words
    all_words = set()
    for c in all_classified:
        for w in c.get("words", []):
            all_words.add(w.get("arabic", ""))
    logger.info(f"  Total unique words across all segments: {len(all_words)}")


# --------------------------------------------------------------------------- #
# Phase 4: Database import
# --------------------------------------------------------------------------- #

def phase_import(dry_run: bool = False):
    """Import classified segments into the database."""
    from fsrs import Scheduler, Card, Rating
    from app.services.sentence_validator import (
        build_lemma_lookup,
        resolve_existing_lemma,
        strip_diacritics,
        normalize_alef,
        tokenize_display,
        map_tokens_to_lemmas,
    )
    from app.services.transliteration import transliterate_arabic
    from app.services.activity_log import log_activity

    if not CLASSIFIED_FILE.exists():
        logger.error(f"No classified file at {CLASSIFIED_FILE}. Run --phase classify first.")
        return

    classified = json.loads(CLASSIFIED_FILE.read_text())
    logger.info(f"Importing from {len(classified)} classified segments...")

    db = SessionLocal()
    Base.metadata.create_all(bind=engine)

    try:
        # Build lemma lookup for dedup
        all_lemmas = db.query(Lemma).all()
        lemma_lookup = build_lemma_lookup(all_lemmas)
        existing_bares = {normalize_alef(l.lemma_ar_bare) for l in all_lemmas}

        # Collect all words to import (dedup across segments)
        words_to_import: dict[str, dict] = {}  # bare_form -> word_info
        sentences_to_import: list[dict] = []
        skipped_egyptian = []

        for seg in classified:
            status = seg.get("dialect_status", "unknown")
            if status == "egyptian_only":
                skipped_egyptian.append(seg)
                continue

            # Use MSA equivalent if available
            arabic_text = seg.get("arabic_cleaned", seg.get("original_arabic", ""))
            if status == "egyptian_with_msa_equivalent" and seg.get("msa_equivalent"):
                arabic_text = seg["msa_equivalent"]

            english = seg.get("english", "")
            original_tokens = tokenize(arabic_text)

            # Collect words
            for word_info in seg.get("words", []):
                word_ar = word_info.get("arabic", "").strip()
                if not word_ar:
                    continue
                bare = strip_diacritics(word_ar)
                bare_norm = normalize_alef(bare)
                if bare_norm not in words_to_import:
                    words_to_import[bare_norm] = {
                        "arabic": word_ar,
                        "bare": bare,
                        "english": word_info.get("english", ""),
                        "pos": word_info.get("pos", ""),
                    }

            # Collect sentences (3+ words)
            if len(original_tokens) >= MIN_ARABIC_WORDS and english:
                sentences_to_import.append({
                    "arabic": arabic_text,
                    "english": english,
                    "track": seg.get("track", ""),
                })

        logger.info(f"  Words to process: {len(words_to_import)}")
        logger.info(f"  Sentences to create: {len(sentences_to_import)}")
        logger.info(f"  Skipped (egyptian_only): {len(skipped_egyptian)}")

        if dry_run:
            logger.info("\n--- DRY RUN: Words ---")
            for bare, info in sorted(words_to_import.items()):
                exists = bare in existing_bares or resolve_existing_lemma(bare, lemma_lookup)
                marker = "EXISTS" if exists else "NEW"
                logger.info(f"  [{marker}] {info['arabic']} ({info['bare']}) = {info['english']}")

            logger.info(f"\n--- DRY RUN: Sentences ({len(sentences_to_import)}) ---")
            for s in sentences_to_import[:10]:
                logger.info(f"  {s['arabic']} = {s['english']}")
            if len(sentences_to_import) > 10:
                logger.info(f"  ... and {len(sentences_to_import) - 10} more")

            logger.info(f"\n--- DRY RUN: Skipped Egyptian ({len(skipped_egyptian)}) ---")
            for s in skipped_egyptian[:10]:
                logger.info(f"  {s.get('arabic_cleaned', s.get('original_arabic', ''))} = {s.get('english', '')}")
            return

        # --- Actually import ---

        # 1. Create Story for CD
        story = Story(
            title_ar="ميشيل توماس - العربية المصرية",
            title_en="Michel Thomas Egyptian Arabic - CD 1",
            body_ar="",
            body_en="",
            source="michel_thomas",
            status="completed",
            difficulty_level="beginner",
        )
        db.add(story)
        db.flush()
        logger.info(f"  Created Story id={story.id}")

        # 2. Import words
        new_lemma_ids: list[int] = []
        n_existing = 0
        now = datetime.now(timezone.utc)

        for bare_norm, info in words_to_import.items():
            # Check existing
            if bare_norm in existing_bares:
                n_existing += 1
                continue
            existing_id = resolve_existing_lemma(info["bare"], lemma_lookup)
            if existing_id:
                n_existing += 1
                continue

            # Strip al-prefix for canonical bare
            bare = info["bare"]
            canonical_bare = bare[2:] if bare.startswith("ال") and len(bare) > 2 else bare
            canonical_norm = normalize_alef(canonical_bare)

            # Skip if canonical form exists
            if canonical_norm in existing_bares:
                n_existing += 1
                continue

            # Create lemma
            lemma = Lemma(
                lemma_ar=info["arabic"],
                lemma_ar_bare=canonical_bare,
                pos=info.get("pos", ""),
                gloss_en=info["english"],
                source="michel_thomas",
            )
            db.add(lemma)
            db.flush()

            # Create ULK as "learning" with fresh FSRS card (skip Leitner)
            scheduler = Scheduler()
            card = Card()
            reviewed_card, _ = scheduler.review_card(card, Rating.Good, now)

            ulk = UserLemmaKnowledge(
                lemma_id=lemma.lemma_id,
                knowledge_state="learning",
                fsrs_card_json=reviewed_card.to_dict(),
                source="michel_thomas",
                times_seen=5,
                times_correct=4,
                total_encounters=10,
                introduced_at=now,
                graduated_at=now,
            )
            db.add(ulk)
            new_lemma_ids.append(lemma.lemma_id)

            # Update lookup
            existing_bares.add(canonical_norm)
            lemma_lookup[canonical_norm] = lemma.lemma_id
            if bare_norm != canonical_norm:
                existing_bares.add(bare_norm)

        logger.info(f"  Created {len(new_lemma_ids)} new lemmas, {n_existing} already existed")

        # 3. Rebuild lemma lookup with new lemmas for sentence mapping
        all_lemmas = db.query(Lemma).all()
        lemma_lookup = build_lemma_lookup(all_lemmas)

        # 4. Create sentences
        n_sentences = 0
        for sent_data in sentences_to_import:
            arabic = sent_data["arabic"]
            english = sent_data["english"]
            tokens = tokenize_display(arabic)

            if len(tokens) < MIN_ARABIC_WORDS:
                continue

            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=lemma_lookup,
                target_lemma_id=0,
                target_bare="",
            )

            # Pick primary target (rarest word)
            target_lid = _pick_primary_target_simple(mappings, db)

            try:
                translit = transliterate_arabic(arabic)
            except Exception:
                translit = ""

            sent = Sentence(
                arabic_text=arabic,
                english_translation=english,
                transliteration=translit,
                source="michel_thomas",
                target_lemma_id=target_lid,
                story_id=story.id,
                is_active=True,
                created_at=now,
                max_word_count=len(tokens),
            )
            db.add(sent)
            db.flush()

            for m in mappings:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=(m.lemma_id == target_lid) if target_lid else False,
                )
                db.add(sw)

            n_sentences += 1

        logger.info(f"  Created {n_sentences} sentences")

        # 5. Update story body with all sentence text
        all_sents = (
            db.query(Sentence)
            .filter(Sentence.story_id == story.id)
            .order_by(Sentence.id)
            .all()
        )
        story.body_ar = "\n".join(s.arabic_text for s in all_sents)
        story.body_en = "\n".join(s.english_translation or "" for s in all_sents)

        # 6. Variant detection on new lemmas
        variants_marked = 0
        if new_lemma_ids:
            try:
                from app.services.variant_detection import (
                    detect_variants_llm,
                    detect_definite_variants,
                    mark_variants,
                )
                camel_vars = detect_variants_llm(db, lemma_ids=new_lemma_ids)
                already = {v[0] for v in camel_vars}
                def_vars = detect_definite_variants(
                    db, lemma_ids=new_lemma_ids, already_variant_ids=already
                )
                all_vars = camel_vars + def_vars
                if all_vars:
                    variants_marked = mark_variants(db, all_vars)
                    for var_id, canon_id, vtype, _ in all_vars:
                        var = db.get(Lemma, var_id)
                        canon = db.get(Lemma, canon_id)
                        logger.info(f"  Variant: {var.lemma_ar_bare} → {canon.lemma_ar_bare} [{vtype}]")
            except Exception as e:
                logger.warning(f"  Variant detection failed: {e}")

        # 7. Activity log
        try:
            log_activity(db, "michel_thomas_imported", (
                f"Imported CD1: {len(new_lemma_ids)} new words, "
                f"{n_existing} existing, {n_sentences} sentences, "
                f"{len(skipped_egyptian)} Egyptian-only skipped"
            ), {
                "cd": 1,
                "words_new": len(new_lemma_ids),
                "words_existing": n_existing,
                "sentences": n_sentences,
                "egyptian_skipped": len(skipped_egyptian),
                "variants_marked": variants_marked,
            })
        except Exception as e:
            logger.warning(f"  Activity logging failed: {e}")

        db.commit()
        logger.info("Import complete!")
        logger.info(f"  Story: id={story.id}")
        logger.info(f"  New words: {len(new_lemma_ids)}")
        logger.info(f"  Existing: {n_existing}")
        logger.info(f"  Sentences: {n_sentences}")
        logger.info(f"  Variants: {variants_marked}")
        logger.info(f"  Egyptian skipped: {len(skipped_egyptian)}")

        # Save Egyptian-only words for reference
        if skipped_egyptian:
            egyptian_file = DATA_DIR / "skipped_egyptian.json"
            egyptian_file.write_text(json.dumps(skipped_egyptian, ensure_ascii=False, indent=2))
            logger.info(f"  Egyptian-only words saved to {egyptian_file}")

    finally:
        db.close()


def _pick_primary_target_simple(mappings: list, db) -> int | None:
    """Pick rarest word as primary target (simplified version)."""
    lemma_ids = [m.lemma_id for m in mappings if m.lemma_id]
    if not lemma_ids:
        return None

    lemmas = (
        db.query(Lemma.lemma_id, Lemma.frequency_rank)
        .filter(Lemma.lemma_id.in_(lemma_ids))
        .all()
    )
    ranked = sorted(lemmas, key=lambda l: l.frequency_rank or 999999, reverse=True)
    return ranked[0].lemma_id if ranked else lemma_ids[0]


# --------------------------------------------------------------------------- #
# Phase 5: Verify
# --------------------------------------------------------------------------- #

def phase_verify():
    """Print verification report."""
    db = SessionLocal()
    Base.metadata.create_all(bind=engine)

    try:
        # Stories
        stories = db.query(Story).filter(Story.source == "michel_thomas").all()
        print(f"\n=== Michel Thomas Stories ({len(stories)}) ===")
        for s in stories:
            print(f"  id={s.id} | {s.title_en} | status={s.status}")

        # Words
        mt_lemmas = (
            db.query(Lemma)
            .filter(Lemma.source == "michel_thomas")
            .all()
        )
        print(f"\n=== Words ({len(mt_lemmas)}) ===")
        for l in mt_lemmas[:20]:
            ulk = db.query(UserLemmaKnowledge).filter(
                UserLemmaKnowledge.lemma_id == l.lemma_id
            ).first()
            state = ulk.knowledge_state if ulk else "no ULK"
            print(f"  {l.lemma_ar} ({l.lemma_ar_bare}) = {l.gloss_en} [{state}]")
        if len(mt_lemmas) > 20:
            print(f"  ... and {len(mt_lemmas) - 20} more")

        # Sentences
        mt_sents = (
            db.query(Sentence)
            .filter(Sentence.source == "michel_thomas")
            .all()
        )
        print(f"\n=== Sentences ({len(mt_sents)}) ===")
        for s in mt_sents[:15]:
            print(f"  [{s.id}] {s.arabic_text}")
            print(f"       = {s.english_translation}")
        if len(mt_sents) > 15:
            print(f"  ... and {len(mt_sents) - 15} more")

        # Skipped Egyptian
        egyptian_file = DATA_DIR / "skipped_egyptian.json"
        if egyptian_file.exists():
            skipped = json.loads(egyptian_file.read_text())
            print(f"\n=== Skipped Egyptian-Only ({len(skipped)}) ===")
            for s in skipped[:10]:
                print(f"  {s.get('arabic_cleaned', s.get('original_arabic', ''))} = {s.get('english', '')}")

    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Import Michel Thomas Arabic audio course")
    parser.add_argument("--audio-dir", type=Path, help="Directory with MP3 files")
    parser.add_argument("--soniox-key", type=str, help="Soniox API key (or set SONIOX_API_KEY env)")
    parser.add_argument("--phase", choices=["transcribe", "extract", "classify", "import", "verify", "all"],
                        default="all", help="Which phase to run")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported")
    args = parser.parse_args()

    # Resolve Soniox key
    import os
    soniox_key = args.soniox_key or os.environ.get("SONIOX_API_KEY", "")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.phase in ("transcribe", "all"):
        if not args.audio_dir:
            parser.error("--audio-dir required for transcribe phase")
        if not soniox_key:
            parser.error("--soniox-key or SONIOX_API_KEY required for transcribe phase")
        phase_transcribe(args.audio_dir, soniox_key)

    if args.phase in ("extract", "all"):
        phase_extract()

    if args.phase in ("classify", "all"):
        phase_classify()

    if args.phase in ("import", "all"):
        phase_import(dry_run=args.dry_run)

    if args.phase in ("verify", "all"):
        phase_verify()


if __name__ == "__main__":
    main()
