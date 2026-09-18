"""
Simulation: Latin/Greek study scheduling under a fixed secondary-language budget.

Arabic is the primary focus; only a small daily budget is left for the Polyglot
languages. Compare two policies:
  BOTH      -- a little of each language EVERY day
  ALTERNATE -- all of one language EVERY OTHER day

The policies are BUDGET-MATCHED: each language receives the same average cards/day
(B/2). Over any 2-day window the per-language card spend is identical; only the
TEMPORAL distribution differs. The simulation asserts cards-spent parity so any
outcome difference is attributable to review timing vs the forgetting curve, not to
one policy getting more practice.

Mechanics mirror Alif/Polyglot:
  - Acquisition Leitner boxes 4h -> 1d -> 3d  (acquisition_service.BOX_INTERVALS)
  - Graduation Tier 0 (first-correct) / Tier 1 (100% acc >=3 seen) /
    Tier 3 (box3, >=5 seen, >=2 calendar days, >=60% acc) -> FSRS
  - FSRS-6 via py-fsrs Scheduler(desired_retention=0.95)  (fsrs_service)
  - Failed recall resets acquisition word to box 1 / lapses FSRS card
  - Forgetting: r = exp(-elapsed / S); S calibrated so an on-time review yields
    ~0.90 recall in acquisition. FSRS words use FSRS's own retrievability.

Axes swept: total daily budget, sessions/day (1 = one sitting, 2 = morning+evening),
same-day interference factor for the BOTH policy.
"""
import math
import random
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

from fsrs import Scheduler, Card, Rating

HOUR = 1.0
DAY = 24.0
BOX_INTERVALS = {1: 4 * HOUR, 2: 1 * DAY, 3: 3 * DAY}
ON_TIME_RETENTION = 0.90
STRENGTH_K = -1.0 / math.log(ON_TIME_RETENTION)  # ~9.49
DAILY_INTRO_CAP = 30
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
scheduler = Scheduler(desired_retention=0.95)


@dataclass
class Word:
    wid: int
    box: int = 0            # 0 = not introduced; 1..3 acquiring
    last_review_h: float = -1.0
    next_due_h: float = 0.0
    times_seen: int = 0
    times_correct: int = 0
    cal_days: set = field(default_factory=set)
    graduated: bool = False
    card: Card | None = None
    state: str = "new"

    def r_acq(self, now_h, skill):
        if self.last_review_h < 0:
            return 1.0
        S = STRENGTH_K * BOX_INTERVALS[self.box] * skill
        return math.exp(-max(now_h - self.last_review_h, 0.0) / S)


def graduate(w, now_h):
    w.graduated = True
    w.state = "known"
    c = Card()
    c, _ = scheduler.review_card(c, Rating.Good, review_datetime=BASE + timedelta(hours=now_h))
    w.card = c


def review_acq(w, now_h, skill, interference, rng):
    r = w.r_acq(now_h, skill) * interference
    correct = rng.random() < r
    w.times_seen += 1
    w.cal_days.add(int(now_h // DAY))
    if correct:
        w.times_correct += 1
        acc = w.times_correct / w.times_seen
        if w.times_seen == 1:                                   # Tier 0
            return graduate(w, now_h)
        if acc == 1.0 and w.times_seen >= 3:                    # Tier 1
            return graduate(w, now_h)
        if w.box >= 3 and w.times_seen >= 5 and acc >= 0.60 and len(w.cal_days) >= 2:  # Tier 3
            return graduate(w, now_h)
        if w.box < 3:
            w.box += 1
        w.last_review_h = now_h
        w.next_due_h = now_h + BOX_INTERVALS[w.box]
    else:
        w.box = 1
        w.last_review_h = now_h
        w.next_due_h = now_h + BOX_INTERVALS[1]


def review_fsrs(w, now_h, skill, interference, rng):
    dt = BASE + timedelta(hours=now_h)
    r = scheduler.get_card_retrievability(w.card, current_datetime=dt)
    p = min(1.0, r ** (1.0 / max(skill, 0.5))) * interference
    correct = rng.random() < p
    w.card, _ = scheduler.review_card(w.card, Rating.Good if correct else Rating.Again, review_datetime=dt)
    if w.card.stability is not None and w.card.stability < 1.0:
        w.state = "lapsed"
    else:
        w.state = "known" if correct else "lapsed"


def run_session(words, now_h, budget, skill, interference, intros_today, rng, counters):
    spent = 0
    dt = BASE + timedelta(hours=now_h)
    # 1) due acquisition words, oldest-due first
    due_acq = [w for w in words if w.box > 0 and not w.graduated and w.next_due_h <= now_h]
    due_acq.sort(key=lambda w: w.next_due_h)
    # 2) due FSRS words
    due_fsrs = [w for w in words if w.graduated and w.card.due <= dt]
    due_fsrs.sort(key=lambda w: w.card.due)
    for w in due_acq + due_fsrs:
        if spent >= budget:
            break
        if w.graduated:
            review_fsrs(w, now_h, skill, interference, rng)
        else:
            review_acq(w, now_h, skill, interference, rng)
        spent += 1
        counters["reviews"] += 1
    # 3) introduce new with leftover budget
    for w in words:
        if spent >= budget or intros_today[0] >= DAILY_INTRO_CAP:
            break
        if w.box != 0:
            continue
        w.box = 1
        w.state = "acquiring"
        w.last_review_h = now_h
        w.next_due_h = now_h + BOX_INTERVALS[1]
        intros_today[0] += 1
        spent += 1
        counters["intros"] += 1
    counters["spent"] += spent
    return spent


def make_words(n, start):
    return [Word(wid=start + i) for i in range(n)]


def plan_for_day(policy, day, B_both, B_alt):
    if policy == "both":
        return {"latin": B_both, "greek": B_both}
    return {"latin": B_alt} if day % 2 == 0 else {"greek": B_alt}


def simulate(policy, days, skill, interference_same_day, pool, sessions_per_day,
             B_both, B_alt, seed):
    rng = random.Random(seed)
    langs = {"latin": make_words(pool, 0), "greek": make_words(pool, 100000)}
    counters = {l: {"spent": 0, "reviews": 0, "intros": 0} for l in langs}
    # session offsets within a day
    if sessions_per_day == 1:
        offsets = [9 * HOUR]
    else:
        offsets = [9 * HOUR, 17 * HOUR]
    for day in range(days):
        plan = plan_for_day(policy, day, B_both, B_alt)
        for lang, day_budget in plan.items():
            words = langs[lang]
            intros_today = [0]
            interference = interference_same_day if policy == "both" else 1.0
            # split day budget across sessions
            per = [day_budget // sessions_per_day] * sessions_per_day
            for i in range(day_budget - sum(per)):
                per[i] += 1
            for off, b in zip(offsets, per):
                run_session(words, day * DAY + off, b, skill, interference,
                            intros_today, rng, counters[lang])
    now_h = days * DAY + 20 * HOUR
    out = {}
    for lang, words in langs.items():
        graduated = known = acquiring = 0
        for w in words:
            if w.graduated:
                graduated += 1
                dt = BASE + timedelta(hours=now_h)
                if w.state == "known" and scheduler.get_card_retrievability(w.card, current_datetime=dt) >= 0.85:
                    known += 1
            elif w.box > 0:
                acquiring += 1
        out[lang] = dict(graduated=graduated, known=known, acquiring=acquiring,
                         **counters[lang])
    return out


def agg_over_seeds(policy, **kw):
    keys = ["graduated", "known", "acquiring", "spent", "reviews", "intros"]
    tot = {k: 0 for k in keys}
    n = 8
    for seed in range(n):
        res = simulate(policy, seed=seed, **kw)
        for v in res.values():
            for k in keys:
                tot[k] += v[k]
    return {k: tot[k] / n for k in keys}


if __name__ == "__main__":
    DAYS, POOL, SKILL = 90, 500, 1.0
    print(f"\n{'='*86}")
    print(f"Latin/Greek scheduling — {DAYS}d, budget-matched, skill={SKILL}, 8 seeds avg")
    print(f"{'='*86}")
    for sessions_per_day in (1, 2):
        for interf in (1.0, 0.90):
            print(f"\n### sessions/day={sessions_per_day}  "
                  f"same-day interference (BOTH only)={interf} ###")
            print(f"{'budget':>7} | {'policy':9} | {'graduated':>9} {'known&ret':>9} "
                  f"{'acquiring':>9} | {'spent':>7} {'reviews':>7} {'intros':>6}")
            for total_daily in (6, 12, 20, 32):
                B_both = total_daily // 2
                B_alt = total_daily
                rows = {}
                for policy in ("both", "alternate"):
                    rows[policy] = agg_over_seeds(
                        policy, days=DAYS, skill=SKILL,
                        interference_same_day=interf, pool=POOL,
                        sessions_per_day=sessions_per_day,
                        B_both=B_both, B_alt=B_alt)
                for policy in ("both", "alternate"):
                    a = rows[policy]
                    print(f"{total_daily:>7} | {policy:9} | {a['graduated']:>9.1f} "
                          f"{a['known']:>9.1f} {a['acquiring']:>9.1f} | "
                          f"{a['spent']:>7.0f} {a['reviews']:>7.0f} {a['intros']:>6.0f}")
                kb, ka = rows["both"]["known"], rows["alternate"]["known"]
                sb, sa = rows["both"]["spent"], rows["alternate"]["spent"]
                lift = (kb - ka) / ka * 100 if ka else 0
                print(f"{'':>7} | {'-> both vs alt known&ret':30} "
                      f"{kb:.1f} vs {ka:.1f} ({lift:+.1f}%)   "
                      f"[spend parity: {sb:.0f} vs {sa:.0f}]")

    # ---- Break-even sweep: how much same-day interference flips the answer? ----
    print(f"\n{'='*86}")
    print("BREAK-EVEN: same-day interference needed for ALTERNATE to overtake BOTH")
    print("(budget=12/day, ALTERNATE has no interference; only BOTH pays it)")
    print(f"{'='*86}")
    for sessions_per_day in (1, 2):
        print(f"\n  sessions/day={sessions_per_day}")
        print(f"  {'interf':>7} | {'both known':>10} {'alt known':>10} | {'winner':>9}")
        alt = agg_over_seeds("alternate", days=DAYS, skill=SKILL,
                             interference_same_day=1.0, pool=POOL,
                             sessions_per_day=sessions_per_day, B_both=6, B_alt=12)
        for interf in (1.00, 0.98, 0.96, 0.94, 0.92, 0.90):
            both = agg_over_seeds("both", days=DAYS, skill=SKILL,
                                  interference_same_day=interf, pool=POOL,
                                  sessions_per_day=sessions_per_day, B_both=6, B_alt=12)
            win = "BOTH" if both["known"] >= alt["known"] else "alternate"
            print(f"  {interf:>7.2f} | {both['known']:>10.1f} {alt['known']:>10.1f} | {win:>9}")
