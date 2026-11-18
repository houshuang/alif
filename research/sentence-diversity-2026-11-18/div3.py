import sqlite3, json, collections, sys, datetime as dt, re
sys.path.insert(0, "/opt/alif/backend")
from app.services.sentence_validator import _is_function_word as isfw, strip_diacritics
c = sqlite3.connect("file:/opt/alif/backend/data/alif.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
now = dt.datetime.utcnow()
L = {r["lemma_id"]: r for r in q("select l.lemma_id, lemma_ar, lemma_ar_bare b, gloss_en g, word_category wc, canonical_lemma_id cl, u.knowledge_state s, u.times_seen ts, u.fsrs_card_json j, u.attention_disposition d, u.acquisition_next_due nd from lemmas l left join user_lemma_knowledge u on u.lemma_id=l.lemma_id")}
content = lambda l: l in L and not isfw(L[l]["b"] or "") and L[l]["wc"] not in ("proper_name", "onomatopoeia")
def card(l):
    try:
        j = json.loads(L[l]["j"]); j = json.loads(j) if isinstance(j, str) else j
        return j if isinstance(j, dict) else {}
    except Exception: return {}
for days in (30, 90):
    tot = collections.Counter(); percard = collections.Counter()
    logs = collections.defaultdict(list)
    for r in q(f"select session_id ss, sentence_id sid, lemma_id, is_acquisition a, fsrs_log_json j, reviewed_at t, rating from review_log where review_mode='reading' and reviewed_at>=date('now','-{days} day')"):
        logs[(r["ss"], r["sid"])].append(r)
    for v in q(f"select session_id ss, sentence_id sid from sentence_review_log where review_mode='reading' and reviewed_at>=date('now','-{days} day')"):
        slots = [w[0] for w in q("select distinct lemma_id from sentence_words where sentence_id=? and lemma_id is not null", v["sid"]) if content(w[0])]
        work = 0; seen = set()
        for r in logs.get((v["ss"], v["sid"]), []):
            if not content(r["lemma_id"]) or r["lemma_id"] in seen: continue
            seen.add(r["lemma_id"])
            if r["a"]: tot["acquiring"] += 1; work += 1; continue
            try:
                j = json.loads(r["j"] or "{}"); j = json.loads(j) if isinstance(j, str) else j
            except Exception: j = {}
            pc = j.get("pre_card") or {}
            if pc.get("due") and dt.datetime.fromisoformat(r["t"][:19]) >= dt.datetime.fromisoformat(pc["due"][:19]): tot["due"] += 1; work += 1
            elif r["rating"] <= 2: tot["notdue_but_missed"] += 1
            else: tot["notdue_logged"] += 1
        tot["slots"] += len(slots); tot["unlogged"] += max(0, len(slots) - len(seen)); tot["views"] += 1
        percard[min(work, 5)] += 1
    print(f"== last {days}d reading:", dict(tot), "| due+acquiring per view:", round((tot['due'] + tot['acquiring']) / tot['views'], 2), "| share of slots doing scheduled work:", round((tot['due'] + tot['acquiring']) / tot['slots'], 3))
    print("   views by # of due/acquiring words on the card:", dict(sorted(percard.items())))

due = set()
for l, r in L.items():
    if not content(l) or r["d"] != "maintain": continue
    if r["s"] in ("known", "learning", "lapsed"):
        j = card(l)
        if j.get("due") and dt.datetime.fromisoformat(j["due"][:19]) <= now: due.add(l)
    elif r["s"] == "acquiring" and r["nd"] and dt.datetime.fromisoformat(r["nd"][:19]) <= now: due.add(l)
print("\n== NOW: due content words (maintain):", len(due))
sw = collections.defaultdict(set)
for r in q("select w.sentence_id sid, w.lemma_id from sentence_words w join sentences s on s.id=w.sentence_id where s.is_active=1 and s.mappings_verified_at is not null and s.source in ('llm','book','corpus') and w.lemma_id is not null"):
    if content(r["lemma_id"]): sw[r["sid"]].add(r["lemma_id"])
dc = collections.Counter(min(len(v & due), 6) for v in sw.values())
print("single-sentence inventory", len(sw), "by # currently-due words:", dict(sorted(dc.items())), "| due words with no sentence:", len(due - set().union(*sw.values())))
rem = set(due); picked = []; pool = dict(sw)
for i in range(120):
    sid = max(pool, key=lambda s: len(pool[s] & rem), default=None)
    if sid is None or not pool[sid] & rem: break
    picked.append(len(pool[sid] & rem)); rem -= pool[sid]; del pool[sid]
print("greedy best-case cover with existing sentences: first 40 cards cover", sum(picked[:40]), "due words; first 120 cover", sum(picked), "| per-card curve:", picked[:10], "...", picked[35:40], "...", picked[-5:])

print("\n== basic-vocabulary checklist against the lemma table")
chk = {"colours": "أحمر أزرق أخضر أصفر أسود أبيض بني رمادي برتقالي وردي بنفسجي ذهبي فضي",
 "trades": "نجار سباك حداد خباز جزار فلاح مزارع صياد خياط حلاق طباخ سائق بناء كناس ممرض مهندس محام قاض شرطي جندي تاجر بائع راع بحار طيار كاتب رسام مغن ممثل صحفي مترجم عامل خادم حارس ساعي كهربائي ميكانيكي صيدلي محاسب نادل بواب حمال إسكافي نساج",
 "people": "عجوز أرملة يتيم شيخ شاب فتى عروس عريس جار ضيف غريب أعمى أصم أعرج فقير غني بخيل كريم توأم حفيد عم خال صهر",
 "dimension adjectives": "طويل قصير عال منخفض واسع ضيق عريض رفيع سميك عميق ضحل ثقيل خفيف سمين نحيف ناعم خشن حاد لين صلب رطب جاف مبلل نظيف وسخ قذر فارغ ممتلئ مستقيم مائل مستدير مربع حلو مر حامض مالح بارد حار دافئ ساخن",
 "household": "طاولة كرسي سرير خزانة رف وسادة بطانية سجادة ستارة مرآة مصباح شمعة مفتاح قفل سلم مطرقة مسمار منشار إبرة خيط مقص سكين ملعقة شوكة صحن كوب إبريق قدر مكنسة سطل حبل كيس صندوق سلة",
 "animals": "كلب قط حصان حمار بقرة خروف ماعز جمل دجاجة ديك بطة أرنب فأر ذئب ثعلب أسد دب قرد فيل غزال نسر غراب حمامة عصفور نحلة ذبابة بعوضة نملة عنكبوت ثعبان ضفدع سمكة سلحفاة"}
bare = collections.defaultdict(list)
for l, r in L.items():
    if r["b"]: bare[re.sub("[أإآ]", "ا", r["b"])].append(l)
for cat, words in chk.items():
    out = collections.defaultdict(list)
    for w in words.split():
        ids = bare.get(re.sub("[أإآ]", "ا", w), []) + bare.get(re.sub("[أإآ]", "ا", w) + "ة", []) * 0
        if not ids: out["MISSING from DB"].append(w); continue
        best = max(ids, key=lambda l: (L[l]["s"] in ("known", "learning", "lapsed", "acquiring"), L[l]["ts"] or 0))
        s = L[best]["s"]
        if s in ("known", "learning", "lapsed", "acquiring"):
            t = L[best]["ts"] or 0
            out["learned, seen<=20" if t <= 20 else "learned, seen 21-60" if t <= 60 else "learned, seen>60"].append(f"{w}:{t}")
        else: out[f"in DB, not learned ({s})"].append(w)
    print(f"-- {cat} ({len(words.split())})")
    for k, v in out.items(): print(f"   {k} [{len(v)}]: {' '.join(v)}")
