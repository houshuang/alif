import sqlite3, json, hashlib, collections, datetime as dt, statistics as st
c = sqlite3.connect("file:snap.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
P = lambda s: dt.datetime.fromisoformat(s)
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
def arm(l, t):
    k = f"fragment_followup_v1:{l}:{t.isoformat()}"
    return "phrase" if hashlib.sha256(k.encode()).digest()[0] % 2 == 0 else "sentence"
T0 = P("2026-11-20 09:40:00")
rows = c.execute("""select id, lemma_id l, rating r, reviewed_at t, response_ms ms, review_mode m, is_acquisition a, fsrs_log_json j, session_id ss, sentence_id sid
  from review_log where reviewed_at>='2026-10-21' and review_mode in ('reading','fragment','quiz') order by reviewed_at, id""").fetchall()
card_ms = {(r["session_id"], r["sentence_id"]): r["response_ms"] for r in c.execute("select session_id, sentence_id, response_ms from sentence_review_log where reviewed_at>='2026-11-19'")}
by = collections.defaultdict(list)
for r in rows:
    if r["a"]: continue
    by[r["l"]].append(r)
units = []
chk = collections.Counter()
for l, rs in by.items():
    seq = [r for r in rs if r["m"] != "quiz"]
    for i, r in enumerate(seq):
        j = J(r["j"])
        if r["r"] <= 2 and not j.get("form_recovery_protected"):
            m_t = P(r["t"]); a = arm(l, m_t)
            later = seq[i+1:]
            if not later: 
                if m_t >= T0 or True: units.append(dict(l=l, arm=a, t=m_t, f1=None, e=None)); 
                continue
            f1 = later[0]
            if P(f1["t"]) < T0: continue
            e = next((x for x in later[1:] if x["m"] == "reading"), None)
            wc = [x for x in rs if x["m"]=="quiz" and r["t"] < x["t"] < f1["t"]]
            units.append(dict(l=l, arm=a, t=m_t, f1=f1, e=e, wc=wc, miss_r=r["r"]))
            if f1["m"] == "fragment": chk[a] += 1
print("validation: fragment-mode first follow-ups by hashed arm (should all be phrase):", dict(chk))
EXCL = {3096, 3199}
for a in ("phrase", "sentence"):
    U = [u for u in units if u["arm"] == a and u["l"] not in EXCL]
    F = [u for u in U if u["f1"]]
    E = [u for u in F if u["e"]]
    print(f"\n== arm {a}: misses {len(U)} | with first follow-up {len(F)} | with endpoint (later full sentence) {len(E)}")
    if not F: continue
    print("   first follow-up was a phrase card:", sum(u['f1']['m']=='fragment' for u in F), "/", len(F))
    print("   first follow-up correct:", sum(u['f1']['r']>=3 for u in F), "/", len(F), f"{sum(u['f1']['r']>=3 for u in F)/len(F):.0%}")
    for mode in ("fragment","reading"):
        X=[u for u in F if u['f1']['m']==mode]
        if X: print(f"     of which {mode}: correct {sum(u['f1']['r']>=3 for u in X)}/{len(X)}")
    gap = [(P(u['f1']['t'])-u['t']).total_seconds()/3600 for u in F]
    print("   hours miss -> first follow-up: median", round(st.median(gap),1), "quartiles", [round(x,1) for x in st.quantiles(gap, n=4)])
    if E:
        ok = sum(u['e']['r']>=3 for u in E)
        print(f"   PRIMARY endpoint correct: {ok}/{len(E)} = {ok/len(E):.0%}")
        g2 = [(P(u['e']['t'])-P(u['f1']['t'])).total_seconds()/3600 for u in E]
        print("   hours follow-up -> endpoint: median", round(st.median(g2),1), "quartiles", [round(x,1) for x in st.quantiles(g2, n=4)])
        # endpoint by follow-up result
        for res in (True, False):
            X=[u for u in E if (u['f1']['r']>=3)==res]
            if X: print(f"     endpoint after follow-up {'hit' if res else 'miss'}: {sum(u['e']['r']>=3 for u in X)}/{len(X)}")
        # restrict endpoint gap >= 12h
        X=[u for u in E if (P(u['e']['t'])-P(u['f1']['t'])).total_seconds()>=12*3600]
        if X: print(f"     endpoint >=12 h after follow-up: {sum(u['e']['r']>=3 for u in X)}/{len(X)}")
        # per-protocol in phrase arm
        for mode in ("fragment","reading"):
            X=[u for u in E if u['f1']['m']==mode]
            if X: print(f"     endpoint where follow-up was {mode}: {sum(u['e']['r']>=3 for u in X)}/{len(X)}")
    # time cost
    fr = [u['f1']['ms']/1000 for u in F if u['f1']['m']=='fragment' and u['f1']['ms'] and P(u['f1']['t'])>P('2026-11-20 12:00:00')]
    se = [card_ms.get((u['f1']['ss'], u['f1']['sid']))/1000 for u in F if u['f1']['m']=='reading' and card_ms.get((u['f1']['ss'], u['f1']['sid']))]
    if fr: print("   seconds per phrase follow-up: n", len(fr), "median", round(st.median(fr),1), "quartiles", [round(x,1) for x in st.quantiles(fr,n=4)])
    if se: print("   seconds per sentence card carrying the follow-up: n", len(se), "median", round(st.median(se),1))
# distinct words
print("\nphrase stock:", dict(c.execute("select count(*) n, count(distinct lemma_id) w, sum(times_shown=0) unshown from word_fragments").fetchone()))
print("fragment reviews per day:", [tuple(r) for r in c.execute("select date(reviewed_at), count(*), sum(rating>=3) from review_log where review_mode='fragment' group by 1")])
# sentence-card due words carried, for time-per-due-word comparison
