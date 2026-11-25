#!/usr/bin/env python3
"""Count the app's own sentence_words / review_log rows credited to a verb through a
participle / verbal-noun surface. Read-only. Outputs app_summary.json and app_forms.tsv.
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

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.models import Lemma, Sentence  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_via_map  # noqa: E402
from app.services.sentence_eligibility import reviewable_sentence_clauses  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    _strip_clitics,
    _count_mark_conflicts,
    _locate_stem,
    _NOMINAL_ONLY_PREFIXES,
    _skeleton,
    _vocalized_letters,
    has_tanwin_alif,
    normalize_alef,
    normalize_arabic,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
    verb_target_inflections,
)

OUT = Path(__file__).resolve().parent
DERIVED_KEYS = ("masdar", "active_participle", "passive_participle")
FINITE_PREFIXES = ("past", "present", "imperative")
NOMINAL_SUFFIXES = ("ة", "ات", "ون", "ين", "ان", "تان", "تين", "ا")


def form_key(v: str) -> str:
    return normalize_alef(strip_diacritics(v))


def main() -> None:
    print("ENGINE", engine.url)
    assert "alif_work.db" in str(engine.url)
    db = SessionLocal()
    lemmas = {l.lemma_id: l for l in db.query(Lemma).all()}
    parents = {lid: l.canonical_lemma_id for lid, l in lemmas.items()}
    derived, finite = {}, {}
    for lid, l in lemmas.items():
        if l.pos != "verb" or l.canonical_lemma_id is not None:
            continue
        f = l.forms_json if isinstance(l.forms_json, dict) else {}
        d = {}
        for k in DERIVED_KEYS:
            v = f.get(k)
            if isinstance(v, str) and v.strip():
                d.setdefault(form_key(v), (k, v))
        fin = set(verb_target_inflections(l.lemma_ar_bare, f))
        fin.add(normalize_arabic(l.lemma_ar_bare or ""))
        for k, v in f.items():
            if isinstance(k, str) and k.startswith(FINITE_PREFIXES) and isinstance(v, str) and v.strip():
                fin.add(form_key(v))
        derived[lid], finite[lid] = d, fin


    reviewable = {r[0] for r in db.query(Sentence.id).filter(reviewable_sentence_clauses()).all()}
    active = {r[0] for r in db.query(Sentence.id).filter(Sentence.is_active.is_(True)).all()}
    print("reviewable", len(reviewable), "active", len(active))

    rows = db.execute(text("select id, sentence_id, surface_form, lemma_id from sentence_words where lemma_id is not null")).fetchall()
    total_sw = len(rows)
    cache: dict[tuple[int, str], tuple | None] = {}

    def decide(vid: int, surface: str, key: str) -> str:
        """nominal / verb / undecided for a surface whose stem spells both the
        verb's masdar-or-participle and one of its finite forms."""
        letters = _vocalized_letters(surface)
        skeleton = _skeleton(letters)
        located = _locate_stem(letters, key)
        if located is None:
            return "undecided"
        start, end, _ = located
        prefix = skeleton[:start]
        if prefix.endswith("ال") or prefix in {"لل", "ولل", "فلل"} or prefix in _NOMINAL_ONLY_PREFIXES:
            return "nominal"
        if end == len(letters) and letters[-1][1] & set("\u064b\u064c\u064d"):
            return "nominal"
        if has_tanwin_alif(surface):
            return "nominal"
        region = letters[start:end]
        if not any(m for _, m in region):
            return "undecided"
        ftype, voc = derived[vid][key]
        ref_n = list(_vocalized_letters(voc))
        ref_v = list(_vocalized_letters(lemmas[vid].lemma_ar or ""))
        n_ok = v_ok = None
        if _skeleton(ref_n) == key:
            c, a = _count_mark_conflicts(region, ref_n, check_last_vowel=False)
            n_ok = (c == 0 and a > 0) if (c or a) else None
        if _skeleton(ref_v) == key:
            c, a = _count_mark_conflicts(region, ref_v, check_last_vowel=end == len(letters))
            v_ok = (c == 0 and a > 0) if (c or a) else None
        if v_ok:
            return "verb"
        if n_ok and v_ok is False:
            return "nominal"
        if n_ok is False:
            return "verb"
        return "undecided"

    def classify(vid: int, surface: str):
        bare = normalize_alef(strip_punctuation(strip_tatweel(strip_diacritics(surface))))
        bases = [bare]
        if has_tanwin_alif(surface) and bare.endswith("ا") and len(bare) >= 4:
            bases.append(bare[:-1])
        stems: dict[str, None] = {}
        for b in bases:
            stems[b] = None
            for s in _strip_clitics(b):
                stems[normalize_alef(s)] = None
        d = derived[vid]
        hit, how = None, None
        for s in stems:
            for cand in (s, s[2:] if s.startswith("ال") else None):
                if cand and cand in d:
                    hit, how = cand, "exact"
                    break
            if hit:
                break
        fin_hit = any(s in finite[vid] for s in stems)
        if not hit and not fin_hit:
            # inflected participle / masdar (feminine, plural, dual) — reaches the verb
            # only through the wide CAMeL layer or an LLM correction.
            for s in stems:
                core = s[2:] if s.startswith("ال") else s
                for suf in NOMINAL_SUFFIXES:
                    if core.endswith(suf) and len(core) > len(suf) + 2:
                        base = core[: -len(suf)]
                        for cand in (base, base + "ة", base + "ي", base + "ى"):
                            if cand in d:
                                hit, how = cand, "inflected"
                                break
                    if hit:
                        break
                if hit:
                    break
        if not hit:
            return ("finite" if fin_hit else "other", None, None, None)
        if fin_hit and how == "exact":
            return ("ambiguous", hit, d[hit][0], decide(vid, surface, hit))
        return ("derived_" + how, hit, d[hit][0], None)

    sw_counts = Counter()
    type_counts = defaultdict(Counter)
    sent_derived: dict[int, set[int]] = defaultdict(set)   # sentence -> verbs with a derived-only surface
    sent_finite: dict[int, set[int]] = defaultdict(set)    # sentence -> verbs with a finite/other surface
    per_form = defaultdict(lambda: {"rows": 0, "sentences": set(), "reviewable": set(), "surfaces": Counter()})
    verb_rows = 0
    ambig = Counter()
    for _id, sid, surface, lid in rows:
        vid = resolve_canonical_via_map(lid, parents)
        if vid not in derived:
            continue
        verb_rows += 1
        key = (vid, surface)
        if key not in cache:
            cache[key] = classify(vid, surface)
        cat, hit, ftype, pos = cache[key]
        nominal = cat.startswith("derived") or (cat == "ambiguous" and pos == "nominal")
        sw_counts[cat] += 1
        if cat == "ambiguous":
            sw_counts["ambiguous_" + pos] += 1
            ambig[(lemmas[vid].lemma_ar, surface, pos)] += 1
        if nominal:
            type_counts[ftype][cat] += 1
            sent_derived[sid].add(vid)
            p = per_form[(vid, hit)]
            p["rows"] += 1
            p["sentences"].add(sid)
            if sid in reviewable:
                p["reviewable"].add(sid)
            p["surfaces"][surface] += 1
        else:
            sent_finite[sid].add(vid)

    derived_sents = set(sent_derived)
    summary = {
        "sentence_words_mapped_total": total_sw,
        "sentence_words_on_verb_lemmas": verb_rows,
        "by_category": dict(sw_counts),
        "derived_rows_est": sum(sum(c.values()) for c in type_counts.values()),
        "by_type": {t: dict(c) for t, c in type_counts.items()},
        "sentences_with_derived_surface": len(derived_sents),
        "of_which_active": len(derived_sents & active),
        "of_which_reviewable": len(derived_sents & reviewable),
        "reviewable_total": len(reviewable),
        "distinct_verb_form_pairs": len(per_form),
    }

    # review_log rows credited to the verb from a sentence where the verb appears only as a derived form
    rl = db.execute(text("select lemma_id, sentence_id, rating, credit_type, is_acquisition from review_log where sentence_id is not null")).fetchall()
    rl_total = len(rl)
    only_derived = both = 0
    rl_rating = Counter()
    rl_verbs = set()
    per_verb_rl = Counter()
    for lid, sid, rating, credit, acq in rl:
        vid = resolve_canonical_via_map(lid, parents)
        if vid in sent_derived.get(sid, ()):
            if vid in sent_finite.get(sid, ()):
                both += 1
            else:
                only_derived += 1
                rl_rating[rating] += 1
                rl_verbs.add(vid)
                per_verb_rl[vid] += 1
    summary.update({
        "review_log_rows_with_sentence": rl_total,
        "review_log_total": db.execute(text("select count(*) from review_log")).scalar(),
        "review_log_credited_to_verb_from_derived_only_sentence": only_derived,
        "review_log_derived_and_finite_same_sentence": both,
        "review_log_derived_ratings": dict(rl_rating),
        "verbs_with_such_review_rows": len(rl_verbs),
    })
    # target sentences whose target is a verb but the target surface is a derived form
    tgt = db.execute(text("select s.id, s.target_lemma_id from sentences s where s.target_lemma_id is not null")).fetchall()
    tcount = sum(1 for sid, t in tgt if resolve_canonical_via_map(t, parents) in sent_derived.get(sid, ()) and resolve_canonical_via_map(t, parents) not in sent_finite.get(sid, ()))
    summary["sentences_whose_target_verb_appears_only_as_derived_form"] = tcount

    with (OUT / "app_ambiguous_surfaces.tsv").open("w", encoding="utf-8") as fh:
        fh.write("verb\tsurface\tverdict\trows\n")
        for (va, sf, pos), n in ambig.most_common():
            fh.write(f"{va}\t{sf}\t{pos}\t{n}\n")
    out_rows = []
    for (vid, key), p in per_form.items():
        v = lemmas[vid]
        ftype, voc = derived[vid][key]
        out_rows.append({
            "derived_form": voc, "form_type": ftype, "verb_lemma_id": vid, "verb_ar": v.lemma_ar,
            "verb_gloss": v.gloss_en or "", "sentence_word_rows": p["rows"],
            "sentences": len(p["sentences"]), "reviewable_sentences": len(p["reviewable"]),
            "review_log_rows_verb_total_from_derived_only_sentences": per_verb_rl.get(vid, 0),
            "top_surfaces": " ".join(f"{s}:{c}" for s, c in p["surfaces"].most_common(4)),
        })
    out_rows.sort(key=lambda r: -r["sentence_word_rows"])
    with (OUT / "app_forms.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(out_rows)
    summary["forms_with_ge5_rows"] = sum(1 for r in out_rows if r["sentence_word_rows"] >= 5)
    (OUT / "app_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
