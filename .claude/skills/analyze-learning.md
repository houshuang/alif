# Analyze Learning Progress

Run the progress analysis script on production to get a comprehensive snapshot of the learner's current state.

## Quick Analysis (today)
```bash
ssh alif "docker exec alif-backend-1 python3 scripts/analyze_progress.py"
```

## Extended Analysis (last N days)
```bash
ssh alif "docker exec alif-backend-1 python3 scripts/analyze_progress.py --days 7"
```

## What It Shows
- Knowledge state counts (known, learning, acquiring, encountered, etc.)
- Acquisition pipeline (words in box 1/2/3 with accuracy)
- Recent graduations (last 7 days)
- Session breakdown (sentences, comprehension, response times)
- Comprehension by word count (calibration check)
- Rating distribution (acquisition vs FSRS)
- Response time analysis
- Struggling/leech words
- Yesterday vs today comparison
- Sentence pool stats

## After Analysis
1. Check `research/experiment-log.md` for context on recent changes
2. Add any new findings as a dated entry in the experiment log
3. Flag anomalies (words stuck in wrong state, unusual accuracy drops, etc.)

## Manual Data Fixes
```bash
# Reset a word to acquiring
ssh alif "docker exec alif-backend-1 python3 -c \"
from app.database import SessionLocal
from app.models import UserLemmaKnowledge
db = SessionLocal()
ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == LEMMA_ID).first()
ulk.knowledge_state = 'acquiring'
ulk.acquisition_box = 1
ulk.acquisition_next_due = None
ulk.graduated_at = None
ulk.fsrs_card_json = None
db.commit()
db.close()
\""

# Always log manual actions
ssh alif "docker exec alif-backend-1 python3 scripts/log_activity.py manual_action 'Description' --detail '{\"key\": \"val\"}'"
```
