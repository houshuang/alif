import sqlite3, json, collections, datetime as dt, statistics as st
c = sqlite3.connect("file:snap.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
P = lambda s: dt.datetime.fromisoformat(s[:26])
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j
    except Exception: return {}
q = lambda s,*a: c.execute(s,a).fetchall()
print("== T. reading timing")
T = q("""select v.id, v.reviewed_at t, v.comprehension cp, v.response_ms ms, v.reading_timing_json j, v.sentence_id sid,
  (select count(*) from sentence_words w where w.sentence_id=v.sentence_id) n,
  (select length(arabic_text) from sentences s where s.id=v.sentence_id) ch
  from sentence_review_log v where reading_timing_json is not null""")
ex = collections.Counter(); el = []
for r in T:
    j = J(r["j"])
    if j.get("eligible"): el.append((r, j["elapsed_ms"]/1000))
    for e in j.get("exclusions", []): ex[e]+=1
print("cards with timing", len(T), "eligible", len(el), "understood", sum(r['cp']=='understood' for r in T), "exclusions", dict(ex))
b = collections.defaultdict(list)
for r, s in el: b[min(r["n"],12)//2*2].append(s/ r["n"])
print("eligible: seconds per token overall median", round(st.median([s/r['n'] for r,s in el]),2), "| words/min", round(60/st.median([s/r['n'] for r,s in el]),1))
for k in sorted(b): print("   tokens", k, "-", k+1, "n", len(b[k]), "median s/token", round(st.median(b[k]),2))
byday = collections.defaultdict(list)
for r,s in el: byday[r["t"][:10]].append(s/r["n"])
print("   per day:", {d:(len(v), round(st.median(v),2)) for d,v in sorted(byday.items())})
# repeat sentences
rep = collections.defaultdict(list)
for r,s in el: rep[r["sid"]].append(s)
print("   sentences timed more than once:", sum(len(v)>1 for v in rep.values()))
# does slow clean reading predict later miss?  word-level: words in eligible clean cards; link to next review of those words
print("\n== C. failure causes (word_review_evidence, protocol 3)")
ev = q("select e.*, r.fsrs_log_json fj from word_review_evidence e left join review_log r on r.id=e.review_log_id where e.created_at>='2026-10-20' and e.review_mode='reading'")
cc = collections.Counter(); bywk = collections.defaultdict(collections.Counter); prot = collections.Counter()
for e in ev:
    if not e["is_schedulable_content"]: continue
    wk = "after 19 Nov" if e["created_at"] >= "2026-11-19" else "20 Oct-18 Nov"
    bywk[wk]["tokens"] += 1
    if e["rating"] <= 2:
        causes = tuple(sorted(J(e["failure_causes_json"]) or [])) if e["failure_causes_json"] else ()
        bywk[wk][("red" if e["rating"]==1 else "yellow", causes)] += 1
        fj = J(e["fj"]) or {}
        if fj.get("form_recovery_protected"): prot[wk, "form_recovery_protected"] += 1
        if fj.get("mixed_up_total_lapse"): prot[wk, "mixed_up_total_lapse"] += 1
for wk, v in bywk.items():
    print(wk, "content tokens", v["tokens"])
    for k, n in sorted(((k,n) for k,n in v.items() if k!="tokens"), key=lambda x:-x[1]): print("    ", k, n)
print("protected / total-lapse rows:", dict(prot))
print("tashkeel visible initially among content tokens after 19 Nov:", dict(collections.Counter((e["front_initial_tashkeel_visible"], e["rating"]<=2) for e in ev if e["is_schedulable_content"] and e["created_at"]>="2026-11-19")))
print("rating_source:", dict(collections.Counter((e["rating_source"], e["rating"]) for e in ev if e["created_at"]>="2026-11-19" and e["is_schedulable_content"])))
print("form recovery ledger tables:", [r[0] for r in q("select name from sqlite_master where name like '%form%' or name like '%recovery%' or name like '%exact%'")])
