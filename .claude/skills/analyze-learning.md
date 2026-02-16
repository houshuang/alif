# Analyze Learning Progress

Run a comprehensive analysis of the learner's progress on production. This skill pulls data directly from the production DB and provides analysis across all key dimensions.

## Prerequisites
- SSH access to production: `ssh alif`
- Container: `alif-backend-1`
- DB: SQLite at `/app/data/alif.db` inside container

## Quick Analysis Script
```bash
ssh alif "docker exec alif-backend-1 python3 scripts/analyze_progress.py --days 7"
```

## Comprehensive Manual Analysis

When the quick script isn't sufficient, run the following queries. All queries go through:
```bash
ssh alif 'docker exec alif-backend-1 python3 -c "SCRIPT"'
```

Use single quotes for outer shell, double quotes for Python strings. For complex scripts, write to `/tmp/claude/analysis.py`, then `scp` + `docker cp` + run from `/app` working directory.

### Key Queries

**IMPORTANT**: FSRS card JSON uses key `"stability"` (not `"s"`). Always use `card.get("stability", 0)`.

#### 1. Knowledge State Distribution
```python
from app.database import SessionLocal
from app.models import UserLemmaKnowledge
from sqlalchemy import func
db = SessionLocal()
states = db.query(
    UserLemmaKnowledge.knowledge_state,
    func.count(UserLemmaKnowledge.id)
).group_by(UserLemmaKnowledge.knowledge_state).all()
for state, count in sorted(states, key=lambda x: -x[1]):
    print(f"  {state}: {count}")
```

#### 2. FSRS Stability Distribution
```python
import json
ulks = db.query(UserLemmaKnowledge).filter(
    UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed"]),
    UserLemmaKnowledge.fsrs_card_json.isnot(None)
).all()
buckets = {"<1d": 0, "1-3d": 0, "3-7d": 0, "7-14d": 0, "14-30d": 0, "30-90d": 0, "90d+": 0}
for u in ulks:
    card = json.loads(u.fsrs_card_json) if isinstance(u.fsrs_card_json, str) else u.fsrs_card_json
    s = card.get("stability", 0) or 0  # KEY: "stability" not "s"
    if s < 1: buckets["<1d"] += 1
    elif s < 3: buckets["1-3d"] += 1
    elif s < 7: buckets["3-7d"] += 1
    elif s < 14: buckets["7-14d"] += 1
    elif s < 30: buckets["14-30d"] += 1
    elif s < 90: buckets["30-90d"] += 1
    else: buckets["90d+"] += 1
```

#### 3. Daily Review Activity
```python
from sqlalchemy import text
from datetime import datetime, timedelta
daily = db.execute(text("""
    SELECT date(reviewed_at) as day,
           count(*) as reviews,
           count(distinct session_id) as sessions,
           round(avg(case when rating >= 3 then 1.0 else 0.0 end) * 100, 1) as accuracy_pct,
           count(distinct lemma_id) as unique_words
    FROM review_log
    WHERE reviewed_at >= :start
    GROUP BY date(reviewed_at) ORDER BY day
"""), {"start": (datetime.utcnow() - timedelta(days=14)).isoformat()}).fetchall()
```

#### 4. Acquisition Pipeline (Leitner Boxes)
```python
for box in [1, 2, 3]:
    words = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.knowledge_state == "acquiring",
        UserLemmaKnowledge.acquisition_box == box,
    ).all()
    print(f"Box {box}: {len(words)} words")
```

#### 5. Sentence Utilization
```python
sent_stats = db.execute(text("""
    SELECT source, count(*) as total,
           sum(case when times_shown > 0 then 1 else 0 end) as shown,
           round(avg(times_shown), 1) as avg_shown
    FROM sentences WHERE is_active = 1
    GROUP BY source ORDER BY total DESC
""")).fetchall()
```

#### 6. Auto-Introduction Effectiveness
```python
intro_acc = db.execute(text("""
    SELECT date(ulk.acquisition_started_at) as intro_day,
           count(*) as words,
           round(avg(case when ulk.times_seen > 0
                     then cast(ulk.times_correct as float) / ulk.times_seen
                     else null end) * 100, 1) as avg_acc,
           sum(case when ulk.knowledge_state = 'known' then 1 else 0 end) as known,
           sum(case when ulk.knowledge_state = 'acquiring' then 1 else 0 end) as still_acq,
           sum(case when ulk.knowledge_state IN ('lapsed', 'suspended') then 1 else 0 end) as failed
    FROM user_lemma_knowledge ulk
    WHERE ulk.acquisition_started_at >= :start
      AND ulk.knowledge_state != 'encountered'
    GROUP BY date(ulk.acquisition_started_at) ORDER BY intro_day
"""), {"start": (datetime.utcnow() - timedelta(days=14)).isoformat()}).fetchall()
```

#### 7. Comprehension by Word Source
```python
comp_by_src = db.execute(text("""
    SELECT ulk.source, count(*) as reviews,
           round(avg(case when rl.rating >= 3 then 1.0 else 0.0 end) * 100, 1) as acc
    FROM review_log rl
    JOIN user_lemma_knowledge ulk ON rl.lemma_id = ulk.lemma_id
    WHERE rl.reviewed_at >= :start
    GROUP BY ulk.source HAVING reviews >= 5 ORDER BY acc
"""), {"start": (datetime.utcnow() - timedelta(days=7)).isoformat()}).fetchall()
```

#### 8. Struggling Words
```python
struggling = db.execute(text("""
    SELECT l.lemma_ar_bare, l.gloss_en, ulk.times_seen,
           round(cast(ulk.times_correct as float) / ulk.times_seen * 100, 1) as acc,
           ulk.knowledge_state, ulk.source
    FROM user_lemma_knowledge ulk
    JOIN lemmas l ON ulk.lemma_id = l.lemma_id
    WHERE ulk.times_seen >= 5
      AND cast(ulk.times_correct as float) / ulk.times_seen < 0.6
      AND ulk.knowledge_state NOT IN ('encountered', 'suspended')
    ORDER BY ulk.times_seen DESC LIMIT 15
""")).fetchall()
```

#### 9. Stability Growth Check (detect stagnation)
```python
# Words with many reviews but low stability = stagnant (over-reviewed same-day)
top_reviewed = db.query(UserLemmaKnowledge).filter(
    UserLemmaKnowledge.knowledge_state == "known",
    UserLemmaKnowledge.fsrs_card_json.isnot(None)
).order_by(UserLemmaKnowledge.times_seen.desc()).limit(10).all()
for u in top_reviewed:
    card = json.loads(u.fsrs_card_json) if isinstance(u.fsrs_card_json, str) else u.fsrs_card_json
    lem = db.query(Lemma).filter(Lemma.lemma_id == u.lemma_id).first()
    s = card.get("stability", 0)
    print(f"  {lem.lemma_ar_bare}: seen={u.times_seen}, correct={u.times_correct}, stability={s:.1f}d")
```

#### 10. Session Details
```python
sessions = db.execute(text("""
    SELECT session_id,
           count(*) as reviews,
           count(distinct lemma_id) as unique_words,
           round(avg(case when rating >= 3 then 1.0 else 0.0 end) * 100, 1) as acc,
           min(reviewed_at) as started,
           sum(case when is_acquisition = 1 then 1 else 0 end) as acq_reviews
    FROM review_log
    WHERE reviewed_at >= :start AND session_id IS NOT NULL
    GROUP BY session_id ORDER BY min(reviewed_at)
"""), {"start": (datetime.utcnow() - timedelta(days=7)).isoformat()}).fetchall()
```

## Known Issues to Watch For

### Stability Stagnation
FSRS stability plateaus at ~2.3d when words are reviewed multiple times on the same day. This is expected FSRS v6 behavior — same-day reviews don't grow stability because elapsed time is near-zero. Common cause: scaffold words (non-target words in reviewed sentences) get credited on every sentence review, leading to many same-day reviews. Words like لون (63 reviews, stability=2.3d) are over-reviewed as scaffold.

### Sentence Over-Generation
Active sentence cap is 300 but can exceed it. Check never-shown percentage — if >30% sentences are never shown, generation is outpacing consumption.

### Auto-Introduction Rate
`_intro_slots_for_accuracy()` uses recent 20-review accuracy. If accuracy drops below 70%, no new words should be introduced. Check by comparing introduction counts against accuracy trends.

## After Analysis
1. Record findings in `research/experiment-log.md` (dated entry)
2. Update `IDEAS.md` if new optimization ideas emerge
3. Flag critical issues to user with specific numbers
4. For manual data fixes, always log via `scripts/log_activity.py`

## REST API Endpoints (alternative)
- `GET /api/stats` — basic counts
- `GET /api/stats/analytics?days=90` — full dashboard (pace, CEFR, daily history)
- `GET /api/stats/deep-analytics` — stability distribution, sessions, struggling words, root coverage
