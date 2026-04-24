"""Phase 1 audit: classify all lemmas in the prod DB by decomposition status.

Uses CAMeL Tools morphology as primary classifier (regex strip-clitics has too
many false positives — كلب gets misread as ك+لب, فهم as ف+هم, etc.). The CAMeL
analyzer returns per-analysis prc0/prc1/enc0 features that mark genuine clitic
boundaries.

Buckets:
  A) canonical            — no CAMeL analysis flags clitics
  B) compound_with_canonical — CAMeL flags clitics AND its lex matches another DB lemma
  C) orphan_compound      — CAMeL flags clitics but lex not in DB (canonical missing)
  D) ambiguous            — multiple CAMeL analyses give different non-trivial lex values

For each compound, attaches: canonical lemma_id, ULK review history, CAMeL features.

Usage:
    /usr/local/bin/python3 scripts/audit_lemma_decomposition.py \
        --db backend/data/alif.prod.db \
        --out research/decomposition-classification-2026-04-24.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path


DIACRITICS = "".join(chr(c) for c in range(0x064B, 0x0653)) + "ٰـ"
_DIA_TABLE = str.maketrans("", "", DIACRITICS)


def strip_diacritics(s: str) -> str:
    return s.translate(_DIA_TABLE)


def normalize_alef(s: str) -> str:
    """Mirror sentence_validator.normalize_alef."""
    if not s:
        return s
    out = strip_diacritics(s)
    out = (out
           .replace("إ", "ا").replace("أ", "ا").replace("آ", "ا").replace("ٱ", "ا")
           .replace("ى", "ي").replace("ة", "ه"))
    return out


def clitic_signals(analysis: dict) -> dict:
    """Return all non-trivial clitic features from a CAMeL analysis.

    Returns dict with keys: enc0, prc0, prc1, prc2, prc3 — each value is the
    label (e.g. '3mp_dobj') or None if absent.
    """
    out = {}
    for key in ("prc0", "prc1", "prc2", "prc3", "enc0"):
        v = analysis.get(key)
        if v and v not in ("0", "na"):
            out[key] = v
    return out


def confidence_tier(signals: dict, compound_pos: str, canonical_pos: str) -> str:
    """Classify how confident we are this is a real compound (vs MLE noise).

    HIGH: enc0 (object pronoun suffix) OR prc1 (prep prefix bi/li/ka). These
        rarely false-positive because they require a specific morpheme shape.
    MEDIUM: prc0=Al_det (definite article). Reliable when canonical also exists
        as a separate bare lemma — that means we have a duplicate.
    LOW: prc2 in (wa_conj, fa_conj, wa_sub, fa_sub) only. These have many false
        positives for short verbs/nouns where MLE prefers compound reading.
    SKIP: POS mismatch suggesting MLE misanalysis (verb misread as pronoun+prefix).
    """
    pos_compat = _pos_compatible(compound_pos, canonical_pos)
    has_enc = "enc0" in signals
    has_prep_prefix = "prc1" in signals  # bi_prep, li_prep, ka_prep
    has_def_article = signals.get("prc0") == "Al_det"
    has_conj_only = (
        not has_enc and not has_prep_prefix and not has_def_article
        and ("prc2" in signals or "prc3" in signals)
    )

    if not pos_compat:
        return "SKIP_POS_MISMATCH"
    if has_enc or has_prep_prefix:
        return "HIGH"
    if has_def_article:
        return "MEDIUM"
    if has_conj_only:
        return "LOW"
    return "MEDIUM"  # multi-clitic without enc/prep, default medium


def _pos_compatible(compound_pos: str | None, canonical_pos: str | None) -> bool:
    """Check if the compound's POS is consistent with the canonical's POS.

    A verb-bare-form being analyzed as 'pronoun + fa-prefix' is incompatible —
    that's the MLE misanalysis pattern. Returns True if compatible OR unknown.
    """
    if not compound_pos or not canonical_pos:
        return True
    cp = compound_pos.lower()
    kp = canonical_pos.lower()
    # Verbs and nouns shouldn't dedup to pronouns/particles
    content_pos = {"verb", "noun", "adj", "adv"}
    function_pos = {"pron", "particle", "prep", "conj"}
    if cp in content_pos and kp in function_pos:
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="backend/data/alif.prod.db")
    ap.add_argument("--out", default="research/decomposition-classification-2026-04-24.json")
    ap.add_argument("--limit", type=int, help="Limit lemmas processed (for testing)")
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Lazy-load CAMeL
    try:
        from camel_tools.morphology.database import MorphologyDB
        from camel_tools.morphology.analyzer import Analyzer
    except ImportError:
        print("ERROR: camel_tools not available. Run with /usr/local/bin/python3", file=sys.stderr)
        sys.exit(1)

    print("Loading CAMeL Tools morphology DB + MLE disambiguator...", file=sys.stderr)
    morph_db = MorphologyDB.builtin_db()
    analyzer = Analyzer(morph_db, backoff="ADD_PROP")
    from camel_tools.disambig.mle import MLEDisambiguator
    disambig = MLEDisambiguator.pretrained()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    lemmas = list(conn.execute("""
        SELECT lemma_id, lemma_ar, lemma_ar_bare, gloss_en, source,
               canonical_lemma_id, root_id
        FROM lemmas
    """))
    if args.limit:
        lemmas = lemmas[: args.limit]
    print(f"Loaded {len(lemmas)} lemmas from {db_path}", file=sys.stderr)

    # Build normalized-bare → lemma_id lookup
    bare_lookup: dict[str, list[int]] = {}
    for row in lemmas:
        bn = normalize_alef(row["lemma_ar_bare"] or "")
        if bn:
            bare_lookup.setdefault(bn, []).append(row["lemma_id"])

    # Pull ULK review history
    ulk_rows = conn.execute("""
        SELECT lemma_id, knowledge_state, source, total_encounters,
               times_seen, times_correct, fsrs_card_json,
               last_reviewed, introduced_at
        FROM user_lemma_knowledge
    """).fetchall()
    ulk_by_lemma = {}
    for r in ulk_rows:
        d = dict(r)
        try:
            fc = json.loads(d.get("fsrs_card_json") or "{}")
            d["stability"] = fc.get("stability")
        except Exception:
            d["stability"] = None
        ulk_by_lemma[r["lemma_id"]] = d

    results = {
        "canonical": [],
        "compound_with_canonical": [],
        "orphan_compound": [],
        "no_analysis": [],
    }

    print("Classifying lemmas via CAMeL Tools...", file=sys.stderr)
    for i, row in enumerate(lemmas):
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(lemmas)}", file=sys.stderr)
        lid = row["lemma_id"]
        bare = row["lemma_ar_bare"] or ""
        bare_norm = normalize_alef(bare)
        if not bare_norm:
            continue

        ulk = ulk_by_lemma.get(lid, {})
        common = {
            "lemma_id": lid,
            "lemma_ar": row["lemma_ar"],
            "lemma_ar_bare": bare,
            "gloss_en": row["gloss_en"],
            "source": row["source"],
            "canonical_lemma_id": row["canonical_lemma_id"],
            "ulk_state": ulk.get("knowledge_state"),
            "ulk_source": ulk.get("source"),
            "times_seen": ulk.get("times_seen") or 0,
            "times_correct": ulk.get("times_correct") or 0,
            "total_encounters": ulk.get("total_encounters") or 0,
            "stability": ulk.get("stability"),
            "last_reviewed": ulk.get("last_reviewed"),
        }

        # Run MLE on diacritized form (preferred), fallback to bare.
        diac_form = row["lemma_ar"] or bare
        top_analysis = None
        try:
            res = disambig.disambiguate([diac_form])
            if res and res[0].analyses:
                top_analysis = res[0].analyses[0].analysis
            else:
                res = disambig.disambiguate([bare])
                if res and res[0].analyses:
                    top_analysis = res[0].analyses[0].analysis
        except Exception:
            top_analysis = None

        if not top_analysis:
            results["no_analysis"].append(common)
            continue

        signals = clitic_signals(top_analysis)
        compound_pos = (row["lemma_ar"] and top_analysis.get("pos")) or top_analysis.get("pos")
        # The compound's *DB-stored* pos column gives us ground truth on what the
        # human curator intended:
        db_pos_row = conn.execute(
            "SELECT pos FROM lemmas WHERE lemma_id = ?", (lid,)
        ).fetchone()
        db_compound_pos = db_pos_row[0] if db_pos_row else None

        if not signals:
            # No clitic features → canonical
            results["canonical"].append(common)
            continue

        # Has clitic features. Resolve canonical via lex.
        lex = top_analysis.get("lex") or ""
        lex_norm = normalize_alef(lex)

        # Filter: if lex equals bare, MLE is stripping a feature (typically
        # Al_det) from lemma_ar that's already absent from bare. Not a real
        # duplication — the bare IS the canonical.
        if lex_norm and lex_norm == bare_norm:
            common["mle_lex_equals_bare"] = {"lex": lex, "signals": signals}
            results["canonical"].append(common)
            continue
        canonical_lid = None
        canonical_pos = None
        if lex_norm and lex_norm in bare_lookup:
            for cand_lid in bare_lookup[lex_norm]:
                if cand_lid != lid:
                    canonical_lid = cand_lid
                    cp_row = conn.execute(
                        "SELECT pos FROM lemmas WHERE lemma_id = ?", (cand_lid,)
                    ).fetchone()
                    canonical_pos = cp_row[0] if cp_row else None
                    break

        tier = confidence_tier(signals, db_compound_pos or "", canonical_pos or "")

        record = {
            **common,
            "db_pos": db_compound_pos,
            "mle_lex": lex,
            "mle_lex_norm": lex_norm,
            "canonical_lemma_id_resolved": canonical_lid,
            "canonical_pos": canonical_pos,
            "clitic_signals": signals,
            "confidence": tier,
        }

        if tier == "SKIP_POS_MISMATCH":
            # MLE misanalysis — keep as canonical, but record the false-positive
            # signal so the report can show how often this fires.
            common["mle_misanalysis"] = {
                "lex": lex, "signals": signals,
                "would_resolve_to": canonical_lid,
            }
            results["canonical"].append(common)
            continue

        if canonical_lid is None:
            results["orphan_compound"].append(record)
        else:
            results["compound_with_canonical"].append(record)

    # Sort each non-canonical bucket by review impact
    for bucket in ("compound_with_canonical", "orphan_compound"):
        results[bucket].sort(
            key=lambda x: (x.get("times_seen") or 0, x.get("total_encounters") or 0),
            reverse=True,
        )

    # Confidence tier breakdown
    tier_counter = Counter(e.get("confidence") for e in results["compound_with_canonical"])
    tier_reviews = Counter()
    for e in results["compound_with_canonical"]:
        tier_reviews[e["confidence"]] += (e.get("times_seen") or 0)

    src_counter = Counter(e.get("source") for e in results["compound_with_canonical"])
    pos_mismatch_count = sum(1 for e in results["canonical"] if e.get("mle_misanalysis"))

    summary = {
        "db_path": str(db_path),
        "total_lemmas": len(lemmas),
        "buckets": {k: len(v) for k, v in results.items()},
        "compound_with_canonical_by_tier": dict(tier_counter),
        "review_impact_by_tier": dict(tier_reviews),
        "review_impact": {
            "compound_with_canonical_times_seen": sum(
                (e.get("times_seen") or 0) for e in results["compound_with_canonical"]
            ),
            "compound_with_canonical_total_encounters": sum(
                (e.get("total_encounters") or 0) for e in results["compound_with_canonical"]
            ),
            "orphan_compound_times_seen": sum(
                (e.get("times_seen") or 0) for e in results["orphan_compound"]
            ),
            "orphan_compound_total_encounters": sum(
                (e.get("total_encounters") or 0) for e in results["orphan_compound"]
            ),
        },
        "compound_source_distribution": dict(src_counter.most_common()),
        "mle_pos_mismatch_filtered": pos_mismatch_count,
    }
    # Drop the empty 'ambiguous' bucket — MLE returns single top result so it's
    # not populated by this strategy.
    results.pop("ambiguous", None)
    print(json.dumps(summary, indent=2, ensure_ascii=False), file=sys.stderr)

    out = {"summary": summary, "buckets": results}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
