#!/usr/bin/env python3
"""Rebuild candidates.tsv from book_derived.py's outputs with (a) a marker rule instead of the
CAMeL guess for tokens whose stem also spells a finite verb form, (b) merged active/passive
labels where the two participles share one unvocalised spelling, (c) a hamza-mismatch flag.
Read-only on the DB. Writes candidates.tsv (final), candidates_raw.tsv (first pass), book_summary2.json."""
import csv, json, os, sys, shutil
from collections import Counter, defaultdict
from pathlib import Path
os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
BACKEND = Path("/Users/stian/src/alif/backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "scripts"))
from app.database import SessionLocal, engine
from app.models import Lemma
from app.services.sentence_validator import (has_tanwin_alif, normalize_alef, strip_diacritics,
    strip_tatweel, strip_punctuation, _NOMINAL_ONLY_PREFIXES)
from reading_readiness import norm_bare
OUT = Path(__file__).resolve().parent
assert "alif_work.db" in str(engine.url)
db = SessionLocal()
lemmas = {l.lemma_id: l for l in db.query(Lemma).all()}
fk = lambda v: normalize_alef(strip_diacritics(v))
raw = OUT / "candidates_raw.tsv"
if not raw.exists():
    shutil.copy(OUT / "candidates.tsv", raw)
rows = list(csv.DictReader(raw.open(encoding="utf-8"), delimiter="\t"))
cache = json.load((OUT / "token_cache.json").open(encoding="utf-8"))
summ = json.load((OUT / "book_summary.json").open(encoding="utf-8"))
TANWIN = set("ًٌٍ")

def marked(tok, key):
    bare = norm_bare(tok)
    i = bare.find(key)
    if i < 0 and key.endswith("ة"):
        i = bare.find(key[:-1] + "ت")
    if i < 0:
        return False
    pre = bare[:i]
    return (pre.endswith("ال") or pre in {"لل", "ولل", "فلل"} or pre in _NOMINAL_ONLY_PREFIXES
            or has_tanwin_alif(tok) or bool(set(tok) & TANWIN))

def exact(s):
    return strip_punctuation(strip_tatweel(strip_diacritics(s))).replace("ٱ", "ا")

agg = defaultdict(lambda: Counter())
for tok, r in cache.items():
    if "derived_key" not in r:
        continue
    a = agg[(r["lemma_id"], r["derived_key"])]
    n = r["n"]
    if r["finite_too"]:
        a["homograph"] += n
        if marked(tok, r["derived_key"]):
            a["homograph_marked"] += n
    else:
        a["unamb"] += n
    a["all"] += n
    a["tok_exact::" + exact(tok)] += n

out = []
for r in rows:
    vid = int(r["verb_lemma_id"]); key = r["derived_key"]
    f = lemmas[vid].forms_json or {}
    types = [k for k in ("masdar", "active_participle", "passive_participle")
             if isinstance(f.get(k), str) and f[k].strip() and fk(f[k]) == key]
    ftype = "+".join(types)
    voc = " / ".join(dict.fromkeys(f[k] for k in types))
    a = agg.get((vid, key), Counter())
    form_exacts = {exact(f[k]) for k in types}
    hamza_ok = sum(n for k, n in a.items() if k.startswith("tok_exact::")
                   and any(fe in k[11:] or (fe.endswith("ة") and fe[:-1] + "ت" in k[11:]) for fe in form_exacts))
    mn = a["unamb"] + a["homograph_marked"]
    out.append({
        "derived_form": voc, "form_type": ftype,
        "verb_lemma_id": vid, "verb_ar": r["verb_ar"], "verb_gloss": r["verb_gloss"],
        "book_tokens_min": mn, "book_tokens_max": a["all"],
        "tokens_unambiguous": a["unamb"],
        "tokens_stem_also_a_finite_verb_form": a["homograph"],
        "of_those_with_article_prep_or_tanwin": a["homograph_marked"],
        "tokens_whose_hamza_spelling_differs_from_form": a["all"] - hamza_ok,
        "extra_tokens_reaching_verb_only_via_wide_camel": r["extra_tokens_wide_resolver_only"],
        "separate_lemma_exists": r["separate_lemma"],
        "separate_variant_row": r["separate_lemma_is_variant_row"],
        "lookup_key_owner_lemma_id": r["lookup_key_owner"], "lookup_key_source": r["lookup_key_source"],
        "verb_knowledge_state": r["verb_knowledge_state"], "verb_fsrs_stability_days": r["verb_fsrs_stability_days"],
        "verb_book_rank": r["verb_book_rank"], "verb_book_count": r["verb_book_count"],
        "top_surfaces": r["top_surfaces"], "resolver_layers": r["layers"],
    })
out.sort(key=lambda r: (-r["book_tokens_min"], -r["book_tokens_max"], -int(r["extra_tokens_reaching_verb_only_via_wide_camel"] or 0)))
with (OUT / "candidates.tsv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0].keys()), delimiter="\t"); w.writeheader(); w.writerows(out)

nosep = [r for r in out if not r["separate_lemma_exists"]]
def cnt(rs, col, th, extra=None):
    return sum(1 for r in rs if r[col] + (int(r[extra] or 0) if extra else 0) >= th)
by_type_tok = defaultdict(Counter)
for r in out:
    t = r["form_type"]
    by_type_tok[t]["min"] += r["book_tokens_min"]; by_type_tok[t]["max"] += r["book_tokens_max"]
    by_type_tok[t]["unamb"] += r["tokens_unambiguous"]; by_type_tok[t]["wide_extra"] += int(r["extra_tokens_reaching_verb_only_via_wide_camel"] or 0)
tot_min = sum(r["book_tokens_min"] for r in out); tot_max = sum(r["book_tokens_max"] for r in out)
clean = [r for r in nosep if r["tokens_whose_hamza_spelling_differs_from_form"] * 2 <= max(r["book_tokens_max"], 1)]
s2 = {
    "content_tokens": summ["tokens_content"], "all_tokens": summ["tokens_total"],
    "tokens_linked": summ["tokens_linked_to_lemma"], "tokens_on_verbs": summ["tokens_on_verb_lemmas"],
    "derived_tokens_min": tot_min, "derived_tokens_max": tot_max,
    "share_content_min_pct": round(100 * tot_min / summ["tokens_content"], 2),
    "share_content_max_pct": round(100 * tot_max / summ["tokens_content"], 2),
    "share_of_verb_tokens_min_pct": round(100 * tot_min / summ["tokens_on_verb_lemmas"], 1),
    "share_of_verb_tokens_max_pct": round(100 * tot_max / summ["tokens_on_verb_lemmas"], 1),
    "tokens_on_forms_without_separate_lemma_min": sum(r["book_tokens_min"] for r in nosep),
    "tokens_on_forms_without_separate_lemma_max": sum(r["book_tokens_max"] for r in nosep),
    "tokens_with_hamza_mismatch": sum(r["tokens_whose_hamza_spelling_differs_from_form"] for r in out),
    "by_type_tokens": {t: dict(c) for t, c in by_type_tok.items()},
    "wide_extra_total": sum(int(r["extra_tokens_reaching_verb_only_via_wide_camel"] or 0) for r in out),
    "forms_seen_in_book": len([r for r in out if r["book_tokens_max"] > 0]),
    "forms_without_separate_lemma": {
        f">={th}": {"min_count": cnt(nosep, "book_tokens_min", th), "max_count": cnt(nosep, "book_tokens_max", th),
                    "min_count_incl_wide": cnt(nosep, "book_tokens_min", th, "extra_tokens_reaching_verb_only_via_wide_camel"),
                    "min_count_hamza_clean": cnt(clean, "book_tokens_min", th)}
        for th in (2, 5, 10, 20, 50)},
    "forms_without_separate_lemma_by_type_min": {
        t: {f">={th}": sum(1 for r in nosep if r["form_type"] == t and r["book_tokens_min"] >= th) for th in (5, 20)}
        for t in sorted({r["form_type"] for r in out})},
    "verb_state_of_forms_ge5_min": dict(Counter(r["verb_knowledge_state"] or "no_ulk" for r in nosep if r["book_tokens_min"] >= 5)),
}
(OUT / "book_summary2.json").write_text(json.dumps(s2, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(s2, ensure_ascii=False, indent=1))

# reverse stats
rev = list(csv.DictReader((OUT / "reverse.tsv").open(encoding="utf-8"), delimiter="\t"))
print("reverse rows", len(rev), "same_root", Counter(r["same_root"] for r in rev), "owner", Counter(r["owner_is"] for r in rev))
print("same-root by type", Counter(r["form_type"] for r in rev if r["same_root"] == "True"))
print("distinct verbs same-root by type", {t: len({r["verb_lemma_id"] for r in rev if r["same_root"] == "True" and r["form_type"] == t}) for t in ("masdar", "active_participle", "passive_participle")})
