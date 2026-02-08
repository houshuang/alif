# Check User Activity Logs

Inspect all user interaction data from the production database and JSONL logs.

## Quick Summary

```bash
ssh alif "docker exec alif-backend-1 python -c \"
import json, glob, os
from app.database import SessionLocal
from app.models import ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma, Sentence

db = SessionLocal()

# JSONL log summary
log_dir = '/app/data/logs'
for f in sorted(glob.glob(os.path.join(log_dir, '*.jsonl'))):
    by_type = {}
    with open(f) as fh:
        for line in fh:
            evt = json.loads(line)
            by_type.setdefault(evt.get('event','?'), []).append(evt)
    print(f'=== {os.path.basename(f)} ===')
    for t, evts in sorted(by_type.items()):
        print(f'  {t}: {len(evts)}')

# DB summary
reviews = db.query(ReviewLog).count()
sentences = db.query(SentenceReviewLog).count()
reviewed_words = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.times_seen > 0).count()
total_words = db.query(UserLemmaKnowledge).count()
print(f'\nDB: {reviews} word reviews, {sentences} sentence reviews, {reviewed_words}/{total_words} words seen')
db.close()
\""
```

## Detailed Review History

```bash
ssh alif "docker exec alif-backend-1 python -c \"
from app.database import SessionLocal
from app.models import ReviewLog, Lemma, Sentence
db = SessionLocal()
for r in db.query(ReviewLog).order_by(ReviewLog.reviewed_at).all():
    lemma = db.query(Lemma).filter(Lemma.lemma_id == r.lemma_id).first()
    word = lemma.lemma_ar if lemma else '?'
    gloss = lemma.gloss_en if lemma else '?'
    sent = ''
    if r.sentence_id:
        s = db.query(Sentence).filter(Sentence.id == r.sentence_id).first()
        if s: sent = (s.english_translation or '')[:50]
    print(f'{r.reviewed_at} | {word} ({gloss}) | rating={r.rating} credit={r.credit_type} signal={r.comprehension_signal} mode={r.review_mode} ms={r.response_ms} | {sent}')
db.close()
\""
```

## Knowledge Scores

```bash
ssh alif "docker exec alif-backend-1 python -c \"
from app.database import SessionLocal
from app.models import UserLemmaKnowledge, Lemma
import json, math
db = SessionLocal()
for k in db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.times_seen > 0).all():
    l = db.query(Lemma).filter(Lemma.lemma_id == k.lemma_id).first()
    card = k.fsrs_card_json
    if isinstance(card, str): card = json.loads(card)
    stab = (card or {}).get('stability') or 0
    s_score = min(1.0, math.log(1+stab)/math.log(366))
    acc = k.times_correct/k.times_seen if k.times_seen else 0
    conf = 1 - math.exp(-k.times_seen/5)
    score = round((0.7*s_score + 0.3*acc) * conf * 100)
    print(f'{l.gloss_en:20s} score={score:3d}  seen={k.times_seen} correct={k.times_correct} stability={stab:.2f}d state={k.knowledge_state}')
db.close()
\""
```

## Full JSONL Events (today)

```bash
ssh alif "docker exec alif-backend-1 cat /app/data/logs/interactions_$(date -u +%Y-%m-%d).jsonl"
```

## Data Capture Points

| Source | Event | Key Fields |
|--------|-------|------------|
| JSONL | `session_start` | session_id, review_mode, total_due_words, covered_due_words, sentence_count, fallback_count |
| JSONL | `sentence_selected` | session_id, sentence_id, selection_order, score, due_words_covered, remaining_due |
| JSONL | `sentence_review` | sentence_id, lemma_id, comprehension_signal, missed_lemma_ids, response_ms, review_mode, words_reviewed, collateral_count |
| JSONL | `tts_request` | text_length, cache_hit, success, latency_ms, error |
| DB | `review_log` | lemma_id, rating, credit_type, comprehension_signal, review_mode, response_ms, sentence_id, session_id, fsrs_log_json |
| DB | `sentence_review_log` | sentence_id, comprehension, review_mode, response_ms, session_id |
| DB | `user_lemma_knowledge` | times_seen, times_correct, knowledge_state, fsrs_card_json, last_reviewed |
