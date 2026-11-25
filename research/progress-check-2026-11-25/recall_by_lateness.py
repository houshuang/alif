import sqlite3, json, collections, datetime as dt
c = sqlite3.connect("file:snap.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
P = lambda s: dt.datetime.fromisoformat(s[:19])
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
print([r[1] for r in c.execute("pragma table_info(sentences)")])
rows = c.execute("""select r.rating r, r.reviewed_at t, r.fsrs_log_json j, r.credit_type ct, s.created_at sc, s.source src
 from review_log r join sentences s on s.id=r.sentence_id where r.reviewed_at>='2026-09-10' and r.review_mode='reading' and r.is_acquisition=0""").fetchall()
def show(lab, keyf):
    for per, lo, hi in (("10 Sep-18 Nov","2026-09-10","2026-11-19"),("19 Nov on","2026-11-19","2027")):
        B=collections.defaultdict(lambda:[0,0])
        for r in rows:
            if not (lo<=r["t"]<hi): continue
            pc=J(r["j"]).get("pre_card") or {}
            if not pc.get("due") or (pc.get("stability") or 0) < 7: continue
            od=(P(r["t"])-P(pc["due"])).total_seconds()/86400
            if od<0: continue
            k=keyf(r,od,pc)
            if k is None: continue
            b=B[k]; b[0]+=1; b[1]+=r["r"]>=3
        print(f"  {lab} | {per}:", {k:f"{v[1]}/{v[0]} {v[1]/v[0]:.0%}" for k,v in sorted(B.items())})
print("due established words (stability >=7d), hit rate (green)")
show("days overdue", lambda r,od,pc: "a 0-7" if od<7 else "b 7-30" if od<30 else "c 30-90" if od<90 else "d 90+")
show("sentence age", lambda r,od,pc: ("new sentence (written from 18 Nov)" if (r["sc"] or "")>="2026-11-18" else "older sentence"))
show("sentence age, overdue<30d only", lambda r,od,pc: None if od>=30 else ("new sentence" if (r["sc"] or "")>="2026-11-18" else "older sentence"))
show("credit", lambda r,od,pc: r["ct"])
