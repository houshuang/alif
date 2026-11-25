import sqlite3, json, collections, datetime as dt, statistics as st
c = sqlite3.connect("file:snap.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
P = lambda s: dt.datetime.fromisoformat(s[:19])
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
q = lambda s,*a: c.execute(s,a).fetchall()
rows = q("select id, lemma_id l, rating r, reviewed_at t, review_mode m, is_acquisition a, credit_type ct, fsrs_log_json j from review_log where reviewed_at>='2026-09-01' order by reviewed_at, id")
print("== R1. red/yellow share of sentence misses on established words, by week")
wk = collections.defaultdict(collections.Counter)
for r in rows:
    if r["m"]!="reading" or r["a"]: continue
    k = P(r["t"]).strftime("%G-W%V"); wk[k]["n"]+=1; wk[k][r["r"]]+=1
for k,v in sorted(wk.items()): print("  ",k,"reviews",v["n"],"red",v[1],"yellow",v[2],"yellow share of misses",f"{v[2]/max(1,v[1]+v[2]):.0%}", "miss rate", f"{(v[1]+v[2])/v['n']:.0%}")
print("\n== R2. calibration: FSRS-predicted recall vs observed (reading, established, pre_card present), by period and overdue ratio")
def bucket(period):
    B = collections.defaultdict(lambda:[0,0,0.0])
    for r in rows:
        if r["m"]!="reading" or r["a"]: continue
        if not (period[0] <= r["t"] < period[1]): continue
        j=J(r["j"]); pc=j.get("pre_card") or {}
        if not pc.get("last_review") or not pc.get("stability"): continue
        el=(P(r["t"])-P(pc["last_review"])).total_seconds()/86400; S=pc["stability"]
        if el < 0.5: continue
        R=(1+ (19/81)*el/S)**-0.5  # approx FSRS-4.5 curve; FSRS-6 decay differs slightly
        due = pc.get("due") and P(r["t"])>=P(pc["due"])
        k = "not due yet" if not due else "due, S<7d" if S<7 else "due, S 7-30d" if S<30 else "due, S 30-120d" if S<120 else "due, S>120d"
        b=B[k]; b[0]+=1; b[1]+= r["r"]>=3; b[2]+=R
    for k in ("not due yet","due, S<7d","due, S 7-30d","due, S 30-120d","due, S>120d"):
        b=B[k]
        if b[0]: print(f"   {k:16s} n {b[0]:4d} observed {b[1]/b[0]:.0%} predicted ~{b[2]/b[0]:.0%}")
print(" 1 Oct - 18 Nov"); bucket(("2026-10-01","2026-11-19"))
print(" 19 Nov on"); bucket(("2026-11-19","2027"))
print("\n== R3. stability path after a miss on an established word (prior S>=30d), misses since 1 Sep")
by=collections.defaultdict(list)
for r in rows:
    if not r["a"]: by[r["l"]].append(r)
paths={1:[],2:[]}
for l,rs in by.items():
    for i,r in enumerate(rs):
        if r["m"]!="reading" or r["r"]>2: continue
        j=J(r["j"]); pc=j.get("pre_card") or {}
        if (pc.get("stability") or 0)<30 or j.get("form_recovery_protected"): continue
        seq=[pc["stability"], j.get("stability")]
        for x in rs[i+1:]:
            if x["r"]<=2: break
            s=J(x["j"]).get("stability")
            if s: seq.append(s)
        paths[r["r"]].append(seq)
for rt,name in ((1,"red"),(2,"yellow")):
    ps=paths[rt]; print(f"  {name}: n {len(ps)}")
    for k,lab in enumerate(["before miss","right after","after 1 success","after 2","after 3","after 4"]):
        v=[p[k] for p in ps if len(p)>k and p[k]]
        if len(v)>=5: print(f"     {lab:16s} n {len(v):3d} median stability {st.median(v):6.1f} d")
print("\n== N. stock and flow")
print("graduated per week:", [tuple(r) for r in q("select strftime('%Y-W%W', graduated_at) w, count(*) from user_lemma_knowledge where graduated_at>='2026-10-01' group by 1")])
print("acquisition started per week:", [tuple(r) for r in q("select strftime('%Y-W%W', acquisition_started_at) w, count(*), sum(coalesce(acquisition_episode_kind,'new')='leech_reintro') from user_lemma_knowledge where acquisition_started_at>='2026-10-01' group by 1")])
print("leech suspended per day:", [tuple(r) for r in q("select date(leech_suspended_at), count(*) from user_lemma_knowledge where leech_suspended_at>='2026-11-10' group by 1")])
now="2026-11-25 06:00:00"
st_rows=q("select knowledge_state s, fsrs_card_json j, acquisition_box b, acquisition_next_due d from user_lemma_knowledge where attention_disposition='maintain'")
due=0; stab=[]; box=collections.Counter(); boxdue=collections.Counter()
for r in st_rows:
    if r["s"]=="acquiring":
        box[r["b"]]+=1
        if r["d"] and r["d"]<=now: boxdue[r["b"]]+=1
    elif r["s"] in ("known","learning","lapsed"):
        j=J(r["j"])
        if j.get("due") and P(j["due"])<=P(now): due+=1
        if j.get("stability"): stab.append(j["stability"])
print("FSRS due now", due, "of", len(stab), "| stability >=30d:", sum(s>=30 for s in stab), ">=90d:", sum(s>=90 for s in stab), "<7d:", sum(s<7 for s in stab))
print("acquiring boxes", dict(box), "due", dict(boxdue))
