import sqlite3, json, collections, datetime as dt, statistics as st
c = sqlite3.connect("file:snap.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
P = lambda s: dt.datetime.fromisoformat(s[:19])
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
q = lambda s,*a: c.execute(s,a).fetchall()
rows = q("select id, lemma_id l, rating r, reviewed_at t, session_id ss, sentence_id sid, is_acquisition a, fsrs_log_json j from review_log where reviewed_at>='2026-10-01' and review_mode='reading' and sentence_id is not null order by id")
cards = collections.defaultdict(list)
for r in rows: cards[(r["ss"], r["sid"])].append(r)
ntok = {r[0]: r[1] for r in q("select sentence_id, count(*) from sentence_words group by 1")}
print("== D. established due words with stability >=30d: hit rate by how many due/acquiring words share the card")
for lab, lo, hi in (("1 Oct-18 Nov","2026-10-01","2026-11-19"),("19 Nov on","2026-11-19","2027")):
    B = collections.defaultdict(lambda:[0,0]); L = collections.defaultdict(lambda:[0,0])
    for k, rs in cards.items():
        if not (lo <= rs[0]["t"] < hi): continue
        info=[]
        for r in rs:
            pc=J(r["j"]).get("pre_card") or {}
            due = bool(r["a"]) or bool(pc.get("due") and P(r["t"])>=P(pc["due"]))
            info.append((r, due, pc.get("stability") or 0))
        nd = sum(d for _,d,_ in info)
        for r,d,S in info:
            if d and not r["a"] and S>=30:
                b=B["1-2" if nd<=2 else "3" if nd==3 else "4" if nd==4 else "5-6"]; b[0]+=1; b[1]+=r["r"]>=3
                n=ntok.get(r["sid"],0); b=L["<=6 tokens" if n<=6 else "7-9" if n<=9 else "10+"]; b[0]+=1; b[1]+=r["r"]>=3
    print(" ",lab, {k:f"{v[1]}/{v[0]} {v[1]/v[0]:.0%}" for k,v in sorted(B.items())}, "| by sentence length", {k:f"{v[1]}/{v[0]} {v[1]/v[0]:.0%}" for k,v in sorted(L.items())})
print("\n== A. acquisition reviews since 20 Nov")
print([tuple(r) for r in q("select date(reviewed_at), count(*), sum(rating>=3), sum(rating=2), sum(rating=1) from review_log where is_acquisition=1 and review_mode='reading' and reviewed_at>='2026-11-16' group by 1")])
b1 = q("select u.lemma_id, u.times_seen ts, u.times_correct tc, u.acquisition_started_at s, u.source src, u.acquisition_next_due d, u.acquisition_box b, (select count(*) from review_log r where r.lemma_id=u.lemma_id and r.reviewed_at>='2026-11-20') rv, (select count(*) from sentences s2 where s2.target_lemma_id=u.lemma_id and s2.is_active=1) ns from user_lemma_knowledge u where knowledge_state='acquiring' and attention_disposition='maintain'")
print("acquiring words:", len(b1), "| by source", dict(collections.Counter(r["src"] for r in b1)))
print("  started:", dict(sorted(collections.Counter(r["s"][:10] if r["s"] else None for r in b1).items(), key=lambda x:str(x[0]))))
print("  reviews since 20 Nov per word:", dict(sorted(collections.Counter(min(r["rv"],5) for r in b1).items())))
print("  never reviewed (times_seen=0):", sum((r["ts"] or 0)==0 for r in b1), "| with zero active target sentences:", sum(r["ns"]==0 for r in b1))
print("  times_seen/correct of box1:", sorted(((r["ts"],r["tc"]) for r in b1 if r["b"]==1), reverse=True)[:25])
print("\nopen misses now & intake events: auto-intro per day")
