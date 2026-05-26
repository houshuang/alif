"""One-shot migration: flip Latin Lemma.lemma_form from u-spelled to v-spelled
(modern reading orthography — distinguish v from u, but keep i for both vocalic
and consonantal). The lookup key (``lemma_bare``) stays untouched — this is
purely a display-side rewrite.

Driven by the per-source policy decided 2026-05-26 (see polyglot/CLAUDE.md):

  - **LLPSI** lemmas (`source='llpsi'`): the TSV ships natively v-spelled
    (`vir`, `vocabulum`, `navis`, `iuvenis`). Look the lemma up by `lemma_bare`
    in `data/vocab/llpsi_fr.tsv` and copy the TSV `lemma` column verbatim
    (macron-stripped). The LLPSI convention IS the target convention.
  - **Roma Aeterna** lemmas (`source='roma_aeterna'`): the TSV ships v too
    (some macron'd, e.g. `cōnsul`). Look up by `lemma_bare` in
    `data/vocab/roma_aeterna.tsv`, macron-strip, and copy.
  - **Reading-intake / quality-gate / frequency-core** lemmas (no TSV row):
    apply the `_to_modern_reading_orthography` heuristic from `la.py`. The
    heuristic is conservative — it transforms word-initial + intervocalic
    u only (so `uocabulum` → `vocabulum`, `iuuenis` → `iuvenis`, but `seruus`
    stays `seruus` because the same shape is vocalic in `pensum`). It does
    NOT touch `i`. Any miss surfaces in `--report` output for manual override.

Idempotent: running twice does nothing the second time. Dry-run is the default
(prints planned changes + counts). Pass ``--apply`` to commit.

USAGE
    polyglot/.venv/bin/python scripts/migrate_latin_lemma_orthography.py
    polyglot/.venv/bin/python scripts/migrate_latin_lemma_orthography.py --apply
    polyglot/.venv/bin/python scripts/migrate_latin_lemma_orthography.py \\
        --db-url sqlite:////opt/alif/polyglot/polyglot.db --apply
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import Lemma  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.languages.la import (  # noqa: E402
    _normalize_latin,
    _to_modern_reading_orthography,
)

LOG = logging.getLogger("migrate_latin_orthography")

DEFAULT_LLPSI = REPO_ROOT / "data" / "vocab" / "llpsi_fr.tsv"
DEFAULT_RA = REPO_ROOT / "data" / "vocab" / "roma_aeterna.tsv"


# ─── TSV loaders ────────────────────────────────────────────────────────────


def _macron_strip(form: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", form)
                   if unicodedata.category(c) != "Mn")


def load_tsv_display_map(path: Path) -> dict[str, str]:
    """``{lemma_bare: display_form}`` from a vocab TSV. Display = TSV `lemma`
    column with macrons stripped (everything else preserved, including v/j
    spelling). When the TSV has both an inflected `velle` and the canonical
    `volo` for the same bare, the LAST entry wins — TSVs are sorted with
    canonical citations later for some lists. Idempotent."""
    out: dict[str, str] = {}
    if not path.exists():
        LOG.warning("TSV not found, skipping: %s", path)
        return out
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            raw = (row.get("lemma") or "").strip()
            if not raw:
                continue
            bare = _normalize_latin(raw)
            display = _macron_strip(raw)
            out[bare] = display
    return out


# ─── Planning ──────────────────────────────────────────────────────────────


@dataclass
class PlannedChange:
    lemma_id: int
    source: str | None
    lemma_bare: str
    current_form: str
    new_form: str
    decided_by: str  # "llpsi_tsv" / "roma_aeterna_tsv" / "heuristic" / "unchanged"


def plan_changes(
    db: Session,
    *,
    llpsi_map: dict[str, str],
    ra_map: dict[str, str],
) -> list[PlannedChange]:
    plans: list[PlannedChange] = []
    lemmas = db.query(Lemma).filter(Lemma.language_code == "la").all()
    LOG.info("Scanning %d Latin lemmas", len(lemmas))
    for lemma in lemmas:
        bare = lemma.lemma_bare or ""
        current = lemma.lemma_form or ""
        # Source preference: LLPSI > RA > heuristic. LLPSI is the densest
        # authoritative list; RA covers later chapters; the heuristic handles
        # everything else (reading-intake, quality-gate, DCC frequency-core).
        if bare in llpsi_map:
            new_form = llpsi_map[bare]
            decider = "llpsi_tsv"
        elif bare in ra_map:
            new_form = ra_map[bare]
            decider = "roma_aeterna_tsv"
        else:
            new_form = _to_modern_reading_orthography(_macron_strip(current))
            decider = "heuristic"
        if new_form == current:
            continue
        plans.append(PlannedChange(
            lemma_id=lemma.lemma_id,
            source=lemma.source,
            lemma_bare=bare,
            current_form=current,
            new_form=new_form,
            decided_by=decider,
        ))
    return plans


def summarize(plans: list[PlannedChange]) -> str:
    if not plans:
        return "No changes — every lemma_form already matches its expected v/j display."
    by_source = Counter((p.source or "(none)", p.decided_by) for p in plans)
    lines = [
        f"Planned changes: {len(plans)} of {len(plans)} lemmas to update.",
        "",
        "By (Lemma.source, decided_by):",
    ]
    for (source, decider), n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:5d}  source={source!r:25s} decided_by={decider}")
    lines.append("")
    lines.append("Sample of planned changes (up to 30):")
    for p in plans[:30]:
        lines.append(
            f"  [{p.lemma_id:6d}] {p.lemma_bare:20s} "
            f"{p.current_form!r:25s} → {p.new_form!r:25s}  ({p.decided_by})"
        )
    if len(plans) > 30:
        lines.append(f"  ... and {len(plans) - 30} more")
    return "\n".join(lines)


# ─── Apply ─────────────────────────────────────────────────────────────────


def apply_changes(db: Session, plans: list[PlannedChange]) -> int:
    n = 0
    for plan in plans:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == plan.lemma_id).one_or_none()
        if lemma is None:
            LOG.warning("Lemma %d disappeared between plan and apply, skipping",
                        plan.lemma_id)
            continue
        # Guard against concurrent edits: only update if the form still matches
        # what we planned against. If something else changed it in between, skip.
        if (lemma.lemma_form or "") != plan.current_form:
            LOG.warning("Lemma %d changed under us (was %r, now %r), skipping",
                        plan.lemma_id, plan.current_form, lemma.lemma_form)
            continue
        lemma.lemma_form = plan.new_form
        n += 1
        if n % 500 == 0:
            db.commit()
            LOG.info("Committed %d updates so far", n)
    db.commit()
    return n


# ─── CLI ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-url", default=os.environ.get("POLYGLOT_DATABASE_URL")
                    or os.environ.get("DATABASE_URL")
                    or f"sqlite:///{REPO_ROOT / 'polyglot.db'}",
                    help="SQLAlchemy URL (defaults to local polyglot.db)")
    ap.add_argument("--llpsi-tsv", type=Path, default=DEFAULT_LLPSI)
    ap.add_argument("--ra-tsv", type=Path, default=DEFAULT_RA)
    ap.add_argument("--apply", action="store_true",
                    help="Commit the changes. Without this flag, prints plan only.")
    ap.add_argument("--report", type=Path, default=None,
                    help="Write the plan to this path (in addition to stdout)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    llpsi_map = load_tsv_display_map(args.llpsi_tsv)
    ra_map = load_tsv_display_map(args.ra_tsv)
    LOG.info("Loaded %d LLPSI + %d Roma Aeterna display mappings",
             len(llpsi_map), len(ra_map))

    engine = create_engine(args.db_url, future=True)
    Sess = sessionmaker(bind=engine, future=True)
    with Sess() as db:
        plans = plan_changes(db, llpsi_map=llpsi_map, ra_map=ra_map)
        report = summarize(plans)
        print(report)
        if args.report:
            args.report.write_text(report + "\n", encoding="utf-8")
            LOG.info("Wrote report to %s", args.report)

        if not args.apply:
            LOG.info("DRY RUN. Pass --apply to commit.")
            return 0

        if not plans:
            LOG.info("Nothing to do.")
            return 0

        n = apply_changes(db, plans)
        log_activity(
            db,
            event_type="latin_lemma_orthography_migrated",
            summary=f"Latin lemma_form migrated to modern v/j orthography ({n} updates)",
            detail={
                "updated": n,
                "by_source": {f"{p.source or 'none'}/{p.decided_by}":
                              sum(1 for q in plans
                                  if (q.source or 'none') == (p.source or 'none')
                                  and q.decided_by == p.decided_by)
                              for p in plans},
            },
            language_code="la",
        )
        LOG.info("Applied %d updates.", n)

    return 0


if __name__ == "__main__":
    sys.exit(main())
