import sqlite3, json, collections, sys, datetime as dt, statistics as st, glob
sys.path.insert(0, "/opt/alif/backend")
from app.services.sentence_validator import _is_function_word as isfw
c = sqlite3.connect("file:/opt/alif/backend/data/alif.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
P = lambda s: dt.datetime.fromisoformat(s[:19])
L = {r["lemma_id"]: r for r in q("select l.lemma_id, lemma_ar a, lemma_ar_bare b, gloss_en g, word_category wc from lemmas l")}
content = lambda l: l in L and not isfw(L[l]["b"] or "") and L[l]["wc"] not in ("proper_name", "onomatopoeia")
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
med = lambda v: round(st.median(v), 1) if v else None
rows = q("select id, lemma_id l, rating r, reviewed_at t, session_id ss, sentence_id sid, review_mode m, is_acquisition a, fsrs_log_json j from review_log where reviewed_at>='2026-10-19' order by reviewed_at, id")

print("== 1. timeline 18-20 Nov, 10-minute blocks: sentence cards / red word misses (content) / word cards answered")
blk = collections.defaultdict(collections.Counter)
for v in q("select reviewed_at t from sentence_review_log where review_mode='reading' and reviewed_at>='2026-11-18'"):
    blk[v["t"][:15]]["cards"] += 1
for r in rows:
    if r["t"] < "2026-11-18": continue
    if r["m"] == "quiz": blk[r["t"][:15]]["wordcards"] += 1; blk[r["t"][:15]]["wc_ok"] += r["r"] >= 3
    elif r["m"] == "reading" and r["r"] == 1 and content(r["l"]): blk[r["t"][:15]]["red"] += 1
for k in sorted(blk): print("  ", k + "0", dict(blk[k]))

print("\n== 2. checkpoint cards shown, from interaction logs (18-20 Nov)")
ev = collections.Counter(); gaps = collections.defaultdict(list)
for f in sorted(glob.glob("/opt/alif/backend/data/logs/interactions_2026-11-1[6-9].jsonl") + glob.glob("/opt/alif/backend/data/logs/interactions_2026-11-20.jsonl")):
    for line in open(f):
        try: e = json.loads(line)
        except Exception: continue
        if e.get("event") == "card_shown":
            d = f[-16:-6]; ev[(d, e.get("card_type"))] += 1
            if e.get("card_type") == "checkpoint":
                g = (e.get("detail") or {}).get("gap_ms")
                if g: gaps[d].append(g / 60000)
for k in sorted(ev): print("  ", k, ev[k])
for d in sorted(gaps): print("   checkpoint gap minutes", d, "n", len(gaps[d]), "median", med(gaps[d]), "max", round(max(gaps[d])))

print("\n== 3. red misses per day vs word cards answered per day (content words, any state)")
d = collections.defaultdict(collections.Counter)
for r in rows:
    if r["m"] == "reading" and r["r"] == 1 and content(r["l"]): d[r["t"][:10]]["red"] += 1
    if r["m"] == "quiz": d[r["t"][:10]]["wordcards"] += 1
for k in sorted(d)[-14:]: print("  ", k, dict(d[k]))

print("\n== 4. miss rate of DUE established words by how overdue, before vs after (reading, non-acquisition)")
def bucket(r):
    j = J(r["j"]); pc = j.get("pre_card") or {}
    if not pc.get("due") or not pc.get("last_review") or not pc.get("stability"): return None
    due, last, t = P(pc["due"]), P(pc["last_review"]), P(r["t"])
    if t < due: return None
    iv = max((due - last).total_seconds(), 3600); late = (t - due).total_seconds() / iv
    stab = pc["stability"]
    return ("stab<7d" if stab < 7 else "stab 7-60d" if stab < 60 else "stab>60d", "late<0.5x" if late < 0.5 else "late 0.5-2x" if late < 2 else "late>2x", late, stab)
for lab, lo, hi in (("before", "2026-10-19", "2026-11-18 12"), ("after", "2026-11-19", "2099")):
    agg = collections.defaultdict(lambda: [0, 0]); lates = []; stabs = []
    for r in rows:
        if r["m"] != "reading" or r["a"] or not content(r["l"]) or not (lo <= r["t"] < hi): continue
        b = bucket(r)
        if not b: continue
        lates.append(b[2]); stabs.append(b[3])
        for key in (b[0], b[1], "ALL"):
            agg[key][0] += 1; agg[key][1] += r["r"] <= 2
    print(f"  {lab}: due established reviews {agg['ALL'][0]} | median lateness (x interval) {med(lates)} | median stability {med(stabs)} d")
    for k in ("ALL", "stab<7d", "stab 7-60d", "stab>60d", "late<0.5x", "late 0.5-2x", "late>2x"):
        n, m = agg[k]
        if n: print(f"      {k:12s} n {n:4d} miss {m/n:.0%}")

print("\n== 5. acquiring words: miss rate before vs after")
for lab, lo, hi in (("before", "2026-10-19", "2026-11-18 12"), ("after", "2026-11-19", "2099")):
    v = [r for r in rows if r["m"] == "reading" and r["a"] and content(r["l"]) and lo <= r["t"] < hi]
    if v: print(f"  {lab}: n {len(v)} miss {sum(r['r']<=2 for r in v)/len(v):.0%}")

print("\n== 6. sentences reviewed since 19 Nov: created when, how many words")
for r in q("select date(s.created_at) d, count(*) n from sentence_review_log v join sentences s on s.id=v.sentence_id where v.review_mode='reading' and v.reviewed_at>='2026-11-19' group by 1 order by 1 desc limit 8"): print("  ", r["d"], r["n"])
