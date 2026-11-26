"""Match and sense-check every word of a snapped page in the background.

A Snap tap used to run the lookup and a sense check (one Sonnet call, 3.7 s
median, 6 s at p90) while the reader waited. This pass does the same work for
the whole page right after OCR and stores the answers on the page, so a tap
reads them without waiting on a model. Taps on words the pass did not cover
(failed chunks, a tap before it finishes) still use the per-tap path.

Mechanical matching cannot replace the check: on four snapped pages 14-24 % of
the matched surfaces were another word (سلطة → "salad", أجد → جَادّ) or needed
a wider gloss. Each surface is checked on its own line, because the resolver
puts different words under one entry (عيون and عيّنت both → عَيْن) and one
verdict per entry then mislabels the other. Evidence:
research/snap-prematch-2026-11/README.md.

A "broader" verdict is written only when the gloss gate approves it
(services/gloss_gate.py); otherwise it stays a proposal on the page, shown as
"Here: …". Version 1 wrote every proposal: a review of its 113 writes found 14
wrong (another word's meaning, a narrowing, a book-specific sense) and 80 memory
hooks cleared, and all 110 lemmas were restored from backup (2026-11-26). The
gate refused all 14 of those and approved 62 of the 78 good ones.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import SnapPage

logger = logging.getLogger(__name__)

PREMATCH_VERSION = 4
# A failed pass is retried when the page is opened again after this long.
RETRY_FAILED_AFTER = timedelta(minutes=10)
# Surfaces per sense-check call; a page has about 100, so three calls in parallel.
SENSE_CHUNK = 40
_MAX_PAGE_WORDS = 1000

_running: set[int] = set()
_lock = threading.Lock()


def is_running(snap_id: int) -> bool:
    with _lock:
        return snap_id in _running


def run_prematch(snap_id: int) -> None:
    """Background entry point; at most one pass per page at a time."""
    with _lock:
        if snap_id in _running:
            return
        _running.add(snap_id)
    try:
        result = prematch_page(snap_id)
    except Exception:
        logger.exception("snap prematch failed for page %s", snap_id)
        result = _failed()
    try:
        _store(snap_id, result)
    finally:
        with _lock:
            _running.discard(snap_id)


def _failed() -> dict:
    return {"status": "failed", "version": PREMATCH_VERSION,
            "finished_at": datetime.now(timezone.utc).isoformat()}


def needs_pass(pm: dict | None, now: datetime | None = None) -> bool:
    """No current pass on the page, or a failed one old enough to retry."""
    pm = pm or {}
    if pm.get("version") != PREMATCH_VERSION:
        return True
    if pm.get("status") != "failed":
        return False
    try:
        finished = datetime.fromisoformat(pm["finished_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now or datetime.now(timezone.utc)) - finished >= RETRY_FAILED_AFTER


def _store(snap_id: int, result: dict) -> None:
    db = SessionLocal()
    try:
        page = db.get(SnapPage, snap_id)
        if page is not None:
            page.prematch_json = result
            db.commit()
    finally:
        db.close()


def _page_text(snap_id: int) -> str | None:
    db = SessionLocal()
    try:
        page = db.get(SnapPage, snap_id)
        return page.arabic_text if page is not None else None
    finally:
        db.close()


def _database_matches(text: str) -> tuple[list[dict], list[dict], list[tuple[dict, str]]]:
    """Vocabulary matches (no model call) and one sense-check item per surface."""
    from app.routers.discover import discover_words_in_text
    from app.services.preread_service import load_candidates

    db = SessionLocal()
    try:
        matched = discover_words_in_text(
            db, text, count=_MAX_PAGE_WORDS, include_oov=True, include_learning=True,
            max_words=_MAX_PAGE_WORDS, database_only=True,
        )
        matched = [w for w in matched if w.get("lemma_id")]
        items: list[dict] = []
        owners: list[tuple[dict, str]] = []
        for w in matched:
            candidates = load_candidates(db, w.get("homograph_candidates"))
            for form in w.get("surface_forms") or [w["surface"]]:
                item = {"index": len(items), "lemma_id": w["lemma_id"], "lemma_ar": w["lemma_ar"],
                        "gloss_en": w.get("gloss_en"), "pos": w.get("pos"), "forms": [form],
                        "guess": w.get("match_guess")}
                if candidates:
                    item["candidates"] = candidates
                items.append(item)
                owners.append((w, form))
        return matched, items, owners
    finally:
        db.close()


def _unmatched_words(text: str) -> list[dict]:
    """Words with no vocabulary entry, glossed in context (one model call)."""
    from app.routers.discover import discover_words_in_text

    db = SessionLocal()
    try:
        words = discover_words_in_text(
            db, text, count=_MAX_PAGE_WORDS, include_oov=True, include_learning=True,
            max_words=_MAX_PAGE_WORDS,
        )
        return [w for w in words if w.get("lemma_source") != "database"]
    finally:
        db.close()


def _check_chunks(text: str, items: list[dict]) -> tuple[dict[int, dict], set[int]]:
    """Sense verdicts by item index, and the indexes whose chunk failed."""
    from app.services.preread_service import check_senses

    chunks = [items[i:i + SENSE_CHUNK] for i in range(0, len(items), SENSE_CHUNK)]
    verdicts: dict[int, dict] = {}
    failed: set[int] = set()
    with ThreadPoolExecutor(max_workers=max(1, len(chunks))) as pool:
        for chunk, part in zip(chunks, pool.map(lambda ch: check_senses(text, ch, allow_name=True), chunks)):
            if part is None:
                failed.update(it["index"] for it in chunk)
            else:
                verdicts.update(part)
    return verdicts, failed


def prematch_page(snap_id: int) -> dict:
    """Run the pass for one page and return what to store on it."""
    from app.routers.discover import _resolve_different_word, _sibling_word
    from app.services.interaction_logger import log_interaction
    from app.services.preread_service import regloss
    from app.services.sentence_validator import build_comprehensive_lemma_lookup

    started = time.monotonic()
    text = _page_text(snap_id)
    if not text:
        return _failed()

    matched, items, owners = _database_matches(text)
    # The gloss for unmatched words and the sense checks run side by side.
    with ThreadPoolExecutor(max_workers=2) as pool:
        unmatched_future = pool.submit(_unmatched_words, text)
        verdicts, failed = _check_chunks(text, items)
        try:
            unmatched = unmatched_future.result()
            unmatched_ok = True
        except Exception:
            logger.exception("snap prematch gloss failed for page %s", snap_id)
            unmatched, unmatched_ok = [], False

    out: dict[str, dict] = {}
    for w in unmatched:
        # A failed gloss leaves the word without one; let the tap retry it.
        if not w.get("gloss_en"):
            continue
        for form in w.get("surface_forms") or [w["surface"]]:
            out.setdefault(form, {"word": {**w, "surface": form, "surface_forms": [form]}})

    counts = {"fits": 0, "broader": 0, "different": 0, "sibling": 0, "name": 0}
    db = SessionLocal()
    try:
        lookup = build_comprehensive_lemma_lookup(db)
        for i, (w, form) in enumerate(owners):
            if i in failed:
                continue
            word = {**w, "surface": form, "surface_forms": [form]}
            v = verdicts.get(i) or {"verdict": "fits"}
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
            if v["verdict"] == "broader":
                rg = {"lemma_id": w["lemma_id"], "regloss": {
                    "from": w.get("gloss_en"), "to": v["gloss_en"],
                    "approved": v.get("approved") is True}}
                if rg["regloss"]["approved"]:
                    try:
                        regloss(db, [rg], source="snap_page")
                        db.commit()
                    except Exception:
                        logger.exception("snap prematch regloss failed for %s", w["lemma_id"])
                        db.rollback()
                sense = {"verdict": "broader", "gloss_en": v["gloss_en"],
                         "gloss_from": w.get("gloss_en"), "written": bool(rg.get("reglossed"))}
            elif v["verdict"] == "sibling":
                real = _sibling_word(db, v["lemma_id"], form)
                db.rollback()
                if real is None:
                    continue
                sense = {"verdict": "different", "sibling": True, "word": real}
            elif v["verdict"] == "name":
                sense = {"verdict": "name"}
            elif v["verdict"] == "different":
                sense = {"verdict": "different",
                         "word": _resolve_different_word(db, form, w["lemma_id"], v, lookup)}
                db.rollback()
            else:
                sense = {"verdict": "fits"}
            out[form] = {"word": word, "sense": sense}
    finally:
        db.close()

    seconds = round(time.monotonic() - started, 1)
    log_interaction(
        event="snap_prematch", snap_id=snap_id, seconds=seconds, surfaces=len(out),
        checked=len(owners) - len(failed), failed_checks=len(failed),
        unmatched=len(unmatched), unmatched_ok=unmatched_ok, verdicts=counts,
    )
    return {
        "status": "ready",
        "version": PREMATCH_VERSION,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "seconds": seconds,
        "failed_checks": len(failed),
        "words": out,
    }
