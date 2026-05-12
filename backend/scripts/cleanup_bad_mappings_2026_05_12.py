#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix or retire active sentences with known bad lemma mappings.

This is intentionally data-specific. It addresses the concrete bad mappings
seen in production reviews on 2026-05-12 while the code-level lookup and
reviewability gates prevent recurrence.

Run dry first:
    python backend/scripts/cleanup_bad_mappings_2026_05_12.py --db backend/data/alif.prod.db --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DIACRITICS = re.compile(
    "[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC"
    "\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]"
)
PUNCT = re.compile(r"[،؟؛«»\u060C\u061B\u061F.,:;!?\"'\-\(\)\[\]{}…\u2010-\u2015\u2212\u2018\u2019\u201C\u201D]")


def strip_diacritics(text: str) -> str:
    return DIACRITICS.sub("", text or "")


def strip_punct(text: str) -> str:
    return PUNCT.sub("", text or "")


def normalize_alef(text: str) -> str:
    return (
        (text or "")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ٱ", "ا")
    )


def bare(surface: str) -> str:
    return strip_punct(strip_diacritics(surface or "").replace("\u0640", "")).strip()


def norm(surface: str) -> str:
    return normalize_alef(bare(surface))


@dataclass(frozen=True)
class Action:
    sentence_id: int
    word_id: int
    surface: str
    old_lemma_id: int
    action: str
    reason: str
    new_lemma_id: int | None = None
    arabic_preview: str = ""


def _lemma_exists(conn: sqlite3.Connection, lemma_id: int) -> bool:
    return conn.execute("SELECT 1 FROM lemmas WHERE lemma_id=?", (lemma_id,)).fetchone() is not None


def _pick_now_lemma(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT lemma_id FROM lemmas WHERE lemma_ar_bare='ان' AND gloss_en LIKE '%now%' ORDER BY lemma_id LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else 943


def _classify(row: sqlite3.Row, ids: dict[str, int]) -> Action | None:
    sid = int(row["sentence_id"])
    wid = int(row["word_id"])
    old = int(row["lemma_id"])
    surface = row["surface_form"] or ""
    arabic = row["arabic_text"] or ""
    english = (row["english_translation"] or "").lower()
    b = bare(surface)
    n = norm(surface)

    if old == 943:
        if "الان" in n:
            new_id = ids["now"]
            if new_id != old:
                return Action(sid, wid, surface, old, "remap", "الآن should map to now, not generic آن/time", new_id, arabic[:90])
            return None
        if "إن" in b:
            return Action(sid, wid, surface, old, "remap", "إِن particle mapped to آن/time", ids["in_particle"], arabic[:90])
        return Action(sid, wid, surface, old, "remap", "أَنْ/أَنَّ particle mapped to آن/time", ids["an_particle"], arabic[:90])

    if old == 1968 and n in {"عليه", "عليها", "عليهم", "عليهما", "عليك", "عليكم", "علينا"}:
        return Action(sid, wid, surface, old, "remap", "preposition+pronoun mapped to Ali", ids["ala"], arabic[:90])

    if old == 866:
        if n.startswith("حقوق"):
            return Action(sid, wid, surface, old, "remap", "حقوق mapped to حق verb", ids["rights"], arabic[:90])
        if n.startswith("يحقق") or n.startswith("تحقق"):
            return Action(sid, wid, surface, old, "retire", "حقّق verb missing from DB; current حق mapping is wrong", None, arabic[:90])

    if old == 216 and (
        n.startswith("يدرس") or n.startswith("درست") or n.startswith("درسوا") or n.startswith("ندرس")
    ):
        return Action(sid, wid, surface, old, "retire", "دَرَسَ study verb missing from DB; current درس noun mapping is wrong", None, arabic[:90])

    if old == 876 and n.startswith("اشبه"):
        return Action(sid, wid, surface, old, "retire", "أشبه mapped to شب grow up", None, arabic[:90])

    if old == 636 and n == "ملك" and ("royal" in english or "king" in english):
        return Action(sid, wid, surface, old, "retire", "مَلِك king missing from DB; current مَلَك angel mapping is wrong", None, arabic[:90])

    if old == 207 and (n.startswith("اصنع") or n.startswith("يفعل") or n.startswith("ففعلت") or n == "فعلت"):
        return Action(sid, wid, surface, old, "retire", "verb surface mapped to فِعْل noun", None, arabic[:90])

    if old == 975 and "خبر" in n and "news" in english:
        return Action(sid, wid, surface, old, "retire", "خَبَر news missing from DB; current خَبَرَ test mapping is wrong", None, arabic[:90])

    if old == 2202 and (n.startswith("سيقان") or "leg" in english):
        return Action(sid, wid, surface, old, "retire", "leg/stem noun missing from DB; current ساق drive mapping is wrong", None, arabic[:90])

    if old == 137 and n.startswith("تسير"):
        return Action(sid, wid, surface, old, "retire", "walk/go verb mapped to سيرة biography", None, arabic[:90])

    if old == 845 and n.startswith("مسرور"):
        return Action(sid, wid, surface, old, "retire", "مسرور adjective missing from DB; current سر verb mapping is wrong", None, arabic[:90])

    return None


def collect_actions(conn: sqlite3.Connection) -> list[Action]:
    ids = {
        "an_particle": 2185,
        "in_particle": 2186,
        "ala": 453,
        "rights": 1318,
        "now": _pick_now_lemma(conn),
    }
    for name, lemma_id in ids.items():
        if not _lemma_exists(conn, lemma_id):
            raise RuntimeError(f"required lemma missing: {name} #{lemma_id}")

    rows = conn.execute(
        """
        SELECT sw.id AS word_id, sw.sentence_id, sw.surface_form, sw.lemma_id,
               s.arabic_text, s.english_translation
        FROM sentence_words sw
        JOIN sentences s ON s.id = sw.sentence_id
        WHERE s.is_active = 1
          AND sw.lemma_id IN (943,1968,866,216,876,636,207,975,2202,137,845)
        ORDER BY sw.sentence_id, sw.position
        """
    ).fetchall()
    actions = []
    seen: set[tuple[int, int, str]] = set()
    for row in rows:
        action = _classify(row, ids)
        if not action:
            continue
        key = (action.sentence_id, action.word_id, action.action)
        if key not in seen:
            actions.append(action)
            seen.add(key)
    return actions


def apply_actions(conn: sqlite3.Connection, actions: list[Action], dry_run: bool) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
    retire_sentence_ids = {a.sentence_id for a in actions if a.action == "retire"}
    remaps = [
        a for a in actions
        if a.action == "remap" and a.sentence_id not in retire_sentence_ids and a.new_lemma_id
    ]

    if not dry_run:
        with conn:
            for action in remaps:
                conn.execute(
                    "UPDATE sentence_words SET lemma_id=? WHERE id=?",
                    (action.new_lemma_id, action.word_id),
                )
            touched = {a.sentence_id for a in remaps}
            for sentence_id in touched:
                conn.execute(
                    "UPDATE sentences SET mappings_verified_at=? WHERE id=?",
                    (now, sentence_id),
                )
            for sentence_id in retire_sentence_ids:
                conn.execute(
                    "UPDATE sentences SET is_active=0, mappings_verified_at=? WHERE id=?",
                    (now, sentence_id),
                )
            detail = {
                "script": Path(__file__).name,
                "remapped": len(remaps),
                "retired": len(retire_sentence_ids),
                "actions": [a.__dict__ for a in actions[:100]],
            }
            conn.execute(
                "INSERT INTO activity_log(event_type, summary, detail_json, created_at) VALUES (?,?,?,?)",
                (
                    "manual_action",
                    f"Cleaned known bad lemma mappings: {len(remaps)} remap(s), {len(retire_sentence_ids)} sentence(s) retired",
                    json.dumps(detail, ensure_ascii=False),
                    now,
                ),
            )

    return {
        "dry_run": dry_run,
        "remaps": len(remaps),
        "retired_sentences": len(retire_sentence_ids),
        "actions": [a.__dict__ for a in actions],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="backend/data/alif.prod.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        actions = collect_actions(conn)
        result = apply_actions(conn, actions, args.dry_run)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"dry_run={result['dry_run']}")
        print(f"remaps={result['remaps']}")
        print(f"retired_sentences={result['retired_sentences']}")
        for action in result["actions"][:80]:
            target = f" -> #{action['new_lemma_id']}" if action["new_lemma_id"] else ""
            print(
                f"{action['action']:6} sentence={action['sentence_id']} word={action['word_id']} "
                f"#{action['old_lemma_id']}{target} {action['surface']}: {action['reason']}"
            )
        if len(result["actions"]) > 80:
            print(f"... {len(result['actions']) - 80} more")


if __name__ == "__main__":
    main()
