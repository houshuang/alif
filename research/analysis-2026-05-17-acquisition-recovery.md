# Acquisition Recovery Analysis - 2026-05-17

Production analysis window: 2026-05-13 through 2026-05-17, Europe/Oslo day boundaries where possible. Local `backend/data/alif.prod.db` was stale, so the final counts came from the live `/opt/alif/backend/data/alif.db`.

## Summary

The issue was not only the raw score. The last five days introduced too many words into the acquisition pipeline before the existing Box 1/2 words had enough sentence practice to consolidate.

Key live findings:

- 107 net-new acquisitions in five days.
- Source mix: 87 `textbook_scan`, 18 `collateral`, 1 `book`, 1 `quran`.
- Intro-card pressure was front-loaded: May 15 had 105 intro cards shown, with sessions reaching 15 intro cards. May 16 still had a 10-intro session. May 17 had one 6-intro session after the first cap fix.
- Current acquisition backlog before repair: 125 acquiring words.
- Box distribution before repair: Box 1 = 28, Box 2 = 83, Box 3 = 14.
- Due acquisition backlog before repair: 105 total; Box 2 due alone was 70.
- Sentence material was not the bottleneck: all 105 due acquiring words had at least one reviewable sentence, 99 had 3+, and 28 had 5+.

The problematic cohort was the "intro-card working memory" path. Of 83 current Box-2 words, 74 had their first acquisition review less than two minutes after the intro card. 43 were Box 2 with exactly one fast correct review. Another 31 had a fast first correct and later failure or low accuracy. These words looked learned to the scheduler but often had not had spaced retrieval yet.

## Decision

Keep the aggressive learning posture, but make new-word intake conditional on actual practice when the pipeline is overloaded.

Implemented changes:

1. Correct reviews inside `FAST_GRAD_INTRO_GAP` after an intro card no longer promote Box 1 to Box 2 and no longer trigger Tier 0/1/2 graduation.
2. Those correct reviews still count as exposure and accuracy; the word stays in Box 1 and retries after `FAST_INTRO_RETRY_INTERVAL` (30 minutes).
3. Frontend auto-skip no longer skips acquiring primary words or `acquisition_repeat` cards. If the system deliberately gives an acquisition repetition sentence, the learner sees it.
4. Recovery-mode intro budget activates only under acquisition overload:
   - Box 1 unreviewed >= 5, or
   - due Box 2 >= 30.
5. On normal low-debt days, the hard budget is still 30/day. Under overload, budget is earned:
   - 0 new words before 40 same-day sentence reviews.
   - 4 new words after 40 same-day sentence reviews with acceptable word-review accuracy.
   - 8 new words after 100 same-day sentence reviews and >=85% word-review accuracy.
   - <80% word-review accuracy pauses new intros even after sentence volume.
6. `word_selector.introduce_word()` and `_auto_introduce_words()` now treat cap-deferred starts as `encountered`, not as successfully introduced words.
7. One-shot repair script added: `backend/scripts/reset_fast_intro_promotions_2026_05_17.py`. It preserves review history and resets current acquiring Box-2/3 words whose first correct acquisition review happened inside the intro-card gap.

## Operational Interpretation

It is still fine to introduce 6-8 new words on a high-practice day while this backlog exists, if the day includes 100+ sentence reviews and accuracy is not slipping. The system should not introduce 30 new words at the start of the day under overload.

The expected next-day experience is:

- early sessions concentrate on due Box 1/2 acquisition words and sentence repetitions;
- intro cards should not exceed the existing per-session cap of 6;
- new words should remain low until the day has real sentence volume;
- words that were reset from fast Box-2 promotion should become due now and reappear as actual sentence practice, not as fresh intro-card memory.

## Quick Check - Tomorrow

Run these against production after at least one real study block:

```bash
ssh alif 'cd /opt/alif/backend && .venv/bin/python3 scripts/reset_fast_intro_promotions_2026_05_17.py'
```

Expected after the repair has already been applied: candidate count should be 0 or very low. If it rises, new intro-card working-memory promotions are still leaking.

```bash
ssh alif 'cd /opt/alif/backend && PYTHONPATH=/opt/alif/backend .venv/bin/python3 - <<"PY"
from datetime import datetime, timezone
from app.database import SessionLocal
from app.models import UserLemmaKnowledge

db = SessionLocal()
now = datetime.now(timezone.utc)
rows = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.knowledge_state == "acquiring").all()
print("acquiring", len(rows))
for box in (1, 2, 3):
    items = [r for r in rows if (r.acquisition_box or 1) == box]
    due = [r for r in items if r.acquisition_next_due and (r.acquisition_next_due.replace(tzinfo=timezone.utc) if r.acquisition_next_due.tzinfo is None else r.acquisition_next_due) <= now]
    unreviewed = [r for r in items if (r.times_seen or 0) == 0]
    print("box", box, "count", len(items), "due", len(due), "unreviewed", len(unreviewed))
db.close()
PY'
```

Healthy direction tomorrow:

- Box 1 unreviewed should drop below 5 after real review volume.
- Box 2 due should start dropping from the pre-fix 70.
- The reset script should not keep finding one-fast-correct Box-2 words.
- Intro-card sessions should stay at 6 or fewer first-time cards.
- If sentence volume is >100 and word accuracy is >=85%, 6-8 new words is acceptable; if sentence volume is low, new words should stay near 0.

## Three-Day Check

By 2026-05-20, check:

- Box 2 due is comfortably below 30, or at least falling day over day.
- Box 1 unreviewed is usually below 5 outside the first session after new intros.
- No large cohort of current Box-2 words has exactly one acquisition review immediately after an intro card.
- Same-day high practice (100+ sentence reviews) produces modest new-word intake, not a 30-word morning dump.
- Sentence review volume per new word is rising. The important measure is not just new-word count, but whether new words get multiple contextual exposures in the first 24-72 hours.

If those are true, keep allowing the aggressive 6-8/day recovery budget on high-practice days. If Box 2 due remains above 30 after three days of 100+ sentence reviews/day, lower `RECOVERY_FULL_INTRO_BUDGET` from 8 to 6 temporarily.
