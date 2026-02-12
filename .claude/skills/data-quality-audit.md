# Data Quality Audit

Run standard data quality checks on the production database.

## Full Audit
```bash
ssh alif "docker exec alif-backend-1 python3 -c \"
from app.database import SessionLocal
from app.models import *
from sqlalchemy import func
import json

db = SessionLocal()

# 1. Lemmas without any sentences
lemma_ids_with_sentences = set(
    r[0] for r in db.query(SentenceWord.lemma_id).distinct().all() if r[0]
)
no_sentence = []
for l in db.query(Lemma).filter(Lemma.is_function_word == False, Lemma.canonical_lemma_id == None).all():
    if l.lemma_id not in lemma_ids_with_sentences:
        ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == l.lemma_id).first()
        if ulk and ulk.knowledge_state not in ('encountered', 'suspended'):
            no_sentence.append(f'{l.lemma_ar} ({l.gloss_en}) state={ulk.knowledge_state}')
print(f'=== Lemmas without sentences (active, non-variant): {len(no_sentence)} ===')
for w in no_sentence[:20]:
    print(f'  {w}')

# 2. Lemmas without roots
no_root = db.query(Lemma).filter(Lemma.root_id == None, Lemma.is_function_word == False).count()
print(f'\n=== Lemmas without roots: {no_root} ===')

# 3. Roots without meanings
no_meaning = db.query(Root).filter((Root.core_meaning == None) | (Root.core_meaning == '')).count()
print(f'=== Roots without core_meaning: {no_meaning} ===')

# 4. Lemmas without frequency data
no_freq = db.query(Lemma).filter(Lemma.frequency_rank == None, Lemma.is_function_word == False).count()
print(f'=== Lemmas without frequency_rank: {no_freq} ===')

# 5. Potential al-prefix duplicates
from sqlalchemy import or_
lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id == None).all()
al_dupes = []
bare_set = {}
for l in lemmas:
    bare = l.bare_form or ''
    if bare.startswith('ال'):
        base = bare[2:]
        if base in bare_set:
            al_dupes.append(f'{l.lemma_ar} ({l.gloss_en}) <-> {bare_set[base].lemma_ar} ({bare_set[base].gloss_en})')
    else:
        bare_set[bare] = l
print(f'\n=== Potential al-prefix duplicates: {len(al_dupes)} ===')
for d in al_dupes[:10]:
    print(f'  {d}')

# 6. ULK in unusual states
orphan_ulk = db.query(UserLemmaKnowledge).filter(
    UserLemmaKnowledge.knowledge_state == 'new',
    UserLemmaKnowledge.times_seen > 0
).count()
print(f'\n=== ULK state=new but times_seen>0: {orphan_ulk} ===')

# 7. Sentences shown too many times
overused = db.query(Sentence).filter(Sentence.times_shown > 10).count()
print(f'=== Sentences shown >10 times: {overused} ===')

# 8. Active sentence count
active = db.query(Sentence).filter(Sentence.retired_at == None).count()
print(f'=== Active sentences: {active} (cap: 200) ===')

db.close()
\""
```

## Known Issue Categories
When issues are found, the typical fixes are:
- **al-prefix duplicates**: Run `python3 scripts/merge_al_lemmas.py`
- **Missing roots**: Run `python3 scripts/backfill_roots.py`
- **Missing root meanings**: Run `python3 scripts/backfill_root_meanings.py`
- **Missing frequency**: Run `python3 scripts/backfill_frequency.py`
- **Variant detection**: Run `python3 scripts/normalize_and_dedup.py`
- **Sentence quality**: Run `python3 scripts/verify_sentences.py` then `retire_sentences.py`
- **Missing sentences**: Run `python3 scripts/update_material.py`

Always log fixes to ActivityLog:
```bash
ssh alif "docker exec alif-backend-1 python3 scripts/log_activity.py data_quality_audit 'Ran audit: fixed X issues' --detail '{\"issues_found\": N, \"fixed\": M}'"
```
