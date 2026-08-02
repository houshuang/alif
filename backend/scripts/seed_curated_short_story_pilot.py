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
                    "فِي غُرْفَةٍ صَغِيرَةٍ، جَلَسَ رَجُلٌ بِجَانِبِ رَبَابَةٍ "
                    "صَامِتَةٍ، وَرَتَّلَ قَصِيدَةً بِنَبْرَةٍ هَادِئَةٍ."
                ),
                "english": (
                    "In a small room, a man sat beside a silent rababa and recited "
                    "a poem in a calm tone."
                ),
            },
            {
                "arabic": (
                    "خَلْفَ السِّتَارِ، رَتَّلَتِ امْرَأَتَانِ الْبَيْتَ "
                    "الْأَخِيرَ بِنَبْرَةٍ أُخْرَى، وَلَمْ يَرَهُمَا الْجُمْهُورُ."
                ),
                "english": (
                    "Behind the curtain, two women recited the final verse in "
                    "another tone, and the audience did not see them."
                ),
            },
            {
                "arabic": (
                    "عِنْدَمَا رَفَعَ الرَّجُلُ السِّتَارَ، رَتَّلُوا الْبَيْتَ "
                    "نَفْسَهُ مَعًا، فَتَغَيَّرَتِ النَّبْرَةُ."
                ),
                "english": (
                    "When the man lifted the curtain, they recited the same verse "
                    "together, and the tone changed."
                ),
            },
            {
                "arabic": (
                    "ضَحِكَ عَازِفُ الرَّبَابَةِ وَقَالَ: «الْآنَ فَقَطْ "
                    "تَحْتَاجُونَ إِلَيَّ»."
                ),
                "english": (
                    "The rababa player laughed and said, “Only now do you need me.”"
                ),
            },
        ],
    },
    {
        "title_ar": "الْفِلْمُ الْأَسْوَدُ",
        "title_en": "The Black Film",
        "style_tag": "wry",
        "narrative_mode": "message_with_context",
        "premise": "A producer mistakes a deliberately black film for a broken file.",
        "target_plan": (
            "The email delivers the film and slogan, and the reply supplies the "
            "missing context for the black screen."
        ),
        "ending_kind": "literal explanation",
        "morphology_focus": False,
        "morphology_target_lemma_id": None,
        "selected_target_lemma_ids": [311, 1079, 1220],
        "sentences": [
            {
                "arabic": (
                    "تَلَقَّى مُنْتِجٌ إِيمِيلًا فِيهِ فِلْمٌ قَصِيرٌ وَشِعَارٌ: "
                    "«لَا تَنْتَظِرِ الضَّوْءَ»."
                ),
                "english": (
                    "A producer received an email containing a short film and a "
                    "slogan: “Do not wait for the light.”"
                ),
            },
            {
                "arabic": (
                    "فَتَحَ الْفِلْمَ، فَبَقِيَتِ الشَّاشَةُ سَوْدَاءَ مِنَ "
                    "الْبِدَايَةِ إِلَى النِّهَايَةِ."
                ),
                "english": (
                    "He opened the film, and the screen remained black from beginning "
                    "to end."
                ),
            },
            {
                "arabic": (
                    "كَتَبَ فِي إِيمِيلٍ جَدِيدٍ: «الشِّعَارُ جَيِّدٌ، وَلَكِنَّ "
                    "الْفِلْمَ بِلَا صُورَةٍ»."
                ),
                "english": (
                    "He wrote in a new email, “The slogan is good, but the film has "
                    "no picture.”"
                ),
            },
            {
                "arabic": (
                    "أَجَابَ الْمُخْرِجُ: «هَذَا هُوَ الْفِلْمُ؛ إِنَّهُ فِلْمٌ "
                    "عَنِ الظَّلَامِ»."
                ),
                "english": (
                    "The director replied, “This is the film; it is a film about "
                    "darkness.”"
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
            "A child follows fallen feathers toward the track, and a station worker "
            "stops the train before returning them safely."
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
                    "وَقَفَ الْمُسَافِرُونَ عَلَى رَصِيفِ الْمَحَطَّةِ، وَحَطَّ "
                    "صَقْرٌ قُرْبَ السِّكَّةِ."
                ),
                "english": (
                    "The travelers stood on the station platform, and a falcon "
                    "landed near the track."
                ),
            },
            {
                "arabic": (
                    "رَأَى طِفْلٌ رِيشًا سَقَطَ مِنَ الصَّقْرِ عَلَى الرَّصِيفِ، "
                    "فَخَطَا نَحْوَهُ."
                ),
                "english": (
                    "A child saw feathers that had fallen from the falcon onto the "
                    "platform, so he stepped toward them."
                ),
            },
            {
                "arabic": (
                    "رَفَعَ عَامِلُ الْمَحَطَّةِ عَلَمًا أَحْمَرَ، فَوَقَفَ "
                    "الْقِطَارُ قَبْلَ الرَّصِيفِ."
                ),
                "english": (
                    "A station worker raised a red flag, and the train stopped before "
                    "the platform."
                ),
            },
            {
                "arabic": (
                    "طَارَ الصَّقْرُ، وَجَمَعَ الْعَامِلُ الرِّيشَ، ثُمَّ وَضَعَهُ "
                    "فِي يَدِ الطِّفْلِ بَعْدَ أَنْ ابْتَعَدَ عَنِ السِّكَّةِ."
                ),
                "english": (
                    "The falcon flew away, and the worker gathered the feathers, then "
                    "put them in the child’s hand after he moved away from the track."
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
