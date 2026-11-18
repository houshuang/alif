import sqlite3, json, collections, datetime as dt, sys, statistics
sys.path.insert(0, "/opt/alif/backend")
try:
    from app.services.sentence_validator import _is_function_word as isfw
except Exception as e:
    print("no fw helper", e); isfw = lambda b: False
c = sqlite3.connect("file:/opt/alif/backend/data/alif.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
print("lemma cols:", [r[1] for r in q("pragma table_info(lemmas)")])
print("credit types 30d:", [dict(r) for r in q("select credit_type, review_mode, is_acquisition a, count(*) n from review_log where reviewed_at>=date('now','-30 day') group by 1,2,3 order by n desc")])

L = {r["lemma_id"]: r for r in q("select l.lemma_id, lemma_ar, lemma_ar_bare b, gloss_en g, pos, word_category wc, u.knowledge_state s, u.times_seen ts, u.attention_disposition d, u.fsrs_card_json j from lemmas l left join user_lemma_knowledge u on u.lemma_id=l.lemma_id")}
def content(lid):
    r = L.get(lid)
    return r and not isfw(r["b"] or "") and r["wc"] not in ("proper_name", "onomatopoeia")
def nm(lid):
    r = L[lid]; return f"{r['lemma_ar']}({(r['g'] or '')[:18]})"

est = [r for r in L.values() if r["s"] in ("known", "learning", "lapsed") and content(r["lemma_id"])]
ts = sorted((r["ts"] or 0) for r in est)
print("\n== established content words:", len(est), "times_seen pct 10/25/50/75/90/99:", [ts[int(len(ts) * p)] for p in (.1, .25, .5, .75, .9, .99)])
b = collections.Counter("<=5" if t <= 5 else "6-10" if t <= 10 else "11-25" if t <= 25 else "26-50" if t <= 50 else "51-100" if t <= 100 else "101-250" if t <= 250 else ">250" for t in ts)
print("buckets", dict(b))
print("top 50 by lifetime times_seen:", ", ".join(f"{nm(r['lemma_id'])}:{r['ts']}" for r in sorted(est, key=lambda r: -(r["ts"] or 0))[:50]))

for days in (30, 90):
    ex = collections.Counter()
    for r in q(f"select lemma_id, count(*) n from review_log where review_mode='reading' and reviewed_at>=date('now','-{days} day') group by 1"):
        if content(r["lemma_id"]): ex[r["lemma_id"]] = r["n"]
    tot = sum(ex.values()); srt = sorted(ex.values(), reverse=True)
    def share(p): k = max(1, int(len(est) * p)); return round(sum(srt[:k]) / tot, 3)
    print(f"\n== last {days}d reading word-exposures: total {tot}, distinct lemmas {len(ex)}; share taken by top 1%/5%/10%/25% of established inventory:", share(.01), share(.05), share(.10), share(.25))
    seen_ids = set(ex)
    print("   established content words with ZERO exposures:", sum(1 for r in est if r["lemma_id"] not in seen_ids), "of", len(est))
    print("   top 40:", ", ".join(f"{nm(l)}:{n}" for l, n in ex.most_common(40)))
    # exposures by lifetime-seen bucket
    bb = collections.Counter()
    for l, n in ex.items():
        t = L[l]["ts"] or 0
        bb["<=10" if t <= 10 else "11-25" if t <= 25 else "26-50" if t <= 50 else "51-100" if t <= 100 else "101-250" if t <= 250 else ">250"] += n
    print("   exposures by word's lifetime times_seen:", {k: round(v / tot, 3) for k, v in bb.items()})

print("\n== 30d exposures of established words: was the word due / how sure was it")
agg = collections.Counter()
for r in q("select lemma_id, rating, credit_type, fsrs_log_json j, reviewed_at t from review_log where review_mode='reading' and is_acquisition=0 and reviewed_at>=date('now','-30 day')"):
    if not content(r["lemma_id"]): continue
    try:
        j = json.loads(r["j"] or "{}")
        if isinstance(j, str): j = json.loads(j)
    except Exception: j = {}
    pc = j.get("pre_card") or {}
    k = "nocard"
    if pc.get("due"):
        due = dt.datetime.fromisoformat(pc["due"][:19]); t = dt.datetime.fromisoformat(r["t"][:19])
        S = pc.get("stability") or 0
        k = "due" if t >= due else ("notdue S>=90" if S >= 90 else "notdue S30-90" if S >= 30 else "notdue S<30")
    agg[k] += 1; agg[k + "|miss"] += r["rating"] <= 2
    if j.get("exposure_only") or j.get("exposure"): agg["flag_exposure_only"] += 1
print(dict(agg))

print("\n== sentence inventory (active+verified): scaffold counts per lemma")
cnt = collections.Counter(); tgt = collections.Counter()
for r in q("select w.lemma_id, w.is_target_word it, count(distinct w.sentence_id) n from sentence_words w join sentences s on s.id=w.sentence_id where s.is_active=1 and s.mappings_verified_at is not null and w.lemma_id is not null group by 1,2"):
    if not content(r["lemma_id"]): continue
    (tgt if r["it"] else cnt)[r["lemma_id"]] += r["n"]
nact = q("select count(*) from sentences where is_active=1 and mappings_verified_at is not null")[0][0]
vals = sorted(cnt.values())
print("active sentences", nact, "lemmas used as scaffold", len(cnt), "median", statistics.median(vals), "p90", vals[int(len(vals) * .9)], "avoid threshold", max(statistics.median(vals) * 1.5, 3), "n over threshold", sum(v >= max(statistics.median(vals) * 1.5, 3) for v in vals), "n>=10:", sum(v >= 10 for v in vals))
print("established content words in NO active sentence at all:", sum(1 for r in est if r["lemma_id"] not in cnt and r["lemma_id"] not in tgt), " only as target:", sum(1 for r in est if r["lemma_id"] not in cnt and r["lemma_id"] in tgt))
print("top 50 scaffold:", ", ".join(f"{nm(l)}:{n}" for l, n in cnt.most_common(50)))
tot = sum(cnt.values()); srt = sorted(cnt.values(), reverse=True)
print("share of scaffold slots by top 50/200/500 lemmas:", [round(sum(srt[:k]) / tot, 3) for k in (50, 200, 500)])

print("\n== scaffold freshness score distribution over active sentences")
sw = collections.defaultdict(list)
for r in q("select w.sentence_id sid, w.lemma_id from sentence_words w join sentences s on s.id=w.sentence_id where s.is_active=1 and s.mappings_verified_at is not null and w.lemma_id is not null"):
    if content(r["lemma_id"]): sw[r["sid"]].append(r["lemma_id"])
fr = []
for sid, lids in sw.items():
    p = 1.0
    for l in lids: p *= min(1.0, 5 / max(L[l]["ts"] or 0, 1))
    fr.append(p ** (1 / len(lids)))
fr.sort()
print("n", len(fr), "raw geo-mean pct 10/50/90:", [round(fr[int(len(fr) * p)], 3) for p in (.1, .5, .9)], "share at floor (<=0.1):", round(sum(f <= .1 for f in fr) / len(fr), 3), "share ==1:", round(sum(f >= .999 for f in fr) / len(fr), 3))

print("\n== probe words (gloss match)")
import re
probes = ["teacher", "student", "doctor", "engineer", "carpenter", "plumber", "sweeper", "farmer", "baker", "nurse", "judge", "merchant", "widow", "old woman", "yellow", "brown", "green", "low", "short", "tall", "old", "new", "narrow", "wide", "table", "chair"]
for p in probes:
    hits = [r for r in L.values() if r["g"] and re.search(rf"(^|[;,/ (]){re.escape(p)}($|[;,/ )])", r["g"].lower()) and len(r["g"]) < 40]
    hits.sort(key=lambda r: -(r["ts"] or 0))
    print(f"  {p:10}", " | ".join(f"{r['lemma_ar']} [{r['g'][:22]}] {r['s'] or 'not-in-ULK'} seen={r['ts'] or 0} scaf={cnt.get(r['lemma_id'], 0)} tgt={tgt.get(r['lemma_id'], 0)}" for r in hits[:4]) or "— no lemma in DB")

print("\n== sentence subjects: first content lemma of each active sentence, top 30")
first = collections.Counter()
for r in q("select w.sentence_id sid, w.lemma_id, min(w.position) from sentence_words w join sentences s on s.id=w.sentence_id where s.is_active=1 and s.mappings_verified_at is not null and w.lemma_id is not null and s.source not in ('book','corpus') group by 1"):
    pass
print("sources:", [dict(r) for r in q("select source, count(*) n, sum(times_shown>0) shown from sentences where is_active=1 and mappings_verified_at is not null group by 1")])
