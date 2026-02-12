# Analyze Learning Data

Analyze the learner's review data, algorithm behavior, and progress from the production database.

## Quick Stats
```bash
ssh alif "docker exec alif-backend-1 python3 -c \"
from app.database import SessionLocal
from app.models import *
from datetime import datetime, timedelta
import json

db = SessionLocal()
now = datetime.utcnow()
today = now.replace(hour=0, minute=0, second=0, microsecond=0)

# Word counts by state
states = {}
for ulk in db.query(UserLemmaKnowledge).all():
    states[ulk.knowledge_state] = states.get(ulk.knowledge_state, 0) + 1
print('=== Words by State ===')
for s, c in sorted(states.items(), key=lambda x: -x[1]):
    print(f'  {s}: {c}')

# Reviews today
today_reviews = db.query(ReviewLog).filter(ReviewLog.reviewed_at >= today).count()
today_sentences = db.query(SentenceReviewLog).filter(SentenceReviewLog.reviewed_at >= today).count()
print(f'\n=== Today ({today.strftime(\"%Y-%m-%d\")}) ===')
print(f'  Word reviews: {today_reviews}')
print(f'  Sentence reviews: {today_sentences}')

# Accuracy (last 7 days)
week_ago = now - timedelta(days=7)
recent = db.query(ReviewLog).filter(ReviewLog.reviewed_at >= week_ago).all()
if recent:
    good = sum(1 for r in recent if r.rating >= 3)
    print(f'\n=== 7-day Accuracy ===')
    print(f'  {good}/{len(recent)} = {good/len(recent)*100:.0f}%')

# Leeches (times_seen>=8, accuracy<40%)
print('\n=== Leeches (seen>=8, acc<40%) ===')
for ulk in db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.times_seen >= 8).all():
    acc = ulk.times_correct / ulk.times_seen if ulk.times_seen else 0
    if acc < 0.4:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == ulk.lemma_id).first()
        print(f'  {lemma.lemma_ar if lemma else \"?\"} ({lemma.gloss_en if lemma else \"?\"}) seen={ulk.times_seen} acc={acc:.0%} state={ulk.knowledge_state}')

# Acquisition funnel
acquiring = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.knowledge_state == 'acquiring').count()
graduated = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.graduated_at != None).count()
encountered = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.knowledge_state == 'encountered').count()
print(f'\n=== Acquisition Funnel ===')
print(f'  Encountered: {encountered}')
print(f'  Acquiring: {acquiring}')
print(f'  Graduated: {graduated}')

db.close()
\""
```

## Detailed Review History (recent)
```bash
ssh alif "docker exec alif-backend-1 python3 -c \"
from app.database import SessionLocal
from app.models import ReviewLog, Lemma
from datetime import datetime, timedelta
db = SessionLocal()
cutoff = datetime.utcnow() - timedelta(days=2)
for r in db.query(ReviewLog).filter(ReviewLog.reviewed_at >= cutoff).order_by(ReviewLog.reviewed_at).all():
    lemma = db.query(Lemma).filter(Lemma.lemma_id == r.lemma_id).first()
    word = lemma.lemma_ar if lemma else '?'
    gloss = (lemma.gloss_en if lemma else '?')[:25]
    print(f'{r.reviewed_at.strftime(\"%m-%d %H:%M\")} | {word} ({gloss}) | rating={r.rating} mode={r.review_mode}')
db.close()
\""
```

## Cross-reference with Experiment Log
After gathering data, always:
1. Read `research/experiment-log.md` for previous observations
2. Compare current metrics against expectations
3. Add new findings as a dated entry in the experiment log
4. Flag any anomalies (unexpected accuracy drops, words stuck in acquisition, etc.)

## JSONL Interaction Logs
```bash
# Today's events
ssh alif "docker exec alif-backend-1 cat /app/data/logs/interactions_$(date -u +%Y-%m-%d).jsonl 2>/dev/null | python3 -c \"
import sys, json
from collections import Counter
events = Counter()
for line in sys.stdin:
    e = json.loads(line)
    events[e.get('event','?')] += 1
for evt, count in events.most_common():
    print(f'  {evt}: {count}')
\""
```
