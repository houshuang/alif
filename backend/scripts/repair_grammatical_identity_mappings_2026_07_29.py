"""Repair deterministic, fully vocalized أَنْ/أَنَّ/إِنْ/إِنَّ mappings.

This is the data companion to the 2026-07-29 grammatical-identity hardening.
It deliberately does *not* run the general lemma resolver: bare forms such as
``أن``, ``بأن``, ``وأن``, and ``وإن`` are genuinely ambiguous in unvocalized
text.  Only an exact sukūn/shadda-bearing identity is eligible.

The workflow is two-phase and fail-closed:

    cd /opt/alif/backend
    .venv/bin/python3 scripts/repair_grammatical_identity_mappings_2026_07_29.py \
        --plan --plan-file /tmp/grammatical_identity_plan.json
    # Review the JSON, back up the live database, then:
    .venv/bin/python3 scripts/repair_grammatical_identity_mappings_2026_07_29.py \
        --apply --plan-file /tmp/grammatical_identity_plan.json \
        --expected-sha256 <printed-plan-sha256> --backup-confirmed

The plan records every row's original mapping and sentence lifecycle state.
Apply rechecks the complete plan before writing anything, then updates all rows
in one transaction while holding the shared material-update lock and a SQLite
writer boundary acquired before live validation. Drift or a busy lock aborts
the whole operation; a non-cooperating DB writer cannot enter between
validation and flush. The script never changes sentence activation,
mapping-verification stamps, target bookkeeping, learner state, or historical
reviews. Target-sensitive rows are reported but excluded for separate
contextual repair.

Lexical ``لأنّ`` is intentionally out of scope.  Prefix handling is limited to
بـ and و, with or without their own vowel mark; the particle itself must retain
the exact vocalization that distinguishes its stored lemma.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ActivityLog, Lemma, Sentence, SentenceWord  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    ARABIC_DIACRITICS,
    _exact_correction_form,
)


PLAN_VERSION = 1
DEFAULT_PLAN_FILE = Path("/tmp/alif_grammatical_identity_plan_20260729.json")
MATERIAL_UPDATE_LOCK = Path(
    os.environ.get(
        "ALIF_UPDATE_MATERIAL_LOCK",
        "/tmp/alif-update-material.lock",
    )
)

# Exact particle identity -> exact canonical lemma identity.
PARTICLE_IDENTITIES = {
    "أَنْ": "أَنْ",
    "أَنَّ": "أَنَّ",
    "إِنْ": "إِنْ",
    "إِنَّ": "إِنَّ",
}

# Prefixes whose composition with these particles is grammatical rather than a
# separately stored lexical headword.  ل is excluded to preserve لأنّ #447.
COMPOSITIONAL_PREFIXES = {"ب", "و"}


def _try_acquire_material_update_lock():
    """Acquire the shared material-writer lock, or return ``None`` if busy."""
    MATERIAL_UPDATE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = MATERIAL_UPDATE_LOCK.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _release_material_update_lock(handle) -> None:
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _strip_leading_combining_marks(value: str) -> str:
    """Remove only marks attached to a prefix that has already been removed."""
    index = 0
    while index < len(value):
        char = value[index]
        if not (unicodedata.combining(char) or ARABIC_DIACRITICS.fullmatch(char)):
            break
        index += 1
    return value[index:]


def exact_particle_identity(surface_form: str | None) -> str | None:
    """Return the exact base particle identity, or ``None`` when ambiguous.

    Examples:
      أَنَّ / بِأَنَّ / بأنَّ -> أَنَّ
      وَإِنْ / وإنْ          -> إِنْ
      أن / بأن / وإن / بان   -> None
      لِأَنَّ                -> None (lexical لأنّ is intentionally preserved)
    """
    exact = _exact_correction_form(surface_form)
    if exact in PARTICLE_IDENTITIES:
        return PARTICLE_IDENTITIES[exact]
    if not exact or exact[0] not in COMPOSITIONAL_PREFIXES:
        return None
    remainder = _strip_leading_combining_marks(exact[1:])
    return PARTICLE_IDENTITIES.get(remainder)


def resolve_identity_lemma_ids(db) -> dict[str, int]:
    """Resolve each exact identity to one gated canonical lemma or abort."""
    rows = (
        db.query(Lemma)
        .filter(
            Lemma.canonical_lemma_id.is_(None),
            Lemma.gates_completed_at.isnot(None),
        )
        .all()
    )
    by_identity: dict[str, list[Lemma]] = {
        identity: [] for identity in PARTICLE_IDENTITIES
    }
    for lemma in rows:
        identity = _exact_correction_form(lemma.lemma_ar)
        if identity in by_identity:
            by_identity[identity].append(lemma)

    resolved: dict[str, int] = {}
    failures: list[str] = []
    for identity, lemmas in by_identity.items():
        if len(lemmas) != 1:
            failures.append(
                f"{identity}: expected one gated canonical lemma, found "
                f"{[(lemma.lemma_id, lemma.lemma_ar) for lemma in lemmas]}"
            )
            continue
        resolved[identity] = lemmas[0].lemma_id
    if failures:
        raise RuntimeError("identity inventory is not unique:\n" + "\n".join(failures))
    return resolved


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _sentence_snapshot(db) -> dict[str, int]:
    return {
        "sentences": int(db.query(func.count(Sentence.id)).scalar() or 0),
        "sentence_words": int(db.query(func.count(SentenceWord.id)).scalar() or 0),
        "active_sentences": int(
            db.query(func.count(Sentence.id))
            .filter(Sentence.is_active.is_(True))
            .scalar()
            or 0
        ),
        "target_words": int(
            db.query(func.count(SentenceWord.id))
            .filter(SentenceWord.is_target_word.is_(True))
            .scalar()
            or 0
        ),
    }


def build_repair_plan(db) -> dict[str, Any]:
    """Build a deterministic, read-only repair plan."""
    identity_ids = resolve_identity_lemma_ids(db)
    rows = (
        db.query(SentenceWord, Sentence)
        .join(Sentence, Sentence.id == SentenceWord.sentence_id)
        .order_by(SentenceWord.id)
        .all()
    )

    repairs: list[dict[str, Any]] = []
    target_sensitive: list[dict[str, Any]] = []
    by_identity: Counter[str] = Counter()
    by_state: Counter[str] = Counter()

    for word, sentence in rows:
        identity = exact_particle_identity(word.surface_form)
        if identity is None:
            continue
        expected_id = identity_ids[identity]
        if word.lemma_id == expected_id:
            continue

        row = {
            "sentence_word_id": word.id,
            "sentence_id": sentence.id,
            "surface_form": word.surface_form,
            "exact_identity": identity,
            "current_lemma_id": word.lemma_id,
            "expected_lemma_id": expected_id,
            "is_target_word": bool(word.is_target_word),
            "sentence_target_lemma_id": sentence.target_lemma_id,
            "sentence_is_active": bool(sentence.is_active),
            "mappings_verified_at": _iso(sentence.mappings_verified_at),
            "quality_reviewed_at": _iso(sentence.quality_reviewed_at),
        }
        # A target correction can require changing Sentence.target_lemma_id,
        # target flags, and possibly rejecting the generated sentence. That is
        # contextual work, not this deterministic collateral-token repair.
        if (
            word.is_target_word
            or sentence.target_lemma_id in {word.lemma_id, expected_id}
        ):
            target_sensitive.append(row)
            continue

        repairs.append(row)
        by_identity[identity] += 1
        if sentence.is_active:
            by_state["active"] += 1
        else:
            by_state["inactive"] += 1

    return {
        "plan_version": PLAN_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "identity_lemma_ids": identity_ids,
        "snapshot": _sentence_snapshot(db),
        "repair_count": len(repairs),
        "target_sensitive_count": len(target_sensitive),
        "counts_by_identity": dict(sorted(by_identity.items())),
        "counts_by_state": dict(sorted(by_state.items())),
        "repairs": repairs,
        "target_sensitive": target_sensitive,
    }


def _validate_plan_header(db, plan: dict[str, Any]) -> dict[str, int]:
    if plan.get("plan_version") != PLAN_VERSION:
        raise RuntimeError(
            f"unsupported plan version {plan.get('plan_version')!r}; "
            f"expected {PLAN_VERSION}"
        )
    repairs = plan.get("repairs")
    if not isinstance(repairs, list):
        raise RuntimeError("plan repairs must be a list")
    if plan.get("repair_count") != len(repairs):
        raise RuntimeError("plan repair_count does not match repairs")

    live_identity_ids = resolve_identity_lemma_ids(db)
    planned_identity_ids = {
        str(identity): int(lemma_id)
        for identity, lemma_id in (plan.get("identity_lemma_ids") or {}).items()
    }
    if planned_identity_ids != live_identity_ids:
        raise RuntimeError(
            "identity inventory drifted since planning: "
            f"planned={planned_identity_ids}, live={live_identity_ids}"
        )
    return live_identity_ids


def _validate_repair_rows(
    db,
    plan: dict[str, Any],
    identity_ids: dict[str, int],
) -> list[SentenceWord]:
    """Recheck every precondition before the first write."""
    validated: list[SentenceWord] = []
    errors: list[str] = []
    seen_ids: set[int] = set()

    for item in plan["repairs"]:
        word_id = item.get("sentence_word_id")
        if not isinstance(word_id, int) or isinstance(word_id, bool):
            errors.append(f"invalid sentence_word_id: {word_id!r}")
            continue
        if word_id in seen_ids:
            errors.append(f"duplicate sentence_word_id: {word_id}")
            continue
        seen_ids.add(word_id)

        word = db.get(SentenceWord, word_id)
        sentence = db.get(Sentence, item.get("sentence_id"))
        if word is None or sentence is None or word.sentence_id != sentence.id:
            errors.append(f"row {word_id}: sentence/word missing or mismatched")
            continue

        identity = exact_particle_identity(word.surface_form)
        expected_id = identity_ids.get(identity or "")
        live_stamp = _iso(sentence.mappings_verified_at)
        live_quality_stamp = _iso(sentence.quality_reviewed_at)
        checks = {
            "surface_form": word.surface_form == item.get("surface_form"),
            "identity": identity == item.get("exact_identity"),
            "current_lemma": word.lemma_id == item.get("current_lemma_id"),
            "expected_lemma": expected_id == item.get("expected_lemma_id"),
            "target_flag": bool(word.is_target_word)
            == bool(item.get("is_target_word")),
            "sentence_target": sentence.target_lemma_id
            == item.get("sentence_target_lemma_id"),
            "active": bool(sentence.is_active)
            == bool(item.get("sentence_is_active")),
            "mapping_stamp": live_stamp == item.get("mappings_verified_at"),
            "quality_stamp": live_quality_stamp == item.get("quality_reviewed_at"),
            "not_target_sensitive": not word.is_target_word
            and sentence.target_lemma_id not in {word.lemma_id, expected_id},
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            errors.append(f"row {word_id}: drifted checks {failed}")
            continue
        validated.append(word)

    if errors:
        raise RuntimeError(
            "repair plan drifted; no rows were changed:\n" + "\n".join(errors)
        )
    return validated


def apply_repair_plan(
    db,
    plan: dict[str, Any],
    *,
    plan_sha256: str,
    commit: bool = True,
) -> dict[str, Any]:
    """Apply a reviewed plan atomically after validating every row."""
    # End any read snapshot left by plan construction, then serialize all
    # SQLite writers before the first live validation query. The advisory flock
    # coordinates our maintenance jobs, but app/manual writers do not all take
    # it; BEGIN IMMEDIATE closes the validation-to-flush overwrite window.
    db.commit()
    db.expire_all()
    try:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))

        identity_ids = _validate_plan_header(db, plan)
        words = _validate_repair_rows(db, plan, identity_ids)
        before = _sentence_snapshot(db)

        for word, item in zip(words, plan["repairs"]):
            word.lemma_id = item["expected_lemma_id"]
        db.flush()

        after = _sentence_snapshot(db)
        if after != before:
            raise RuntimeError(
                "sentence lifecycle/count invariant changed; rolling back: "
                f"before={before}, after={after}"
            )

        result = {
            "updated": len(words),
            "target_sensitive_untouched": int(
                plan.get("target_sensitive_count") or 0
            ),
            "counts_by_identity": plan.get("counts_by_identity") or {},
            "counts_by_state": plan.get("counts_by_state") or {},
            "plan_sha256": plan_sha256,
            "snapshot": after,
        }
        db.add(
            ActivityLog(
                event_type="grammatical_identity_mapping_repair",
                summary=(
                    f"Repaired {len(words)} exact vocalized grammatical mappings; "
                    f"left {result['target_sensitive_untouched']} target-sensitive"
                ),
                detail_json={
                    **result,
                    "script": (
                        "repair_grammatical_identity_mappings_2026_07_29.py"
                    ),
                    "ambiguous_bare_forms_changed": 0,
                    "sentence_activation_changed": 0,
                    "mapping_verification_stamps_changed": 0,
                    "review_history_changed": 0,
                },
            )
        )
        if commit:
            db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _plan_bytes(plan: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            plan,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _print_summary(plan: dict[str, Any]) -> None:
    print(f"repairable exact mappings: {plan['repair_count']}")
    print(f"target-sensitive (untouched): {plan['target_sensitive_count']}")
    print(f"by identity: {plan['counts_by_identity']}")
    print(f"by state: {plan['counts_by_state']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan",
        action="store_true",
        help="write a reviewed plan file (default is read-only audit)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply an existing reviewed plan atomically",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=DEFAULT_PLAN_FILE,
        help=f"plan path (default: {DEFAULT_PLAN_FILE})",
    )
    parser.add_argument(
        "--expected-sha256",
        help="required with --apply; must match the reviewed plan hash",
    )
    parser.add_argument(
        "--backup-confirmed",
        action="store_true",
        help="required with --apply after an immediate database backup",
    )
    args = parser.parse_args()

    lock_handle = None
    db = SessionLocal()
    try:
        if args.apply:
            if not args.expected_sha256:
                parser.error("--apply requires --expected-sha256")
            if not args.backup_confirmed:
                parser.error("--apply requires --backup-confirmed")
            lock_handle = _try_acquire_material_update_lock()
            if lock_handle is None:
                raise RuntimeError(
                    "another material writer holds "
                    f"{MATERIAL_UPDATE_LOCK}; no rows were changed"
                )
            raw = args.plan_file.read_bytes()
            plan = json.loads(raw)
            plan_hash = hashlib.sha256(raw).hexdigest()
            if plan_hash != args.expected_sha256:
                raise RuntimeError(
                    "reviewed plan hash mismatch: "
                    f"expected {args.expected_sha256}, got {plan_hash}"
                )
            result = apply_repair_plan(
                db,
                plan,
                plan_sha256=plan_hash,
                commit=True,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        plan = build_repair_plan(db)
        _print_summary(plan)
        if args.plan:
            payload = _plan_bytes(plan)
            args.plan_file.write_bytes(payload)
            print(f"plan: {args.plan_file}")
            print(f"sha256: {hashlib.sha256(payload).hexdigest()}")
    finally:
        db.close()
        if lock_handle is not None:
            _release_material_update_lock(lock_handle)


if __name__ == "__main__":
    main()
