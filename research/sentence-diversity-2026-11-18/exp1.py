"""Read-only generation experiment: current multi-target prompt vs due-word menu prompt."""
import json, random, sys, datetime as dt, collections, time
sys.path.insert(0, "/opt/alif/backend")
from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge as U
from app.services import llm
from app.services.sentence_generator import group_words_for_multi_target
from app.services.sentence_validator import (
    _is_function_word as isfw, build_comprehensive_lemma_lookup, build_lemma_lookup,
    lookup_lemma_id, strip_diacritics, tokenize, validate_sentence_multi_target,
)
random.seed(1118)
OUT = "/tmp/exp1_out.jsonl"
db = SessionLocal()
now = dt.datetime.utcnow()
rows = db.query(Lemma, U).join(U, U.lemma_id == Lemma.lemma_id).all()
def card(u):
    j = u.fsrs_card_json
    try:
        j = json.loads(j) if isinstance(j, str) else j
        j = json.loads(j) if isinstance(j, str) else j
    except Exception: j = None
    return j if isinstance(j, dict) else {}
info = {}; known = []; due = []
for l, u in rows:
    if l.canonical_lemma_id or not l.gates_completed_at or not l.gloss_en: continue
    if l.word_category in ("proper_name", "onomatopoeia") or isfw(l.lemma_ar_bare or ""): continue
    if u.knowledge_state not in ("known", "learning", "lapsed", "acquiring") or u.attention_disposition != "maintain": continue
    c = card(u)
    d = {"arabic": l.lemma_ar, "english": l.gloss_en, "lemma_id": l.lemma_id, "pos": l.pos or "",
         "lemma_ar": l.lemma_ar, "gloss_en": l.gloss_en, "lemma_ar_bare": l.lemma_ar_bare, "root_id": l.root_id}
    is_due = bool(u.knowledge_state != "acquiring" and c.get("due") and dt.datetime.fromisoformat(c["due"][:19]) <= now)
    info[l.lemma_id] = {"ts": u.times_seen or 0, "S": c.get("stability") or 0, "due": is_due, "ar": l.lemma_ar, "g": l.gloss_en,
                        "due_str": c.get("due") or ""}
    known.append(d)
    if is_due: d["due_str"] = c["due"]; due.append(d)
all_lemmas = [l for l, u in rows]
known_lookup = build_lemma_lookup(all_lemmas)
comp = build_comprehensive_lemma_lookup(db)
db.close()
known_bare = set(known_lookup.keys())
print("known", len(known), "due", len(due), flush=True)

def evaluate(arm, call, sent, menu_bares):
    ar = sent.get("arabic", "")
    v = validate_sentence_multi_target(ar, menu_bares, known_bare, min_targets=1,
                                       known_lemma_lookup=known_lookup, comprehensive_lemma_lookup=comp)
    lids = []
    for t in tokenize(ar):
        if isfw(strip_diacritics(t)): continue
        lid = lookup_lemma_id(t, comp)
        if lid and lid in info and lid not in lids: lids.append(lid)
    rec = {"arm": arm, "call": call, "arabic": ar, "english": sent.get("english", ""), "valid": v.valid, "unknown": v.unknown_words,
           "tokens": len(tokenize(ar)), "content": len(lids), "due": [info[l]["ar"] for l in lids if info[l]["due"]],
           "heavy": [info[l]["ar"] for l in lids if info[l]["ts"] > 100],
           "safe": sum(1 for l in lids if not info[l]["due"] and info[l]["S"] >= 180)}
    rec["n_due"] = len(rec["due"])
    return rec

results = []
def save():
    with open(OUT, "w") as f:
        for r in results: f.write(json.dumps(r, ensure_ascii=False) + "\n")

# ── Arm A: the live path. Oldest-due words, groups of 4, 500 random known words.
seedA = sorted(due, key=lambda d: d["due_str"])[:24]
groups = group_words_for_multi_target(seedA)[:6]
for i, g in enumerate(groups):
    t0 = time.time()
    try:
        res = llm.generate_sentences_multi_target(
            target_words=[{"arabic": w["lemma_ar"], "english": w["gloss_en"]} for w in g],
            known_words=random.sample(known, 500), count=4, difficulty_hint="beginner", max_words=12)
    except Exception as e:
        print("A", i, "failed", repr(e)[:200], flush=True); continue
    bares = {(w["lemma_ar_bare"] or strip_diacritics(w["lemma_ar"])): w["lemma_id"] for w in g}
    for s in res: results.append(evaluate("A_current", i, {"arabic": s.arabic, "english": s.english}, bares))
    save(); print("A", i, len(res), round(time.time() - t0), "s", flush=True)

# ── Arms B/C: a large menu of due words; the model picks natural combinations.
SYS = llm.MULTI_TARGET_SYSTEM_PROMPT.replace(
    "- Prefer pronouns (أنا، هو، هي، نحن) and generic definite nouns (المعلم، الولد، الفتاة) as subjects\n",
    "- Take the people, things, actions and qualities in each sentence from the PRIORITY list wherever one fits\n",
).replace("Each sentence must include AT LEAST 2 of the specified target words.", "Each sentence is built mainly from the PRIORITY words.")
SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "object", "properties": {
    "arabic": {"type": "string"}, "english": {"type": "string"}, "transliteration": {"type": "string"}},
    "required": ["arabic", "english", "transliteration"]}}}, "required": ["sentences"]}
def menu_call(arm, i, min_menu, word_range, n=12):
    menu = seedA[i * 12:(i + 1) * 12] + random.sample([d for d in due if d not in seedA], 138)
    support = random.sample([k for k in known if not info[k["lemma_id"]]["due"]], 200)
    prompt = f"""Create {n} different natural MSA sentences for a beginner-to-intermediate Arabic learner.

PRIORITY WORDS (the learner needs to see these; each sentence MUST contain at least {min_menu} of them):
{llm.format_known_words_by_pos(menu)}

SUPPORTING VOCABULARY (use only when no priority word fits):
{llm.format_known_words_by_pos(support)}

IMPORTANT:
- Do NOT use any Arabic content words that are not in the two lists above (function words are fine).
- Choose combinations of priority words that belong together naturally; never force unrelated words into one sentence.
- Use each priority word in at most one sentence, so the {n} sentences cover as many different priority words as possible.
- Subjects, objects, adjectives and verbs should all come from the priority list when a fitting one exists.
- Prefer short everyday contexts over poetic or encyclopedic sentences.
Each sentence should be {word_range} words, each with a different structure.
Include full diacritics on all Arabic text."""
    t0 = time.time()
    try:
        out = llm.generate_completion(prompt=prompt, system_prompt=SYS, json_schema=SCHEMA, temperature=0.5, timeout=300,
                                      model_override="claude_sonnet", task_type="sentence_gen_experiment")
    except Exception as e:
        print(arm, i, "failed", repr(e)[:200], flush=True); return
    bares = {(w["lemma_ar_bare"] or strip_diacritics(w["lemma_ar"])): w["lemma_id"] for w in menu}
    for s in out.get("sentences", []): results.append(evaluate(arm, i, s, bares))
    save(); print(arm, i, len(out.get("sentences", [])), round(time.time() - t0), "s", flush=True)
for i in range(2): menu_call("B_menu_min3", i, 3, "7-11")
for i in range(2): menu_call("C_menu_min4", i, 4, "9-13")

# ── Quality review with the production gate.
valid = [r for r in results if r["valid"]]
for i in range(0, len(valid), 12):
    chunk = valid[i:i + 12]
    try:
        revs = llm.review_sentences_quality([{"arabic": r["arabic"], "english": r["english"]} for r in chunk])
        for r, rv in zip(chunk, revs):
            r["natural"] = rv.natural; r["translation_ok"] = rv.translation_correct; r["review_done"] = rv.review_completed; r["reason"] = rv.reason
    except Exception as e:
        print("review failed", repr(e)[:200], flush=True)
    save()
for arm in ("A_current", "B_menu_min3", "C_menu_min4"):
    rs = [r for r in results if r["arm"] == arm]; vs = [r for r in rs if r["valid"]]; ok = [r for r in vs if r.get("natural") and r.get("translation_ok")]
    if not rs: continue
    m = lambda xs, k: round(sum(k(x) for x in xs) / max(len(xs), 1), 2)
    print(f"\n{arm}: generated {len(rs)}, valid {len(vs)}, passed quality gate {len(ok)} (reviewed {sum(1 for r in vs if r.get('review_done'))})")
    print("  passed sentences: tokens", m(ok, lambda r: r["tokens"]), "content", m(ok, lambda r: r["content"]), "due/sentence", m(ok, lambda r: r["n_due"]),
          "heavy(>100 seen)/sentence", m(ok, lambda r: len(r["heavy"])), "safe(S>=180,not due)/sentence", m(ok, lambda r: r["safe"]),
          "distinct due covered", len({d for r in ok for d in r["due"]}), "due-dist", dict(sorted(collections.Counter(r["n_due"] for r in ok).items())))
    print("  heavy words used:", collections.Counter(h for r in ok for h in r["heavy"]).most_common(12))
print("DONE", flush=True)
