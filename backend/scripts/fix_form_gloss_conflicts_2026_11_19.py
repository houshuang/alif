#!/usr/bin/env python3
"""One-off: repair lemmas whose Arabic headword and English gloss named different words.

Source list: research/model-check-2026-11-18/pos-audit-form-gloss-conflicts.txt
(83 rows from the 2026-11-19 part-of-speech audit) plus the rows around
#3986-#4014, where one story import assigned its glosses one or more rows off.

Each fix was chosen by hand from how the lemma is used in stored sentences:
when the gloss and the usage agree, the headword is revocalized on the same
bare form (وَصْل -> وَصَلَ for "to arrive"); when the headword and the usage
agree, the gloss and label are corrected (دَفَع "payment" -> "to pay").

Dry run by default; --write applies. A row is skipped when its headword no
longer matches what was reviewed, or when a new bare form already belongs to
another lemma. Headword changes clear forms_json so enrichment regenerates it.
"""

import argparse
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma
from app.services.sentence_validator import normalize_alef, strip_diacritics

# (lemma_id, reviewed headword, changes)
FIXES: list[tuple[int, str, dict]] = [
    (26, "عِنْدي", {"gloss_en": "I have"}),
    (150, "سُوءٌ", {"pos": "noun", "gloss_en": "evil, badness; bad"}),
    (162, "بِلا", {"pos": "prep", "gloss_en": "without"}),
    (163, "لَا", {"pos": "particle", "gloss_en": "no, not"}),
    (186, "كَيْك", {"pos": "noun", "gloss_en": "cake"}),
    (473, "فَاسٌ", {"lemma_ar": "فَأْس"}),
    (691, "أَلَا", {"pos": "particle", "gloss_en": "is it not?; that not (أَنْ + لَا)"}),
    (1593, "بِنا", {"pos": "prep", "gloss_en": "with us, by us"}),
    (2016, "وَشَّم", {"lemma_ar": "وَشْم"}),
    (2099, "لَكّ", {"lemma_ar": "لَكَ", "gloss_en": "to you, for you"}),
    (2123, "أَخْيَر", {"lemma_ar": "أَخِيرًا"}),
    (2143, "وَصْل", {"lemma_ar": "وَصَلَ"}),
    (2150, "حَصِر", {"lemma_ar": "حَصْر", "gloss_en": "limiting, restriction"}),
    (2155, "ضَخَّم", {"lemma_ar": "ضَخْم"}),
    (2168, "تَدْرِيجِيّ", {"lemma_ar": "تَدْرِيجِيًّا"}),
    (2204, "شُكْر", {"lemma_ar": "شَكَرَ"}),
    (2206, "نَصّ", {"pos": "noun", "gloss_en": "text"}),
    (2314, "لَوْح", {"lemma_ar": "لَوَّحَ"}),
    (2339, "كُسْكُس", {"pos": "noun", "gloss_en": "couscous"}),
    (2376, "دَفَع", {"lemma_ar": "دَفَعَ", "pos": "verb", "gloss_en": "to pay; to push"}),
    (2418, "جِلْد", {"lemma_ar": "جَلَدَ", "gloss_en": "to whip, to flog"}),
    (2421, "شَاطِر", {"lemma_ar": "شَاطَرَ"}),
    (2448, "غَسَلَ", {"pos": "verb", "gloss_en": "to wash"}),
    (2449, "دَهَنَ", {"pos": "verb", "gloss_en": "to paint; to grease"}),
    (2470, "وَعْد", {"lemma_ar": "وَعَدَ"}),
    (2486, "عَرَض", {"pos": "noun", "gloss_en": "symptom"}),
    (2498, "فَحَصَ", {"pos": "verb", "gloss_en": "to examine"}),
    (2514, "خَشِن", {"pos": "adj", "gloss_en": "rough, coarse"}),
    (2644, "حَثّ", {"lemma_ar": "حَثَّ"}),
    (2718, "حَجَزَ", {"pos": "verb", "gloss_en": "to book, to reserve"}),
    (2721, "شَكَّ", {"lemma_ar": "شَكّ"}),
    (2812, "كَارِيكَاتُورِيّ", {"pos": "adj", "gloss_en": "caricature-like, cartoonish"}),
    (2994, "أَهْلِيَّة", {"lemma_ar": "أَهْلِيّ", "gloss_en": "civil; domestic"}),
    (3036, "صَرْصَرَ", {"pos": "verb", "gloss_en": "to creak"}),
    (3040, "دَعَمَ", {"pos": "verb", "gloss_en": "to support"}),
    (3055, "مَنْفِيّ", {"pos": "adj", "gloss_en": "exiled; an exile", "lemma_ar_bare": "منفي"}),
    (3074, "رُومَانْسِيَّة", {"lemma_ar": "رُومَانْسِيّ"}),
    (3090, "غَلَّفَ", {"lemma_ar": "غِلَاف"}),
    (3114, "طَرِبَ", {"lemma_ar": "طَرَب"}),
    (3126, "طَوَّقَ", {"pos": "verb", "gloss_en": "to encircle, to surround"}),
    (3137, "قَشَرَ", {"pos": "verb", "gloss_en": "to peel"}),
    (3199, "وَجَمَ", {"pos": "verb", "gloss_en": "to fall silent (from grief or anger)"}),
    (3258, "خَدَّر", {"lemma_ar": "خَدَر"}),
    (3312, "ضَعُفَ", {"pos": "verb", "gloss_en": "to be weak, to weaken"}),
    (3429, "ضَمّ", {"lemma_ar": "ضَمَّ"}),
    (3459, "غَشَّ", {"lemma_ar": "غِشّ"}),
    (3512, "بَثّ", {"lemma_ar": "بَثَّ"}),
    (3572, "ضالّ", {"pos": "adj", "gloss_en": "astray, straying"}),
    (3574, "مُهْتَدِي", {"pos": "adj", "gloss_en": "guided, rightly guided"}),
    (3582, "فاسِق", {"pos": "adj", "gloss_en": "disobedient, transgressor"}),
    (3673, "قَصِف", {"lemma_ar": "قَصْف", "pos": "noun", "gloss_en": "bombardment, shelling"}),
    (3696, "مَحَوَر", {"lemma_ar": "مِحْوَر", "pos": "noun", "gloss_en": "axis, pivot; focus"}),
    (3844, "فَكّ", {"lemma_ar": "فَكَّ"}),
    (3934, "رَفَعَ", {"pos": "verb", "gloss_en": "to raise, to lift"}),
    # Story import with glosses assigned to the wrong rows.
    (3986, "ٱِنْتَفَض", {"pos": "verb", "gloss_en": "to shudder, to shake; to rise up"}),
    (3987, "ٱِنْتِصاب", {"pos": "noun", "gloss_en": "erection, standing upright"}),
    (3988, "عاوَد", {"pos": "verb", "gloss_en": "to return to, to resume; to recur"}),
    (3989, "الْكَمَادَات", {"pos": "noun", "gloss_en": "compresses, poultices"}),
    (3990, "ٱِرْتِخاء", {"pos": "noun", "gloss_en": "relaxation, limpness"}),
    (3992, "راوَنْد", {"pos": "noun", "gloss_en": "rhubarb"}),
    (3993, "مُفَلْطَح", {"pos": "adj", "gloss_en": "flattened, flat"}),
    (3994, "القَصِيب", {"pos": "noun", "gloss_en": "rod, stalk; penis"}),
    (3995, "ٱِنْتَفَخ", {"pos": "verb", "gloss_en": "to swell, to puff up"}),
    (3996, "تَعارِيج", {"pos": "noun", "gloss_en": "curves, windings"}),
    (3997, "يَنْتَصِب", {"pos": "verb", "gloss_en": "to stand upright, to become erect"}),
    (3998, "ٱِنْتَعَش", {"pos": "verb", "gloss_en": "to revive, to recover"}),
    (3999, "ساوَر", {"pos": "verb", "gloss_en": "to assail, to occur to (a doubt, a thought)"}),
    (4003, "أَرِسْتُقراطِيّ", {"pos": "adj", "gloss_en": "aristocratic"}),
    (4004, "تَماسَك", {"pos": "verb", "gloss_en": "to hold together; to pull oneself together"}),
    (4005, "خَصْر", {"pos": "noun", "gloss_en": "waist"}),
    (4006, "مُنْتَصِب", {"pos": "adj", "gloss_en": "upright, erect"}),
    (4007, "حِضْن", {"pos": "noun", "gloss_en": "lap, bosom, embrace"}),
    (4008, "شَرَس", {"lemma_ar": "شَرِس", "pos": "adj", "gloss_en": "fierce, vicious"}),
    (4010, "لَحِس", {"lemma_ar": "لَحِسَ", "pos": "verb", "gloss_en": "to lick"}),
    (4011, "الكَلُّوت", {"pos": "noun", "gloss_en": "underpants (colloquial)"}),
    (4013, "لَايَخْرُج", {"pos": "expr", "gloss_en": "does not come out"}),
    (4014, "عَضّ", {"lemma_ar": "عَضَّ", "pos": "verb", "gloss_en": "to bite"}),
    (4034, "جَزَمَ", {"lemma_ar": "جَزْم", "gloss_en": "decisiveness, resolve"}),
    (4040, "صُحبَة", {"lemma_ar": "صَحِبَ"}),
    (4045, "عَزَّ", {"pos": "verb", "gloss_en": "to be mighty; to be dear"}),
    (4142, "تَنَقَّلَ", {"pos": "verb", "gloss_en": "to move about, to travel around"}),
    (4162, "رَعي", {"lemma_ar": "رَعَى"}),
    (4181, "لَمْح", {"lemma_ar": "لَمَحَ"}),
    (4426, "حَامَ", {"lemma_ar": "حَامٍ", "lemma_ar_bare": "حامي"}),
    (4472, "تَأَفُّف", {"pos": "noun", "gloss_en": "grumbling, show of annoyance"}),
    (4475, "حَتْم", {"lemma_ar": "حَتَّمَ"}),
    (4773, "تَفَاهُم", {"pos": "noun", "gloss_en": "mutual understanding"}),
]

# Left alone: #2024 صَب "youth" (usage mixes صِبَا and الصَّبّ), #2118 كَيْفِيّ
# "method" (needs the separate headword كَيْفِيَّة), #2177 (fixed 2026-11-19),
# #2500 and #3270 (variants), #3413 مَضّ "to pass" (مَضَى exists as #4575),
# #4009 أَعاض (source word unclear).


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    from app.services.transliteration import transliterate_arabic

    db = SessionLocal()
    try:
        bare_owner = {l.lemma_ar_bare: l.lemma_id for l in db.query(Lemma).all()}
        applied: list[int] = []
        reenrich: list[int] = []
        for lemma_id, reviewed, changes in FIXES:
            lemma = db.get(Lemma, lemma_id)
            if lemma is None or (
                unicodedata.normalize("NFC", lemma.lemma_ar)
                != unicodedata.normalize("NFC", reviewed)
            ):
                print(f"  [skip] #{lemma_id}: headword is {lemma.lemma_ar if lemma else None!r}, reviewed {reviewed!r}")
                continue
            new_bare = changes.get("lemma_ar_bare")
            if new_bare and bare_owner.get(new_bare, lemma_id) != lemma_id:
                print(f"  [skip] #{lemma_id} {reviewed}: bare {new_bare} belongs to #{bare_owner[new_bare]}")
                continue
            new_ar = changes.get("lemma_ar")
            if new_ar and not new_bare:
                expected = normalize_alef(strip_diacritics(new_ar))
                current = normalize_alef(lemma.lemma_ar_bare or "")
                if expected.rstrip("ا") != current.rstrip("ا") and expected != current:
                    print(f"  [skip] #{lemma_id} {reviewed}: {new_ar} does not fit bare {lemma.lemma_ar_bare}")
                    continue
            before = (lemma.lemma_ar, lemma.lemma_ar_bare, lemma.pos, lemma.gloss_en)
            for field, value in changes.items():
                setattr(lemma, field, value)
            if new_ar:
                lemma.transliteration_ala_lc = transliterate_arabic(new_ar)
            if new_ar or changes.get("pos") in ("verb", "noun", "adj"):
                if lemma.forms_json:
                    lemma.forms_json = None
                    reenrich.append(lemma_id)
            after = (lemma.lemma_ar, lemma.lemma_ar_bare, lemma.pos, lemma.gloss_en)
            print(f"  #{lemma_id}: {before} -> {after}")
            applied.append(lemma_id)
        print(f"{len(applied)} of {len(FIXES)} fixes, {len(reenrich)} need forms regenerated")
        if not args.write:
            db.rollback()
            print("Dry run. Pass --write to apply.")
            return
        db.commit()
        from app.services.activity_log import log_activity

        log_activity(
            db,
            "manual_fix",
            f"Repaired {len(applied)} lemmas whose headword and gloss named different words",
            {"lemma_ids": applied},
        )
    finally:
        db.close()

    if reenrich:
        from app.services.lemma_enrichment import enrich_lemmas_batch

        print("Regenerating forms:", enrich_lemmas_batch(reenrich))


if __name__ == "__main__":
    main()
