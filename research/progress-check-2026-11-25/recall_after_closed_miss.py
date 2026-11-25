import sqlite3, json, collections, datetime as dt, statistics as st
c = sqlite3.connect("file:snap.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
P = lambda s: dt.datetime.fromisoformat(s[:19])
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
rows = c.execute("select lemma_id l, rating r, reviewed_at t, review_mode m, fsrs_log_json j from review_log where is_acquisition=0 and review_mode in ('reading','fragment') and reviewed_at>='2026-03-01' order by reviewed_at, id").fetchall()
by = collections.defaultdict(list)
for r in rows: by[r["l"]].append(r)
for minS in (30, 90):
    B = collections.defaultdict(lambda:[0,0]); n=0; ratio=[]
    for l, rs in by.items():
        for i, r in enumerate(rs):
            if r["r"] > 2: continue
            j = J(r["j"]); pc = j.get("pre_card") or {}
            S0 = pc.get("stability") or 0
            if S0 < minS or j.get("form_recovery_protected"): continue
            if i+3 >= len(rs): continue
            a, b, x = rs[i+1], rs[i+2], rs[i+3]
            if a["r"] < 3 or b["r"] < 3: continue
            if (P(b["t"]) - P(a["t"])).total_seconds() < 6*3600: continue   # two successes on separate occasions
            gap = (P(x["t"]) - P(b["t"])).total_seconds()/86400
            Sb = J(b["j"]).get("stability") or 0
            n += 1; ratio.append(Sb/S0)
            k = "a <3 d" if gap < 3 else "b 3-10 d" if gap < 10 else "c 10-30 d" if gap < 30 else "d 30-90 d" if gap < 90 else "e 90+ d"
            B[k][0] += 1; B[k][1] += x["r"] >= 3
    print(f"prior stability >= {minS} d: misses followed by two correct reviews on separate occasions, then a third review: n {n}; stability after the second success as share of pre-miss: median {st.median(ratio):.2f}")
    for k, v in sorted(B.items()): print(f"    third review after {k[2:]:8s}: {v[1]}/{v[0]} correct {v[1]/v[0]:.0%}")
# comparison: ordinary due reviews of never-recently-missed words at similar gaps
