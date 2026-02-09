"""Import Duolingo exported lexemes into the Alif database."""

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Root, Lemma, UserLemmaKnowledge
from app.services.fsrs_service import create_new_card
from app.services.variant_detection import detect_variants, detect_definite_variants, mark_variants

DATA_FILE = Path(__file__).resolve().parent.parent / "app" / "data" / "duolingo_raw.json"

# Names to skip — proper nouns, Duolingo characters, cities, countries
SKIP_NAMES = {
    # Arabic proper names
    "مُحَمَّد", "مَحْمود", "إِبْراهيم", "عَلي", "عُمَر", "غَسّان", "بَشير",
    "لَمى", "مَها", "فَريد", "سامْية", "رانْيا", "أَرْوى", "تامِر", "ريم",
    "دَوود", "زَيد", "شادي", "رَواد", "كَري", "مايْك", "سام",
    "روزا", "جودي", "بوب", "جورج", "جاك", "سيث", "دُوو",
    # Countries and cities
    "باريس", "أَمْسْتِرْدام", "بَيْروت", "هارْفارْد", "بَرْلين",
    "نْيويورْك", "أَلْمانْيا", "فَرَنْسا", "أُسْتُرالْيا",
    "كَنَدا", "إِسْكُتْلَنْدا", "هولَنْدا", "إِنْجِلْتِرا",
    "عُمان", "تونِس", "لُبنان", "سوريا", "أَمريكا",
    "ريجا", "باكو", "داكار", "جوبا", "كوبا", "بيرو", "كوريا",
    "اَلْأَزْهَر",
}

# Nationality adjectives to skip
SKIP_NATIONALITIES = {
    "لُبْنانِيّة", "لُبنانِيّ", "أُرْدُنِيّة", "أُرْدُنِيّ",
    "مَغْرِبِيّة", "مَغْرِبِيّ", "فَرَنْسِيّة", "فَرَنْسِيّ",
    "هولَنْدِيّ", "سورِيّة", "سورِيّ", "تونِسِيّة", "تونِسِيّ",
    "عُمانِيّ", "أَمْريكِيّة", "أَمريكِيّ", "إِسْكُتْلَنْدِيّ",
    "إِنْجِليزِيّ", "كَنَدِيّ", "إِسْلامِيّة",
    "عَرَبِيّة", "عَرَبِيّ",
}

ALL_SKIP = SKIP_NAMES | SKIP_NATIONALITIES

# Common Arabic possessive/pronoun suffixes to strip
POSSESSIVE_SUFFIXES = [
    ("ـتَك", "ـة"),    # -tak → -a (feminine)
    ("ـتِك", "ـة"),    # -tik → -a (feminine)
    ("ـتي", "ـة"),     # -ti → -a (feminine)
]

# Known root families: base_lemma_bare -> root_string
KNOWN_ROOTS = {
    "كلب": "ك.ل.ب",
    "كتب": "ك.ت.ب",
    "بيت": "ب.ي.ت",
    "بنت": "ب.ن.ت",
    "ولد": "و.ل.د",
    "رجل": "ر.ج.ل",
    "جمل": "ج.م.ل",
    "جدد": "ج.د.د",
    "كبر": "ك.ب.ر",
    "سهل": "س.ه.ل",
    "سرع": "س.ر.ع",
    "طول": "ط.و.ل",
    "علم": "ع.ل.م",
    "ترجم": "ت.ر.ج.م",
    "هندس": "ه.ن.د.س",
    "حسب": "ح.س.ب",
    "زوج": "ز.و.ج",
    "جار": "ج.و.ر",
    "بحر": "ب.ح.ر",
    "سمك": "س.م.ك",
    "دجج": "د.ج.ج",
    "ملك": "م.ل.ك",
    "لغو": "ل.غ.و",
    "بصل": "ب.ص.ل",
    "حدق": "ح.د.ق",
    "غرف": "غ.ر.ف",
    "مدن": "م.د.ن",
    "بنى": "ب.ن.ي",
    "باب": "ب.و.ب",
    "بلد": "ب.ل.د",
    "سيرة": "س.ي.ر",
    "شاشة": "ش.و.ش",
    "جبن": "ج.ب.ن",
    "برد": "ب.ر.د",
    "غلا": "غ.ل.و",
    "غرب": "غ.ر.ب",
    "وسع": "و.س.ع",
    "جوع": "ج.و.ع",
    "زرق": "ز.ر.ق",
    "بيض": "ب.ي.ض",
    "بنن": "ب.ن.ن",
    "حمم": "ح.م.م",
    "طبخ": "ط.ب.خ",
    "بيع": "ب.ي.ع",
    "زور": "ز.و.ر",
    "شيء": "ش.ي.ء",
    "سعد": "س.ع.د",
    "ذكو": "ذ.ك.و",
    "حكم": "ح.ك.م",
    "سمو": "س.م.و",
}


def strip_diacritics(text: str) -> str:
    """Remove Arabic diacritical marks from text."""
    # Arabic diacritics Unicode range
    diacritics = re.compile(
        "[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED\u08D3-\u08FF]"
    )
    return diacritics.sub("", text)


def strip_al_prefix(bare: str) -> str:
    """Remove the definite article اَلْ / ال prefix."""
    if bare.startswith("ال"):
        return bare[2:]
    return bare


def is_multi_word(text: str) -> bool:
    """Check if text contains multiple words (spaces)."""
    return " " in text.strip()


def try_extract_base_lemma(text: str, bare: str) -> Optional[str]:
    """Try to extract a base lemma from an inflected form with possessive suffix.

    Returns bare form of the base lemma, or None.
    """
    # Common suffix patterns on bare text:
    # -ي (my), -ك (your masc), -ك (your fem) on end
    # For taa-marbuta words: ـتي ـتك ـتِك
    if bare.endswith("تي") or bare.endswith("تك"):
        # e.g. زوجتي -> زوجة, بيتي stays (no taa marbuta)
        candidate = bare[:-2] + "ة"
        return candidate
    if bare.endswith("ي") and len(bare) > 2:
        return bare[:-1]
    if bare.endswith("ك") and len(bare) > 2:
        return bare[:-1]
    return None


def load_lexemes() -> list[dict]:
    """Parse the multi-line JSON file. Each line is a page with learnedLexemes."""
    lexemes = []
    with open(DATA_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            page = json.loads(line)
            lexemes.extend(page.get("learnedLexemes", []))
    return lexemes


def run_import(db: Session) -> dict:
    """Import Duolingo lexemes into the database.

    Returns summary dict with counts.
    """
    lexemes = load_lexemes()
    imported = 0
    skipped_names = 0
    skipped_phrases = 0
    roots_created: dict[str, Root] = {}
    seen_bare: set[str] = set()
    new_lemma_ids: list[int] = []

    # Load existing roots
    for root_obj in db.query(Root).all():
        roots_created[root_obj.root] = root_obj

    # Load existing lemmas (bare forms) to avoid duplicates
    for lemma_obj in db.query(Lemma).all():
        seen_bare.add(lemma_obj.lemma_ar_bare)

    for lex in lexemes:
        text = lex["text"]
        translations = lex.get("translations", [])
        audio_url = lex.get("audioURL")
        gloss = translations[0] if translations else ""

        # Skip multi-word phrases
        if is_multi_word(text):
            skipped_phrases += 1
            continue

        # Skip names/countries/nationalities
        if text in ALL_SKIP:
            skipped_names += 1
            continue

        bare = strip_diacritics(text)

        # Skip if bare form matches a name (without diacritics)
        bare_no_al = strip_al_prefix(bare)

        # Already imported? Check both bare and al-stripped forms
        if bare in seen_bare or bare_no_al in seen_bare:
            continue

        # Use al-stripped form as the canonical bare form
        # "الكلب" -> stored as bare "كلب", not "الكلب"
        is_al_form = bare != bare_no_al
        canonical_bare = bare_no_al if is_al_form else bare
        # Strip "the " from English gloss for al- forms
        if is_al_form and gloss.lower().startswith("the "):
            gloss = gloss[4:]

        # Try to find/create root
        root_obj = None
        lookup_bare = bare_no_al
        for root_bare, root_str in KNOWN_ROOTS.items():
            if lookup_bare == root_bare or lookup_bare.startswith(root_bare[:3]):
                if root_str not in roots_created:
                    root_obj = Root(root=root_str)
                    db.add(root_obj)
                    db.flush()
                    roots_created[root_str] = root_obj
                else:
                    root_obj = roots_created[root_str]
                break

        lemma = Lemma(
            lemma_ar=text,
            lemma_ar_bare=canonical_bare,
            root_id=root_obj.root_id if root_obj else None,
            gloss_en=gloss,
            source="duolingo",
            audio_url=audio_url,
        )
        db.add(lemma)
        db.flush()

        knowledge = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json=create_new_card(),
            source="duolingo",
            times_seen=0,
            times_correct=0,
        )
        db.add(knowledge)
        new_lemma_ids.append(lemma.lemma_id)
        seen_bare.add(canonical_bare)
        if bare != canonical_bare:
            seen_bare.add(bare)
        imported += 1

    # Detect and mark variants among newly imported lemmas
    variants_marked = 0
    if new_lemma_ids:
        camel_vars = detect_variants(db, lemma_ids=new_lemma_ids)
        already = {v[0] for v in camel_vars}
        def_vars = detect_definite_variants(db, lemma_ids=new_lemma_ids, already_variant_ids=already)
        all_vars = camel_vars + def_vars
        if all_vars:
            variants_marked = mark_variants(db, all_vars)
            for var_id, canon_id, vtype, _ in all_vars:
                var = db.get(Lemma,var_id)
                canon = db.get(Lemma,canon_id)
                print(f"  Variant: {var.lemma_ar_bare} → {canon.lemma_ar_bare} [{vtype}]")

    db.commit()

    roots_found = len(roots_created)
    print(f"Imported {imported} words, skipped {skipped_names} names, "
          f"skipped {skipped_phrases} phrases, found {roots_found} roots, "
          f"marked {variants_marked} variants")

    return {
        "imported": imported,
        "skipped_names": skipped_names,
        "skipped_phrases": skipped_phrases,
        "roots_found": roots_found,
        "variants_marked": variants_marked,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.database import engine, Base, SessionLocal
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        result = run_import(session)
        print(json.dumps(result, indent=2))
    finally:
        session.close()
