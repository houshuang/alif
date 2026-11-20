import sqlite3, json, collections, datetime as dt, glob
c = sqlite3.connect("file:/opt/alif/backend/data/alif.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
now = dt.datetime.utcnow(); P = lambda s: dt.datetime.fromisoformat(s[:19])
def J(x):
    try:
        j = json.loads(x or "{}"); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
print("== acquiring now by box / due")
for r in q("select acquisition_box b, count(*) n, sum(acquisition_next_due<=datetime('now')) due from user_lemma_knowledge where knowledge_state='acquiring' group by 1"): print("  ", dict(r))
print("== acquisitions started per day")
for r in q("select date(acquisition_started_at) d, count(*) n from user_lemma_knowledge where acquisition_started_at>='2026-11-12' group by 1"): print("  ", dict(r))
print("== acquisition reviews per day (reading)")
for r in q("select date(reviewed_at) d, sum(is_acquisition) acq, count(*) n from review_log where review_mode='reading' and reviewed_at>='2026-11-14' group by 1"): print("  ", dict(r))
ev = collections.Counter()
for f in sorted(glob.glob("/opt/alif/backend/data/logs/interactions_2026-11-1[7-9].jsonl") + glob.glob("/opt/alif/backend/data/logs/interactions_2026-11-20.jsonl")):
    for line in open(f):
        try: e = json.loads(line)
        except Exception: continue
        if e.get("event") in ("auto_introduce", "session_start", "established_lapse_recovery_selected", "word_graduated", "intro_deferred", "auto_introduce_deferred"):
            ev[(f[-16:-6], e["event"])] += 1
        if e.get("event") == "session_start" and f[-16:-6] >= "2026-11-19" and not e.get("prefetch"):
            print("   session_start", e.get("ts", "")[:16], {k: e.get(k) for k in ("total_due_words", "covered_due_words", "sentence_count", "intro_budget", "recovery_level", "true_new_budget") if k in e})
for k in sorted(ev): print("  ", k, ev[k])
print("== recently missed established words that are due now: sentences available")
rows = q("select lemma_id l, rating r, reviewed_at t, review_mode m, is_acquisition a, fsrs_log_json j from review_log where reviewed_at>='2026-11-10' order by reviewed_at")
last = {}
for r in rows:
    if r["m"] != "quiz" and not r["a"]: last[r["l"]] = r
res = collections.Counter(); dens = []
duenow = set()
for r in q("select lemma_id l, fsrs_card_json j from user_lemma_knowledge where knowledge_state in ('known','learning','lapsed')"):
    cd = J(r["j"])
    if cd.get("due") and P(cd["due"]) <= now: duenow.add(r["l"])
for l, r in last.items():
    if r["r"] != 1 or l not in duenow: continue
    ss = q("select s.id, s.last_reading_shown_at sh, s.last_reading_comprehension cp, s.created_at cr from sentences s join sentence_words w on w.sentence_id=s.id where w.lemma_id=? and s.is_active=1 and s.mappings_verified_at is not null group by s.id", l)
    fresh = [s for s in ss if not s["sh"]]
    res["words"] += 1; res["with any active sentence"] += bool(ss); res["with a never-shown sentence"] += bool(fresh)
    res["never-shown sentence made since 18 Nov"] += any(s["cr"] >= "2026-11-18" for s in fresh)
print("  ", dict(res))
