"""Repair lemmas whose citation form cannot find its own lemma row.

Background: the 2026-07-15 collision investigation
(research/spec-2026-07-15-lookup-clitic-collision.md §7/§8) found ~100
canonical lemmas whose own headword only resolved through lookup_lemma's
greedy CAMeL last resort — i.e. their `lemma_ar_bare` disagrees with the
headword (truncations like مِقْلَمَة/مقلم, ه↔ة typos like سَمْبُوسَة/سمبوسه,
loanword spelling variance like سَنْدَوِيتْش/ساندويتش, and lexicalized
adverbial/clitic headwords like غَداً/غد, بِبُطْء/بطء). With citation-strict
/add live (PR #212), an unrepaired row means re-adding that word would
CREATE A DUPLICATE instead of no-op'ing. Classified audit:
research/corrupt-bare-audit-2026-07-15.json.

Design follows bare_shape_check.py (Gate 1b) conventions: collision-check
before every change, never force past a conflict, ActivityLog every applied
action, idempotent.

Actions (chosen per row, mechanically proposed, human-reviewed):
  rewrite_bare  — lemma_ar_bare := the headword's own bare. Only for
                  unambiguous truncation/typo rows where the old bare is
                  wrong-word-shaped. Refused if the new bare (or its
                  normalized lookup key) already belongs to another
                  canonical lemma.
  add_alias     — additive: forms_json["citation_alias"] = lemma_ar.
                  build_lemma_lookup Pass 2 indexes every string-valued
                  forms_json entry, so the headword's bare becomes a lookup
                  key WITHOUT touching the lemma's identity key. Used for
                  lexicalized adverbials (غَداً — bare غد is the real noun),
                  clitic-bearing displays (بِبُطْء), defective participles,
                  and both-spellings-valid loanwords. Refused if the alias
                  key is owned by a different lemma.
  skip          — shadowed keys (function-word overrides + known bare-form
                  homographs: أنّ/إنّ, آن, أيّ …), anything uncertain.

Workflow (two-phase, review between):
  --plan [--plan-file P]   read-only: derive live audit, propose actions,
                           write plan JSON to P (default
                           /tmp/bare_repair_plan.json). Review it.
  --apply --plan-file P    execute the reviewed plan. Every row's
                           preconditions (current bare, current forms_json
                           alias absence, collision freedom) are re-checked
                           at apply time; drifted rows are skipped and
                           reported, never forced.
  --census                 standalone: citation-strict self-resolution
                           census over all canonical lemmas (also runs
                           automatically after --apply). Success = every
                           failure is class `shadowed`.

Run on the server:
  cd /opt/alif/backend
  .venv/bin/python3 scripts/repair_corrupt_bares.py --plan
  # review /tmp/bare_repair_plan.json
  .venv/bin/python3 scripts/repair_corrupt_bares.py --apply --plan-file /tmp/bare_repair_plan.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import ActivityLog, Lemma  # noqa: E402
from app.services.lemma_quality import _normalize  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    build_lemma_lookup,
    lookup_lemma_citation,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
)

ALIAS_KEY = "citation_alias"
# On rewrite, the replaced bare is preserved here so every lookup key that
# exists today keeps existing (Pass 2 indexes it) — unless the old bare was a
# wrong-word key (curated below), where removal is the point.
OLD_BARE_KEY = "old_bare_form"

# ── Curated per-row overrides (human review 2026-07-15) ────────────────────
# The mechanical classifier can't tell a proclitic from a root letter, or a
# spelling variant from a different word. Keyed by (lemma_id, current bare)
# so a drifted row never gets a stale decision.
#
# rewrite/no-stash: the old bare is a DIFFERENT WORD's key (احتجاب "veiling",
# طن "ton", زق, ضواغط, جزل, معرج) or clitic-strip damage on a root letter
# (بلوفر, وجم, بغض, وكر) — removing the wrong key is intended.
REVIEW_OVERRIDES: dict[tuple[int, str], dict] = {
    (2403, "لوفر"): {"action": "rewrite_bare", "stash": False,
                     "reason": "loanword بلوفر — ب is part of the word, bare was clitic-strip damage"},
    (3199, "جم"): {"action": "rewrite_bare", "stash": False,
                   "reason": "verb وجم — و is a root letter, not a conjunction"},
    (3720, "غض"): {"action": "rewrite_bare", "stash": False,
                   "reason": "noun بغض — ب is a root letter; غض is a different verb"},
    (4035, "كر"): {"action": "rewrite_bare", "stash": False,
                   "reason": "noun وكر — و is a root letter"},
    (2516, "احتجاب"): {"action": "rewrite_bare", "stash": False,
                       "reason": "bare was a different word (iḥtijāb 'veiling')"},
    (2552, "زق"): {"action": "rewrite_bare", "stash": False,
                   "reason": "bare was a different word (زق)"},
    (2723, "جزل"): {"action": "rewrite_bare", "stash": False,
                    "reason": "internal ي truncation; جزل is a different adjective"},
    (2945, "ضواغط"): {"action": "rewrite_bare", "stash": False,
                      "reason": "bare was the plural of a different noun (ضاغط)"},
    (3331, "طن"): {"action": "rewrite_bare", "stash": False,
                   "reason": "bare was a different word (طن 'ton')"},
    (4178, "معرج"): {"action": "rewrite_bare", "stash": False,
                     "reason": "internal ا truncation; معرج is a different noun"},
    # Proper names whose lemma_ar carries stray punctuation (كِلَارَا:) — the
    # display itself is the corruption; strip it so the bare key matches.
    (3338, "كلارا"): {"action": "clean_display"},
    (3343, "بيجوتي"): {"action": "clean_display"},
    (3348, "ماكبث"): {"action": "clean_display"},
    (3362, "مايلز"): {"action": "clean_display"},
}

# Arabic tanwin-fath (ً) — a headword carrying it is an adverbial accusative
# (غَداً, شُكْراً): the bare noun without the tanwin seat is usually the RIGHT
# identity key, so those rows get an alias, never a bare rewrite.
TANWIN_FATH = "ً"
# Single-letter proclitics that can open a lexicalized display form (بِبُطْء).
CLITIC_OPENERS = ("ب", "ل", "ك", "و", "ف")


def display_bare(lemma_ar: str) -> str:
    """The headword's own bare form (hamza preserved; lookup normalizes later)."""
    return strip_punctuation(strip_tatweel(strip_diacritics(lemma_ar or "")))


def canonical_lemmas(db) -> list[Lemma]:
    return (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .all()
    )


def run_census(db, lookup=None, lemmas=None) -> list[dict]:
    """Citation-strict self-resolution over all canonicals. Returns failures."""
    lemmas = lemmas or canonical_lemmas(db)
    lookup = lookup or build_lemma_lookup(lemmas)
    failures = []
    for lem in lemmas:
        if not lem.lemma_ar_bare:
            continue
        word = lem.lemma_ar or lem.lemma_ar_bare
        got = lookup_lemma_citation(
            _normalize(word), lookup, original_bare=display_bare(word)
        )
        if got != lem.lemma_id:
            key_owner = lookup.get(_normalize(word))
            failures.append({
                "lemma_id": lem.lemma_id,
                "lemma_ar": lem.lemma_ar,
                "bare": lem.lemma_ar_bare,
                "gloss": lem.gloss_en,
                "resolves_to": got,
                "class": "shadowed" if key_owner not in (None, lem.lemma_id) else "unresolved",
            })
    return failures


def key_owner(lookup, key: str) -> int | None:
    return lookup.get(_normalize(key))


def propose(db) -> list[dict]:
    """Mechanical per-row proposal for every census failure. Never writes."""
    lemmas = canonical_lemmas(db)
    lookup = build_lemma_lookup(lemmas)
    bare_owners: dict[str, list[int]] = {}
    for lem in lemmas:
        if lem.lemma_ar_bare:
            bare_owners.setdefault(_normalize(lem.lemma_ar_bare), []).append(lem.lemma_id)

    plan = []
    for f in run_census(db, lookup=lookup, lemmas=lemmas):
        lem = db.get(Lemma, f["lemma_id"])
        ar = lem.lemma_ar or ""
        d_bare = display_bare(ar)
        cur_bare = (lem.lemma_ar_bare or "").strip()
        row = {
            "lemma_id": lem.lemma_id, "lemma_ar": ar, "bare": cur_bare,
            "gloss": lem.gloss_en, "pos": lem.pos, "display_bare": d_bare,
        }

        # 0. Curated human-review override wins over every mechanical rule.
        override = REVIEW_OVERRIDES.get((lem.lemma_id, cur_bare))
        if override:
            if override["action"] == "rewrite_bare":
                others = [o for o in bare_owners.get(_normalize(d_bare), []) if o != lem.lemma_id]
                if others:
                    plan.append({**row, "action": "skip",
                                 "reason": f"override rewrite target collides with {others}"})
                else:
                    plan.append({**row, "action": "rewrite_bare", "new_bare": d_bare,
                                 "stash": override.get("stash", False),
                                 "reason": "REVIEWED: " + override["reason"]})
            elif override["action"] == "clean_display":
                cleaned = ar.strip(" ‏:,.;!؟?،؛«»\"'")
                plan.append({**row, "action": "clean_display", "new_lemma_ar": cleaned,
                             "reason": "REVIEWED: stray punctuation in headword"})
            continue

        # 1. Shadowed: the headword's key exists and belongs to another lemma
        #    (function-word overrides أنّ/إنّ, homographs آن/أيّ). Out of scope.
        owner = key_owner(lookup, d_bare)
        if owner not in (None, lem.lemma_id):
            plan.append({**row, "action": "skip",
                         "reason": f"key owned by #{owner} (override/homograph — separate issue)"})
            continue
        if not d_bare or len(d_bare) < 2:
            plan.append({**row, "action": "skip", "reason": "display bare too short/empty"})
            continue

        norm_d, norm_cur = _normalize(d_bare), _normalize(cur_bare)

        # 2. Adverbial tanwin headword (غَداً): bare noun is the right identity.
        if TANWIN_FATH in ar and norm_d.endswith("ا") and norm_d[:-1] == norm_cur:
            plan.append({**row, "action": "add_alias", "alias": ar,
                         "reason": "adverbial tanwin headword; bare noun identity kept"})
            continue

        # 3. Clitic-opening lexicalized display (بِبُطْء = ب + بطء).
        if any(norm_d == c + norm_cur for c in CLITIC_OPENERS):
            plan.append({**row, "action": "add_alias", "alias": ar,
                         "reason": "headword carries a proclitic; bare identity kept"})
            continue

        # 4. Defective-participle convention: bare WITH ي is correct
        #    (قاضٍ/قاضي per bare_shape_check); headword strips to bare-minus-ي.
        if norm_cur == norm_d + "ي":
            plan.append({**row, "action": "add_alias", "alias": ar,
                         "reason": "defective participle; ya-bearing bare is the convention"})
            continue

        # 5. Truncation/typo: current bare is a strict prefix of the headword
        #    bare, or equals it with a final ه↔ة swap → rewrite.
        heh_taa_fix = (norm_cur[:-1] == norm_d[:-1] and norm_cur.endswith("ه")
                       and norm_d.endswith("ة")) if norm_cur and norm_d else False
        if (norm_d.startswith(norm_cur) and len(norm_d) > len(norm_cur)) or heh_taa_fix:
            others = [o for o in bare_owners.get(norm_d, []) if o != lem.lemma_id]
            if others:
                plan.append({**row, "action": "skip",
                             "reason": f"rewrite target bare collides with {others}"})
            else:
                # stash=True: the old bare (often the masculine/collective
                # counterpart, e.g. كعك for كعكة) stays a lookup key via
                # forms_json, so no text mapping that works today breaks.
                plan.append({**row, "action": "rewrite_bare", "new_bare": d_bare,
                             "stash": True,
                             "reason": "bare truncated vs headword"
                                       + (" (ه→ة)" if heh_taa_fix else "")})
            continue

        # 6. Everything else (loanword spelling divergence etc.): additive alias
        #    unless the alias key is taken.
        plan.append({**row, "action": "add_alias", "alias": ar,
                     "reason": "display/bare spelling divergence; alias keeps identity stable"})
    return plan


def apply_plan(db, plan: list[dict], dry_run: bool = False) -> dict:
    """Execute a reviewed plan. Re-validates every precondition; skips on drift."""
    lemmas = canonical_lemmas(db)
    lookup = build_lemma_lookup(lemmas)
    stats = Counter()
    applied, skipped = [], []
    # Keys claimed by earlier rows in THIS run — the prebuilt lookup can't see
    # them, so without this two rewrites could race onto the same key.
    claimed: set[str] = set()

    for row in plan:
        action = row["action"]
        if action == "skip":
            stats["planned_skip"] += 1
            continue
        lem = db.get(Lemma, row["lemma_id"])
        if lem is None or lem.canonical_lemma_id is not None:
            skipped.append((row["lemma_id"], "gone or became variant since plan"))
            continue
        if (lem.lemma_ar_bare or "").strip() != row["bare"] or (lem.lemma_ar or "") != row["lemma_ar"]:
            skipped.append((row["lemma_id"], "row drifted since plan (bare/headword changed)"))
            continue

        if action == "rewrite_bare":
            new_bare = row["new_bare"]
            owner = key_owner(lookup, new_bare)
            if owner not in (None, lem.lemma_id):
                skipped.append((lem.lemma_id, f"new bare key now owned by #{owner}"))
                continue
            if _normalize(new_bare) in claimed:
                skipped.append((lem.lemma_id, "new bare key claimed earlier in this run"))
                continue
            claimed.add(_normalize(new_bare))
            if not dry_run:
                lem.lemma_ar_bare = new_bare
                if row.get("stash"):
                    forms = dict(lem.forms_json) if isinstance(lem.forms_json, dict) else {}
                    forms.setdefault(OLD_BARE_KEY, row["bare"])
                    lem.forms_json = forms
            stats["rewritten"] += 1
            applied.append({"lemma_id": lem.lemma_id, "action": action,
                            "old_bare": row["bare"], "new_bare": new_bare,
                            "stashed_old": bool(row.get("stash"))})

        elif action == "add_alias":
            alias_val = row["alias"]
            alias_key = _normalize(display_bare(alias_val))
            owner = key_owner(lookup, display_bare(alias_val))
            if owner not in (None, lem.lemma_id):
                skipped.append((lem.lemma_id, f"alias key owned by #{owner}"))
                continue
            if alias_key in claimed:
                skipped.append((lem.lemma_id, "alias key claimed earlier in this run"))
                continue
            claimed.add(alias_key)
            forms = dict(lem.forms_json) if isinstance(lem.forms_json, dict) else {}
            if forms.get(ALIAS_KEY) == alias_val:
                stats["already_aliased"] += 1
                continue
            if not dry_run:
                forms[ALIAS_KEY] = alias_val
                lem.forms_json = forms  # reassign: JSON column mutation tracking
            stats["aliased"] += 1
            applied.append({"lemma_id": lem.lemma_id, "action": action, "alias": alias_val})

        elif action == "clean_display":
            new_ar = row["new_lemma_ar"]
            if not new_ar or display_bare(new_ar) != display_bare(row["lemma_ar"]):
                skipped.append((lem.lemma_id, "clean_display would alter the word itself"))
                continue
            if not dry_run:
                lem.lemma_ar = new_ar
            stats["display_cleaned"] += 1
            applied.append({"lemma_id": lem.lemma_id, "action": action,
                            "old_lemma_ar": row["lemma_ar"], "new_lemma_ar": new_ar})
        else:
            skipped.append((row["lemma_id"], f"unknown action {action!r}"))

    if applied and not dry_run:
        db.add(ActivityLog(
            event_type="corrupt_bare_repair",
            summary=(
                f"Repaired citation-form self-resolution: {stats['rewritten']} bares "
                f"rewritten, {stats['aliased']} citation aliases added "
                f"(spec-2026-07-15 §8 follow-up c)"
            ),
            detail_json={"applied": applied,
                         "skipped": [{"lemma_id": a, "reason": b} for a, b in skipped],
                         "source": "repair_corrupt_bares.py"},
        ))
        db.commit()
    return {"stats": dict(stats), "applied": applied, "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--census", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="with --apply: validate only")
    ap.add_argument("--plan-file", default="/tmp/bare_repair_plan.json")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.plan:
            plan = propose(db)
            Path(args.plan_file).write_text(json.dumps(plan, ensure_ascii=False, indent=1))
            print(f"plan rows: {len(plan)}  {dict(Counter(r['action'] for r in plan))}")
            print(f"written to {args.plan_file} — review before --apply")
            for r in plan:
                extra = r.get("new_bare") or (display_bare(r.get("alias", "")) if r.get("alias") else "")
                print(f'  #{r["lemma_id"]:5d} {r["action"]:12s} {r["lemma_ar"]:16s} '
                      f'bare={r["bare"]:12s} -> {extra:12s} | {r["reason"]} | ({r["gloss"]})')
        elif args.apply:
            plan = json.loads(Path(args.plan_file).read_text())
            result = apply_plan(db, plan, dry_run=args.dry_run)
            print(json.dumps({"stats": result["stats"],
                              "skipped": result["skipped"]}, ensure_ascii=False, indent=1))
            print("census after apply:")
            failures = run_census(db)
            classes = Counter(f["class"] for f in failures)
            print(f"  self-resolution failures: {len(failures)}  {dict(classes)}")
            for f in failures:
                if f["class"] != "shadowed":
                    print(f'  STILL UNRESOLVED: #{f["lemma_id"]} {f["lemma_ar"]} '
                          f'bare={f["bare"]} ({f["gloss"]})')
        elif args.census:
            failures = run_census(db)
            print(f"failures: {len(failures)}  {dict(Counter(f['class'] for f in failures))}")
            for f in failures:
                print(f'  #{f["lemma_id"]:5d} [{f["class"]:10s}] {f["lemma_ar"]:16s} '
                      f'bare={f["bare"]:12s} -> {f["resolves_to"]} ({f["gloss"]})')
        else:
            ap.print_help()
    finally:
        db.close()


if __name__ == "__main__":
    main()
