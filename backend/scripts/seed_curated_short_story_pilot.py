"""Validate and seed three editor-guided short-story pilot candidates.

These candidates preserve target groups discovered by the production planner,
but make the causal actions that the adversarial editor found missing explicit.
Every candidate still passes through the normal vocabulary, morphology,
translation, repetition, recent-echo, and Codex cohesion gates. A rejected
candidate is never inserted.
"""

from __future__ import annotations

import json
from typing import Any

from app.database import SessionLocal
from app.services.passage_generator import (
    PASSAGE_EXPERIMENT_VERSION,
    _due_maintenance_targets,
    _eligible_passage_words,
    _recent_passage_history,
    store_maintenance_passage,
)


CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "title_ar": "النَّبْرَةُ الثَّالِثَةُ",
        "title_en": "The Third Tone",
        "style_tag": "wry",
        "narrative_mode": "shared_action",
        "premise": (
            "A hidden pair joins a reciter, and the silent instrumentalist gets "
            "the last word."
        ),
        "target_plan": (
            "Rababa anchors the room, recite changes naturally across actors, "
            "and tone records the audible result."
        ),
        "ending_kind": "understated role reversal",
        "morphology_focus": True,
        "morphology_target_lemma_id": 3179,
        "selected_target_lemma_ids": [2917, 3179, 3220],
        "sentences": [
            {
                "arabic": (
                    "فِي غُرْفَةٍ صَغِيرَةٍ، جَلَسَ عَازِفٌ بِجَانِبِ رَبَابَةٍ "
                    "صَامِتَةٍ، وَرَتَّلَ قَصِيدَةً بِنَبْرَةٍ هَادِئَةٍ."
                ),
                "english": (
                    "In a small room, a player sat beside a silent rababa and recited "
                    "a poem in a calm tone."
                ),
            },
            {
                "arabic": (
                    "خَلْفَ بَابٍ مُغْلَقٍ، رَتَّلَتِ امْرَأَتَانِ الْبَيْتَ "
                    "الْأَخِيرَ بِصَوْتٍ آخَرَ، وَلَمْ يَرَهُمَا الْجُمْهُورُ."
                ),
                "english": (
                    "Behind a closed door, two women recited the final verse in "
                    "another voice, and the audience did not see them."
                ),
            },
            {
                "arabic": (
                    "عِنْدَمَا فَتَحَ الْعَازِفُ الْبَابَ، رَتَّلُوا الْبَيْتَ "
                    "نَفْسَهُ مَعًا، فَاخْتَلَفَتِ النَّبْرَاتُ."
                ),
                "english": (
                    "When the player opened the door, they recited the same verse "
                    "together, and the tones differed."
                ),
            },
            {
                "arabic": (
                    "عَزَفَ الرَّجُلُ عَلَى الرَّبَابَةِ، فَاتَّبَعُوا صَوْتَهَا "
                    "وَأَكْمَلُوا الْقَصِيدَةَ مَعًا."
                ),
                "english": (
                    "The man played the rababa, so they followed its sound and finished "
                    "the poem together."
                ),
            },
        ],
    },
    {
        "title_ar": "الْمِقْلَمَةُ السَّوْدَاءُ",
        "title_en": "The Black Pencil Case",
        "style_tag": "wry",
        "narrative_mode": "message_with_context",
        "premise": "An engineer uses the contents to distinguish her case from her painter father's.",
        "target_plan": (
            "The email starts the search, the eraser distinguishes the cases, and "
            "the father's reply identifies the owner."
        ),
        "ending_kind": "wry identification",
        "morphology_focus": False,
        "morphology_target_lemma_id": None,
        "selected_target_lemma_ids": [311, 2212, 2214],
        "sentences": [
            {
                "arabic": (
                    "قَرَأَتْ مُهَنْدِسَةٌ إِيمِيلًا مِنْ أَبِيهَا الرَّسَّامِ "
                    "يَسْأَلُ عَنْ "
                    "مِقْلَمَةٍ سَوْدَاءَ."
                ),
                "english": (
                    "An engineer read an email from her painter father asking about a "
                    "black pencil case."
                ),
            },
            {
                "arabic": (
                    "فَتَحَتْ حَقِيبَتَهَا، فَوَجَدَتْ مِقْلَمَتَيْنِ؛ "
                    "فِي إِحْدَاهُمَا مِمْحَاةٌ جَدِيدَةٌ، وَفِي الْأُخْرَى "
                    "مِفْتَاحٌ صَغِيرٌ."
                ),
                "english": (
                    "She opened her bag and found two pencil cases; one contained "
                    "a new eraser, and the other contained a small key."
                ),
            },
            {
                "arabic": (
                    "أَرْسَلَتْ إِيمِيلًا فِيهِ صُورَةٌ لَهُمَا، وَسَأَلَتْ: "
                    "«أَيَّتُهُمَا مِقْلَمَتُكَ؟»"
                ),
                "english": (
                    "She sent an email with a picture of them and asked, “Which one is "
                    "your pencil case?”"
                ),
            },
            {
                "arabic": (
                    "أَجَابَ الْأَبُ: «الَّتِي فِيهَا الْمِمْحَاةُ لِي؛ أَمَّا "
                    "الْمِفْتَاحُ فَهُوَ مِفْتَاحُ مَكْتَبِكِ»."
                ),
                "english": (
                    "Her father replied, “The one with the eraser is mine; the key is "
                    "the key to your office.”"
                ),
            },
        ],
    },
    {
        "title_ar": "الرِّيشُ عَلَى الرَّصِيفِ",
        "title_en": "Feathers on the Platform",
        "style_tag": "suspenseful",
        "narrative_mode": "near_miss",
        "premise": (
            "A passenger climbs down for fallen feathers, and a station worker stops "
            "an approaching train to pull him back."
        ),
        "target_plan": (
            "The falcon drops the feathers, the platform fixes the danger in space, "
            "and all three targets recur through the rescue."
        ),
        "ending_kind": "safe return",
        "morphology_focus": False,
        "morphology_target_lemma_id": None,
        "selected_target_lemma_ids": [1061, 1417, 2709],
        "sentences": [
            {
                "arabic": (
                    "وَقَفَ رَجُلٌ عَلَى رَصِيفِ الْمَحَطَّةِ، وَظَهَرَ قِطَارٌ "
                    "فِي الْبُعْدِ، وَحَطَّ صَقْرٌ عَلَى الرَّصِيفِ."
                ),
                "english": (
                    "A man stood on the station platform, a train appeared in the "
                    "distance, and a falcon landed on the platform."
                ),
            },
            {
                "arabic": (
                    "طَارَ الصَّقْرُ، وَسَقَطَ مِنْهُ رِيشٌ تَحْتَ الرَّصِيفِ، "
                    "فَنَزَلَ الرَّجُلُ مِنَ الرَّصِيفِ لِيَجْمَعَهُ."
                ),
                "english": (
                    "The falcon flew away, and feathers fell from it below the platform, "
                    "so the man climbed down from the platform to gather them."
                ),
            },
            {
                "arabic": (
                    "رَآهُ عَامِلُ الْمَحَطَّةِ، فَرَفَعَ عَلَمًا أَحْمَرَ، "
                    "فَوَقَفَ الْقِطَارُ قَبْلَ دُخُولِ الْمَحَطَّةِ."
                ),
                "english": (
                    "A station worker saw him and raised a red flag, and the train "
                    "stopped before entering the station."
                ),
            },
            {
                "arabic": (
                    "سَحَبَ الْعَامِلُ الرَّجُلَ إِلَى الرَّصِيفِ؛ وَعِنْدَمَا "
                    "دَخَلَ الْقِطَارُ الْمَحَطَّةَ، طَارَ الرِّيشُ."
                ),
                "english": (
                    "The worker pulled the man back onto the platform; when the train "
                    "entered the station, the feathers flew away."
                ),
            },
        ],
    },
)


def seed() -> dict[str, Any]:
    db = SessionLocal()
    created: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        eligible = _eligible_passage_words(db)
        due = _due_maintenance_targets(db, limit=10_000)
        due_by_id = {int(word["lemma_id"]): word for word in due}
        eligible_by_id = {int(word["lemma_id"]): word for word in eligible}

        for candidate in CANDIDATES:
            target_ids = [int(item) for item in candidate["selected_target_lemma_ids"]]
            missing_due = [lemma_id for lemma_id in target_ids if lemma_id not in due_by_id]
            if missing_due:
                failures.append({
                    "title_en": candidate["title_en"],
                    "error": f"targets are no longer due and stable: {missing_due}",
                })
                continue
            target_words = [
                {**eligible_by_id[lemma_id], **due_by_id[lemma_id]}
                for lemma_id in target_ids
                if lemma_id in eligible_by_id
            ]
            if len(target_words) != len(target_ids):
                failures.append({
                    "title_en": candidate["title_en"],
                    "error": "one or more targets are no longer eligible",
                })
                continue
            try:
                story = store_maintenance_passage(
                    db,
                    candidate,
                    target_words=target_words,
                    eligible_words=eligible,
                    quality_gate=True,
                    experiment_version=PASSAGE_EXPERIMENT_VERSION,
                    recent_passages=_recent_passage_history(db),
                )
            except Exception as exc:
                db.rollback()
                failures.append({
                    "title_en": candidate["title_en"],
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            created.append({"story_id": story.id, "title_en": story.title_en})
    finally:
        db.close()

    return {
        "requested": len(CANDIDATES),
        "created": created,
        "failures": failures,
        "complete": len(created) == len(CANDIDATES),
    }


if __name__ == "__main__":
    result = seed()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["complete"] else 1)
