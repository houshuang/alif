import sqlite3, statistics as st, collections
c = sqlite3.connect("file:/opt/alif/backend/data/alif.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
v = [r["ms"]/1000 for r in q("select response_ms ms from review_log where review_mode='quiz' and reviewed_at>='2026-10-01' and response_ms>0")]
print("word cards: n", len(v), "median s", st.median(v) if v else None, "quartiles", [round(x,1) for x in st.quantiles(v, n=4)] if len(v) > 3 else None)
b = collections.defaultdict(list)
for r in q("select v.response_ms ms, v.comprehension cp, (select count(*) from sentence_words w where w.sentence_id=v.sentence_id) n from sentence_review_log v where v.review_mode='reading' and v.reviewed_at>='2026-08-01' and v.response_ms>0"):
    k = "2-3" if r["n"] <= 3 else "4-5" if r["n"] <= 5 else "6-8" if r["n"] <= 8 else "9+"
    b[k].append((r["ms"]/1000, r["cp"]))
for k in ("2-3", "4-5", "6-8", "9+"):
    x = b[k]
    if x: print(f"sentence cards with {k} tokens: n {len(x)} median s {st.median([a for a,_ in x]):.1f} understood {sum(cp=='understood' for _,cp in x)/len(x):.0%}")
print("active sentences by token count:", [dict(r) for r in q("select case when n<=3 then '2-3' when n<=5 then '4-5' when n<=8 then '6-8' else '9+' end k, count(*) c from (select s.id, count(*) n from sentences s join sentence_words w on w.sentence_id=s.id where s.is_active=1 group by s.id) group by 1")])
print("days with reviews last 30d and red+yellow misses on established words per active day:", [dict(r) for r in q("select count(distinct date(reviewed_at)) days, sum(rating<=2 and is_acquisition=0) misses from review_log where review_mode='reading' and reviewed_at>=date('now','-30 day')")])
