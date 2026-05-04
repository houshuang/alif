"""Add tashkeel (full diacritization) to lemmas whose lemma_ar lacks vowels.

Identifies lemmas where lemma_ar == lemma_ar_bare (i.e., never had any
diacritics stored) and uses Claude CLI to add proper tashkeel based on the
bare form, gloss, and POS. Validates each output: stripped vocalized form
must equal the original bare form. Skips non-Arabic-script lemmas (Hebrew,
Latin, etc.).

After running, re-run backfill_transliteration.py and backfill_forms_translit.py
to refresh transliterations for the newly-vocalized lemmas.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity
from app.services.sentence_validator import strip_diacritics, normalize_alef
from app.services.claude_code import generate_structured


_ARABIC_RANGE = range(0x0600, 0x0700)


def _is_arabic_script(text: str) -> bool:
    """True if at least one char is in the Arabic Unicode block."""
    return any(ord(c) in _ARABIC_RANGE for c in text)


_SCHEMA = {
    "type": "object",
    "properties": {
        "vocalized": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "vocalized_ar": {"type": "string"},
                },
                "required": ["lemma_id", "vocalized_ar"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["vocalized"],
    "additionalProperties": False,
}


_SYSTEM = """You are an expert in Arabic morphology and orthography.

Your task: add full tashkeel (Arabic diacritical marks) to a list of Arabic
lemmas given as bare unvocalized text plus an English gloss and part of speech.

Rules:
- Output the same word with proper diacritics (fatha, kasra, damma, sukun,
  shadda where appropriate). Use lemma/dictionary form (no case ending).
- For verbs, use the canonical past-tense 3rd-singular masculine form (the
  citation form), e.g. "كَتَبَ" not "كتب".
- For nouns and adjectives, use the singular indefinite form with no tanwīn.
- For ت marbuṭa words ending in ة, do not add a final tanwin or vowel.
- For function words / particles (e.g. قد, لو, كي, لقد), use their
  conventional vocalization.
- The unvocalized letters MUST remain identical — only diacritics are added.
- If a word is foreign or you genuinely cannot vocalize it, output the bare
  word unchanged.
"""


def _build_prompt(batch):
    lines = ["Add tashkeel to each lemma. Reply with the JSON schema only.\n"]
    for l in batch:
        lines.append(f'  - lemma_id={l.lemma_id}, bare="{l.lemma_ar_bare}", pos={l.pos or "?"}, gloss="{l.gloss_en or "?"}"')
    return "\n".join(lines)


def vocalize_batch(batch, timeout=180):
    """Returns dict {lemma_id: vocalized_ar} for the batch."""
    prompt = _build_prompt(batch)
    result = generate_structured(
        prompt=prompt,
        system_prompt=_SYSTEM,
        json_schema=_SCHEMA,
        model="haiku",
        timeout=timeout,
    )
    return {entry["lemma_id"]: entry["vocalized_ar"] for entry in result.get("vocalized", [])}


def main(dry_run=False, batch_size=20):
    db = SessionLocal()
    try:
        rows = (
            db.query(Lemma)
            .filter(Lemma.lemma_ar == Lemma.lemma_ar_bare)
            .order_by(Lemma.lemma_id)
            .all()
        )

        arabic = [l for l in rows if _is_arabic_script(l.lemma_ar_bare or "")]
        skipped_script = len(rows) - len(arabic)

        print(f"Found {len(rows)} unvocalized lemmas")
        print(f"  {len(arabic)} Arabic-script (will vocalize)")
        print(f"  {skipped_script} non-Arabic-script (will skip)\n")

        updated = 0
        rejected_changed_letters = 0
        rejected_no_diacritics = 0
        unchanged = 0

        for i in range(0, len(arabic), batch_size):
            batch = arabic[i : i + batch_size]
            print(f"--- Batch {i // batch_size + 1} ({len(batch)} lemmas) ---")
            try:
                proposals = vocalize_batch(batch)
            except Exception as e:
                print(f"  Batch failed: {e}")
                continue

            for l in batch:
                proposal = proposals.get(l.lemma_id)
                if not proposal:
                    print(f"  {l.lemma_id} {l.lemma_ar_bare}: no proposal")
                    continue

                # Validate: stripped proposal must match original bare form
                # (after normalizing alef variants — LLM often restores hamza
                # forms like اعراب → إعراب, which is a valid correction).
                stripped = strip_diacritics(proposal)
                if normalize_alef(stripped) != normalize_alef(l.lemma_ar_bare):
                    print(f"  {l.lemma_id} {l.lemma_ar_bare}: REJECTED (changed letters: got {stripped!r})")
                    rejected_changed_letters += 1
                    continue

                # Validate: must contain at least one diacritic
                if proposal == l.lemma_ar_bare:
                    print(f"  {l.lemma_id} {l.lemma_ar_bare}: unchanged (LLM returned bare form)")
                    unchanged += 1
                    continue

                print(f"  {l.lemma_id} {l.lemma_ar_bare} -> {proposal}")
                if not dry_run:
                    l.lemma_ar = proposal
                updated += 1

            if not dry_run:
                db.commit()  # Per-batch commit to avoid holding the lock

        print()
        print(f"Vocalized:                {updated}")
        print(f"LLM returned bare form:   {unchanged}")
        print(f"Rejected (letter drift):  {rejected_changed_letters}")
        print(f"Skipped (non-Arabic):     {skipped_script}")

        if not dry_run and updated > 0:
            log_activity(
                db,
                event_type="lemma_vocalization_completed",
                summary=f"Added tashkeel to {updated} previously-unvocalized lemmas",
                detail={
                    "updated": updated,
                    "rejected": rejected_changed_letters,
                    "unchanged": unchanged,
                    "skipped_non_arabic": skipped_script,
                },
            )

    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    batch_size = 20
    for arg in sys.argv:
        if arg.startswith("--batch-size="):
            batch_size = int(arg.split("=")[1])
    main(dry_run=dry_run, batch_size=batch_size)
