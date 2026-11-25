"""Reading-speed measure check for Alif. Read-only against a production snapshot.

Run:  /Users/stian/src/alif/backend/.venv/bin/python analysis.py [path-to-db]
Prints every number used in report.md.
"""
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from scipy import stats

DB = sys.argv[1] if len(sys.argv) > 1 else (
    "/private/tmp/claude-501/-Users-stian-src-alif/bece1fa3-bc6e-47bf-9bfb-0809a977e658/"
    "scratchpad/alif_work.db")
FIRM_DAYS = 30
BANDS = [(1, 500), (501, 1000), (1001, 2000)]
rng = np.random.default_rng(20261125)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓ]")


def letters(s: str) -> int:
    return len(ARABIC_LETTER.findall(s or ""))


def h(title):
    print("\n" + "=" * 100 + f"\n{title}\n" + "=" * 100)


def q(x, ps=(10, 25, 50, 75, 90)):
    return {f"p{p}": round(float(np.percentile(x, p)), 1) for p in ps}


# ---------------------------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------------------------
srl = pd.read_sql_query(
    """select id, sentence_id, session_id, reviewed_at, comprehension, response_ms, review_mode,
              client_review_id, reading_timing_json
       from sentence_review_log where reviewed_at >= '2026-08-18' and review_mode = 'reading'
       order by reviewed_at, id""", con, parse_dates=["reviewed_at"])
srl["day"] = srl.reviewed_at.dt.strftime("%Y-%m-%d")
tj = srl.reading_timing_json.map(lambda s: json.loads(s) if isinstance(s, str) else None)
srl["timed"] = tj.notna()
srl["elapsed_ms"] = tj.map(lambda t: t.get("elapsed_ms") if t else np.nan)
srl["eligible"] = tj.map(lambda t: bool(t.get("eligible")) if t else False)
srl["exclusions"] = tj.map(lambda t: t.get("exclusions", []) if t else [])

sw = pd.read_sql_query("select id as sw_id, sentence_id, position, surface_form, lemma_id from sentence_words", con)
sw["letters"] = sw.surface_form.map(letters)
sent = sw.groupby("sentence_id").agg(n_words=("sw_id", "count"), n_letters=("letters", "sum"))
srl = srl.join(sent, on="sentence_id")

ev = pd.read_sql_query(
    """select client_review_id, sentence_word_id, position, lemma_id, canonical_lemma_id, rating,
              front_initial_tashkeel_visible as tash, front_toggle_count, failure_causes_json,
              is_schedulable_content as content, is_function_word as fn, is_proper_name as pn,
              surface_form
       from word_review_evidence where protocol_version = 3 and review_mode = 'reading'""", con)
ev["letters"] = ev.surface_form.map(letters)
ev["has_cause"] = ev.failure_causes_json.map(lambda s: isinstance(s, str) and s not in ("[]", "null", ""))

lem = pd.read_sql_query("select lemma_id, lemma_ar, lemma_ar_bare, pos, gloss_en, canonical_lemma_id from lemmas", con)
lem["lem_letters"] = lem.lemma_ar_bare.map(letters)
ulk = pd.read_sql_query("select lemma_id, knowledge_state, fsrs_card_json, times_seen, times_correct from user_lemma_knowledge", con)
ulk["stability"] = ulk.fsrs_card_json.map(lambda s: (json.loads(s) or {}).get("stability") if isinstance(s, str) else None).astype(float)
ulk["firm"] = (ulk.knowledge_state == "known") & (ulk.stability >= FIRM_DAYS)
bf = pd.read_sql_query("select rank, lemma_id from book_frequency_entries", con)
rank_of = bf.dropna(subset=["lemma_id"]).groupby("lemma_id")["rank"].min()

# ---------------------------------------------------------------------------------------------
# 1. What exists
# ---------------------------------------------------------------------------------------------
h("1. WHAT EXISTS")
timed = srl[srl.timed].copy()
el = srl[srl.eligible].copy()
print("first timed review:", timed.reviewed_at.min(), "| last:", timed.reviewed_at.max())
print("reading reviews 2026-11-03..snapshot:", int((srl.day >= "2026-11-03").sum()),
      "| with timing object:", len(timed), "| eligible:", len(el))
since = srl[srl.day >= timed.day.min()]
per_day = since.groupby("day").agg(reading_reviews=("id", "count"), timed=("timed", "sum"),
                                   eligible=("eligible", "sum"))
per_day["eligible_share"] = (per_day.eligible / per_day.timed).round(2)
print(per_day)
n_days_cal = (pd.Timestamp(timed.day.max()) - pd.Timestamp(timed.day.min())).days + 1
last_day_partial = timed.day.max()
full_days = per_day.drop(index=last_day_partial)
print(f"calendar days with timing: {n_days_cal} (last day {last_day_partial} is a partial morning)")
print(f"eligible per day, all {n_days_cal} days: {len(el)/n_days_cal:.1f}; "
      f"full days only: {full_days.eligible.mean():.1f}")
RATE = len(el) / n_days_cal

print("\nExclusion reasons among timed, not eligible (a review can carry several):")
ne = timed[~timed.eligible]
c = Counter(r for rs in ne.exclusions for r in rs)
for r, n in c.most_common():
    print(f"  {r:32s} {n:4d}  {n/len(ne):5.0%} of excluded, {n/len(timed):5.0%} of timed")
MARK = {"marked_word", "assistance", "assistance_or_unknown"}
only_mark = sum(1 for rs in ne.exclusions if set(rs) <= MARK)
interrupted = sum(1 for rs in ne.exclusions if set(rs) - MARK)
print(f"  excluded only for marks/help: {only_mark}; with an interruption/clock/manual reason: {interrupted}")
und = timed[timed.comprehension == "understood"]
print(f"  timed and 'understood': {len(und)}; of those eligible: {int(und.eligible.sum())}; "
      f"lost to other reasons: {Counter(r for rs in und[~und.eligible].exclusions for r in rs)}")
print("  comprehension of timed:", dict(timed.comprehension.value_counts()))

el["ms_per_word"] = el.elapsed_ms / el.n_words
el["ms_per_letter"] = el.elapsed_ms / el.n_letters
print("\nEligible reviews: distributions")
print("  seconds per sentence :", {k: round(v / 1000, 1) for k, v in q(el.elapsed_ms).items()},
      "min", el.elapsed_ms.min() / 1000, "max", el.elapsed_ms.max() / 1000)
print("  running words        :", q(el.n_words), "| letters:", q(el.n_letters))
print("  ms per running word  :", q(el.ms_per_word))
print("  ms per letter        :", q(el.ms_per_letter))
print("  words per minute     :", q(60000 / el.ms_per_word))
print("  distinct sentences among eligible:", el.sentence_id.nunique(), "of", len(el))
print("  elapsed_ms vs legacy response_ms (eligible): pearson r =",
      round(el.elapsed_ms.corr(el.response_ms), 3),
      "| median response_ms - elapsed_ms =", (el.response_ms - el.elapsed_ms).median())
print("  ms per word, all timed non-eligible 'partial/no_idea' for contrast:",
      q(ne.elapsed_ms.dropna() / ne.loc[ne.elapsed_ms.notna(), "n_words"]))

# ---------------------------------------------------------------------------------------------
# Baseline model: expected time for a sentence of a given length
# ---------------------------------------------------------------------------------------------
h("BASELINE: sentence time as a function of length (eligible only)")


def ols(X, y):
    X1 = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    pred = X1 @ beta
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    return beta, pred, r2


for name, X in [("words", el[["n_words"]].values), ("letters", el[["n_letters"]].values),
                ("words+letters", el[["n_words", "n_letters"]].values)]:
    beta, pred, r2 = ols(X, el.elapsed_ms.values)
    print(f"  linear ms ~ {name:14s}: intercept {beta[0]:8.0f} ms, slopes {np.round(beta[1:], 0)}, R2 {r2:.2f}")
beta_l, pred_l, r2_l = ols(np.log(el[["n_letters"]].values), np.log(el.elapsed_ms.values))
print(f"  log ms ~ log letters: exponent {beta_l[1]:.2f}, R2 {r2_l:.2f}")
BETA, _, _ = ols(el[["n_letters"]].values, el.elapsed_ms.values)


def expected_ms(n_letters, beta=BETA):
    return beta[0] + beta[1] * n_letters


raw_ratio = el.elapsed_ms / expected_ms(el.n_letters)
PACE_NORM = float(raw_ratio.median())  # so that pace 1.0 = the learner's own median clean sentence
el["pace"] = raw_ratio / PACE_NORM
el["logpace"] = np.log(el.pace)
print("  pace ratio (actual / expected for its length):", q(el.pace), "| SD of log pace:", round(el.logpace.std(), 3))

# other things that move sentence time
prior = srl.groupby("sentence_id").cumcount()
srl["prior_reviews_of_sentence"] = prior
el = el.join(srl.set_index("id")[["prior_reviews_of_sentence"]], on="id")
allprior = pd.read_sql_query("select sentence_id, count(*) n from sentence_review_log group by 1", con).set_index("sentence_id").n
el["first_time_sentence"] = el.apply(
    lambda r: int(con.execute("select count(*) from sentence_review_log where sentence_id=? and reviewed_at<?",
                              (int(r.sentence_id), str(r.reviewed_at))).fetchone()[0] == 0), axis=1)
srl["pos_in_session"] = srl.groupby("session_id").cumcount()
el = el.join(srl.set_index("id")[["pos_in_session"]], on="id")
tash = ev.groupby("client_review_id").tash.mean()
el = el.join(tash.rename("tash_share"), on="client_review_id")
print("  first-ever showing of the sentence:", dict(el.first_time_sentence.value_counts()),
      "| median pace first-time vs repeat:",
      el.groupby("first_time_sentence").pace.median().round(2).to_dict())
el["tash_shown"] = el.tash_share >= 0.5
print("  eligible cards with tashkeel shown on the front:", el.tash_shown.value_counts().to_dict(),
      "| median pace:", el.groupby("tash_shown").pace.median().round(2).to_dict())
print("  pace vs position in session: spearman", np.round(stats.spearmanr(el.pos_in_session, el.pace), 3),
      "| first card of a session median pace:",
      round(el[el.pos_in_session == 0].pace.median(), 2) if (el.pos_in_session == 0).any() else "n/a",
      "n", int((el.pos_in_session == 0).sum()))
print("  pace by day:", el.groupby("day").pace.median().round(2).to_dict())
kw = stats.kruskal(*[d.logpace.values for _, d in el.groupby("day") if len(d) >= 5])
day_r2 = 1 - (el.logpace - el.groupby("day").logpace.transform("mean")).var() / el.logpace.var()
print(f"  day differences: Kruskal-Wallis p={kw.pvalue:.3f}; share of log-pace variance between days {day_r2:.2f}")
t0, t1 = el[el.tash_share < .5].logpace, el[el.tash_share >= .5].logpace
print(f"  tashkeel shown vs hidden: Mann-Whitney p={stats.mannwhitneyu(t0, t1).pvalue:.3f} "
      f"(hidden n={len(t0)}, shown n={len(t1)})")

# ---------------------------------------------------------------------------------------------
# 2. Per-word measures from eligible sentences
# ---------------------------------------------------------------------------------------------
h("2a. PER-LEMMA MEDIAN over eligible sentences")
evc = ev[(ev.content == 1) & (ev.fn == 0) & (ev.pn == 0)]
tok = evc.merge(el[["client_review_id", "id", "sentence_id", "reviewed_at", "ms_per_word", "pace", "logpace",
                    "n_words"]], on="client_review_id")
# one row per (review, lemma)
app = tok.drop_duplicates(["id", "canonical_lemma_id"]).sort_values("reviewed_at")
n_app = app.groupby("canonical_lemma_id").agg(n=("id", "count"), n_sent=("sentence_id", "nunique"))
print("content-word tokens in eligible reviews:", len(tok), "| distinct lemmas:", len(n_app))
for k in (1, 2, 3, 5):
    print(f"  lemmas with >= {k} eligible appearances: {(n_app.n >= k).sum()}  "
          f"(in >= {k} distinct sentences: {(n_app.n_sent >= k).sum()})")
print("  content lemmas per eligible sentence:", q(app.groupby("id").size()))


def per_lemma(df, col, min_n):
    g = df.groupby("canonical_lemma_id")[col]
    out = g.median()[g.size() >= min_n]
    return out


def split_half(df, col, min_each=1, reps=200):
    """Random split-half correlation of per-lemma medians, averaged over random splits."""
    df = df[["canonical_lemma_id", col]].copy()
    cnt = df.groupby("canonical_lemma_id")[col].transform("size")
    df = df[cnt >= 2 * min_each]
    n_lem = df.canonical_lemma_id.nunique()
    if n_lem < 8:
        return n_lem, np.nan, np.nan
    rs = []
    for _ in range(reps):
        d = df.sample(frac=1, random_state=int(rng.integers(1 << 31)))
        half = d.groupby("canonical_lemma_id").cumcount() % 2
        a = d[half == 0].groupby("canonical_lemma_id")[col].median()
        b = d[half == 1].groupby("canonical_lemma_id")[col].median()
        j = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
        rs.append(stats.spearmanr(j.a, j.b)[0])
    r = float(np.nanmean(rs))
    return n_lem, r, 2 * r / (1 + r)


for col in ("ms_per_word", "pace"):
    for me in (1, 2):
        n, r, sb = split_half(app, col, me)
        print(f"  split-half ({col}, >= {2*me} appearances): lemmas {n}, mean spearman r {r:.2f}, "
              f"Spearman-Brown full-length {sb:.2f}")

meta = lem.set_index("lemma_id").join(ulk.set_index("lemma_id")).join(rank_of.rename("book_rank"))
for min_n in (1, 3):
    m = pd.concat([per_lemma(app, "ms_per_word", min_n).rename("ms_per_word"),
                   per_lemma(app, "pace", min_n).rename("pace")], axis=1).join(meta)
    print(f"  validity, lemmas with >= {min_n} appearances (n={len(m)}), Spearman rho (p):")
    for col in ("ms_per_word", "pace"):
        parts = []
        for v in ("stability", "times_seen", "lem_letters", "book_rank"):
            j = m[[col, v]].dropna()
            rho, p = stats.spearmanr(j[col], j[v]) if len(j) > 5 else (np.nan, np.nan)
            parts.append(f"{v} {rho:+.2f} (p={p:.2f}, n={len(j)})")
        print(f"    {col:12s}: " + "; ".join(parts))

h("2b. REGRESSION / ATTRIBUTION on eligible data")
n_obs, n_par = el.id.nunique(), app.canonical_lemma_id.nunique()
print(f"observations (eligible sentences): {n_obs}; per-lemma parameters wanted: {n_par}; "
      f"lemmas appearing in >= 3 sentences: {(n_app.n_sent >= 3).sum()}")


def ridge_cv(app_df, sent_df, ycol="logpace", min_n=3, lams=(1, 3, 10, 30, 100), folds=5, seed=1):
    """Sentence log-pace ~ sum of per-lemma effects (ridge). Returns CV R2 against a zero-effect model."""
    keep = app_df.groupby("canonical_lemma_id").id.nunique()
    keep = keep[keep >= min_n].index
    a = app_df[app_df.canonical_lemma_id.isin(keep)]
    sid = {s: i for i, s in enumerate(sent_df.id.values)}
    lid = {l: i for i, l in enumerate(keep)}
    X = np.zeros((len(sid), len(lid)))
    for r, l in zip(a.id.values, a.canonical_lemma_id.values):
        X[sid[r], lid[l]] = 1
    y = sent_df[ycol].values - sent_df[ycol].values.mean()
    idx = np.random.default_rng(seed).permutation(len(y)) % folds
    res = {}
    for lam in lams:
        sse = 0.0
        for f in range(folds):
            tr, te = idx != f, idx == f
            b = np.linalg.solve(X[tr].T @ X[tr] + lam * np.eye(X.shape[1]), X[tr].T @ (y[tr] - y[tr].mean()))
            sse += ((y[te] - y[tr].mean() - X[te] @ b) ** 2).sum()
        res[lam] = 1 - sse / (y ** 2).sum()
    return len(lid), res


n_l, res = ridge_cv(app, el)
print(f"ridge on eligible data, {n_l} lemma effects: 5-fold CV R2 by penalty "
      f"{ {k: round(v, 3) for k, v in res.items()} }  (<= 0 means no better than length alone)")

# ---------------------------------------------------------------------------------------------
# Larger stand-in sample: legacy response_ms on clean reviews (NOT a clean clock)
# ---------------------------------------------------------------------------------------------
h("STAND-IN SAMPLE: legacy response_ms on clean reviews since 2026-08-18 (volume test only)")
g = ev.groupby("client_review_id").agg(n_ev=("rating", "size"), min_rating=("rating", "min"),
                                       toggles=("front_toggle_count", "sum"), causes=("has_cause", "sum"))
px = srl.join(g, on="client_review_id")
px = px[(px.comprehension == "understood") & (px.n_ev == px.n_words) & (px.min_rating == 3)
        & (px.toggles == 0) & (px.causes == 0) & px.response_ms.notna()]
# passage children share one response time within a session: drop them
dup = px.groupby(["session_id", "response_ms"]).id.transform("size")
px = px[dup == 1]
print("clean-by-marks reading reviews with response_ms:", len(px))
px = px[(px.response_ms >= 1500) & (px.response_ms <= 120000) & (px.n_words >= 3)].copy()
print("after dropping <1.5 s, >120 s and <3-word rows:", len(px), "| days:", px.day.nunique(),
      "| first", px.day.min(), "last", px.day.max())
bpx, _, r2px = ols(px[["n_letters"]].values, px.response_ms.values)
px["pace"] = px.response_ms / (bpx[0] + bpx[1] * px.n_letters)
px["pace"] = px.pace / px.pace.median()
px["logpace"] = np.log(px.pace)
# remove slow drift and good/bad days: subtract the median log pace of the calendar week
px["week"] = px.reviewed_at.dt.strftime("%G-%V")
px["pace_dt"] = np.exp(px.logpace - px.groupby("week").logpace.transform("median"))
print("  share of stand-in log-pace variance between calendar weeks:",
      round(1 - np.log(px.pace_dt).var() / px.logpace.var(), 2))
px["ms_per_word"] = px.response_ms / px.n_words
print(f"length model: intercept {bpx[0]:.0f} ms + {bpx[1]:.0f} ms/letter, R2 {r2px:.2f}; SD log pace {px.logpace.std():.3f}")
both = srl[srl.timed & srl.elapsed_ms.notna() & srl.response_ms.notna()]
print("all timed rows: spearman(elapsed_ms, response_ms) =",
      round(stats.spearmanr(both.elapsed_ms, both.response_ms)[0], 3), "n", len(both))
overlap = px[px.day >= timed.day.min()]
print(f"stand-in rows on timing days: {len(overlap)} vs eligible {len(el)} "
      f"(eligible / clean-by-marks = {len(el)/max(len(overlap),1):.2f})")

ptok = evc.merge(px[["client_review_id", "id", "sentence_id", "reviewed_at", "ms_per_word", "pace", "pace_dt", "logpace"]],
                 on="client_review_id")
papp = ptok.drop_duplicates(["id", "canonical_lemma_id"]).sort_values("reviewed_at")
pn = papp.groupby("canonical_lemma_id").id.count()
print("distinct lemmas:", len(pn), "| appearances per lemma:", q(pn), "| lemmas with >=6:", (pn >= 6).sum(),
      ">=10:", (pn >= 10).sum(), ">=20:", (pn >= 20).sum())
print("split-half reliability of the per-lemma median pace, by appearances per half:")
for me in (1, 2, 3, 5, 8, 12):
    n, r, sb = split_half(papp, "pace", me, reps=100)
    print(f"  >= {me:2d} per half (>= {2*me:2d} total): lemmas {n:4d}, half-vs-half r {r:+.2f}, full-length {sb:+.2f}")
print("same, exactly k per half (subsample k*2 appearances per lemma) to get reliability at fixed evidence:")
REL = {}
for col in ("pace", "pace_dt"):
    for k in (1, 3, 5):
        rs = []
        elig = pn[pn >= 2 * k].index
        sub = papp[papp.canonical_lemma_id.isin(elig)]
        for _ in range(100):
            d = sub.sample(frac=1, random_state=int(rng.integers(1 << 31)))
            d = d[d.groupby("canonical_lemma_id").cumcount() < 2 * k]
            half = d.groupby("canonical_lemma_id").cumcount() % 2
            a = np.log(d[half == 0].groupby("canonical_lemma_id")[col].median())
            b = np.log(d[half == 1].groupby("canonical_lemma_id")[col].median())
            rs.append(stats.pearsonr(a, b.reindex(a.index))[0])
        REL[(col, k)] = float(np.mean(rs))
        print(f"  {col:8s} k={k} appearances per estimate: lemmas {len(elig):4d}, reliability r {np.mean(rs):+.3f}")
# word-level variance component implied by the single-appearance reliability (week-detrended)
r1 = max(REL[("pace_dt", 1)], 0)
var_word = r1 * np.log(px.pace_dt).var()
sd_clean = el.logpace.std()
r1_clean = var_word / (var_word + sd_clean ** 2) if var_word > 0 else 0
print(f"  implied between-word SD of log pace: {np.sqrt(var_word):.3f} "
      f"(about {np.sqrt(var_word)*el.elapsed_ms.median()/1000:.1f} s on a median clean sentence)")
print(f"  projected reliability on the CLEAN clock (sentence SD {sd_clean:.3f}), Spearman-Brown from one appearance r={r1_clean:.3f}:")
print("   ", {k: round(k * r1_clean / (1 + (k - 1) * r1_clean), 2) for k in (1, 3, 5, 10, 20, 30, 50)})
print("    chance that a fast/not-fast label (split at the median) repeats on fresh data of the same size (50% = coin):",
      {k: round(0.5 + np.arcsin(k * r1_clean / (1 + (k - 1) * r1_clean)) / np.pi, 2) for k in (3, 5, 10, 20, 50)})
print(f"    SD of log pace after removing day means: {(el.logpace - el.groupby('day').logpace.transform('mean')).std():.3f}")

n_l, res = ridge_cv(papp, px, min_n=5, lams=(3, 10, 30, 100, 300))
print(f"ridge attribution on stand-in ({n_l} lemmas with >=5 sentences, {len(px)} sentences): CV R2 "
      f"{ {k: round(v, 3) for k, v in res.items()} }")

m = pd.concat([per_lemma(papp, "pace_dt", 5).rename("pace"), pn.rename("n")], axis=1).dropna().join(meta)
print(f"validity on stand-in, lemmas with >= 5 appearances (n={len(m)}), Spearman rho (p):")
for v in ("stability", "times_seen", "lem_letters", "book_rank"):
    j = m[["pace", v]].dropna()
    rho, p = stats.spearmanr(j.pace, j[v])
    print(f"  pace vs {v:12s} {rho:+.3f} (p={p:.3f}, n={len(j)})")

# Were words that were later missed slower beforehand?
h("LATER-MISSED CHECK")
allev = pd.read_sql_query(
    """select e.canonical_lemma_id, e.rating, e.client_review_id, l.reviewed_at, l.id
       from word_review_evidence e join sentence_review_log l on l.client_review_id = e.client_review_id
       where e.protocol_version = 3 and e.review_mode='reading' and e.is_schedulable_content = 1
         and e.is_function_word = 0 and e.is_proper_name = 0""", con, parse_dates=["reviewed_at"])
allev = allev.groupby(["id", "canonical_lemma_id"]).agg(rating=("rating", "min"), reviewed_at=("reviewed_at", "first")).reset_index()
allev = allev.sort_values(["canonical_lemma_id", "reviewed_at"])
allev["next_rating"] = allev.groupby("canonical_lemma_id").rating.shift(-1)


def later_missed(appdf, label):
    j = appdf.merge(allev[["id", "canonical_lemma_id", "next_rating"]], on=["id", "canonical_lemma_id"]).dropna(subset=["next_rating"])
    miss, hit = j[j.next_rating < 3], j[j.next_rating >= 3]
    if len(miss) < 5:
        print(f"{label}: only {len(miss)} clean appearances followed by a miss (of {len(j)} with a later review); no test possible")
        return
    u = stats.mannwhitneyu(miss.pace, hit.pace, alternative="greater")
    print(f"{label}: appearances with a later review {len(j)}; next was a miss {len(miss)} "
          f"(median pace {miss.pace.median():.3f}), next was correct {len(hit)} (median pace {hit.pace.median():.3f}); "
          f"one-sided Mann-Whitney p={u.pvalue:.3f}")
    # sentence-clustered bootstrap of the difference in median
    ids = j.id.unique()
    diffs = []
    for _ in range(500):
        s = pd.DataFrame({"id": rng.choice(ids, len(ids))}).merge(j, on="id")
        diffs.append(s[s.next_rating < 3].pace.median() - s[s.next_rating >= 3].pace.median())
    print(f"   difference in median pace {np.median(diffs):+.3f}, 95% sentence-bootstrap interval "
          f"[{np.percentile(diffs, 2.5):+.3f}, {np.percentile(diffs, 97.5):+.3f}]")


later_missed(app, "eligible timings")
later_missed(papp.assign(pace=papp.pace_dt), "stand-in response_ms (week-detrended)")

# ---------------------------------------------------------------------------------------------
# 3. Coverage per book-frequency band
# ---------------------------------------------------------------------------------------------
h("3. COVERAGE PER BAND")
# any token (incl. function words) in an eligible review counts as an appearance of its lemma and canonical
etok = ev.merge(el[["client_review_id", "id", "sentence_id", "pace"]], on="client_review_id")
long = pd.concat([etok[["id", "sentence_id", "pace", "lemma_id"]],
                  etok[["id", "sentence_id", "pace", "canonical_lemma_id"]].rename(columns={"canonical_lemma_id": "lemma_id"})])
long = long.drop_duplicates(["id", "lemma_id"])
cnt_el = long.groupby("lemma_id").id.count()


def band_table(counts, label):
    rows = []
    for lo, hi in BANDS:
        b = bf[(bf["rank"] >= lo) & (bf["rank"] <= hi)]
        inapp = b.dropna(subset=["lemma_id"])
        c = inapp.lemma_id.map(counts).fillna(0)
        firm = inapp.lemma_id.map(ulk.set_index("lemma_id").firm).fillna(False).astype(bool)
        rows.append({"band": f"{lo}-{hi}", "list": len(b), "in_app": len(inapp), "firm": int(firm.sum()),
                     ">=1": int((c >= 1).sum()), ">=3": int((c >= 3).sum()), ">=5": int((c >= 5).sum()),
                     "firm&>=3": int(((c >= 3) & firm).sum())})
    print(label)
    print(pd.DataFrame(rows).to_string(index=False))


band_table(cnt_el, f"eligible timed appearances so far ({len(el)} reviews over {n_days_cal} days):")

# Projection: replay the app's own clean reviews (stand-in for what eligible reviews will contain),
# thinned to the observed eligible rate, and see how long it takes for band words to reach >= 3.
allclean = srl.join(g, on="client_review_id")
allclean = allclean[(allclean.comprehension == "understood") & (allclean.n_ev == allclean.n_words)
                    & (allclean.min_rating == 3) & (allclean.toggles == 0) & (allclean.causes == 0)]
ctok = ev.merge(allclean[["client_review_id", "id", "day"]], on="client_review_id")
clong = pd.concat([ctok[["id", "day", "lemma_id"]],
                   ctok[["id", "day", "canonical_lemma_id"]].rename(columns={"canonical_lemma_id": "lemma_id"})]).drop_duplicates(["id", "lemma_id"])
recent = allclean[allclean.day >= "2026-11-16"]
ratio = len(el) / max((allclean.day >= timed.day.min()).sum(), 1)
print(f"\nclean-by-marks reading reviews: last 10 days (since 11-16) {len(recent)}, "
      f"since timing began {(allclean.day >= timed.day.min()).sum()}; eligible/clean ratio {ratio:.2f}")
print(f"eligible rate used for projection: {RATE:.1f} per calendar day")

band_ids = {f"{lo}-{hi}": set(bf[(bf['rank'] >= lo) & (bf['rank'] <= hi)].lemma_id.dropna().astype(int)) for lo, hi in BANDS}
firm_ids = set(ulk[ulk.firm].lemma_id)


# Replay: walk back through the learner's own "understood" reading reviews (the kind of review that
# becomes an eligible timing) and ask how many of them were needed before band words had >= 3.
hist = pd.read_sql_query(
    """select l.id, l.reviewed_at, l.sentence_id from sentence_review_log l
       where l.review_mode='reading' and l.comprehension='understood' order by l.reviewed_at desc""", con)
canon = lem.set_index("lemma_id").canonical_lemma_id
sw_sets = sw.dropna(subset=["lemma_id"]).assign(lemma_id=lambda d: d.lemma_id.astype(int)).groupby("sentence_id").lemma_id.apply(
    lambda x: set(x) | {int(canon.get(i)) for i in x if pd.notna(canon.get(i))})
hist_days = (pd.Timestamp(hist.reviewed_at.max()) - pd.Timestamp(hist.reviewed_at.min())).days
print(f"understood reading reviews in history: {len(hist)} over {hist_days} days "
      f"({len(hist)/hist_days:.1f} per calendar day all-time)")
for label, d0 in (("last 30 days", "2026-10-26"), ("last 90 days", "2026-08-27")):
    n = (hist.reviewed_at >= d0).sum()
    days = (pd.Timestamp("2026-11-25") - pd.Timestamp(d0)).days
    print(f"  understood reviews {label}: {n} = {n/days:.1f} per calendar day; x eligible/understood ratio "
          f"{len(el)/len(und):.2f} = {n/days*len(el)/len(und):.1f} eligible per day")
RATE_LOW = (hist.reviewed_at >= "2026-08-27").sum() / 90 * len(el) / len(und)
horizons = (14, 30, 60, 90, 180, 365)


def replay(rate, label):
    counts = Counter()
    rows = {b: {} for b in band_ids}
    rowsf = {b: {} for b in band_ids}
    done = 0
    for hz in horizons:
        n = int(round(rate * hz))
        if n > len(hist):
            for b in band_ids:
                rows[b][f"{hz}d"] = rowsf[b][f"{hz}d"] = "n/a"
            continue
        for sid in hist.sentence_id.values[done:n]:
            counts.update(sw_sets.get(sid, ()))
        done = n
        for b, ids in band_ids.items():
            f = ids & firm_ids
            rows[b][f"{hz}d"] = f"{sum(1 for l in ids if counts.get(l, 0) >= 3) / len(ids):.0%}"
            rowsf[b][f"{hz}d"] = f"{sum(1 for l in f if counts.get(l, 0) >= 3) / len(f):.0%}"
    print(f"{label}: share of band words with >= 3 appearances after N days")
    print(pd.DataFrame([{"band": b, "of": "in-app words", **rows[b]} for b in band_ids]
                       + [{"band": b, "of": "today's firm words", **rowsf[b]} for b in band_ids]).to_string(index=False))


replay(RATE, f"Replay at {RATE:.1f} eligible/day (the 5 days observed)")
replay(RATE_LOW, f"Replay at {RATE_LOW:.1f} eligible/day (90-day average activity)")
# how concentrated are appearances?
for lo, hi in BANDS:
    ids = band_ids[f"{lo}-{hi}"]
    c90 = clong[(clong.day >= "2026-08-27") & clong.lemma_id.isin(ids)].groupby("lemma_id").id.count()
    print(f"band {lo}-{hi}: in the last 90 days of clean reviews, {len(c90)} of {len(ids)} in-app words appeared at all; "
          f">=3 times: {(c90 >= 3).sum()}")

# ---------------------------------------------------------------------------------------------
# 4. Proposed definition and today's counts
# ---------------------------------------------------------------------------------------------
h("4. PROPOSED 'FAST' DEFINITION — today's counts")
print(f"baseline today: expected_ms = {BETA[0]:.0f} + {BETA[1]:.0f} * letters (fit on {len(el)} eligible reviews)")
FAST_PACE, MIN_SENT = 1.0, 5
long2 = long.copy()
long2["fluent"] = long2.pace <= FAST_PACE
agg = long2.groupby("lemma_id").agg(n_sent=("sentence_id", "nunique"),
                                    fluent_sent=("sentence_id", lambda s: s[long2.loc[s.index, "fluent"]].nunique()),
                                    med_pace=("pace", "median"))
agg["fast"] = (agg.fluent_sent >= MIN_SENT) & (agg.med_pace <= FAST_PACE)
agg["fast2"] = (agg.n_sent >= 3) & (agg.med_pace <= FAST_PACE)
print(f"lemmas (any kind) with >= {MIN_SENT} distinct eligible sentences: {(agg.n_sent >= MIN_SENT).sum()}; "
      f"meeting the definition: {agg.fast.sum()}")
rows = []
ulkf = ulk.set_index("lemma_id").firm
for lo, hi in BANDS:
    b = bf[(bf["rank"] >= lo) & (bf["rank"] <= hi)]
    inapp = b.dropna(subset=["lemma_id"]).lemma_id.astype(int)
    firm = inapp.map(ulkf).fillna(False).astype(bool)
    a = agg.reindex(inapp.values)
    ev3 = (a.n_sent >= MIN_SENT).fillna(False).values
    fast = a.fast.fillna(False).astype(bool).values
    fast2 = a.fast2.fillna(False).astype(bool).values
    ev3r = (a.n_sent >= 3).fillna(False).values
    rows.append({"band": f"{lo}-{hi}", "list": len(b), "in_app": len(inapp), "firm": int(firm.sum()),
                 "enough timing (>=5 sent.)": int(ev3.sum()), "firm, enough timing": int((ev3 & firm.values).sum()),
                 "fast": int(fast.sum()), "firm and fast": int((fast & firm.values).sum()),
                 "firm, measured, not fast": int((ev3 & firm.values & ~fast).sum()),
                 "relaxed to >=3 sentences: measured": int(ev3r.sum()),
                 "relaxed: firm and measured": int((ev3r & firm.values).sum()),
                 "relaxed: firm and fast": int((fast2 & firm.values).sum())})
print(pd.DataFrame(rows).T.to_string(header=False))

# chance level of the label: permute sentence paces among eligible sentences
perm = []
sid_pace = long2.drop_duplicates("id").set_index("id").pace
for _ in range(300):
    p = pd.Series(rng.permutation(sid_pace.values), index=sid_pace.index)
    l3 = long2.assign(pace=long2.id.map(p))
    l3["fluent"] = l3.pace <= FAST_PACE
    ns = l3.groupby("lemma_id").sentence_id.nunique()
    md = l3.groupby("lemma_id").pace.median()
    perm.append(int(((ns >= 3) & (md <= FAST_PACE)).sum()))
print(f"lemmas labelled fast if sentence paces are shuffled at random (no word effect at all): "
      f"mean {np.mean(perm):.1f}, 95% range [{np.percentile(perm, 2.5):.0f}, {np.percentile(perm, 97.5):.0f}] "
      f"vs observed {int(agg.fast2.sum())} (relaxed >=3-sentence rule, all lemmas)")

# noise budget: how many appearances for a word-level claim
sd = el.logpace.std()
print(f"\nnoise budget: SD of log pace between eligible sentences = {sd:.3f}  "
      f"(a typical sentence is {np.exp(sd)-1:.0%} above or below its expected time)")
med_w = el.n_words.median()
med_ms = el.elapsed_ms.median()
for extra in (1000, 2000, 4000):
    eff = np.log((med_ms + extra) / med_ms)
    n_needed = (2 * sd / eff) ** 2
    print(f"  a word that costs {extra/1000:.0f} s extra in a median sentence ({med_ms/1000:.0f} s) shifts log pace by "
          f"{eff:.3f}; appearances needed for the word's mean to stand 2 SE clear of baseline: {n_needed:.0f}")

# ---------------------------------------------------------------------------------------------
# 5. Inputs for capture/selection suggestions
# ---------------------------------------------------------------------------------------------
h("5. NUMBERS BEHIND THE SUGGESTIONS")
print("SD of log pace by sentence length (eligible):",
      el.groupby(pd.cut(el.n_words, [0, 6, 8, 10, 30])).logpace.agg(["count", "std"]).round(3).to_dict("index"))
print("stand-in: SD of log pace by sentence length:",
      px.groupby(pd.cut(px.n_words, [2, 4, 6, 8, 10, 14, 40])).logpace.agg(["count", "std"]).round(3).to_dict("index"))
print("timed reviews per number of marked (rating<3) content tokens:")
mk = ev[ev.client_review_id.isin(timed.client_review_id)].groupby("client_review_id").apply(
    lambda d: int((d.rating < 3).sum()), include_groups=False)
print("  ", mk.value_counts().sort_index().to_dict())
one = timed.join(mk.rename("n_marked"), on="client_review_id")
interrupt = one.exclusions.map(lambda rs: bool(set(rs) - MARK))
print(f"   timed reviews with marks but no interruption (clock itself valid): {int(((one.n_marked > 0) & ~interrupt).sum())}; "
      f"with exactly one marked word and a valid clock: {int(((one.n_marked == 1) & ~interrupt).sum())}")
frag = pd.read_sql_query("select review_mode, count(*) n, sum(response_ms is not null) with_ms from review_log "
                         "where reviewed_at >= '2026-11-21' group by 1", con)
print("review_log rows since 2026-11-21 by mode (fragment/quiz are single-word or phrase cards):")
print(frag.to_string(index=False))
fr = pd.read_sql_query("select response_ms from review_log where review_mode in ('fragment','quiz') and response_ms is not null "
                       "and reviewed_at >= '2026-11-01'", con)
if len(fr):
    print("  response_ms on fragment/quiz cards since 11-01:", q(fr.response_ms), "n", len(fr))
words_per_el = long.groupby("id").lemma_id.nunique()
for lo, hi in BANDS:
    ids = band_ids[f"{lo}-{hi}"]
    per = long[long.lemma_id.isin(ids)].groupby("id").lemma_id.nunique().reindex(el.id).fillna(0)
    print(f"band {lo}-{hi}: band words per eligible sentence, mean {per.mean():.2f}")
top = cnt_el.sort_values(ascending=False).head(12)
print("most-timed lemmas (appearances):", [(lem.set_index('lemma_id').lemma_ar_bare.get(i), int(n)) for i, n in top.items()])

# direct word-level latency that already exists: word cards (review_mode='quiz')
qz = pd.read_sql_query("select lemma_id, rating, response_ms, reviewed_at from review_log where review_mode='quiz' "
                       "and response_ms is not null and response_ms between 200 and 30000 order by reviewed_at", con)
print(f"word cards (quiz) with response_ms: {len(qz)} on {qz.lemma_id.nunique()} lemmas since {qz.reviewed_at.min()[:10]}; "
      f"median ms correct {qz[qz.rating>=3].response_ms.median():.0f}, missed {qz[qz.rating<3].response_ms.median():.0f}")
qc = qz[qz.rating >= 3].copy()
qc["lr"] = np.log(qc.response_ms)
qc["k"] = qc.groupby("lemma_id").cumcount()
w = qc[qc.k < 2].pivot(index="lemma_id", columns="k", values="lr").dropna()
print(f"  word cards: first vs second correct answer on the same word, n={len(w)} words, pearson r = "
      f"{stats.pearsonr(w[0], w[1])[0]:.2f} (reliability of ONE word-card timing)")
qb = qz[qz.lemma_id.isin(set().union(*band_ids.values()))]
print(f"  word cards on band 1-2000 words: {len(qb)} on {qb.lemma_id.nunique()} lemmas")
