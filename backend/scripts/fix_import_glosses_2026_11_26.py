"""Repair imported words whose stored gloss or headword came from one sentence.

The discover gloss step stored the meaning of the snapped sentence as the word's
gloss until 2026-11-26 (أَبْصَرَ "was born"). An audit of the 280 non-name
lemmas created by snap/preread/bookifier/dragoman (research/gloss-audit-2026-11-26/)
flagged 25; 24 are repaired here (أُصُول is left as it is).

- GLOSSES: new general gloss (and part of speech where wrong).
- RENAMES: wrong headword form, no entry with the right one: re-headword in place.
- VARIANTS: the right headword already exists: the damaged lemma becomes a variant
  of it (canonical redirect, as everywhere else), and its learning row is folded
  into the canonical's.
Each touched lemma's word card is cleared and rebuilt; the idiom that caused the
error is added to the card as a set phrase.

    PYTHONPATH=/opt/limbic python3 scripts/fix_import_glosses_2026_11_26.py          # dry run
    PYTHONPATH=/opt/limbic python3 scripts/fix_import_glosses_2026_11_26.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.sentence_validator import normalize_arabic

# lemma_id -> (gloss_en, pos or None, set phrase (ar, en) or None)
GLOSSES = {
    4990: ("to see, perceive, catch sight of", None, ("أَبْصَرَ النُّورَ", "was born; came out")),
    5009: ("to keep pace with, keep up with; to rival", None, ("لَا يُجَارَى", "unrivalled, matchless")),
    4996: ("circuit, round; course (of a journey)", None, ("فِي نِهَايَةِ الْمَطَافِ", "in the end, ultimately")),
    4992: ("contracting; contract", None, None),
    4839: ("more fitting, more appropriate; more likely", None, ("بِالْأَحْرَى", "rather, more precisely")),
    4193: ("to rub, scrub; to knead, massage", None, None),
    4196: ("kissing", None, None),
    4187: ("to throw, hurl; to accuse (of slander); to ejaculate", None, None),
    4186: ("to suck", None, None),
    4201: ("passing, conveying; passing (a ball, a law); scrolling", None, None),
    4244: ("to turn (something); to draw (attention)", None, ("لَفَتَ الِانْتِبَاهَ", "to attract attention")),
    4220: ("to digress; to go on (speaking)", None, None),
    4330: ("hard, solid, firm; steel", "adj", None),
    4847: ("process, operation; surgical operation; military operation", None, None),
    4845: ("public; general", None, None),
    4793: ("national; nationalist", None, None),
    5007: ("resident; residing, staying; permanent", None, None),
    4192: ("stuck, adhering; attached, adjacent", None, None),
}
# lemma_id -> (lemma_ar, pos, gloss_en)
RENAMES = {
    4998: ("مُعَايَنَة", "noun", "inspection, examination; viewing"),
    4849: ("مُشَوَّه", "adj", "distorted, deformed, disfigured"),
    4993: ("اِسْتِشَارِيّ", "adj", "advisory, consultative; (as noun) consultant"),
}
# damaged lemma_id -> canonical lemma_id
VARIANTS = {4999: 5057, 4785: 1771, 4806: 408}
VARIANT_PHRASES = {408: ("قِيلَ", "it was said")}

_STATE_RANK = {None: 0, "new": 0, "encountered": 1, "acquiring": 2, "learning": 3,
               "lapsed": 3, "known": 4}


def _fold_ulk(db, damaged: int, target: int) -> str:
    dk = db.query(UserLemmaKnowledge).filter_by(lemma_id=damaged).first()
    if dk is None:
        return "no learning row"
    tk = db.query(UserLemmaKnowledge).filter_by(lemma_id=target).first()
    if tk is None or _STATE_RANK.get(dk.knowledge_state, 0) > _STATE_RANK.get(tk.knowledge_state, 0):
        # The damaged row is further along: it becomes the canonical's row.
        if tk is not None:
            dk.times_seen = (dk.times_seen or 0) + (tk.times_seen or 0)
            dk.times_correct = (dk.times_correct or 0) + (tk.times_correct or 0)
            dk.total_encounters = (dk.total_encounters or 0) + (tk.total_encounters or 0)
            db.delete(tk)
            db.flush()
        dk.lemma_id = target
        return f"moved learning row ({dk.knowledge_state}) to {target}"
    tk.times_seen = (tk.times_seen or 0) + (dk.times_seen or 0)
    tk.times_correct = (tk.times_correct or 0) + (dk.times_correct or 0)
    tk.total_encounters = (tk.total_encounters or 0) + (dk.total_encounters or 0)
    db.delete(dk)
    return f"folded counts into {target} ({tk.knowledge_state})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    rebuild: list[int] = []
    phrases: dict[int, tuple[str, str]] = {}
    for lid, (gloss, pos, phrase) in GLOSSES.items():
        lemma = db.get(Lemma, lid)
        print(f"gloss  {lid} {lemma.lemma_ar}: {lemma.gloss_en!r} -> {gloss!r}" + (f", pos {lemma.pos}->{pos}" if pos else ""))
        lemma.gloss_en = gloss
        if pos:
            lemma.pos = pos
        lemma.word_card_json = None
        rebuild.append(lid)
        if phrase:
            phrases[lid] = phrase
    for lid, (ar, pos, gloss) in RENAMES.items():
        lemma = db.get(Lemma, lid)
        print(f"rename {lid} {lemma.lemma_ar} -> {ar} ({pos}, {gloss!r})")
        lemma.lemma_ar = ar
        lemma.lemma_ar_bare = normalize_arabic(ar)
        lemma.pos, lemma.gloss_en, lemma.word_card_json = pos, gloss, None
        rebuild.append(lid)
    for damaged, target in VARIANTS.items():
        lemma, canon = db.get(Lemma, damaged), db.get(Lemma, target)
        assert canon.canonical_lemma_id is None, f"{target} is itself a variant"
        lemma.canonical_lemma_id = target
        print(f"variant {damaged} {lemma.lemma_ar} -> {target} {canon.lemma_ar}: {_fold_ulk(db, damaged, target)}")
        if target in VARIANT_PHRASES:
            phrases[target] = VARIANT_PHRASES[target]
    if not args.apply:
        db.rollback()
        print("dry run: nothing written")
        return
    db.commit()

    from app.services.activity_log import log_activity
    from app.services.word_card import enrich_word_cards_for

    stats = enrich_word_cards_for(db, rebuild)
    print("word cards:", stats)
    for lid, (ar, en) in phrases.items():
        lemma = db.get(Lemma, lid)
        card = dict(lemma.word_card_json or {"version": 1, "chain": [], "forms": {}, "phrases": []})
        if not any(p.get("ar") == ar for p in card.get("phrases", [])):
            card["phrases"] = [{"ar": ar, "en": en, "attested": True}] + list(card.get("phrases", []))
            lemma.word_card_json = card
    db.commit()
    log_activity(db, "manual_action",
                 f"Repaired {len(GLOSSES)} glosses, {len(RENAMES)} headwords, {len(VARIANTS)} duplicates "
                 "from the 2026-11-26 import gloss audit", {"script": Path(__file__).name})
    db.close()


if __name__ == "__main__":
    main()
