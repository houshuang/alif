#!/usr/bin/env python3
"""Stage everyday concrete vocabulary as intake targets.

Vocabulary reached the learner through frequency lists and books, which
under-sample plain household words: on 2026-11-18 there was no lemma for
yellow, plumber, farmer, narrow, hammer or cow. This script holds a curated
checklist (colours, trades, kinds of people, physical adjectives, household
objects, animals) and stages whatever the learner has not learned yet as
`reading_targets` rows under the program `everyday_core`.

It never creates cards and never bypasses `start_acquisition()`: ordinary
intake introduces the targets within the daily budget, after learner picks.
Missing words are created with `source="everyday_core"` and go through
`run_quality_gates()` before they get a target. A word whose spelling matches
an existing lemma with a different meaning is listed and left alone.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/stage_everyday_words.py            # dry run
  PYTHONPATH=. python3 scripts/stage_everyday_words.py --apply
  PYTHONPATH=. python3 scripts/stage_everyday_words.py --status
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, ReadingTarget, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_lemma_id  # noqa: E402
from app.services.sentence_validator import strip_diacritics  # noqa: E402

PROGRAM = "everyday_core"
SOURCE = "everyday_core"
LEARNED = {"acquiring", "learning", "known", "lapsed"}

# (citation form, part of speech, gloss). Alternatives in the gloss are
# separated by "/"; an existing lemma counts as the same word only when its
# gloss shares one of them.
WORDS: dict[str, list[tuple[str, str, str]]] = {
    "colours": [
        ("أَحْمَر", "adj", "red"), ("أَزْرَق", "adj", "blue"), ("أَخْضَر", "adj", "green"),
        ("أَصْفَر", "adj", "yellow"), ("أَسْوَد", "adj", "black"), ("أَبْيَض", "adj", "white"),
        ("بُنِّيّ", "adj", "brown"), ("رَمَادِيّ", "adj", "grey/gray"),
        ("بُرْتُقَالِيّ", "adj", "orange"), ("وَرْدِيّ", "adj", "pink/rosy"),
        ("بَنَفْسَجِيّ", "adj", "purple/violet"), ("ذَهَبِيّ", "adj", "golden/gold"),
        ("فِضِّيّ", "adj", "silver/silvery"),
    ],
    "trades": [
        ("نَجَّار", "noun", "carpenter"), ("سَبَّاك", "noun", "plumber"),
        ("حَدَّاد", "noun", "blacksmith/smith"), ("خَبَّاز", "noun", "baker"),
        ("جَزَّار", "noun", "butcher"), ("فَلَّاح", "noun", "farmer/peasant"),
        ("مُزَارِع", "noun", "farmer"), ("صَيَّاد", "noun", "hunter/fisherman"),
        ("خَيَّاط", "noun", "tailor"), ("حَلَّاق", "noun", "barber"), ("طَبَّاخ", "noun", "cook"),
        ("سَائِق", "noun", "driver"), ("بَنَّاء", "noun", "builder/mason"),
        ("كَنَّاس", "noun", "street sweeper/sweeper"), ("مُمَرِّض", "noun", "nurse"),
        ("مُهَنْدِس", "noun", "engineer"), ("مُحَامٍ", "noun", "lawyer"), ("قَاضٍ", "noun", "judge"),
        ("شُرْطِيّ", "noun", "policeman/police"), ("جُنْدِيّ", "noun", "soldier"),
        ("تَاجِر", "noun", "merchant/trader"), ("بَائِع", "noun", "seller/vendor/salesman"),
        ("رَاعٍ", "noun", "shepherd/herder/herdsman"), ("بَحَّار", "noun", "sailor"), ("طَيَّار", "noun", "pilot"),
        ("رَسَّام", "noun", "painter/artist"), ("مُغَنٍّ", "noun", "singer"),
        ("مُمَثِّل", "noun", "actor"), ("صَحَفِيّ", "noun", "journalist"),
        ("مُتَرْجِم", "noun", "translator"), ("عَامِل", "noun", "worker"),
        ("خَادِم", "noun", "servant"), ("حَارِس", "noun", "guard"),
        ("كَهْرَبَائِيّ", "noun", "electrician/electric"), ("مِيكَانِيكِيّ", "noun", "mechanic"),
        ("صَيْدَلِيّ", "noun", "pharmacist"), ("مُحَاسِب", "noun", "accountant"),
        ("نَادِل", "noun", "waiter"), ("بَوَّاب", "noun", "doorman/gatekeeper"),
        ("حَمَّال", "noun", "porter"), ("إِسْكَافِيّ", "noun", "cobbler/shoemaker"),
        ("نَسَّاج", "noun", "weaver"), ("بَقَّال", "noun", "grocer"),
        ("نَحَّات", "noun", "sculptor"), ("صَائِغ", "noun", "goldsmith/jeweller/jeweler"),
    ],
    "people": [
        ("عَجُوز", "noun", "old woman/old"), ("أَرْمَلَة", "noun", "widow"),
        ("أَرْمَل", "noun", "widower"), ("يَتِيم", "noun", "orphan"),
        ("شَيْخ", "noun", "old man/sheikh/elder"), ("شَابّ", "noun", "young man/youth"),
        ("عَرُوس", "noun", "bride"), ("عَرِيس", "noun", "groom/bridegroom"),
        ("غَرِيب", "noun", "stranger/strange/weird"), ("أَعْمَى", "adj", "blind"),
        ("أَصَمّ", "adj", "deaf"), ("أَعْرَج", "adj", "lame"), ("أَخْرَس", "adj", "mute/dumb"),
        ("فَقِير", "adj", "poor"), ("غَنِيّ", "adj", "rich/wealthy"),
        ("بَخِيل", "adj", "stingy/miserly/miser"), ("كَرِيم", "adj", "generous/noble"),
        ("تَوْأَم", "noun", "twin"), ("حَفِيد", "noun", "grandson/grandchild"),
        ("صِهْر", "noun", "son-in-law/brother-in-law/in-law"),
        ("حَمَاة", "noun", "mother-in-law"), ("مُرَاهِق", "noun", "teenager/adolescent"),
        ("رَضِيع", "noun", "infant/baby"), ("جَبَان", "adj", "cowardly/coward"),
        ("شُجَاع", "adj", "brave/courageous"), ("كَسُول", "adj", "lazy"),
    ],
    "physical adjectives": [
        ("طَوِيل", "adj", "long/tall"), ("قَصِير", "adj", "short"), ("عَالٍ", "adj", "high/loud"),
        ("مُنْخَفِض", "adj", "low"), ("وَاسِع", "adj", "wide/spacious"),
        ("ضَيِّق", "adj", "narrow/tight"), ("عَرِيض", "adj", "broad/wide"),
        ("رَفِيع", "adj", "thin/fine/slender"), ("سَمِيك", "adj", "thick"),
        ("عَمِيق", "adj", "deep"), ("ضَحْل", "adj", "shallow"), ("ثَقِيل", "adj", "heavy"),
        ("خَفِيف", "adj", "light"), ("سَمِين", "adj", "fat"), ("نَحِيف", "adj", "thin/slim/skinny"),
        ("نَاعِم", "adj", "soft/smooth"), ("خَشِن", "adj", "rough/coarse"),
        ("حَادّ", "adj", "sharp"), ("لَيِّن", "adj", "soft/pliable/flexible"),
        ("صُلْب", "adj", "hard/solid"), ("رَطْب", "adj", "moist/damp/humid"),
        ("جَافّ", "adj", "dry"), ("مُبَلَّل", "adj", "wet"), ("نَظِيف", "adj", "clean"),
        ("وَسِخ", "adj", "dirty"), ("قَذِر", "adj", "filthy/dirty"), ("فَارِغ", "adj", "empty"),
        ("مُمْتَلِئ", "adj", "full"), ("مُسْتَقِيم", "adj", "straight"),
        ("مَائِل", "adj", "slanted/leaning/sloping"), ("مُسْتَدِير", "adj", "round"),
        ("مُرَبَّع", "adj", "square"), ("حُلْو", "adj", "sweet"), ("مُرّ", "adj", "bitter"),
        ("حَامِض", "adj", "sour"), ("مَالِح", "adj", "salty"), ("دَافِئ", "adj", "warm"),
        ("سَاخِن", "adj", "hot"), ("مُظْلِم", "adj", "dark"), ("لَامِع", "adj", "shiny/bright"),
        ("هَشّ", "adj", "fragile/brittle"), ("مَتِين", "adj", "sturdy/solid/strong"),
        ("أَمْلَس", "adj", "smooth"), ("مُسَطَّح", "adj", "flat"),
    ],
    "household": [
        ("وِسَادَة", "noun", "pillow/cushion"), ("بَطَّانِيَّة", "noun", "blanket"),
        ("سَجَّادَة", "noun", "carpet/rug"), ("قُفْل", "noun", "lock"),
        ("سُلَّم", "noun", "ladder/stairs"), ("مِطْرَقَة", "noun", "hammer"),
        ("مِسْمَار", "noun", "nail"), ("مِنْشَار", "noun", "saw"), ("إِبْرَة", "noun", "needle"),
        ("خَيْط", "noun", "thread/string"), ("مِقَصّ", "noun", "scissors"),
        ("إِبْرِيق", "noun", "jug/pitcher/teapot"), ("قِدْر", "noun", "pot/cooking pot"),
        ("مِكْنَسَة", "noun", "broom"), ("سَطْل", "noun", "bucket/pail"),
        ("سَلَّة", "noun", "basket"), ("مِقْلَاة", "noun", "frying pan/pan"),
        ("فُرْن", "noun", "oven"), ("ثَلَّاجَة", "noun", "refrigerator/fridge"),
        ("صَابُون", "noun", "soap"), ("مِنْشَفَة", "noun", "towel"), ("مِشْط", "noun", "comb"),
        ("فُرْشَاة", "noun", "brush"), ("مِظَلَّة", "noun", "umbrella"),
        ("مِجْرَفَة", "noun", "shovel"), ("فَأْس", "noun", "axe/ax"),
        ("زُجَاجَة", "noun", "bottle"), ("غِطَاء", "noun", "lid/cover"),
        ("دُرْج", "noun", "drawer"), ("حَنَفِيَّة", "noun", "tap/faucet"),
    ],
    "animals": [
        ("قِطّ", "noun", "cat"), ("حِمَار", "noun", "donkey"), ("بَقَرَة", "noun", "cow"),
        ("خَرُوف", "noun", "sheep/lamb"), ("مَاعِز", "noun", "goat"),
        ("دَجَاجَة", "noun", "chicken/hen"), ("دِيك", "noun", "rooster/cock"),
        ("نَسْر", "noun", "eagle/vulture"), ("غُرَاب", "noun", "crow/raven"),
        ("حَمَامَة", "noun", "pigeon/dove"), ("عُصْفُور", "noun", "sparrow/small bird/bird"),
        ("نَحْلَة", "noun", "bee"), ("نَمْلَة", "noun", "ant"), ("عَنْكَبُوت", "noun", "spider"),
        ("سَمَكَة", "noun", "fish"), ("سُلَحْفَاة", "noun", "turtle/tortoise"),
        ("ثَوْر", "noun", "bull/ox"), ("خِنْزِير", "noun", "pig"), ("نَمِر", "noun", "tiger/leopard"),
        ("فَرَاشَة", "noun", "butterfly"), ("دُودَة", "noun", "worm"), ("بُومَة", "noun", "owl"),
        ("حُوت", "noun", "whale"), ("عَقْرَب", "noun", "scorpion"),
        ("قُنْفُذ", "noun", "hedgehog"), ("سِنْجَاب", "noun", "squirrel"),
    ],
}

_HAMZA = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"})


def _key(text: str) -> str:
    return strip_diacritics(text).translate(_HAMZA)


def _same_sense(gloss: str, existing_gloss: str | None) -> bool:
    existing = (existing_gloss or "").lower()
    return any(re.search(rf"\b{re.escape(alt.strip().lower())}", existing) for alt in gloss.split("/"))


def _add_target(db, lemma_id: int) -> bool:
    row = db.query(ReadingTarget).filter_by(lemma_id=lemma_id, program=PROGRAM).first()
    if row is not None:
        return False
    db.add(ReadingTarget(lemma_id=lemma_id, program=PROGRAM, chapter=1, text_count=1))
    return True


def _ensure_knowledge(db, lemma: Lemma) -> None:
    if db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first() is None:
        db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="encountered",
                                  source=lemma.source or SOURCE))
        db.flush()


def stage(apply: bool) -> dict:
    db = SessionLocal()
    by_key: dict[str, list[Lemma]] = {}
    # Variant rows are included so a word already known under its canonical
    # (سَمَكَة under سَمَك) is recognised instead of being created again.
    for lemma in db.query(Lemma).all():
        if lemma.lemma_ar_bare and lemma.word_category not in ("proper_name", "onomatopoeia"):
            by_key.setdefault(_key(lemma.lemma_ar_bare), []).append(lemma)
    states = dict(db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state).all())

    report: dict[str, list] = {"learned": [], "targeted_existing": [], "created": [],
                               "sense_conflict": [], "suspended": []}
    new_ids: list[int] = []
    for category, words in WORDS.items():
        for lemma_ar, pos, gloss in words:
            found = list(by_key.get(_key(lemma_ar), []))
            if lemma_ar.endswith("ٍ"):
                # Defective nouns (قَاضٍ) are stored with their final ya.
                found += by_key.get(_key(lemma_ar) + "ي", [])
            matches = [l for l in found if _same_sense(gloss, l.gloss_en)]
            others = [l for l in found if l not in matches]
            if matches:
                canonicals = {resolve_canonical_lemma_id(db, l.lemma_id) for l in matches}
                matches = [l for l in (db.get(Lemma, lid) for lid in canonicals) if l is not None]
                best = max(matches, key=lambda l: states.get(l.lemma_id) in LEARNED)
                state = states.get(best.lemma_id)
                entry = {"word": lemma_ar, "gloss": gloss, "lemma_id": best.lemma_id,
                         "existing": best.lemma_ar, "existing_gloss": best.gloss_en, "state": state}
                if state in LEARNED:
                    report["learned"].append(entry)
                elif state == "suspended" or best.gates_completed_at is None:
                    report["suspended"].append(entry)
                else:
                    if apply:
                        _ensure_knowledge(db, best)
                        _add_target(db, best.lemma_id)
                    report["targeted_existing"].append(entry)
                continue
            if others:
                # Same spelling, different meaning: creating a second lemma on a
                # shared bare form needs a human look at the mapping first.
                report["sense_conflict"].append({
                    "word": lemma_ar, "gloss": gloss,
                    "existing": [(l.lemma_id, l.lemma_ar, l.gloss_en) for l in others],
                })
                continue
            entry = {"word": lemma_ar, "gloss": gloss.replace("/", ", "), "pos": pos, "category": category}
            if apply:
                lemma = Lemma(lemma_ar=lemma_ar, lemma_ar_bare=strip_diacritics(lemma_ar),
                              gloss_en=entry["gloss"], pos=pos, source=SOURCE)
                db.add(lemma)
                db.flush()
                entry["lemma_id"] = lemma.lemma_id
                new_ids.append(lemma.lemma_id)
            report["created"].append(entry)
    if apply:
        db.commit()

    if apply and new_ids:
        from app.services.lemma_quality import run_quality_gates

        run_quality_gates(db, new_ids, background_enrich=False)
        db.commit()
        for entry in report["created"]:
            canonical = resolve_canonical_lemma_id(db, entry["lemma_id"])
            lemma = db.get(Lemma, canonical)
            state = db.query(UserLemmaKnowledge.knowledge_state).filter_by(lemma_id=canonical).scalar()
            entry["canonical_lemma_id"] = canonical
            entry["gated"] = lemma.gates_completed_at is not None
            if state in LEARNED:
                continue
            _ensure_knowledge(db, lemma)
            _add_target(db, canonical)
        db.commit()
    if apply:
        log_activity(db, "everyday_core_stage",
                     f"Staged everyday vocabulary: {len(report['targeted_existing'])} existing, "
                     f"{len(report['created'])} created",
                     {k: v for k, v in report.items() if k != "learned"})
        db.commit()
    db.close()
    return report


def status() -> dict:
    db = SessionLocal()
    rows = (
        db.query(ReadingTarget, Lemma, UserLemmaKnowledge.knowledge_state)
        .join(Lemma, Lemma.lemma_id == ReadingTarget.lemma_id)
        .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(ReadingTarget.program == PROGRAM)
        .all()
    )
    out: dict[str, list] = {}
    for target, lemma, state in rows:
        key = "retired" if target.retired_at else (state or "none")
        out.setdefault(key, []).append(f"{lemma.lemma_ar} ({lemma.gloss_en})")
    db.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Write lemmas and targets (default: dry run)")
    ap.add_argument("--status", action="store_true", help="Show the staged words by learning state")
    args = ap.parse_args()
    if args.status:
        for key, words in status().items():
            print(f"{key} [{len(words)}]: {'، '.join(words)}")
        return
    report = stage(args.apply)
    print("APPLIED" if args.apply else "DRY RUN")
    for key in ("created", "targeted_existing", "sense_conflict", "suspended"):
        print(f"\n== {key} [{len(report[key])}]")
        for entry in report[key]:
            print("  ", json.dumps(entry, ensure_ascii=False))
    print(f"\n== learned [{len(report['learned'])}]")
    print("  ", "، ".join(f"{e['word']}={e['existing_gloss']}" for e in report["learned"]))


if __name__ == "__main__":
    main()
