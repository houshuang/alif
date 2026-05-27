"""Generate root-showcase sentences for top-N candidate roots.

Phase 4 of the root-showcase pipeline. Reads candidate roots from
root_showcase_candidates JSON (Phase 1 output) and for each top-N root
invokes generate_and_store_showcases_for_root() which:
  - Builds palette from gated canonical lemmas under the root
  - Generates N candidate sentences via Claude Sonnet
  - Validates each through the existing multi-target verifier path
  - Requires ≥3 distinct palette lemmas per accepted sentence
  - Persists with kind='root_showcase' and root_focus_id stamped

Dry-run by default. Use --apply to actually persist generated sentences.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.services.activity_log import log_activity
from app.services.root_showcase import generate_and_store_showcases_for_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates-json",
        default=None,
        help="Path to root_showcase_candidates JSON (default: research/root-showcase-candidates-<today>.json)",
    )
    parser.add_argument("--top", type=int, default=5, help="Top N candidate roots to generate for")
    parser.add_argument("--count", type=int, default=3, help="Sentences per root to attempt")
    parser.add_argument("--apply", action="store_true", help="Actually persist sentences (default: dry-run that still calls LLM but rolls back)")
    parser.add_argument("--root-id", type=int, default=None, help="Override: generate for a single root_id, ignoring candidates JSON")
    parser.add_argument("--no-quality-review", action="store_true", help="Skip the Haiku naturalness/translation review pass")
    parser.add_argument(
        "--output",
        default=None,
        help="Where to write the report (default: research/root-showcase-runs-<date>.json)",
    )
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    repo_root = Path(__file__).resolve().parents[2]
    output_path = Path(args.output) if args.output else repo_root / "research" / f"root-showcase-runs-{today}.json"

    if args.root_id is not None:
        targets = [{"root_id": args.root_id, "root": f"#{args.root_id}"}]
    else:
        candidates_path = Path(args.candidates_json) if args.candidates_json else repo_root / "research" / f"root-showcase-candidates-{today}.json"
        if not candidates_path.exists():
            print(f"ERROR: candidates file not found: {candidates_path}")
            print("Run root_showcase_candidates.py first.")
            sys.exit(1)
        candidates_doc = json.loads(candidates_path.read_text())
        targets = candidates_doc["candidates"][: args.top]

    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN (will roll back after persistence)'}")
    print(f"Processing {len(targets)} root(s); requesting {args.count} sentences each")
    print()

    runs: list[dict[str, Any]] = []
    total_persisted = 0
    all_sentence_ids: list[int] = []

    for i, target in enumerate(targets, 1):
        root_id = target["root_id"]
        root_str = target.get("root", f"#{root_id}")
        print(f"[{i}/{len(targets)}] {root_str} (id={root_id})")

        db = SessionLocal()
        try:
            result = generate_and_store_showcases_for_root(
                db, root_id, count=args.count,
                quality_review=not args.no_quality_review,
                persist=args.apply,
            )
        except Exception as e:
            db.rollback()
            print(f"  FAILED: {e}")
            runs.append({"root_id": root_id, "root": root_str, "error": str(e)})
            continue
        finally:
            db.close()

        run_dict = {
            "root_id": result.root_id,
            "root": result.root,
            "palette_size": result.palette_size,
            "requested": result.requested,
            "generated": result.generated,
            "persisted": result.persisted,
            "sentence_ids": result.sentence_ids if args.apply else [],
            "rejected_reasons": result.rejected_reasons,
        }
        runs.append(run_dict)

        if args.apply:
            total_persisted += result.persisted
            all_sentence_ids.extend(result.sentence_ids)

        print(f"  palette={result.palette_size}, generated={result.generated}, "
              f"persisted={'(rolled back) ' if not args.apply else ''}{result.persisted}")
        for reason in result.rejected_reasons[:5]:
            print(f"    rejected: {reason}")

        if args.apply and result.sentence_ids:
            print(f"  sentence_ids: {result.sentence_ids}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "applied": args.apply,
        "top_processed": len(targets),
        "total_persisted": total_persisted,
        "all_sentence_ids": all_sentence_ids,
        "runs": runs,
    }, ensure_ascii=False, indent=2))
    print()
    print(f"Report: {output_path}")

    if args.apply and all_sentence_ids:
        db = SessionLocal()
        try:
            log_activity(
                db,
                event_type="root_showcases_generated",
                summary=f"Generated {total_persisted} root-showcase sentences across {len(targets)} roots",
                detail={
                    "sentence_ids": all_sentence_ids,
                    "roots": [r["root"] for r in runs if r.get("persisted", 0) > 0],
                    "report_path": str(output_path),
                },
            )
        finally:
            db.close()


if __name__ == "__main__":
    main()
