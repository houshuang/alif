"""Repair the reviewed Momo Chapter 5 reader mappings (Story #240).

Dry-run is the default. ``--apply`` requires a server-side backup path and
checks the exact story/token/lemma preimage before creating or changing data.
Every genuinely new lexical identity goes through the shared synchronous
quality gates; existing gated identities are reused by explicit ID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.models import (  # noqa: E402
    ActivityLog,
    Lemma,
    Story,
    StoryWord,
    UserLemmaKnowledge,
)
from app.services.lemma_quality import run_quality_gates  # noqa: E402
from app.services.sentence_validator import strip_diacritics  # noqa: E402
from app.services.story_service import (  # noqa: E402
    _build_knowledge_map,
    _recalculate_story_counts,
    get_book_page_detail,
)

STORY_ID = 240
EXPECTED_TITLE = "مومو — الفصل الخامس"
EXPECTED_WORD_COUNT = 611

# position -> (surface, currently mapped lemma_id)
EXPECTED = {
    0: ("الفصل", 214), 17: ("يمكن", 2087), 39: ("يهدأ", 3889),
    47: ("لتلك", 650), 51: ("الشعر،", 881), 55: ("يشدها", 848),
    84: ("فقد", 2189), 88: ("مضى", 3413), 111: ("التى", 582),
    112: ("رآها", 3992), 117: ("قرأها؛", 2090), 118: ("فلنقل", 4339),
    122: ("تسير", 137), 124: ("الأقدام،", 561), 127: ("نبتت", 727),
    138: ("موجودة", 2073), 142: ("خياله", 3075), 146: ("مروج", 996),
    163: ("حلقات", 2074), 165: ("وخواطر", 2628),
    168: ("وبالمناسبة", 2393), 174: ("بشوق", 3916), 181: ("أى", 360),
    182: ("مدى", 2173), 184: ("خياله،", 3075), 200: ("السلالم", 484),
    205: ("يلى:", 650), 210: ("فكما", 2051), 212: ("أكيد", 3576),
    213: ("معلوم", 722),
    221: ("بحروب", 678), 223: ("تعد", 2470), 226: ("لتدافع", 3807),
    231: ("المستمرة", 2088), 237: ("الأقوام", 1293),
    241: ("الغضب", 3270), 247: ("التى", 582), 250: ("لدرجة", 3034),
    253: ("بإبادة", 1219), 263: ("ملكهم", 636), 272: ("فقد", 2189),
    283: ("معروفة", 389), 294: ("أحد", 233), 298: ("الملك", 636),
    303: ("والتى", 582), 306: ("نموها", 1012), 309: ("ذهب", 494),
    319: ("بأى", 431), 322: ("الملك", 636), 328: ("التى", 582),
    330: ("بالفعل", 207), 335: ("وبدلاً", 750), 337: ("أمر", 1185),
    342: ("طبق", 2354), 367: ("سرها", 845), 375: ("خيال", 3075),
    377: ("اشتقها", 2275), 383: ("بقصد", 3559),
    385: ("والسخرية", 3965), 388: ("أفضل،", 1794),
    394: ("ذهبًا", 494), 402: ("بلون", 780), 403: ("ذهبى", 494),
    405: ("بالقدر", 2781), 411: ("القلق،", 3471), 414: ("الملك", 636),
    416: ("أبان", 2527), 422: ("ذهب", 494), 426: ("نموها،", 1012),
    438: ("النمو،", 2481), 442: ("بذلك،", 698), 443: ("ونمت", 1012),
    469: ("بدينة", 854), 470: ("وسمينة،", 501), 471: ("وسرعان", 2130),
    475: ("طبق", 2354), 485: ("أفضل\"،", 1794),
    489: ("حوض", 1725), 490: ("استحمامها،", 2244),
    496: ("يعد", 2470), 498: ("الاستحمام", 2244),
    513: ("النقل", 4339), 522: ("تزن", 1010), 524: ("وزن", 1010),
    528: ("أحد", 233), 532: ("مكلفين", 2731), 539: ("للسباع،", 2272),
    543: ("تعنى", 434), 567: ("الذهب", 494), 573: ("معلوم", 722),
    586: ("الذهب", 494), 597: ("أفضل\"،", 1794),
    602: ("عامًا", 2174), 603: ("وكتبت", 229),
}

# key -> new fully enriched lexical identity and all positions using it.
NEW = {
    "can": ("أَمْكَنَ", "to be possible; can", "verb", [17]),
    "calm": ("هَدَأَ", "to calm down; become quiet", "verb", [39]),
    "hair": ("شَعْر", "hair", "noun", [51]),
    "pull": ("شَدَّ", "to pull; take along", "verb", [55]),
    "pass": ("مَضَى", "to pass; go by", "verb", [88]),
    "see": ("رَأَى", "to see", "verb", [112]),
    "walk": ("سَارَ", "to walk; go", "verb", [122]),
    "foot": ("قَدَم", "foot", "noun", [124]),
    "sprout": ("نَبَتَ", "to grow; sprout", "verb", [127]),
    "imagination": ("خَيَال", "imagination; fantasy", "noun", [142, 184, 375]),
    "meadow": ("مَرْج", "meadow; pasture", "noun", [146]),
    "episode": ("حَلْقَة", "installment; episode; ring", "noun", [163]),
    "thought": ("خَاطِرَة", "thought; idea", "noun", [165]),
    "occasion": ("مُنَاسَبَة", "occasion; opportunity", "noun", [168]),
    "longing": ("شَوْق", "longing; anticipation", "noun", [174]),
    "extent": ("مَدًى", "extent; range", "noun", [182]),
    "stairs": ("سُلَّم", "stairs; ladder", "noun", [200]),
    "follow": ("وَلِيَ", "to follow; come next", "verb", [205]),
    "certain": ("أَكِيد", "certain; sure", "adjective", [212]),
    "war": ("حَرْب", "war", "noun", [221]),
    "count": ("عَدَّ", "to count", "verb", [223]),
    "defend": ("دَافَعَ", "to defend", "verb", [226]),
    "anger": ("غَضَب", "anger; rage", "noun", [241]),
    "extermination": ("إِبَادَة", "extermination; annihilation", "noun", [253]),
    "someone": ("أَحَد", "someone; one", "pron", [294, 528]),
    "growth": ("نُمُوّ", "growth", "noun", [306, 426, 438]),
    "gold": ("ذَهَب", "gold", "noun", [309, 394, 422, 567, 586]),
    "actually": ("بِالْفِعْل", "indeed; actually", "adverb", [330]),
    "instead": ("بَدَل", "substitute; instead", "noun", [335]),
    "command": ("أَمَرَ", "to order; command", "verb", [337]),
    "secret": ("سِرّ", "secret", "noun", [367]),
    "intent": ("قَصْد", "intent; purpose", "noun", [383]),
    "derive": ("اِشْتَقَّ", "to derive; coin", "verb", [377]),
    "mockery": ("سُخْرِيَة", "mockery; ridicule", "noun", [385]),
    "amount": ("قَدْر", "amount; degree", "noun", [405]),
    "anxiety": ("قَلَق", "anxiety; worry", "noun", [411]),
    "explain": ("أَبَانَ", "to explain; make clear", "verb", [416]),
    "plump": ("بَدِين", "fat; plump", "adjective", [469]),
    "fat": ("سَمِين", "fat; plump", "adjective", [470]),
    "soon": ("سُرْعَان", "soon", "adverb", [471]),
    "bathing": ("اِسْتِحْمَام", "bathing", "noun", [490, 498]),
    "transfer": ("نَقْل", "transfer; moving", "noun", [513]),
    "weigh": ("وَزَنَ", "to weigh", "verb", [522]),
    "weight": ("وَزْن", "weight", "noun", [524]),
    "beast": ("سَبُع", "wild beast", "noun", [539]),
    "mean": ("عَنَى", "to mean", "verb", [543]),
    "general": ("عَامّ", "general; universal", "adjective", [602]),
}

# Reviewed, already gated destinations.
EXISTING = {
    47: 443,
    84: 2054, 272: 2054,
    111: 2042, 247: 2042, 303: 2042, 328: 2042,
    117: 398, 118: 408,
    181: 451, 319: 451,
    210: 2065, 237: 3541, 250: 2269,
    263: 4523, 298: 4523, 322: 4523, 414: 4523,
    342: 2354, 402: 1676, 403: 1812, 442: 443, 443: 2481,
    475: 2354, 496: 4258, 603: 4028,
}

# Same lexical identity, but its global entry is objectively malformed or too
# narrow. Clear generated fields whose morphology depended on the bad POS and
# run the normal enrichment pipeline again.
METADATA_FIXES = {
    214: {"gloss_en": "chapter; section; class; classroom; season"},
    2354: {"gloss_en": "plate; dish", "pos": "noun", "reset": True},
    1794: {"gloss_en": "better; best", "pos": "adjective", "reset": True},
    1812: {"gloss_en": "golden", "pos": "adjective", "reset": True},
    3498: {"gloss_en": "severe; intense", "pos": "adjective", "reset": True},
    3471: {"gloss_en": "anxious; worried", "pos": "adjective", "reset": True},
    378: {"gloss_en": "bigger; biggest"},
    443: {"gloss_en": "that (masculine ذَلِكَ; feminine تِلْكَ)"},
    1725: {"gloss_en": "basin; pool; sink"},
}

# Exact values of every global lemma field the repair mutates. These are
# checked both in the live database and in the supplied backup before apply.
EXPECTED_METADATA = {
    214: ("فَصْل", "class, classroom", "noun"),
    378: ("أَكْبَر", "big", "adj"),
    443: ("ذلِكَ", "that (masc.)", "pron"),
    1725: ("حَوْض", "sink", "noun"),
    1794: ("أَفْضَل", "best", "verb"),
    1812: ("ذَهَبِيّ", "golden", "noun"),
    2354: ("طَبَق", "dish", "verb"),
    3471: ("قَلِق", "anxiety", "noun"),
    3498: ("شَدِيد", "severe, intense", "noun_prop"),
}

CONTEXT_GLOSSES = {
    0: "chapter", 138: "present; there", 213: "known", 231: "continuous",
    283: "known", 342: "plate; dish", 388: "better", 475: "plate; dish",
    485: "better", 489: "basin; tub", 532: "assigned", 573: "known",
    597: "better",
}


def _validate_preimage(db):
    story = db.get(Story, STORY_ID)
    if story is None or story.title_ar != EXPECTED_TITLE:
        raise RuntimeError("Story #240 identity changed")
    words = db.query(StoryWord).filter_by(story_id=STORY_ID).all()
    if len(words) != EXPECTED_WORD_COUNT:
        raise RuntimeError(f"Expected {EXPECTED_WORD_COUNT} StoryWords, found {len(words)}")
    by_position = {word.position: word for word in words}
    for position, (surface, lemma_id) in EXPECTED.items():
        word = by_position.get(position)
        actual = (word.surface_form, word.lemma_id) if word else None
        if actual != (surface, lemma_id):
            raise RuntimeError(
                f"Preimage mismatch at {position}: expected {(surface, lemma_id)!r}, got {actual!r}"
            )
    for lemma_id in EXISTING.values():
        lemma = db.get(Lemma, lemma_id)
        if lemma is None or lemma.gates_completed_at is None or lemma.canonical_lemma_id is not None:
            raise RuntimeError(f"Existing destination #{lemma_id} is not gated canonical data")
    for lemma_id, expected in EXPECTED_METADATA.items():
        lemma = db.get(Lemma, lemma_id)
        actual = (lemma.lemma_ar, lemma.gloss_en, lemma.pos) if lemma else None
        if actual != expected:
            raise RuntimeError(
                f"Metadata preimage mismatch for #{lemma_id}: "
                f"expected {expected!r}, got {actual!r}"
            )
    return story, by_position


def _new_positions():
    return {position for _ar, _gloss, _pos, positions in NEW.values() for position in positions}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_backup(path: Path) -> str:
    """Prove the supplied backup is a healthy copy of the exact live preimage."""
    with sqlite3.connect(path) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup SQLite quick_check failed")
        story = conn.execute(
            "SELECT title_ar FROM stories WHERE id = ?", (STORY_ID,)
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM story_words WHERE story_id = ?", (STORY_ID,)
        ).fetchone()[0]
        if story != (EXPECTED_TITLE,) or count != EXPECTED_WORD_COUNT:
            raise RuntimeError("Backup does not contain the reviewed Story #240 preimage")
        for position, expected in EXPECTED.items():
            row = conn.execute(
                "SELECT surface_form, lemma_id FROM story_words "
                "WHERE story_id = ? AND position = ?",
                (STORY_ID, position),
            ).fetchone()
            if row != expected:
                raise RuntimeError(
                    f"Backup preimage mismatch at {position}: {row!r} != {expected!r}"
                )
        for lemma_id, expected in EXPECTED_METADATA.items():
            row = conn.execute(
                "SELECT lemma_ar, gloss_en, pos FROM lemmas WHERE lemma_id = ?",
                (lemma_id,),
            ).fetchone()
            if row != expected:
                raise RuntimeError(
                    f"Backup metadata preimage mismatch for #{lemma_id}: "
                    f"{row!r} != {expected!r}"
                )
    return _sha256_file(path)


def _verify_applied(db):
    story = db.get(Story, STORY_ID)
    if story is None or story.title_ar != EXPECTED_TITLE:
        raise RuntimeError("Story #240 identity changed")
    by_position = {
        word.position: word
        for word in db.query(StoryWord).filter_by(story_id=STORY_ID).all()
    }
    if len(by_position) != EXPECTED_WORD_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_WORD_COUNT} StoryWords, found {len(by_position)}"
        )
    surface_mismatches = {
        position: (
            by_position.get(position).surface_form if by_position.get(position) else None,
            surface,
        )
        for position, (surface, _old_id) in EXPECTED.items()
        if by_position.get(position) is None
        or by_position[position].surface_form != surface
    }
    if surface_mismatches:
        raise RuntimeError(f"Reviewed surface mismatch: {surface_mismatches}")
    new_ids_by_key = {}
    target_by_position = dict(EXISTING)
    for key, (arabic, gloss, pos, positions) in NEW.items():
        rows = db.query(Lemma).filter(
            Lemma.lemma_ar == arabic,
            Lemma.gloss_en == gloss,
            Lemma.pos == pos,
            Lemma.canonical_lemma_id.is_(None),
            Lemma.gates_completed_at.isnot(None),
        ).all()
        if len(rows) != 1:
            raise RuntimeError(f"Expected one applied canonical for {key}, found {len(rows)}")
        new_ids_by_key[key] = rows[0].lemma_id
        for position in positions:
            target_by_position[position] = rows[0].lemma_id
    mismatches = {
        position: (by_position[position].lemma_id, lemma_id)
        for position, lemma_id in target_by_position.items()
        if by_position[position].lemma_id != lemma_id
    }
    if mismatches:
        raise RuntimeError(f"Applied mapping mismatch: {mismatches}")
    target_ids = set(target_by_position.values())
    invalid_targets = {}
    for lemma_id in target_ids:
        lemma = db.get(Lemma, lemma_id)
        if (
            lemma is None
            or lemma.canonical_lemma_id is not None
            or lemma.gates_completed_at is None
        ):
            invalid_targets[lemma_id] = (
                None
                if lemma is None
                else {
                    "canonical_lemma_id": lemma.canonical_lemma_id,
                    "gates_completed_at": lemma.gates_completed_at,
                }
            )
    if invalid_targets:
        raise RuntimeError(f"Applied targets are not gated canonicals: {invalid_targets}")
    gloss_mismatches = {
        position: (by_position[position].gloss_en, gloss)
        for position, gloss in CONTEXT_GLOSSES.items()
        if by_position[position].gloss_en != gloss
    }
    if gloss_mismatches:
        raise RuntimeError(f"Applied contextual gloss mismatch: {gloss_mismatches}")
    metadata_mismatches = {}
    for lemma_id, change in METADATA_FIXES.items():
        lemma = db.get(Lemma, lemma_id)
        expected_pos = change.get("pos") or EXPECTED_METADATA[lemma_id][2]
        actual = (
            lemma.gloss_en if lemma else None,
            lemma.pos if lemma else None,
            lemma.canonical_lemma_id if lemma else None,
            bool(lemma and lemma.gates_completed_at),
        )
        expected = (change["gloss_en"], expected_pos, None, True)
        if actual != expected:
            metadata_mismatches[lemma_id] = (actual, expected)
    if metadata_mismatches:
        raise RuntimeError(f"Applied metadata mismatch: {metadata_mismatches}")

    new_ids = list(new_ids_by_key.values())
    learning_rows_before = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id.in_(new_ids)
    ).count()
    if learning_rows_before:
        raise RuntimeError(
            f"Repair unexpectedly created {learning_rows_before} learner rows"
        )

    pages = [get_book_page_detail(db, STORY_ID, page) for page in range(1, 4)]
    if any(not (page["english_translation"] or "").strip() for page in pages):
        raise RuntimeError("A repaired reader page has no English translation")
    if any(not page["passages"] for page in pages):
        raise RuntimeError("A repaired reader page has no sentence passages")
    tokens = [token for page in pages for token in page["tokens"]]
    api_by_position = {int(token["position"]): token for token in tokens}
    if (
        len(tokens) != EXPECTED_WORD_COUNT
        or len(api_by_position) != EXPECTED_WORD_COUNT
    ):
        raise RuntimeError(
            "Reader payload token coverage mismatch: "
            f"tokens={len(tokens)}, unique_positions={len(api_by_position)}"
        )
    api_surface_mismatches = {
        position: (api_by_position.get(position, {}).get("surface_form"), surface)
        for position, (surface, _old_id) in EXPECTED.items()
        if api_by_position.get(position, {}).get("surface_form") != surface
    }
    if api_surface_mismatches:
        raise RuntimeError(f"Reader payload surface mismatch: {api_surface_mismatches}")
    api_mapping_mismatches = {
        position: (api_by_position[position]["lemma_id"], lemma_id)
        for position, lemma_id in target_by_position.items()
        if api_by_position[position]["lemma_id"] != lemma_id
        or not api_by_position[position]["has_full_entry"]
    }
    if api_mapping_mismatches:
        raise RuntimeError(
            f"Reader payload mapping mismatch: {api_mapping_mismatches}"
        )
    api_gloss_mismatches = {
        position: (api_by_position[position]["gloss_en"], gloss)
        for position, gloss in CONTEXT_GLOSSES.items()
        if api_by_position[position]["gloss_en"] != gloss
    }
    if api_gloss_mismatches:
        raise RuntimeError(
            f"Reader payload contextual gloss mismatch: {api_gloss_mismatches}"
        )
    db.expire_all()
    learning_rows_after = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id.in_(new_ids)
    ).count()
    if learning_rows_after != learning_rows_before:
        raise RuntimeError(
            "Reading repaired pages mutated learner state: "
            f"before={learning_rows_before}, after={learning_rows_after}"
        )

    audit = (
        db.query(ActivityLog)
        .filter(ActivityLog.event_type == "momo_reader_mapping_repair")
        .order_by(ActivityLog.id.desc())
        .first()
    )
    if audit is None or not isinstance(audit.detail_json, dict):
        raise RuntimeError("Repair audit record is missing")
    detail = audit.detail_json
    if detail.get("story_id") != STORY_ID:
        raise RuntimeError("Repair audit record references the wrong story")
    if detail.get("reviewed_preimages") != len(EXPECTED):
        raise RuntimeError("Repair audit record has the wrong preimage count")
    if detail.get("target_positions") != sorted(target_by_position):
        raise RuntimeError("Repair audit record has the wrong mapping positions")
    if detail.get("new_lemma_ids") != new_ids_by_key:
        raise RuntimeError("Repair audit record has the wrong new lemma IDs")
    backup_path = Path(detail.get("backup_path") or "")
    backup_sha256 = detail.get("backup_sha256")
    if not backup_path.is_file():
        raise RuntimeError(f"Repair backup is missing: {backup_path}")
    if _sha256_file(backup_path) != backup_sha256:
        raise RuntimeError("Repair backup SHA-256 no longer matches its audit record")
    if _validate_backup(backup_path) != backup_sha256:
        raise RuntimeError("Repair backup no longer contains the reviewed preimage")

    quick_check = db.execute(text("PRAGMA quick_check")).scalar()
    if quick_check != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {quick_check}")
    return {
        "verified": True,
        "mapped_positions": len(target_by_position),
        "reviewed_surfaces": len(EXPECTED),
        "context_glosses": len(CONTEXT_GLOSSES),
        "metadata_repairs": len(METADATA_FIXES),
        "new_lemmas": len(new_ids),
        "reader_pages": len(pages),
        "reader_tokens": len(tokens),
        "english_pages": sum(
            bool((page["english_translation"] or "").strip()) for page in pages
        ),
        "passage_counts": [len(page["passages"]) for page in pages],
        "learning_rows_created": learning_rows_after,
        "audit_log_id": audit.id,
        "backup_path": str(backup_path),
        "backup_sha256": backup_sha256,
        "quick_check": quick_check,
        "story_counts": {
            "total": story.total_words,
            "known": story.known_count,
            "unknown": story.unknown_count,
            "readiness_pct": story.readiness_pct,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify-applied", action="store_true")
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument(
        "--force-api",
        action="store_true",
        help="route quality-gate claude_haiku calls directly to the OpenAI API",
    )
    args = parser.parse_args()
    if args.apply and (not args.backup_path or not args.backup_path.is_file()):
        raise SystemExit("--apply requires an existing --backup-path")

    db = SessionLocal()
    try:
        if args.verify_applied:
            print(json.dumps(_verify_applied(db), ensure_ascii=False, indent=2))
            return
        story, by_position = _validate_preimage(db)
        print(json.dumps({
            "story_id": story.id,
            "reviewed_preimages": len(EXPECTED),
            "mapping_positions": len(set(EXISTING) | _new_positions()),
            "context_gloss_positions": len(CONTEXT_GLOSSES),
            "new_lexical_identities": len(NEW),
            "metadata_repairs": sorted(METADATA_FIXES),
            "apply": args.apply,
        }, ensure_ascii=False, indent=2))
        if not args.apply:
            return

        backup_sha256 = _validate_backup(args.backup_path)

        if args.force_api:
            # Production's CLI audit providers can be temporarily unhealthy.
            # Their normal fallback is the same OpenAI API, but only after two
            # long timeouts per call. This bounded maintenance switch skips
            # those timeouts without changing the gate sequence or schemas.
            import app.services.llm as llm_service
            original_generate = llm_service.generate_completion

            def _api_first_generate(*call_args, **call_kwargs):
                if call_kwargs.get("model_override") == "claude_haiku":
                    call_kwargs["model_override"] = "openai"
                return original_generate(*call_args, **call_kwargs)

            llm_service.generate_completion = _api_first_generate
            import importlib
            for module_name in (
                "app.services.variant_detection",
                "app.services.lemma_enrichment",
                "app.services.grammar_tagger",
            ):
                module = importlib.import_module(module_name)
                if hasattr(module, "generate_completion"):
                    module.generate_completion = _api_first_generate

        # Re-check that a new canonical identity was not added since this
        # manifest was reviewed. Same-bare homographs are expected, but an exact
        # Arabic/gloss/POS match would be an accidental duplicate.
        for key, (arabic, gloss, pos, _positions) in NEW.items():
            exact = [lemma for lemma in db.query(Lemma).filter(
                Lemma.lemma_ar == arabic,
                Lemma.gloss_en == gloss,
                Lemma.pos == pos,
                Lemma.canonical_lemma_id.is_(None),
            ).all()]
            if exact:
                raise RuntimeError(f"Reviewed new identity {key!r} now exists: #{exact[0].lemma_id}")

        metadata_ids = []
        for lemma_id, change in METADATA_FIXES.items():
            lemma = db.get(Lemma, lemma_id)
            if lemma is None:
                raise RuntimeError(f"Metadata target #{lemma_id} disappeared")
            lemma.gloss_en = change["gloss_en"]
            if change.get("pos"):
                lemma.pos = change["pos"]
            if change.get("reset"):
                lemma.forms_json = None
                lemma.grammar_features_json = None
                lemma.etymology_json = None
                lemma.gates_completed_at = None
                metadata_ids.append(lemma_id)

        new_ids_by_key = {}
        for key, (arabic, gloss, pos, _positions) in NEW.items():
            lemma = Lemma(
                lemma_ar=arabic,
                lemma_ar_bare=strip_diacritics(arabic),
                gloss_en=gloss,
                pos=pos,
                source="book",
                source_story_id=STORY_ID,
            )
            db.add(lemma)
            db.flush()
            new_ids_by_key[key] = lemma.lemma_id
        db.commit()

        gate_ids = list(new_ids_by_key.values()) + metadata_ids
        gate_summary = run_quality_gates(db, gate_ids, background_enrich=False)
        db.expire_all()
        for lemma_id in gate_ids:
            lemma = db.get(Lemma, lemma_id)
            if lemma.gates_completed_at is None or lemma.canonical_lemma_id is not None:
                raise RuntimeError(
                    f"Quality gates did not preserve canonical gated lemma #{lemma_id}"
                )

        target_by_position = dict(EXISTING)
        for key, (_arabic, _gloss, _pos, positions) in NEW.items():
            for position in positions:
                target_by_position[position] = new_ids_by_key[key]
        if set(target_by_position) != (set(EXISTING) | _new_positions()):
            raise RuntimeError("Repair manifest has overlapping or missing mapping positions")

        all_target_ids = set(target_by_position.values())
        knowledge = _build_knowledge_map(db, lemma_ids=all_target_ids)
        lemmas = {
            lemma.lemma_id: lemma
            for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(all_target_ids)).all()
        }
        changed = 0
        for position, lemma_id in target_by_position.items():
            word = by_position[position]
            if word.lemma_id != lemma_id:
                changed += 1
            word.lemma_id = lemma_id
            word.gloss_en = CONTEXT_GLOSSES.get(position) or lemmas[lemma_id].gloss_en
            word.is_known_at_creation = knowledge.get(lemma_id) in ("learning", "known")
        for position, gloss in CONTEXT_GLOSSES.items():
            by_position[position].gloss_en = gloss

        _recalculate_story_counts(db, story)
        db.add(ActivityLog(
            event_type="momo_reader_mapping_repair",
            summary=(
                f"Reviewed Story #240: remapped {changed} tokens across three pages; "
                f"created and fully gated {len(new_ids_by_key)} lexical identities"
            ),
            detail_json={
                "story_id": STORY_ID,
                "backup_path": str(args.backup_path),
                "backup_sha256": backup_sha256,
                "reviewed_preimages": len(EXPECTED),
                "target_positions": sorted(target_by_position),
                "new_lemma_ids": new_ids_by_key,
                "metadata_repairs": sorted(METADATA_FIXES),
                "gate_summary": gate_summary,
                "script": "repair_momo_ch5_reader_mappings.py",
                "force_api": args.force_api,
            },
        ))
        db.commit()
        db.refresh(story)
        print(json.dumps({
            "changed_mappings": changed,
            "new_lemma_ids": new_ids_by_key,
            "gate_summary": gate_summary,
            "story_counts": {
                "total": story.total_words,
                "known": story.known_count,
                "unknown": story.unknown_count,
                "readiness_pct": story.readiness_pct,
            },
            "backup_path": str(args.backup_path),
            "backup_sha256": backup_sha256,
        }, ensure_ascii=False, default=str, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
