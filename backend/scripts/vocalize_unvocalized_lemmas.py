"""Add tashkeel (full diacritization) to lemmas whose lemma_ar lacks stem vowels.

Identifies lemmas where lemma_ar contains no lexical diacritics and uses
Claude CLI to add proper tashkeel based on the existing letter sequence
(including any attached al-prefix), gloss, and POS. Final case vowels/tanwīn
alone do not count as lexical vocalization. Skips non-Arabic-script lemmas
(Hebrew, Latin, etc.).

The old filter `lemma_ar == lemma_ar_bare` missed lemmas where lemma_ar
differs from lemma_ar_bare only by an attached clitic (e.g. الغلام vs
غلام) — these were still fully unvocalized and producing garbage
transliterations like `al-ghlām` instead of `al-ghulām`. The current filter
also catches forms with only a final case mark, e.g. `محظوظةً`.

The actual vocalization logic lives in `app/services/lemma_vocalization.py`
so the runtime enrichment path can share it (so newly-imported unvocalized
lemmas are caught and fixed before transliteration runs).

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
from app.services.lemma_vocalization import (
    apply_vocalization,
    is_arabic_script,
    needs_vocalization,
    validate_proposal,
    vocalize_batch,
)


def main(dry_run=False, batch_size=20):
    db = SessionLocal()
    try:
        # All lemmas where lemma_ar has no lexical diacritics. The previous
        # zero-diacritic check missed case-ending-only forms like محظوظةً.
        candidates = db.query(Lemma).order_by(Lemma.lemma_id).all()
        rows = [l for l in candidates if needs_vocalization(l) or
                (l.lemma_ar and not is_arabic_script(l.lemma_ar))]
        unvocalized = [l for l in rows if needs_vocalization(l)]
        skipped_script = len(rows) - len(unvocalized)

        print(f"Found {len(unvocalized)} unvocalized Arabic-script lemmas")
        print(f"  (skipping {skipped_script} non-Arabic-script rows)\n")

        updated = 0
        rejected_changed_letters = 0
        unchanged = 0

        for i in range(0, len(unvocalized), batch_size):
            batch = unvocalized[i : i + batch_size]
            print(f"--- Batch {i // batch_size + 1} ({len(batch)} lemmas) ---")
            try:
                proposals = vocalize_batch(batch)
            except Exception as e:
                print(f"  Batch failed: {e}")
                continue

            for l in batch:
                proposal = proposals.get(l.lemma_id)
                if not proposal:
                    print(f"  {l.lemma_id} {l.lemma_ar}: no proposal")
                    continue

                if proposal == l.lemma_ar:
                    print(f"  {l.lemma_id} {l.lemma_ar}: unchanged (LLM returned bare form)")
                    unchanged += 1
                    continue

                if not validate_proposal(proposal, l.lemma_ar):
                    print(f"  {l.lemma_id} {l.lemma_ar}: REJECTED (letter drift: {proposal!r})")
                    rejected_changed_letters += 1
                    continue

                print(f"  {l.lemma_id} {l.lemma_ar} -> {proposal}")
                if not dry_run:
                    apply_vocalization(l, proposal)
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
