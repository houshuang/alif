"""Backfill proper nouns in existing stories: mark as function words with name_type.

For story-imported lemmas that are proper nouns (personal/place names):
1. Update their StoryWord entries: is_function_word=True, name_type, keep gloss
2. Remove the orphaned Lemma entries (no real vocab value)
3. Recalculate story readiness

Usage:
    python scripts/backfill_story_proper_nouns.py [--dry-run]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma, StoryWord, UserLemmaKnowledge, ReviewLog
from app.services.story_service import _recalculate_story_counts

# Manually classified proper nouns from existing story imports
PROPER_NOUNS = {
    2008: {"name_type": "personal", "gloss": "Zuhair"},       # زهير
    2009: {"name_type": "personal", "gloss": "Awfa"},          # أوفى - Umm Awfa
    2012: {"name_type": "place", "gloss": "al-Durraj"},        # دراج
    2013: {"name_type": "place", "gloss": "al-Mutathallam"},   # متثلم
    2014: {"name_type": "place", "gloss": "al-Raqmatayn"},     # بالرقمتين
    2011: {"name_type": "place", "gloss": "Hawmanah"},         # بحومانه
}

# إذا is a function word that shouldn't have been imported
FUNCTION_WORDS = {
    2007: "if/when",  # إذا
}


def main():
    dry_run = "--dry-run" in sys.argv
    db = SessionLocal()

    try:
        stories_to_recalc = set()

        # Handle proper nouns
        for lemma_id, info in PROPER_NOUNS.items():
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
            if not lemma:
                print(f"  Lemma {lemma_id} not found, skipping")
                continue

            # Find all StoryWords pointing to this lemma
            story_words = db.query(StoryWord).filter(StoryWord.lemma_id == lemma_id).all()
            print(f"  {lemma.lemma_ar_bare} (id={lemma_id}): {info['name_type']} name '{info['gloss']}' — {len(story_words)} story words")

            if not dry_run:
                for sw in story_words:
                    sw.is_function_word = True
                    sw.name_type = info["name_type"]
                    sw.gloss_en = info["gloss"]
                    sw.lemma_id = None
                    stories_to_recalc.add(sw.story_id)

                # Delete ULK and reviews if any
                db.query(ReviewLog).filter(ReviewLog.lemma_id == lemma_id).delete()
                db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == lemma_id).delete()
                db.delete(lemma)

        # Handle function words
        for lemma_id, gloss in FUNCTION_WORDS.items():
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
            if not lemma:
                print(f"  Lemma {lemma_id} not found, skipping")
                continue

            story_words = db.query(StoryWord).filter(StoryWord.lemma_id == lemma_id).all()
            print(f"  {lemma.lemma_ar_bare} (id={lemma_id}): function word '{gloss}' — {len(story_words)} story words")

            if not dry_run:
                for sw in story_words:
                    sw.is_function_word = True
                    sw.gloss_en = gloss
                    sw.lemma_id = None
                    stories_to_recalc.add(sw.story_id)

                db.query(ReviewLog).filter(ReviewLog.lemma_id == lemma_id).delete()
                db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == lemma_id).delete()
                db.delete(lemma)

        # Recalculate story counts
        if not dry_run and stories_to_recalc:
            from app.models import Story
            for story_id in stories_to_recalc:
                story = db.query(Story).filter(Story.id == story_id).first()
                if story:
                    _recalculate_story_counts(db, story)
                    print(f"  Story {story_id}: readiness={story.readiness_pct}%, unknown={story.unknown_count}")

            db.commit()
            print(f"\nDone! Converted {len(PROPER_NOUNS)} proper nouns + {len(FUNCTION_WORDS)} function words")
        else:
            print(f"\nDry run: would convert {len(PROPER_NOUNS)} proper nouns + {len(FUNCTION_WORDS)} function words")

    finally:
        db.close()


if __name__ == "__main__":
    main()
