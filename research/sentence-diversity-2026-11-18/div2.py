import sqlite3, json, collections, sys
sys.path.insert(0, "/opt/alif/backend")
from app.services.sentence_validator import _is_function_word as isfw
c = sqlite3.connect("file:/opt/alif/backend/data/alif.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
print("srl cols:", [r[1] for r in q("pragma table_info(sentence_review_log)")])
L = {r["lemma_id"]: r for r in q("select l.lemma_id, lemma_ar, lemma_ar_bare b, gloss_en g, word_category wc, thematic_domain td, pos, frequency_rank fr, u.knowledge_state s, u.times_seen ts, u.fsrs_card_json j from lemmas l left join user_lemma_knowledge u on u.lemma_id=l.lemma_id")}
content = lambda l: l in L and not isfw(L[l]["b"] or "") and L[l]["wc"] not in ("proper_name", "onomatopoeia")
nm = lambda l: f"{L[l]['lemma_ar']}({(L[l]['g'] or '')[:16]})"
est = {l for l, r in L.items() if r["s"] in ("known", "learning", "lapsed") and content(l)}
def stab(l):
    try:
        j = json.loads(L[l]["j"]);  j = json.loads(j) if isinstance(j, str) else j
        return j.get("stability") or 0
    except Exception: return 0
for days in (30, 90):
    ex = collections.Counter(); cards = 0; slots = 0
    for r in q(f"select sentence_id sid, count(*) n from sentence_review_log where reviewed_at>=date('now','-{days} day') group by 1"):
        cards += r["n"]
        for w in q("select distinct lemma_id from sentence_words where sentence_id=? and lemma_id is not null", r["sid"]):
            if content(w[0]): ex[w[0]] += r["n"]; slots += r["n"]
    srt = sorted((ex[l] for l in est), reverse=True); tot = sum(srt)
    print(f"\n== TRUE on-screen exposures last {days}d: sentence views {cards}, content-word slots {slots} ({slots/max(cards,1):.1f}/view), distinct lemmas {len(ex)}")
    print("   established words never on screen:", sum(1 for l in est if not ex[l]), "of", len(est), "| seen once:", sum(1 for l in est if ex[l] == 1), "| 2-4:", sum(1 for l in est if 2 <= ex[l] <= 4), "| 5-9:", sum(1 for l in est if 5 <= ex[l] <= 9), "| 10+:", sum(1 for l in est if ex[l] >= 10))
    print("   share of slots taken by top 28/139/278 words (1/5/10%):", [round(sum(srt[:k]) / tot, 3) for k in (28, 139, 278)])
    print("   top 60:", ", ".join(f"{nm(l)}:{n}" for l, n in ex.most_common(60)))
    by = collections.Counter(); bys = collections.Counter()
    for l in est:
        t = L[l]["ts"] or 0; S = stab(l)
        by["seen<=25" if t <= 25 else "26-50" if t <= 50 else "51-100" if t <= 100 else ">100"] += ex[l]
        bys["S<30" if S < 30 else "S30-180" if S < 180 else "S180-365" if S < 365 else "S>=365"] += ex[l]
    print("   slots by lifetime seen:", {k: round(v / tot, 3) for k, v in by.items()}, "| by stability:", {k: round(v / tot, 3) for k, v in bys.items()})
inv = collections.Counter()
for l in est:
    S = stab(l); inv["S<30" if S < 30 else "S30-180" if S < 180 else "S180-365" if S < 365 else "S>=365"] += 1
print("\ninventory by stability:", dict(inv))
print("\nthematic_domain coverage:", [dict(r) for r in q("select thematic_domain td, count(*) n from lemmas group by 1 order by n desc limit 25")])
print("pos of established:", collections.Counter(L[l]["pos"] for l in est).most_common(12))
print("encountered pool (not yet learning):", [dict(r) for r in q("select u.source, count(*) n from user_lemma_knowledge u where knowledge_state='encountered' group by 1 order by n desc limit 8")])
print("lemmas with no ULK:", q("select count(*) from lemmas l left join user_lemma_knowledge u on u.lemma_id=l.lemma_id where u.lemma_id is null and l.canonical_lemma_id is null and l.gates_completed_at is not null")[0][0])
