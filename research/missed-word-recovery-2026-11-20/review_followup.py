import sqlite3, json, collections, sys, datetime as dt, statistics as st
sys.path.insert(0, "/opt/alif/backend")
from app.services.sentence_validator import _is_function_word as isfw
c = sqlite3.connect("file:/opt/alif/backend/data/alif.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
P = lambda s: dt.datetime.fromisoformat(s[:19])
now = dt.datetime.utcnow()
print("now", now, "| last review", q("select max(reviewed_at) m from review_log")[0]["m"])
L = {r["lemma_id"]: r for r in q("select l.lemma_id, lemma_ar a, lemma_ar_bare b, gloss_en g, word_category wc, u.knowledge_state s, u.fsrs_card_json j from lemmas l left join user_lemma_knowledge u on u.lemma_id=l.lemma_id")}
content = lambda l: l in L and not isfw(L[l]["b"] or "") and L[l]["wc"] not in ("proper_name", "onomatopoeia")
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
med = lambda v: round(st.median(v), 1) if v else None

START = "2026-10-10"
rows = q("select id, lemma_id l, rating r, reviewed_at t, response_ms ms, session_id ss, sentence_id sid, review_mode m, is_acquisition a, credit_type ct, fsrs_log_json j from review_log where reviewed_at>=? order by reviewed_at, id", START)
print("review_log rows since", START, len(rows))

# ---------- A. density and accuracy per reading card ----------
bycard = collections.defaultdict(list)
for r in rows:
    if r["m"] == "reading" and r["sid"]: bycard[(r["ss"], r["sid"])].append(r)
views = q("select session_id ss, sentence_id sid, reviewed_at t, response_ms ms, comprehension cp from sentence_review_log where review_mode='reading' and reviewed_at>=? order by reviewed_at", START)
cards = []
for v in views:
    seen = set(); work = miss = red = n = workmiss = 0
    for r in bycard.get((v["ss"], v["sid"]), []):
        if not content(r["l"]) or r["l"] in seen: continue
        seen.add(r["l"]); n += 1
        j = J(r["j"]); pc = j.get("pre_card") or {}
        isw = bool(r["a"]) or bool(pc.get("due") and P(r["t"]) >= P(pc["due"]))
        work += isw
        if r["r"] <= 2: miss += 1; workmiss += isw
        if r["r"] == 1: red += 1
    if n: cards.append(dict(t=v["t"], n=n, work=work, miss=miss, red=red, workmiss=workmiss, ms=v["ms"]))
def summ(cs, label):
    if not cs: print(label, "no cards"); return
    N = len(cs); W = sum(x["n"] for x in cs); K = sum(x["work"] for x in cs)
    print(f"{label:22s} cards {N:5d} | words/card {W/N:4.1f} | due+acq/card {K/N:4.2f} | word miss {sum(x['miss'] for x in cs)/W:5.1%} (red {sum(x['red'] for x in cs)/W:5.1%}) | miss among due+acq {sum(x['workmiss'] for x in cs)/max(K,1):5.1%} | cards all green {sum(x['miss']==0 for x in cs)/N:4.0%} | median s/card {med([x['ms']/1000 for x in cs if x['ms']])} | sec per due+acq word {round(sum(x['ms'] or 0 for x in cs)/1000/max(K,1),1)}")
print("\n== A1. per day (UTC)")
days = collections.defaultdict(list)
for x in cards: days[x["t"][:10]].append(x)
for d in sorted(days)[-12:]: summ(days[d], d)
CUT = "2026-11-19 00:00:00"
pre = [x for x in cards if "2026-10-19" <= x["t"] < "2026-11-18 12:00:00"]; post = [x for x in cards if x["t"] >= CUT]
print("\n== A2. before (19 Oct - 18 Nov noon) vs after (19 Nov on)")
summ(pre, "before"); summ(post, "after")
print("\n== A3. by number of due+acquiring words on the card")
for lab, cs in (("before", pre), ("after", post)):
    for lo, hi in ((0, 1), (2, 3), (4, 4), (5, 9)):
        summ([x for x in cs if lo <= x["work"] <= hi], f"{lab} {lo}-{hi}")
print("   distribution after:", dict(sorted(collections.Counter(min(x['work'], 7) for x in post).items())), "| before:", dict(sorted(collections.Counter(min(x['work'], 7) for x in pre).items())))

# ---------- B. what happens after a miss on an established word ----------
bylem = collections.defaultdict(list)
for r in rows: bylem[r["l"]].append(r)
def follow(rating, lo, hi, label, minstab=7.0):
    ev = []
    for l, rs in bylem.items():
        if not content(l): continue
        for i, r in enumerate(rs):
            if r["m"] != "reading" or r["r"] != rating or r["a"] or not (lo <= r["t"] < hi): continue
            j = J(r["j"]); pc = j.get("pre_card") or {}
            if j.get("form_recovery_protected"): continue
            if (pc.get("stability") or 0) < minstab: continue
            t0 = P(r["t"]); later = rs[i + 1:]
            gaps = [((P(x["t"]) - t0).total_seconds() / 3600, x) for x in later]
            e = dict(l=l, t0=t0, stab=pc.get("stability"), poststab=j.get("stability"))
            qz = [(g, x) for g, x in gaps if x["m"] == "quiz"]
            e["quiz_gap_min"] = qz[0][0] * 60 if qz else None
            e["quiz_ok"] = (qz[0][1]["r"] >= 3) if qz else None
            e["n_quiz_24h"] = sum(1 for g, x in qz if g <= 24)
            sent = [(g, x) for g, x in gaps if x["m"] != "quiz"]
            e["n_sent_24h"] = sum(1 for g, x in sent if g <= 24)
            e["n_sent_72h"] = sum(1 for g, x in sent if g <= 72)
            e["n_sent_7d"] = sum(1 for g, x in sent if g <= 168)
            e["first_sent_gap_h"] = sent[0][0] if sent else None
            e["first_sent_ok"] = (sent[0][1]["r"] >= 3) if sent else None
            dl = [(g, x) for g, x in sent if g >= 12]
            e["delayed_gap_h"] = dl[0][0] if dl else None
            e["delayed_ok"] = (dl[0][1]["r"] >= 3) if dl else None
            e["obs_h"] = (now - t0).total_seconds() / 3600
            ev.append(e)
    if not ev: print(label, "none"); return ev
    N = len(ev)
    def share(f, pool=None):
        pool = ev if pool is None else pool
        return f"{sum(1 for e in pool if f(e))}/{len(pool)}" + (f" ({sum(1 for e in pool if f(e))/len(pool):.0%})" if pool else "")
    print(f"\n-- {label}: {N} misses (rating {rating}) on words with stability >= {minstab}d before the miss; median prior stability {med([e['stab'] for e in ev])}d, median stability right after {med([e['poststab'] for e in ev if e['poststab']])}d")
    print("   word card (quick check) within 30 min:", share(lambda e: e["quiz_gap_min"] is not None and e["quiz_gap_min"] <= 30), "| within 24 h:", share(lambda e: e["quiz_gap_min"] is not None and e["quiz_gap_min"] <= 1440), "| first check correct:", share(lambda e: e["quiz_ok"], [e for e in ev if e["quiz_ok"] is not None]), "| median gap min", med([e["quiz_gap_min"] for e in ev if e["quiz_gap_min"] is not None]))
    for h, k in ((24, "n_sent_24h"), (72, "n_sent_72h"), (168, "n_sent_7d")):
        pool = [e for e in ev if e["obs_h"] >= h]
        print(f"   seen again in a sentence within {h:3d} h (only misses old enough to know):", share(lambda e: e[k] >= 1, pool), "| twice or more:", share(lambda e: e[k] >= 2, pool))
    got = [e for e in ev if e["first_sent_gap_h"] is not None]
    print("   next sentence appearance: median gap h", med([e["first_sent_gap_h"] for e in got]), "| quartiles", [round(x, 1) for x in st.quantiles([e["first_sent_gap_h"] for e in got], n=4)] if len(got) > 3 else None, "| correct then:", share(lambda e: e["first_sent_ok"], got), "| never seen again so far:", N - len(got))
    for lo_, hi_, nm in ((0, 0.5, "<30 min"), (0.5, 12, "30 min-12 h"), (12, 48, "12-48 h"), (48, 168, "2-7 d"), (168, 1e9, ">7 d")):
        pool = [e for e in got if lo_ <= e["first_sent_gap_h"] < hi_]
        if pool: print(f"      next appearance {nm:12s}: {len(pool):4d} | correct {sum(e['first_sent_ok'] for e in pool)/len(pool):.0%}")
    dl = [e for e in ev if e["delayed_gap_h"] is not None]
    print("   first sentence appearance at least 12 h later: correct", share(lambda e: e["delayed_ok"], dl), "| median gap days", med([e["delayed_gap_h"] / 24 for e in dl]))
    return ev
print("\n== B. after a miss on an established word")
b1 = follow(1, "2026-10-12", "2026-11-11", "RED before (12 Oct - 11 Nov)")
a1 = follow(1, CUT, "2099", "RED after (19 Nov on)")
b2 = follow(2, "2026-10-12", "2026-11-11", "YELLOW before")
a2 = follow(2, CUT, "2099", "YELLOW after")

# does a correct word card change the outcome later? (before period, red misses)
print("\n== B2. red misses before: outcome at the first sentence appearance >=12 h later, by what the word card did")
for nm, f in (("no word card", lambda e: e["quiz_gap_min"] is None or e["quiz_gap_min"] > 1440), ("word card correct first time", lambda e: e["quiz_gap_min"] is not None and e["quiz_gap_min"] <= 1440 and e["quiz_ok"]), ("word card missed first time", lambda e: e["quiz_gap_min"] is not None and e["quiz_gap_min"] <= 1440 and not e["quiz_ok"])):
    pool = [e for e in (b1 or []) if f(e) and e["delayed_gap_h"] is not None]
    if pool: print(f"   {nm:30s} n {len(pool):4d} | correct {sum(e['delayed_ok'] for e in pool)/len(pool):.0%} | median gap days {med([e['delayed_gap_h']/24 for e in pool])}")

# ---------- C. the word cards themselves ----------
print("\n== C. word cards (review_mode=quiz) per day")
qd = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    if r["m"] == "quiz":
        d = r["t"][:10]; qd[d]["n"] += 1; qd[d]["ok"] += r["r"] >= 3; qd[d]["acq"] += bool(r["a"])
for d in sorted(qd)[-10:]: print("  ", d, dict(qd[d]))
# what a correct word card does to an established word's schedule
print("\n== C2. established words: schedule set by the word card after a red miss (last 30 days)")
out = collections.Counter(); ivs = collections.defaultdict(list); nxt = collections.defaultdict(list)
for l, rs in bylem.items():
    if not content(l): continue
    for i, r in enumerate(rs):
        if r["m"] != "quiz" or r["a"] or r["t"] < "2026-10-20": continue
        j = J(r["j"]); pc = j.get("pre_card") or {}
        prev = [x for x in rs[:i] if x["m"] != "quiz"]
        if not prev or prev[-1]["r"] != 1: continue
        gapmin = (P(r["t"]) - P(prev[-1]["t"])).total_seconds() / 60
        key = ("correct" if r["r"] >= 3 else "missed")
        out[(key, "state after=" + str(j.get("state")), "pre_state=" + str(j.get("pre_knowledge_state")))] += 1
        # interval set: next row's pre_card, or current card
        if i + 1 < len(rs): pc2 = (J(rs[i + 1]["j"]).get("pre_card") or {})
        else: pc2 = J(L[l]["j"])
        if pc2.get("due"):
            ivs[key].append((P(pc2["due"]) - P(r["t"])).total_seconds() / 86400)
        ns = [x for x in rs[i + 1:] if x["m"] != "quiz"]
        if ns: nxt[key].append(((P(ns[0]["t"]) - P(r["t"])).total_seconds() / 86400, ns[0]["r"] >= 3, gapmin))
for k, v in sorted(out.items(), key=lambda x: -x[1]): print("  ", k, v)
for k in ivs:
    v = sorted(ivs[k]); print(f"   interval set after a {k} word card: n {len(v)} | median {med(v)} d | quartiles {[round(x,2) for x in st.quantiles(v, n=4)] if len(v)>3 else v}")
for k in nxt:
    v = nxt[k]; print(f"   next sentence after a {k} word card: n {len(v)} | median wait {med([x[0] for x in v])} d | correct {sum(x[1] for x in v)/len(v):.0%}")
    for lo_, hi_ in ((0, 1), (1, 3), (3, 7), (7, 999)):
        p = [x for x in v if lo_ <= x[0] < hi_]
        if p: print(f"        wait {lo_}-{hi_} d: n {len(p)} correct {sum(x[1] for x in p)/len(p):.0%}")

# ---------- D. where the recently missed established words stand now ----------
print("\n== D. established words missed (red) in a sentence in the last 10 days, no correct sentence review since")
st_now = collections.Counter(); over = []; wait = []
for l, rs in bylem.items():
    if not content(l): continue
    sent = [x for x in rs if x["m"] != "quiz" and not x["a"]]
    if not sent: continue
    last = sent[-1]
    if last["r"] != 1 or (now - P(last["t"])).days > 10: continue
    j = J(last["j"]); pc = j.get("pre_card") or {}
    if (pc.get("stability") or 0) < 7: continue
    card = J(L[l]["j"]); due = P(card["due"]) if card.get("due") else None
    hadq = any(x["m"] == "quiz" and x["r"] >= 3 and x["t"] > last["t"] for x in rs)
    st_now[(L[l]["s"], "word card ok" if hadq else "no ok word card", "due now" if due and due <= now else "not due yet")] += 1
    if due and due <= now: over.append((now - due).total_seconds() / 3600)
    elif due: wait.append((due - now).total_seconds() / 86400)
for k, v in sorted(st_now.items(), key=lambda x: -x[1]): print("  ", k, v)
print("   due now: n", len(over), "median hours overdue", med(over), "| not due: n", len(wait), "median days until due", med(wait), "max", round(max(wait), 1) if wait else None)
tot_due = 0
for l, r in L.items():
    if content(l) and r["s"] in ("known", "learning", "lapsed"):
        cd = J(r["j"])
        if cd.get("due") and P(cd["due"]) <= now: tot_due += 1
print("   all established words due now:", tot_due, "| in state lapsed:", sum(1 for l, r in L.items() if r["s"] == "lapsed"))
