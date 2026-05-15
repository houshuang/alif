#!/usr/bin/env python3
"""Split the Form I/II/IV merge on canonical #3569 into homograph canonicals.

The Quran inflected-lemma cleanup (2026-05-15) created canonical #3569 with
lemma_ar="نَزَل" (Form I "to descend") but linked three different verb forms
to it because they share the unvocalized bare "نزل":
  - #2828 انزل  → أَنْزَلَ (Form IV "to bring down")  — gloss leaked into #3569
  - #2878 نزلنا → نَزَّلَ  (Form II "to send down")    — different sense
  - #3569 itself = Form I

This script creates two homograph canonicals (Form II and Form IV) using the
ALLOW_HOMOGRAPH precedent (lemma_ar_bare has no DB unique constraint), then
re-links the variants and cleans Form I's lemma_ar / gloss / forms_json.

Data attached to #3569 from prior merging is NOT moved back — the sentence
context determines which sense each row represented, and that signal was
discarded during merge. Future imports of these forms will lemmatize to the
correct homograph via CAMeL's vocalized lex (matched by lemma_ar / vocalized
form), not the shared bare.

Usage:
    python3 scripts/split_nzl_homograph_2026_05_15.py --dry-run
    python3 scripts/split_nzl_homograph_2026_05_15.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, Root, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        nzl_root = db.query(Root).filter(Root.root == "ن.ز.ل").first()
        if not nzl_root:
            print("ERROR: root ن.ز.ل not in DB", file=sys.stderr)
            sys.exit(1)
        root_id = nzl_root.root_id

        form1 = db.get(Lemma, 3569)  # current "merged" canonical
        v2828 = db.get(Lemma, 2828)  # Form IV variant
        v2878 = db.get(Lemma, 2878)  # Form II variant
        for tag, l in (("Form I #3569", form1), ("#2828 (Form IV)", v2828),
                       ("#2878 (Form II)", v2878)):
            if l is None:
                print(f"ERROR: lemma for {tag} missing", file=sys.stderr)
                sys.exit(1)
            print(f"  {tag}: lemma_ar={l.lemma_ar!r} bare={l.lemma_ar_bare!r} "
                  f"canonical_id={l.canonical_lemma_id}")

        # Verify pre-condition: both variants currently point at #3569
        if v2828.canonical_lemma_id != 3569 or v2878.canonical_lemma_id != 3569:
            print(f"ERROR: expected both variants to point at #3569, got "
                  f"{v2828.canonical_lemma_id} and {v2878.canonical_lemma_id}",
                  file=sys.stderr)
            sys.exit(1)

        # Step 1: Fix Form I canonical (#3569) — clean gloss, clean forms_json
        # Keep lemma_ar='نَزَل' (Form I citation), update gloss to be unambiguously Form I.
        new_form1_gloss = "to descend, come down (Form I)"
        new_form1_lemma_ar = "نَزَلَ"  # add fatha for 3ms perfect
        clean_forms_json = {
            "verb_form": "I",
            "present": "يَنْزِلُ",
            "past_3fs": "نَزَلَتْ",
            "past_3p": "نَزَلُوا",
            "past_1s": "نَزَلْتُ",
            "past_3fp": "نَزَلْنَ",
            "present_3fp": "يَنْزِلْنَ",
            "present_3mp": "يَنْزِلُونَ",
            "masdar": "نُزُول",
            "active_participle": "نَازِل",
            "passive_participle": "مَنْزُول",
            "imperative": "اِنْزِلْ",
        }
        print()
        print(f"Step 1: clean Form I #{form1.lemma_id}")
        print(f"  lemma_ar: {form1.lemma_ar!r} -> {new_form1_lemma_ar!r}")
        print(f"  gloss:    {form1.gloss_en!r} -> {new_form1_gloss!r}")
        print(f"  forms_json: dropping 'inflected' key (Form IV pollution)")
        if not args.dry_run:
            form1.lemma_ar = new_form1_lemma_ar
            form1.gloss_en = new_form1_gloss
            form1.forms_json = clean_forms_json
            db.flush()

        # Step 2: Create Form II homograph canonical
        print()
        print("Step 2: create Form II homograph canonical (لNَزَّلَ, bare=نزل)")
        if args.dry_run:
            print("  [dry] would CREATE")
            form2_id = -1
        else:
            form2 = Lemma(
                lemma_ar="نَزَّلَ",
                lemma_ar_bare="نزل",
                gloss_en="to send down, reveal (Form II causative)",
                pos="verb",
                source="quran",
                root_id=root_id,
                word_category=None,
                forms_json={
                    "verb_form": "II",
                    "present": "يُنَزِّلُ",
                    "past_1p": "نَزَّلْنَا",
                    "masdar": "تَنْزِيل",
                    "active_participle": "مُنَزِّل",
                    "passive_participle": "مُنَزَّل",
                },
            )
            db.add(form2)
            db.flush()
            form2_id = form2.lemma_id
            db.add(UserLemmaKnowledge(
                lemma_id=form2_id,
                knowledge_state="encountered",
                source="quran",
                total_encounters=0,
            ))
            print(f"  created Form II #{form2_id}")

        # Step 3: Create Form IV homograph canonical
        print()
        print("Step 3: create Form IV homograph canonical (أَنْزَلَ, bare=نزل)")
        if args.dry_run:
            print("  [dry] would CREATE")
            form4_id = -1
        else:
            form4 = Lemma(
                lemma_ar="أَنْزَلَ",
                lemma_ar_bare="نزل",
                gloss_en="to bring down, send down (Form IV)",
                pos="verb",
                source="quran",
                root_id=root_id,
                word_category=None,
                forms_json={
                    "verb_form": "IV",
                    "present": "يُنْزِلُ",
                    "past_1s": "أَنْزَلْتُ",
                    "masdar": "إِنْزَال",
                    "active_participle": "مُنْزِل",
                    "passive_participle": "مُنْزَل",
                },
            )
            db.add(form4)
            db.flush()
            form4_id = form4.lemma_id
            db.add(UserLemmaKnowledge(
                lemma_id=form4_id,
                knowledge_state="encountered",
                source="quran",
                total_encounters=0,
            ))
            print(f"  created Form IV #{form4_id}")

        # Step 4: Re-link variants to their proper homographs
        print()
        print("Step 4: re-link variants to proper homographs")
        if not args.dry_run:
            v2828.canonical_lemma_id = form4_id
            v2878.canonical_lemma_id = form2_id
            db.flush()
        print(f"  #2828 (انزل أَنْزَلَ 'to bring down') → Form IV #{form4_id}")
        print(f"  #2878 (نزلنا 'we sent down') → Form II #{form2_id}")

        if args.dry_run:
            db.rollback()
            print("\nDry run — no DB writes.")
            return

        db.commit()
        log_activity(
            db,
            event_type="manual_action",
            summary="Split ن.ز.ل Form I/II/IV merge into homograph canonicals",
            detail={
                "script": "split_nzl_homograph_2026_05_15",
                "form1_id": form1.lemma_id,
                "form2_id": form2_id,
                "form4_id": form4_id,
                "relinked": {"#2828": form4_id, "#2878": form2_id},
            },
        )
        db.commit()
        print(f"\nDone: Form I=#{form1.lemma_id} Form II=#{form2_id} Form IV=#{form4_id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
