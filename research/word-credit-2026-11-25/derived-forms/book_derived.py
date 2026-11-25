#!/usr/bin/env python3
"""Count book-text tokens credited to a verb lemma through a participle / verbal-noun form.

Read-only. Reuses the token loop and resolver of scripts/build_book_frequency.py.
Run:
  cd /Users/stian/src/alif/backend && DATABASE_URL=sqlite:////.../alif_work.db \
      ALIF_SKIP_MIGRATIONS=1 .venv/bin/python <this file>
Outputs (next to this file): candidates.tsv, reverse.tsv, book_summary.json, token_cache.json
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
BACKEND = Path("/Users/stian/src/alif/backend")
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.database import SessionLocal, engine  # noqa: E402
from app.models import BookFrequencyEntry, Lemma, UserLemmaKnowledge  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_via_map  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    LemmaMatchProvenance,
    _camel_lex_key,
    _is_function_word,
    _strip_clitics,
    build_comprehensive_lemma_lookup,
    has_tanwin_alif,
    lookup_lemma_id,
    normalize_alef,
    normalize_arabic,
    requires_exact_running_text_alias,
    strip_diacritics,
    strip_tatweel,
    verb_target_inflections,
)
from build_book_frequency import CONTENT_POS, DEFAULT_SOURCES, is_function_surface  # noqa: E402
from reading_readiness import norm_bare, tokenize  # noqa: E402

OUT = Path(__file__).resolve().parent
DERIVED_KEYS = ("masdar", "active_participle", "passive_participle")
FINITE_PREFIXES = ("past", "present", "imperative")


def form_key(value: str) -> str:
    """Exactly the key Pass 2 of build_lemma_lookup registers (sentence_validator.py:3321)."""
    return normalize_alef(strip_diacritics(value))


def main() -> None:
    print("ENGINE", engine.url)
    assert "alif_work.db" in str(engine.url)
    db = SessionLocal()
    lemmas = {l.lemma_id: l for l in db.query(Lemma).all()}
    parents = {lid: l.canonical_lemma_id for lid, l in lemmas.items()}
    ulk = {u.lemma_id: u for u in db.query(UserLemmaKnowledge).all()}
    book_rank = {
        e.lemma_id: (e.rank, e.count)
        for e in db.query(BookFrequencyEntry).all()
        if e.lemma_id is not None
    }
    lookup = build_comprehensive_lemma_lookup(db)
    db.rollback()

    # verb -> derived keys / finite keys
    derived: dict[int, dict[str, tuple[str, str]]] = {}  # lemma_id -> key -> (type, vocalised)
    finite: dict[int, set[str]] = {}
    for lid, l in lemmas.items():
        if l.pos != "verb" or l.canonical_lemma_id is not None:
            continue
        f = l.forms_json if isinstance(l.forms_json, dict) else {}
        d: dict[str, tuple[str, str]] = {}
        for k in DERIVED_KEYS:
            v = f.get(k)
            if isinstance(v, str) and v.strip():
                d.setdefault(form_key(v), (k, v))
        fin = set(verb_target_inflections(l.lemma_ar_bare, f))
        fin.add(normalize_arabic(l.lemma_ar_bare or ""))
        for k, v in f.items():
            if isinstance(k, str) and k.startswith(FINITE_PREFIXES) and isinstance(v, str) and v.strip():
                fin.add(form_key(v))
        derived[lid] = d
        finite[lid] = fin

    from app.services.morphology import get_best_lemma_mle

    def stems_of(tok: str) -> list[str]:
        bare = norm_bare(tok)
        bases = [bare]
        if has_tanwin_alif(tok) and bare.endswith("ا") and len(bare) >= 4:
            bases.append(bare[:-1])
        out: dict[str, None] = {}
        for b in bases:
            out[b] = None
            for s in _strip_clitics(b):
                out[normalize_alef(s)] = None
        return list(out)

    def camel(tok: str):
        try:
            a = get_best_lemma_mle(tok)
        except Exception:
            a = None
        return a or {}

    cache: dict[str, dict] = {}

    def classify(tok: str) -> dict:
        bare = norm_bare(tok)
        rec: dict = {"content": False}
        if not requires_exact_running_text_alias(tok) and is_function_surface(bare):
            return rec
        prov = LemmaMatchProvenance()
        lemma_id = lookup_lemma_id(tok, lookup, prov, strict_fallback=True)
        if lemma_id is not None:
            lemma = lemmas.get(resolve_canonical_via_map(lemma_id, parents))
            if lemma is None or lemma.word_category in {"proper_name", "onomatopoeia"}:
                return rec
            lemma_bare = normalize_alef(strip_diacritics(lemma.lemma_ar_bare or ""))
            if lemma.pos == "particle" or lemma.function_word_override or _is_function_word(lemma_bare):
                return rec
            rec.update(content=True, lemma_id=lemma.lemma_id, layer=prov.layer,
                       key_source=prov.key_source, tie=prov.homograph_tie,
                       alts=list(prov.alternatives))
            if lemma.lemma_id in derived:
                stems = stems_of(tok)
                d = derived[lemma.lemma_id]
                hit = None
                for s in stems:
                    for cand in (s, s[2:] if s.startswith("ال") else None):
                        if cand and cand in d:
                            hit = cand
                            break
                    if hit:
                        break
                fin_hit = any(s in finite[lemma.lemma_id] for s in stems)
                if hit:
                    rec["derived_key"] = hit
                    rec["derived_type"] = d[hit][0]
                    rec["finite_too"] = fin_hit
                    if fin_hit:
                        rec["camel_pos"] = (camel(tok).get("pos") or "").lower()
            return rec
        # Unmatched under the strict resolver (book-frequency "not in Alif" rows).
        a = camel(tok)
        if not a.get("lex"):
            return rec
        pos = (a.get("pos") or "").lower()
        if pos == "noun_prop" or not pos.startswith(CONTENT_POS):
            return rec
        key = normalize_alef(strip_tatweel(strip_diacritics(a["lex"])))
        if len(key) < 2:
            return rec
        rec.update(content=True, lemma_id=None, camel_key=key, camel_pos=pos.split("_")[0])
        # What the wide (non-strict) resolver used for app sentences would do with it.
        prov2 = LemmaMatchProvenance()
        wide = lookup_lemma_id(tok, lookup, prov2, strict_fallback=False)
        if wide is not None:
            wl = lemmas.get(resolve_canonical_via_map(wide, parents))
            if wl is not None and wl.lemma_id in derived:
                lex_key = _camel_lex_key(strip_diacritics(tok), lookup)
                d = derived[wl.lemma_id]
                if lex_key and lex_key in d:
                    rec["wide_verb"] = wl.lemma_id
                    rec["wide_key"] = lex_key
                    rec["wide_type"] = d[lex_key][0]
                    rec["wide_layer"] = prov2.layer
        return rec

    corpus = json.loads((BACKEND / "data/corpora/barec_sentences.json").read_text(encoding="utf-8"))
    texts = [r["text"] for r in corpus if r.get("source") in set(DEFAULT_SOURCES)]
    tok_counts: Counter = Counter()
    total = 0
    for text in texts:
        for tok in tokenize(text):
            if not norm_bare(tok):
                continue
            total += 1
            tok_counts[tok] += 1
    print(f"{len(texts)} sentences, {total} tokens, {len(tok_counts)} distinct")
    for i, tok in enumerate(tok_counts):
        cache[tok] = classify(tok)
        if i % 10000 == 0:
            print("  classified", i, flush=True)

    content = sum(n for t, n in tok_counts.items() if cache[t]["content"])
    linked = sum(n for t, n in tok_counts.items() if cache[t].get("lemma_id"))
    verb_tok = sum(n for t, n in tok_counts.items() if cache[t].get("lemma_id") in derived)

    # aggregate per (verb, key)
    agg: dict[tuple[int, str], dict] = defaultdict(lambda: {"n": 0, "n_only": 0, "n_both": 0,
                                                             "both_camel_nominal": 0, "surfaces": Counter(), "layers": Counter()})
    by_type = defaultdict(lambda: Counter())
    for tok, n in tok_counts.items():
        r = cache[tok]
        if "derived_key" not in r:
            continue
        a = agg[(r["lemma_id"], r["derived_key"])]
        a["n"] += n
        a["surfaces"][tok] += n
        a["layers"][r["layer"]] += n
        t = r["derived_type"]
        if r["finite_too"]:
            a["n_both"] += n
            by_type[t]["both"] += n
            if r.get("camel_pos", "").startswith(("noun", "adj")):
                a["both_camel_nominal"] += n
                by_type[t]["both_camel_nominal"] += n
        else:
            a["n_only"] += n
            by_type[t]["only"] += n

    # wide-resolver extra
    wide_agg: dict[tuple[int, str], int] = Counter()
    wide_type = Counter()
    for tok, n in tok_counts.items():
        r = cache[tok]
        if "wide_verb" in r:
            wide_agg[(r["wide_verb"], r["wide_key"])] += n
            wide_type[r["wide_type"]] += n

    # which lemma owns each derived key in the lookup; separate-lemma existence
    bare_index: dict[str, list[Lemma]] = defaultdict(list)
    for l in lemmas.values():
        b = normalize_arabic(l.lemma_ar_bare or "")
        bare_index[b].append(l)
        if b.startswith("ال") and len(b) > 2:
            bare_index[b[2:]].append(l)

    def stab(lid):
        u = ulk.get(lid)
        if not u:
            return "", ""
        s = ""
        c = u.fsrs_card_json
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except Exception:
                c = None
        if isinstance(c, dict) and c.get("stability") is not None:
            s = f"{float(c['stability']):.1f}"
        return u.knowledge_state or "", s

    # tokens resolving to a separate lemma for the same key (reverse side)
    lemma_tok = Counter()
    for tok, n in tok_counts.items():
        lid = cache[tok].get("lemma_id")
        if lid:
            lemma_tok[lid] += n

    rows = []
    reverse_rows = []
    all_pairs = set(agg) | set(wide_agg)
    for vid, d in derived.items():
        for key in d:
            all_pairs.add((vid, key))
    for vid, key in all_pairs:
        v = lemmas[vid]
        ftype, voc = derived[vid][key]
        a = agg.get((vid, key))
        others = [l for l in bare_index.get(key, []) if l.lemma_id != vid]
        sep_nonvariant = [l for l in others if l.canonical_lemma_id is None]
        sep_variant = [l for l in others if l.canonical_lemma_id is not None]
        owner = lookup.get(key)
        state, st = stab(vid)
        rank, vcount = book_rank.get(vid, ("", ""))
        n = a["n"] if a else 0
        n_only = a["n_only"] if a else 0
        n_both = a["n_both"] if a else 0
        nominal_est = a["both_camel_nominal"] if a else 0
        row = {
            "derived_form": voc, "derived_key": key, "form_type": ftype,
            "verb_lemma_id": vid, "verb_ar": v.lemma_ar, "verb_gloss": v.gloss_en or "",
            "book_tokens_on_verb_total": n,
            "book_tokens_unambiguous": n_only,
            "book_tokens_spelled_like_finite_verb_form": n_both,
            "of_those_camel_says_nominal": nominal_est,
            "book_tokens_est": n_only + nominal_est,
            "extra_tokens_wide_resolver_only": wide_agg.get((vid, key), 0),
            "separate_lemma": "; ".join(f"#{l.lemma_id} {l.lemma_ar} [{l.pos}] {l.gloss_en or ''}" for l in sep_nonvariant),
            "separate_lemma_is_variant_row": "; ".join(f"#{l.lemma_id} {l.lemma_ar} -> #{l.canonical_lemma_id}" for l in sep_variant),
            "lookup_key_owner": owner if owner is not None else "",
            "lookup_key_source": lookup.key_sources.get(key, ""),
            "verb_knowledge_state": state, "verb_fsrs_stability_days": st,
            "verb_book_rank": rank, "verb_book_count": vcount,
            "top_surfaces": " ".join(f"{s}:{c}" for s, c in (a["surfaces"].most_common(4) if a else [])),
            "layers": " ".join(f"{k}:{c}" for k, c in (a["layers"].most_common() if a else [])),
        }
        if n or wide_agg.get((vid, key)):
            rows.append(row)
        if sep_nonvariant:
            for l in sep_nonvariant:
                reverse_rows.append({
                    "derived_form": voc, "derived_key": key, "form_type": ftype,
                    "verb_lemma_id": vid, "verb_ar": v.lemma_ar, "verb_gloss": v.gloss_en or "",
                    "own_lemma_id": l.lemma_id, "own_lemma_ar": l.lemma_ar, "own_pos": l.pos, "own_gloss": l.gloss_en or "",
                    "lookup_key_owner": owner, "owner_is": "own_lemma" if owner == l.lemma_id else ("verb" if owner == vid else "other"),
                    "book_tokens_to_own_lemma": lemma_tok.get(l.lemma_id, 0),
                    "book_tokens_to_verb_via_this_form": n,
                    "own_state": stab(l.lemma_id)[0], "verb_state": state,
                    "same_root": (l.root_id == v.root_id) if (l.root_id and v.root_id) else "",
                })

    rows.sort(key=lambda r: (-r["book_tokens_est"], -r["book_tokens_on_verb_total"], -r["extra_tokens_wide_resolver_only"]))
    with (OUT / "candidates.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    reverse_rows.sort(key=lambda r: -(r["book_tokens_to_own_lemma"] + r["book_tokens_to_verb_via_this_form"]))
    with (OUT / "reverse.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(reverse_rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(reverse_rows)

    # summary
    def no_sep(r):
        return not r["separate_lemma"]
    thresholds = {}
    for th in (2, 5, 10, 20, 50):
        thresholds[th] = {
            "est_no_separate": sum(1 for r in rows if no_sep(r) and r["book_tokens_est"] >= th),
            "unambiguous_no_separate": sum(1 for r in rows if no_sep(r) and r["book_tokens_unambiguous"] >= th),
            "est_plus_wide_no_separate": sum(1 for r in rows if no_sep(r) and r["book_tokens_est"] + r["extra_tokens_wide_resolver_only"] >= th),
            "est_any": sum(1 for r in rows if r["book_tokens_est"] >= th),
        }
    per_type_thresh = {t: {th: sum(1 for r in rows if no_sep(r) and r["form_type"] == t and r["book_tokens_est"] >= th) for th in (5, 20)} for t in DERIVED_KEYS}
    verbs_with = {k: sum(1 for d in derived.values() if any(t == k for t, _ in d.values())) for k in DERIVED_KEYS}
    sep_counts = {k: len({r["verb_lemma_id"] for r in reverse_rows if r["form_type"] == k}) for k in DERIVED_KEYS}
    summary = {
        "sentences": len(texts), "tokens_total": total, "tokens_content": content,
        "tokens_linked_to_lemma": linked, "tokens_on_verb_lemmas": verb_tok,
        "by_type": {t: dict(c) for t, c in by_type.items()},
        "derived_unambiguous_total": sum(c["only"] for c in by_type.values()),
        "derived_ambiguous_total": sum(c["both"] for c in by_type.values()),
        "derived_ambiguous_camel_nominal": sum(c["both_camel_nominal"] for c in by_type.values()),
        "wide_resolver_extra_by_type": dict(wide_type),
        "thresholds": thresholds, "per_type_thresholds_no_separate": per_type_thresh,
        "nonvariant_verbs": len(derived), "verbs_with_form": verbs_with,
        "verbs_whose_form_has_own_nonvariant_lemma": sep_counts,
        "reverse_rows": len(reverse_rows),
        "reverse_owner": dict(Counter(r["owner_is"] for r in reverse_rows)),
        "reverse_tokens_to_verb_despite_own_lemma": sum(r["book_tokens_to_verb_via_this_form"] for r in reverse_rows),
        "layers_derived": dict(sum((a["layers"] for a in agg.values()), Counter())),
    }
    (OUT / "book_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "token_cache.json").write_text(
        json.dumps({t: {**cache[t], "n": n} for t, n in tok_counts.items() if cache[t].get("derived_key") or cache[t].get("wide_verb")},
                   ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
