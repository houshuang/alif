"""Rank roots as candidates for root-showcase sentence generation.

For each root, compute:
  - user_coverage = (known + acquiring) / total_gated_canonical_lemmas
  - score        = user_coverage * productivity_score
  - lemma palette grouped by wazn family
  - wazn families missing (relative to a canonical derivation set)

Read-only: no LLM calls, no DB writes. Output is JSON + HTML report into
research/. Run before generation to eyeball candidate roots and decide whether
the gap-filling LLM step should fire for missing wazn families.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func
from app.database import SessionLocal
from app.models import Lemma, Root, UserLemmaKnowledge


WAZN_TO_FAMILY: dict[str, str] = {
    # Verb forms
    "form_1": "verb_I", "form_2": "verb_II", "form_3": "verb_III",
    "form_4": "verb_IV", "form_5": "verb_V", "form_6": "verb_VI",
    "form_7": "verb_VII", "form_8": "verb_VIII", "form_9": "verb_IX",
    "form_10": "verb_X",
    # Agent (active participle)
    "fa'il": "agent",
    "muf'il": "agent", "mufa''il": "agent", "mufa'il": "agent",
    "mutafa''il": "agent", "mutafa'il": "agent", "munfa'il": "agent",
    "mufta'il": "agent", "mustaf'il": "agent",
    # Patient (passive participle)
    "maf'ul": "patient",
    "muf'al": "patient", "mufa''al": "patient", "mufa'al": "patient",
    "mufta'al": "patient", "mustaf'al": "patient",
    # Masdar (verbal nouns)
    "fa'l": "masdar_I", "fi'l": "masdar_I", "fu'l": "masdar_I",
    "fa'al": "masdar_I", "fi'al": "masdar_I", "fu'al": "masdar_I",
    "fa'ala": "masdar_I", "fi'ala": "masdar_I", "fu'la": "masdar_I",
    "fa'la": "masdar_I", "fi'la": "masdar_I", "fa'ula": "masdar_I",
    "fa'lan": "masdar_I", "fa'ul": "masdar_I", "fu'ul": "masdar_I",
    "taf'il": "masdar_II",
    "if'al": "masdar_IV",
    "ifti'al": "masdar_VIII",
    "istif'al": "masdar_X",
    # Place / time noun
    "maf'al": "place_or_time",
    "maf'il": "place_or_time",
    "maf'ala": "place_or_time",
    # Instrument noun
    "mif'al": "instrument",
    "mif'ala": "instrument",
    "mif'aal": "instrument",
    # Intensive / profession
    "fa''al": "intensive_profession",
    # Common adjective patterns from root
    "fa'iil": "adj_fa'iil",
    "nisba": "nisba_adj",
    "af'al": "elative",
}

# Families to report as missing if absent from a root. Adjectival/nisba forms
# are not flagged — those depend on whether the root semantically supports an
# adjective, which is hard to know up front.
TARGET_FAMILIES = [
    "verb_I", "verb_II", "verb_III", "verb_IV", "verb_V",
    "verb_VIII", "verb_X",
    "agent", "patient",
    "masdar_I", "masdar_II", "masdar_IV", "masdar_VIII", "masdar_X",
    "place_or_time", "instrument",
]


def family_for_wazn(wazn: str | None) -> str | None:
    if not wazn:
        return None
    return WAZN_TO_FAMILY.get(wazn)


def collect_root_data(db) -> list[dict[str, Any]]:
    """Build per-root candidate records for all roots with gated canonical lemmas."""
    # Pull all gated canonical lemmas (canonical = no canonical_lemma_id; variants
    # don't count toward palette since they merge into their canonical).
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.gates_completed_at.isnot(None))
        .filter(Lemma.canonical_lemma_id.is_(None))
        .filter(Lemma.root_id.isnot(None))
        .all()
    )

    knowledge = {k.lemma_id: k for k in db.query(UserLemmaKnowledge).all()}

    by_root: dict[int, list[Lemma]] = defaultdict(list)
    for l in lemmas:
        by_root[l.root_id].append(l)

    roots_by_id = {r.root_id: r for r in db.query(Root).filter(Root.root_id.in_(by_root.keys())).all()}

    candidates: list[dict[str, Any]] = []
    for root_id, root_lemmas in by_root.items():
        root = roots_by_id.get(root_id)
        if not root:
            continue

        state_counts: Counter[str] = Counter()
        wazn_counts: Counter[str] = Counter()
        families_present: set[str] = set()
        palette: list[dict[str, Any]] = []

        for l in root_lemmas:
            k = knowledge.get(l.lemma_id)
            state = k.knowledge_state if k else "unstudied"
            state_counts[state] += 1

            fam = family_for_wazn(l.wazn)
            if fam:
                families_present.add(fam)
            if l.wazn:
                wazn_counts[l.wazn] += 1

            palette.append({
                "lemma_id": l.lemma_id,
                "lemma_ar": l.lemma_ar,
                "pos": l.pos,
                "wazn": l.wazn,
                "family": fam,
                "gloss_en": l.gloss_en,
                "state": state,
            })

        total = len(root_lemmas)
        known = state_counts.get("known", 0) + state_counts.get("lapsed", 0)
        acquiring = state_counts.get("acquiring", 0)
        # Coverage = recognised + actively learning, vs total in vocab
        coverage = (known + acquiring) / total if total else 0.0
        productivity = root.productivity_score or 0
        # Score balances three axes:
        #   - how many derivations under this root the user already knows
        #     (the actual usable palette for a showcase sentence)
        #   - the root's productivity in the language (corpus frequency × derivation breadth)
        #   - takes sqrt of productivity so a 100× corpus-frequency gap doesn't
        #     drown out palette differences
        usable_palette = known + acquiring
        score = int(usable_palette * (productivity ** 0.5))

        missing_families = [f for f in TARGET_FAMILIES if f not in families_present]

        palette.sort(key=lambda p: (p["family"] or "z_unmapped", p["lemma_id"]))

        candidates.append({
            "root_id": root_id,
            "root": root.root,
            "core_meaning_en": root.core_meaning_en,
            "productivity_score": productivity,
            "total_canonical_lemmas": total,
            "state_counts": dict(state_counts),
            "user_coverage": round(coverage, 3),
            "score": int(score),
            "families_present": sorted(families_present),
            "missing_target_families": missing_families,
            "wazn_counts": dict(wazn_counts),
            "palette": palette,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def render_html(candidates: list[dict[str, Any]], top: int, generated_at: str) -> str:
    rows = []
    for i, c in enumerate(candidates[:top], 1):
        states = c["state_counts"]
        states_str = ", ".join(f"{k}: {v}" for k, v in sorted(states.items()))
        present = ", ".join(c["families_present"])
        missing = ", ".join(c["missing_target_families"])

        palette_rows = []
        for p in c["palette"]:
            state_class = f"st-{p['state']}"
            family = p["family"] or "—"
            palette_rows.append(
                f"<tr class='{state_class}'>"
                f"<td>#{p['lemma_id']}</td>"
                f"<td class='ar'>{p['lemma_ar']}</td>"
                f"<td>{p['pos'] or ''}</td>"
                f"<td>{p['wazn'] or ''}</td>"
                f"<td>{family}</td>"
                f"<td>{p['gloss_en'] or ''}</td>"
                f"<td>{p['state']}</td>"
                f"</tr>"
            )
        palette_html = (
            "<table class='palette'>"
            "<thead><tr><th>id</th><th>arabic</th><th>pos</th><th>wazn</th>"
            "<th>family</th><th>gloss</th><th>state</th></tr></thead>"
            f"<tbody>{''.join(palette_rows)}</tbody></table>"
        )

        rows.append(f"""
        <details class='root'>
          <summary>
            <span class='rank'>#{i}</span>
            <span class='root-ar'>{c['root']}</span>
            <span class='meta'>score={c['score']:,} · cov={c['user_coverage']:.0%} · gated={c['total_canonical_lemmas']} · prod={c['productivity_score']:,}</span>
            <span class='core'>{c['core_meaning_en'] or ''}</span>
          </summary>
          <div class='detail'>
            <div class='counts'><strong>States:</strong> {states_str}</div>
            <div class='present'><strong>Families present:</strong> {present or '—'}</div>
            <div class='missing'><strong>Missing target families:</strong> {missing or '—'}</div>
            {palette_html}
          </div>
        </details>
        """)

    return f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<title>Root showcase candidates — {generated_at}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .intro {{ color: #555; margin-bottom: 2rem; }}
  details.root {{ border-bottom: 1px solid #eee; padding: 0.6rem 0; }}
  details.root summary {{ cursor: pointer; display: flex; gap: 0.8rem; align-items: baseline; flex-wrap: wrap; }}
  .rank {{ color: #888; font-variant-numeric: tabular-nums; min-width: 2.5rem; }}
  .root-ar {{ font-size: 1.4rem; font-family: 'Scheherazade New', 'Noto Naskh Arabic', serif; }}
  .meta {{ color: #444; font-variant-numeric: tabular-nums; font-size: 0.9rem; }}
  .core {{ color: #666; font-style: italic; font-size: 0.9rem; flex: 1; }}
  .detail {{ margin: 0.8rem 0 0 3rem; font-size: 0.9rem; }}
  .detail > div {{ margin: 0.3rem 0; }}
  table.palette {{ margin-top: 0.6rem; border-collapse: collapse; width: 100%; }}
  table.palette th, table.palette td {{ padding: 0.2rem 0.6rem; text-align: left; border-bottom: 1px solid #f5f5f5; font-size: 0.85rem; }}
  table.palette th {{ background: #fafafa; color: #555; font-weight: 500; }}
  td.ar {{ font-family: 'Scheherazade New', 'Noto Naskh Arabic', serif; font-size: 1.1rem; }}
  tr.st-known {{ background: #e8f5e9; }}
  tr.st-acquiring {{ background: #fff8e1; }}
  tr.st-encountered {{ background: #f3e5f5; }}
  tr.st-lapsed {{ background: #ffebee; }}
  tr.st-suspended {{ color: #999; }}
  tr.st-unstudied {{ color: #777; }}
  .legend {{ margin: 1rem 0; padding: 0.6rem 1rem; background: #fafafa; border-radius: 4px; font-size: 0.85rem; }}
  .legend span {{ display: inline-block; padding: 0.1rem 0.5rem; margin-right: 0.4rem; border-radius: 3px; }}
</style>
</head>
<body>
<h1>Root showcase candidates</h1>
<div class='intro'>
  Generated {generated_at}. Top {top} of {len(candidates)} roots (≥3 gated canonical lemmas).
  Score = <code>(known + acquiring) × √productivity_score</code> — rewards both palette
  size <em>and</em> corpus frequency, so a 1-lemma root can't win.
  <br><strong>Caveat:</strong> "Missing target families" relies on <code>Lemma.wazn</code>
  which is NULL on ~50% of gated lemmas. The list overcounts. Phase 3 (LLM gap-fill)
  inspects the full palette directly rather than trusting this column.
</div>
<div class='legend'>
  <strong>State colour:</strong>
  <span class='st-known' style='background:#e8f5e9;'>known</span>
  <span class='st-acquiring' style='background:#fff8e1;'>acquiring</span>
  <span class='st-encountered' style='background:#f3e5f5;'>encountered</span>
  <span class='st-lapsed' style='background:#ffebee;'>lapsed</span>
  <span>suspended / unstudied</span>
</div>
{''.join(rows)}
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=50, help="Top N roots to include in HTML")
    parser.add_argument("--min-palette", type=int, default=3, help="Skip roots with fewer than N canonical gated lemmas (showcase needs a palette)")
    parser.add_argument("--json", default=None, help="JSON output path (default: research/root-showcase-candidates-<date>.json)")
    parser.add_argument("--html", default=None, help="HTML output path (default: research/root-showcase-candidates-<date>.html)")
    args = parser.parse_args()

    today = date.today().isoformat()
    repo_root = Path(__file__).resolve().parents[2]
    json_path = Path(args.json) if args.json else repo_root / "research" / f"root-showcase-candidates-{today}.json"
    html_path = Path(args.html) if args.html else repo_root / "research" / f"root-showcase-candidates-{today}.html"

    db = SessionLocal()
    try:
        candidates = collect_root_data(db)
    finally:
        db.close()

    candidates = [c for c in candidates if c["total_canonical_lemmas"] >= args.min_palette]
    candidates.sort(key=lambda c: c["score"], reverse=True)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps({
        "generated_at": generated_at,
        "total_roots": len(candidates),
        "candidates": candidates,
    }, ensure_ascii=False, indent=2))
    html_path.write_text(render_html(candidates, args.top, generated_at))

    top_preview = candidates[:10]
    print(f"Analysed {len(candidates)} roots")
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    print()
    print("Top 10:")
    for i, c in enumerate(top_preview, 1):
        print(f"  {i:2}. {c['root']:<10} score={c['score']:>12,} cov={c['user_coverage']:.0%} "
              f"gated={c['total_canonical_lemmas']:>3} missing={len(c['missing_target_families'])}")


if __name__ == "__main__":
    main()
