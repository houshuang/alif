"""
Box-interval tuning under PRIOR KNOWLEDGE + the familiarity illusion.

The policy sim (sim_alt_vs_both) asked *when* to study. This asks *how the boxes
should be shaped* for a learner (you) with broad Latin/Greek background, where many
words are "I've seen this, but I'm fuzzy on the exact sense."

The modelling difference that matters: we separate what the app SEES (your self-rating
-- "yeah I know that") from what is TRUE (whether the precise gloss is actually
durable). A familiar-but-fuzzy word produces a confident correct rating on first
sight (high *felt* stability) while its *true* stability is still low. The app
graduates it; two weeks later it's gone. This is the classic fluency illusion, and it
is exactly the risk in trusting "these will stick easily."

Memory model (per word):
  - true stability  S_true  governs whether you ACTUALLY remember at elapsed t
  - felt stability  S_felt  governs your self-rating at the keyboard
  - on a correct self-rating, both grow by factor g; on a miss, both reset to S0
  - retrievability r = exp(-elapsed / S);  rating_correct ~ Bernoulli(max(r_felt, guess))
  - DURABILITY at graduation is judged on S_true, not S_felt

Learner profiles (Latin/Greek prior):
  arabic_like   : no prior; S0_true=S0_felt (baseline, what the boxes were tuned for)
  genuine_prior : truly well-known; S0_true=S0_felt both HIGH -> fast grad is correct
  illusory_prior: feels known, isn't; S0_felt HIGH but S0_true LOW -> fast grad fails
  mixed_prior   : half genuine, half illusory (the realistic case)

Box configs compared (all single-session/day, budget-matched):
  arabic_4h_1d_3d : current Polyglot/Alif boxes, Tier-0 instant grad on
  longer_1d_3d_7d : stretched boxes for a strong learner, Tier-0 on
  no_tier0        : current boxes but NO instant graduation (forces >=1 spaced rep)
  trust_fast      : graduate after a single correct (maximally trusts your rating)

Metric: DURABLE graduations per card spent  (learning-per-minute, keeping time low).
A graduation is "durable" if true stability at handoff >= DURABLE_DAYS.
"""
import math
import random
from dataclasses import dataclass, field

HOUR = 1.0
DAY = 24.0
GUESS = 0.05               # floor: occasionally right by luck
GROWTH = 2.3              # stability multiplier per successful spaced rep
DURABLE_DAYS = 7.0        # handoff to FSRS counts only if true stability >= this
DAILY_INTRO_CAP = 30


@dataclass
class BoxConfig:
    name: str
    intervals_h: list      # box 1..k interval in hours
    tier0: bool            # first-correct -> instant graduate


CONFIGS = [
    BoxConfig("arabic_4h_1d_3d", [4 * HOUR, 1 * DAY, 3 * DAY], tier0=True),
    BoxConfig("longer_1d_3d_7d", [1 * DAY, 3 * DAY, 7 * DAY], tier0=True),
    BoxConfig("no_tier0_4h_1d_3d", [4 * HOUR, 1 * DAY, 3 * DAY], tier0=False),
    BoxConfig("trust_fast_1d", [1 * DAY], tier0=True),
]


@dataclass
class Word:
    wid: int
    s0_true: float
    s0_felt: float
    box: int = 0                  # 0 = not introduced
    n: int = 0                    # consecutive correct since last reset
    last_h: float = -1.0
    next_due_h: float = 0.0
    seen: int = 0
    graduated: bool = False
    durable: bool = False

    def s_true(self):
        return self.s0_true * (GROWTH ** self.n)

    def s_felt(self):
        return self.s0_felt * (GROWTH ** self.n)


def make_word(wid, profile, rng):
    if profile == "arabic_like":
        s0 = 6 * HOUR
        return Word(wid, s0, s0)
    if profile == "genuine_prior":
        s0 = 2 * DAY
        return Word(wid, s0, s0)
    if profile == "illusory_prior":
        return Word(wid, s0_true=8 * HOUR, s0_felt=2 * DAY)
    if profile == "mixed_prior":
        if rng.random() < 0.5:
            s0 = 2 * DAY
            return Word(wid, s0, s0)
        return Word(wid, s0_true=8 * HOUR, s0_felt=2 * DAY)
    raise ValueError(profile)


def review(w, now_h, cfg, rng):
    """One acquisition review. Mutates w; sets graduated/durable on graduation."""
    elapsed = now_h - w.last_h if w.last_h >= 0 else 0.0
    r_felt = math.exp(-elapsed / w.s_felt()) if w.last_h >= 0 else 1.0
    correct = rng.random() < max(r_felt, GUESS)
    w.seen += 1
    if not correct:
        w.box = 1
        w.n = 0
        w.last_h = now_h
        w.next_due_h = now_h + cfg.intervals_h[0]
        return
    # correct self-rating
    w.n += 1
    # Tier-0: first-ever review correct -> instant graduate
    if cfg.tier0 and w.seen == 1:
        return _graduate(w)
    # reached last box and correct -> graduate
    if w.box >= len(cfg.intervals_h):
        return _graduate(w)
    w.box += 1
    w.last_h = now_h
    w.next_due_h = now_h + cfg.intervals_h[w.box - 1]


def _graduate(w):
    w.graduated = True
    w.durable = w.s_true() >= DURABLE_DAYS * DAY


def run(cfg, profile, days, budget_per_day, pool, seed):
    rng = random.Random(seed)
    words = [make_word(i, profile, rng) for i in range(pool)]
    cards = 0
    for day in range(days):
        now0 = day * DAY + 9 * HOUR
        spent = 0
        intros = 0
        due = sorted([w for w in words if 0 < w.box and not w.graduated and w.next_due_h <= now0],
                     key=lambda w: w.next_due_h)
        for w in due:
            if spent >= budget_per_day:
                break
            review(w, now0, cfg, rng)
            spent += 1
            cards += 1
        for w in words:
            if spent >= budget_per_day or intros >= DAILY_INTRO_CAP:
                break
            if w.box != 0:
                continue
            # introduction = first exposure: establishes S0, due after box-1 interval
            w.box = 1
            w.last_h = now0
            w.next_due_h = now0 + cfg.intervals_h[0]
            intros += 1
            spent += 1
            cards += 1
    grad = sum(1 for w in words if w.graduated)
    durable = sum(1 for w in words if w.durable)
    # "leaked" = graduated but NOT durable (illusion victims that will lapse in FSRS)
    leaked = grad - durable
    return dict(cards=cards, graduated=grad, durable=durable, leaked=leaked,
                durable_per_100_cards=100.0 * durable / cards if cards else 0)


if __name__ == "__main__":
    DAYS, BUDGET, POOL, SEEDS = 60, 8, 400, 12
    profiles = ["arabic_like", "genuine_prior", "illusory_prior", "mixed_prior"]
    print(f"\n{'='*92}")
    print(f"Box tuning x prior knowledge — {DAYS}d, {BUDGET} cards/day, 1 session/day, "
          f"{SEEDS} seeds avg")
    print(f"Durable = true stability >= {DURABLE_DAYS:.0f}d at graduation. "
          f"Metric: durable graduations per 100 cards.")
    print(f"{'='*92}")
    for profile in profiles:
        print(f"\n### profile = {profile} ###")
        print(f"  {'config':20} | {'graduated':>9} {'durable':>8} {'leaked':>7} "
              f"| {'durable/100 cards':>17}")
        best = None
        for cfg in CONFIGS:
            agg = {}
            for seed in range(SEEDS):
                r = run(cfg, profile, DAYS, BUDGET, POOL, seed)
                for k, v in r.items():
                    agg[k] = agg.get(k, 0) + v
            agg = {k: v / SEEDS for k, v in agg.items()}
            tag = ""
            if best is None or agg["durable_per_100_cards"] > best[1]:
                best = (cfg.name, agg["durable_per_100_cards"])
            print(f"  {cfg.name:20} | {agg['graduated']:>9.1f} {agg['durable']:>8.1f} "
                  f"{agg['leaked']:>7.1f} | {agg['durable_per_100_cards']:>17.2f}")
        print(f"  -> most efficient: {best[0]} ({best[1]:.2f} durable/100 cards)")
