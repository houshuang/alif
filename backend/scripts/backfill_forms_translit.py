"""Compute ALA-LC transliteration for all lemma form values.

Populates forms_translit_json from existing forms_json using deterministic
transliteration. Skips forms without diacritics (transliteration unreliable).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models import Lemma
from app.services.transliteration import transliterate_forms


def main():
    db = SessionLocal()
    try:
        lemmas = (
            db.query(Lemma)
            .filter(Lemma.forms_json.isnot(None))
            .all()
        )
        updated = 0
        for lemma in lemmas:
            if not isinstance(lemma.forms_json, dict):
                continue
            translit = transliterate_forms(lemma.forms_json)
            if translit:
                lemma.forms_translit_json = translit
                updated += 1
        db.commit()
        print(f"Updated {updated} / {len(lemmas)} lemma forms transliterations")
    finally:
        db.close()


if __name__ == "__main__":
    main()
