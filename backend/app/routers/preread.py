"""Pre-read: photograph upcoming book pages, get them ready to read.

Flow: ``POST /api/preread`` creates a batch, ``POST /api/preread/{id}/pages``
uploads each photo (stored only until it is read), ``POST /api/preread/{id}/start``
runs the job in the background, and ``GET /api/preread/{id}`` reports progress
and the result. The job reads every page into a SnapPage (full translation,
reopenable in Snap), builds the automatic plan (services/preread_service.py),
enters the selected words as learner picks, brings shaky known words forward,
and finally generates review sentences.
"""
from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import PrereadSet, SnapPage
from app.services.interaction_logger import log_interaction
from app.services.add_context import verify_add_contexts
from app.services.preread_service import PREREAD_SOURCE, analyze_pages, practice_names, pull_forward, regloss

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/preread", tags=["preread"])

MAX_PAGES = 40
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_OCR_WORKERS = 4
# A job whose status has not moved for this long is presumed dead (server
# restart) and may be started again; finished pages are not read twice.
_STALE_AFTER = timedelta(minutes=15)
_RUNNING = {"reading", "analyzing", "applying"}


def _image_dir(set_id: int) -> Path:
    return settings.log_dir.parent / "preread" / str(set_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _set_status(set_id: int, status: str, **fields) -> None:
    db = SessionLocal()
    try:
        row = db.get(PrereadSet, set_id)
        if row is None:
            return
        row.status = status
        row.updated_at = _now()
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()
    finally:
        db.close()


def _summary(row: PrereadSet) -> dict:
    pages = row.pages_json or []
    result = row.result_json or {}
    stats = result.get("stats") or {}
    return {
        "preread_id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "status": row.status,
        "page_count": len(pages),
        "pages_read": sum(1 for p in pages if p.get("snap_id")),
        "learn_count": stats.get("learn"),
        "shaky_count": stats.get("shaky"),
        "error": row.error,
    }


def _read_one(set_id: int, page: dict) -> dict:
    """OCR + translate one stored photo into a SnapPage. Returns the updated page."""
    from app.services.ocr_service import extract_text_and_translation

    path = Path(page["image"])
    try:
        ocr = extract_text_and_translation(path.read_bytes())
    except Exception as e:
        logger.warning("preread %s page %s OCR failed: %s", set_id, page["page"], e)
        return {**page, "error": f"OCR failed: {e}"[:300]}
    arabic = (ocr.get("arabic_text") or "").strip()
    if not arabic:
        return {**page, "error": "No Arabic text found"}
    db = SessionLocal()
    try:
        snap = SnapPage(
            arabic_text=arabic,
            translation_en=(ocr.get("translation_en") or "").strip(),
            sentences_json=ocr.get("sentences") or [],
            words_json=[],
        )
        db.add(snap)
        db.commit()
        return {**page, "snap_id": snap.id, "error": None}
    finally:
        db.close()


def _read_pages(set_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        pages = [dict(p) for p in (db.get(PrereadSet, set_id).pages_json or [])]
    finally:
        db.close()
    todo = [p for p in pages if not p.get("snap_id")]
    done: dict[int, dict] = {}

    def record(updated: dict) -> None:
        done[updated["page"]] = updated
        merged = [done.get(p["page"], p) for p in pages]
        _set_status(set_id, "reading", pages_json=merged)

    with ThreadPoolExecutor(max_workers=_OCR_WORKERS) as pool:
        for updated in pool.map(lambda p: _read_one(set_id, p), todo):
            record(updated)
    return [done.get(p["page"], p) for p in pages]


def _mark_checked(db, context_id: int, w: dict) -> None:
    """Pre-read already checked this word against its pages (check_senses);
    record that verdict on the add context instead of checking it again."""
    from app.models import WordAddContext

    row = db.get(WordAddContext, context_id)
    if row is None:
        return
    row.check_verdict = "broader" if w.get("regloss") else "fits"
    row.check_json = {"via": "preread_sense_check", "regloss": w.get("regloss"),
                      "sense_from": w.get("sense_from")}
    row.checked_at = datetime.now(timezone.utc)
    db.commit()


def _apply_plan(plan: dict, set_id: int | None = None, snap_ids: list[int] | None = None) -> dict:
    """Enter the learn words as learner picks and bring shaky words forward.
    Each entered word keeps the sentence it was found in (word_add_contexts)."""
    from app.routers.discover import (
        WordIn,
        _create_and_introduce,
        _log_add,
        build_comprehensive_lemma_lookup,
    )

    db = SessionLocal()
    new_ids, existing_ids, hook_ids, context_ids = [], [], [], []
    snap_ids = snap_ids or []
    try:
        try:
            hook_ids = regloss(db, plan["learn"] + plan["shaky"])
            db.commit()
        except Exception as e:
            db.rollback()
            hook_ids = []
            for w in plan["learn"] + plan["shaky"]:
                w.pop("reglossed", None)
            logger.warning("preread regloss failed: %s", e)
        for w in plan["learn"] + plan["shaky"]:
            if w.get("reglossed"):
                log_interaction(event="preread_regloss", lemma_id=w["lemma_id"],
                                gloss_from=w["regloss"]["from"], gloss_to=w["regloss"]["to"])
        for w in plan["learn"]:
            try:
                lookup = build_comprehensive_lemma_lookup(db)
                out = _create_and_introduce(
                    db,
                    WordIn(
                        lemma_ar_bare=w["lemma_ar_bare"],
                        lemma_ar=w.get("lemma_ar"),
                        gloss_en=w.get("gloss_en"),
                        pos=w.get("pos"),
                        transliteration=w.get("transliteration"),
                        register=w.get("register"),
                        dialect=w.get("dialect"),
                        source=PREREAD_SOURCE,
                        surface=w.get("surface"),
                        context_ar=w.get("example_ar"),
                        snap_id=(
                            snap_ids[w["first_page"] - 1]
                            if w.get("first_page") and w["first_page"] <= len(snap_ids) else None
                        ),
                    ),
                    lookup,
                    preread_set_id=set_id,
                )
                db.commit()
                _log_add(out)
                w["lemma_id"] = out["lemma_id"]
                w["knowledge_state"] = out["state"]
                w["added"] = out["state"] == "acquiring"
                if out.get("context_id"):
                    context_ids.append(out["context_id"])
                    if w.get("sense_checked"):
                        _mark_checked(db, out["context_id"], w)
                if w.get("sense_from"):
                    log_interaction(event="preread_sense_reroute", lemma_id=out["lemma_id"],
                                    from_lemma_id=w["sense_from"]["lemma_id"],
                                    created=out["created"], state=out["state"])
                if out["created"]:
                    new_ids.append(out["lemma_id"])
                elif w["added"]:
                    existing_ids.append(out["lemma_id"])
            except Exception as e:
                db.rollback()
                w["added"] = False
                w["error"] = str(e)[:200]
                logger.warning("preread add failed for %s: %s", w.get("lemma_ar_bare"), e)
        named = practice_names(db, plan["names"])
        new_ids += named["gate_ids"]
        existing_ids += [i for i in named["ids"] if i not in named["gate_ids"]]
        shaky_ids = [w["lemma_id"] for w in plan["shaky"] if w.get("lemma_id")]
        moved = set(pull_forward(db, shaky_ids)) if shaky_ids else set()
        db.commit()
        for w in plan["shaky"]:
            w["pulled_forward"] = w.get("lemma_id") in moved
    finally:
        db.close()
    return {"new_ids": new_ids, "existing_ids": existing_ids, "moved": len(moved),
            "hook_ids": hook_ids, "context_ids": context_ids}


def _prepare_material(new_ids: list[int], existing_ids: list[int], hook_ids: list[int] | None = None) -> None:
    """Quality-gate new lemmas, generate review sentences in batches for every
    word that has fewer than the minimum, then regenerate memory hooks cleared
    by a regloss."""
    from sqlalchemy import exists, or_

    from app.models import Sentence, SentenceWord
    from app.services.lemma_quality import run_quality_gates
    from app.services.material_generator import MIN_SENTENCES_PER_WORD, batch_generate_material
    from app.services.sentence_eligibility import reviewable_sentence_clauses

    if new_ids:
        db = SessionLocal()
        try:
            run_quality_gates(db, new_ids, background_enrich=True)
            db.commit()
        except Exception as e:
            logger.warning("preread gating failed: %s", e)
            db.rollback()
        finally:
            db.close()

    db = SessionLocal()
    try:
        needy = []
        for lid in new_ids + existing_ids:
            have = (
                db.query(Sentence.id)
                .filter(
                    reviewable_sentence_clauses(),
                    or_(
                        Sentence.target_lemma_id == lid,
                        exists().where(
                            SentenceWord.sentence_id == Sentence.id,
                            SentenceWord.lemma_id == lid,
                        ),
                    ),
                )
                .limit(MIN_SENTENCES_PER_WORD)
                .count()
            )
            if have < MIN_SENTENCES_PER_WORD:
                needy.append(lid)
    finally:
        db.close()
    for i in range(0, len(needy), 10):
        chunk = needy[i:i + 10]
        try:
            res = batch_generate_material(chunk, count_per_word=MIN_SENTENCES_PER_WORD)
            logger.info("preread material batch %s: %s", chunk, res)
        except Exception as e:
            logger.warning("preread material batch failed for %s: %s", chunk, e)

    try:
        from app.services.material_generator import generate_paired_material
        logger.info("preread paired material: %s", generate_paired_material(new_ids + existing_ids))
    except Exception as e:
        logger.warning("preread paired material failed: %s", e)

    from app.services.memory_hooks import generate_memory_hooks

    for lid in hook_ids or []:
        try:
            generate_memory_hooks(lid)
        except Exception as e:
            logger.warning("preread hook regeneration failed for %s: %s", lid, e)


def run_preread(set_id: int) -> None:
    """The background job. Each phase uses its own short DB sessions; no
    session is held across OCR or LLM calls."""
    try:
        _set_status(set_id, "reading", error=None)
        pages = _read_pages(set_id)
        snap_ids = [p["snap_id"] for p in sorted(pages, key=lambda p: p["page"]) if p.get("snap_id")]
        if not snap_ids:
            _set_status(set_id, "failed", error="None of the pages could be read")
            return

        _set_status(set_id, "analyzing")
        db = SessionLocal()
        try:
            texts = {s.id: s.arabic_text for s in db.query(SnapPage).filter(SnapPage.id.in_(snap_ids)).all()}
            db.rollback()
            plan = analyze_pages(db, [texts[i] for i in snap_ids if i in texts])
        finally:
            db.close()

        _set_status(set_id, "applying")
        applied = _apply_plan(plan, set_id, snap_ids)
        result = {
            "learn": plan["learn"],
            "shaky": plan["shaky"],
            "names": plan["names"],
            "stats": {
                **plan["stats"],
                "added": sum(1 for w in plan["learn"] if w.get("added")),
                "created": len(applied["new_ids"]),
                "pulled_forward": applied["moved"],
                "names_added": sum(1 for w in plan["names"] if w.get("added")),
                "reglossed": sum(1 for w in plan["learn"] + plan["shaky"] if w.get("reglossed")),
            },
        }
        _set_status(set_id, "done", result_json=result, finished_at=_now())
        log_interaction(
            event="preread_done", preread_id=set_id, pages=len(snap_ids),
            learn=len(plan["learn"]), added=result["stats"]["added"],
            shaky=len(plan["shaky"]), pulled_forward=applied["moved"],
            names=len(plan["names"]),
        )
        shutil.rmtree(_image_dir(set_id), ignore_errors=True)
        # Words the sense check did not cover (new words) are checked against
        # their sentence now; already-checked rows are skipped.
        if applied["context_ids"]:
            verify_add_contexts(applied["context_ids"])
        _prepare_material(applied["new_ids"], applied["existing_ids"], applied["hook_ids"])
    except Exception as e:
        logger.exception("preread %s failed", set_id)
        _set_status(set_id, "failed", error=str(e)[:500])


@router.post("")
def create_preread(db: Session = Depends(get_db)):
    row = PrereadSet(status="uploading", pages_json=[])
    db.add(row)
    db.commit()
    return _summary(row)


@router.get("")
def list_prereads(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    rows = (
        db.query(PrereadSet)
        .filter(PrereadSet.status != "uploading")
        .order_by(PrereadSet.id.desc())
        .limit(limit)
        .all()
    )
    return {"prereads": [_summary(r) for r in rows]}


@router.post("/{set_id}/pages")
async def upload_page(set_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    row = db.get(PrereadSet, set_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pre-read not found")
    if row.status != "uploading":
        raise HTTPException(status_code=409, detail="Pages can only be added before start")
    pages = list(row.pages_json or [])
    if len(pages) >= MAX_PAGES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_PAGES} pages")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20MB limit")
    n = len(pages) + 1
    folder = _image_dir(set_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{n:03d}.jpg"
    path.write_bytes(data)
    pages.append({"page": n, "image": str(path), "snap_id": None, "error": None})
    row.pages_json = pages
    row.updated_at = _now()
    db.commit()
    return _summary(row)


@router.post("/{set_id}/start")
def start_preread(set_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    row = db.get(PrereadSet, set_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pre-read not found")
    if not row.pages_json:
        raise HTTPException(status_code=400, detail="No pages uploaded")
    if row.status == "done":
        return _summary(row)
    if row.status in _RUNNING:
        updated = _aware(row.updated_at)
        if updated and _now() - updated < _STALE_AFTER:
            return _summary(row)
    row.status = "reading"
    row.error = None
    row.updated_at = _now()
    db.commit()
    log_interaction(event="preread_start", preread_id=set_id, pages=len(row.pages_json))
    background_tasks.add_task(run_preread, set_id)
    return _summary(row)


@router.get("/{set_id}")
def get_preread(set_id: int, db: Session = Depends(get_db)):
    row = db.get(PrereadSet, set_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pre-read not found")
    out = _summary(row)
    out["pages"] = [
        {"page": p["page"], "snap_id": p.get("snap_id"), "error": p.get("error")}
        for p in (row.pages_json or [])
    ]
    out["result"] = row.result_json
    return out
