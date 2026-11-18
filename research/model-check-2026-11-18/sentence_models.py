"""Read-only comparison of sentence-writing models on the production dense-sentence path.

Every arm gets the same due-word menus (same random seed) and goes through the
production validator, quality review and mapping verification with dry_run=True.
"""
import json, random, sys, time
sys.path.insert(0, "/opt/alif/backend")
from app.services import llm, material_generator as mg, codex_cli

ARMS = sys.argv[1].split(",")
CALLS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
OUT = "/tmp/model_exp_out.jsonl"
mg.DUE_MENU_STOCK_TARGET = 100000
state = {"arm": None, "recs": {}, "t": []}

_orig_completion = llm.generate_completion
def routed(*a, **kw):
    if kw.get("task_type") != "sentence_gen_due_menu":
        return _orig_completion(*a, **kw)
    arm = state["arm"]; t0 = time.time()
    try:
        if arm in ("sonnet", "opus"):
            return llm._generate_via_claude_cli(prompt=kw["prompt"], system_prompt=kw.get("system_prompt", ""), model=arm,
                                                json_schema=kw.get("json_schema"), timeout=600, task_type="sentence_gen_experiment")
        model, _, effort = arm.partition("@")
        return codex_cli.generate_via_codex_cli(prompt=kw["prompt"], system_prompt=kw.get("system_prompt", ""),
                                                json_schema=kw.get("json_schema"), timeout=900, model=model,
                                                reasoning_effort=effort or None)
    finally:
        state["t"].append(round(time.time() - t0))
llm.generate_completion = routed

_orig_gen = llm.generate_sentences_due_menu
def gen(*a, **kw):
    res = _orig_gen(*a, **kw)
    for r in res:
        state["recs"][r.arabic] = {"arm": state["arm"], "arabic": r.arabic, "english": r.english, "tokens": len(r.arabic.split())}
    return res
llm.generate_sentences_due_menu = gen

_orig_rev = llm.review_sentences_quality
def rev(items, *a, **kw):
    out = _orig_rev(items, *a, **kw)
    for it, r in zip(items, out):
        rec = state["recs"].get(it["arabic"])
        if rec is not None:
            rec.update(valid=True, natural=r.natural, translation_ok=r.translation_correct, reason=r.reason)
    return out
llm.review_sentences_quality = rev

_orig_val = mg.validate_multi_target_sentences_batch
def val(db, accepted, lookup):
    out = _orig_val(db, accepted, lookup)
    for mres, mappings in out:
        rec = state["recs"].get(mres.arabic)
        if rec is not None:
            rec["mapping_ok"] = True
    return out
mg.validate_multi_target_sentences_batch = val

for arm in ARMS:
    state.update(arm=arm, recs={}, t=[])
    random.seed(1118)
    t0 = time.time()
    try:
        stats = mg.generate_due_menu_material(max_calls=CALLS, dry_run=True)
    except Exception as e:
        stats = {"error": repr(e)[:300]}
    with open(OUT, "a") as f:
        for r in state["recs"].values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.write(json.dumps({"arm": arm, "summary": stats, "gen_seconds": state["t"], "total_s": round(time.time() - t0)}, ensure_ascii=False) + "\n")
    print("ARM", arm, stats, state["t"], round(time.time() - t0), flush=True)
print("DONE", flush=True)
