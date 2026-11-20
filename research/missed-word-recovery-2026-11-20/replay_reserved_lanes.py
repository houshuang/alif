import sys, collections, logging
from datetime import datetime, timezone
from app.database import SessionLocal
from app.models import UserLemmaKnowledge as U
from app.services.sentence_selector import build_session
from app.services.missed_word_recovery import open_misses
db = SessionLocal(); now = datetime.now(timezone.utc)
ulk = {u.lemma_id: u for u in db.query(U).all()}
om = open_misses(db, now)
print("open misses", len(om), "| by successes", dict(collections.Counter(m.successes for m in om.values())), "| with a missed word card", sum(1 for m in om.values() if m.word_card_misses))
seen = set(); tot = collections.Counter()
for rnd in range(4):
    s = build_session(db, limit=10, allow_intro_mutations=False, log_events=False, exclude_sentence_ids=seen)
    db.rollback(); d = s.get("selection_diagnostics") or {}
    print(f"--- batch {rnd}: cards {len(s['items'])} base {d.get('base_card_count')} repeats {d.get('acquisition_repeat_card_count')} reasons {d.get('selection_reason_counts')}")
    for it in s["items"]:
        seen.add(it.get("sentence_id")); st = collections.Counter(); miss = 0
        for w in it.get("words") or []:
            u = ulk.get(w.get("lemma_id"))
            if u and w.get("is_due"):
                st[u.knowledge_state] += 1; miss += w.get("lemma_id") in om
        r = (it.get("selection_info") or {}).get("reason")
        tot["cards"] += 1; tot["due words"] += sum(st.values()); tot["open-miss words"] += miss; tot["acquiring words"] += st["acquiring"]
        print(f"   {r:28s} due {dict(st)} open-miss words {miss}")
print("TOTAL over 4 batches", dict(tot))
