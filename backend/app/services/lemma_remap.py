"""Move stored word links onto the lemma the word actually is.

A sentence, story or Quran verse stored before a word had its own lemma links
that word to whatever caught it then: نهر "river" to نَهار "day time",
الإنسان to آنِسَة "Miss", بيده to بَادَ "to perish". Creating the right lemma
later fixes new text only, and the cron's Step 0b heals only words with no link.
This pass re-resolves every stored surface in `sentence_words`, `story_words`
and `quranic_verse_words` against the current vocabulary (all lemma sources) and
proposes a move when:

- the surface-aware resolver is sure (no CAMeL guess, no homograph tie) and
  lands on a gated canonical noun or adjective other than the stored lemma;
- the surface spells that lemma's headword or one of its stored forms (plural,
  dual, feminine) after the proclitics, with no contradicting vowel; and
- the surface does not also spell the stored lemma (ذلك inside كذلك keeps ذلك,
  even though ذل + ك "your humiliation" would spell ذُلّ).

Spelling alone still admits homographs (الْمَقَالَ "the article" spells مَقَالٍ, a
plural of مِقْلَاة "frying pan"), so every distinct move (old lemma, new lemma,
surface) is judged by Claude Opus with up to three of the sentences it occurs
in; it is approved only when the new lemma is the word in all of them. Verdicts
are stored in `lemma_remap_verdicts`, so a move is judged once. Approved moves
are applied compare-and-set; a story word takes the new lemma's gloss; a
sentence whose only token of its target moved follows it; reviewable sentences
that changed are re-verified. Review history is not rewritten.

Runs as Step 0c of the material cron (scripts/update_material.py) and by hand
with scripts/remap_stale_links.py.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from app.database import SessionLocal

logger = logging.getLogger(__name__)

MARKS = "ًٌٍَُِّْ"
SHADDA = "ّ"
PROCLITICS = ("وبال", "فبال", "وكال", "ولل", "فلل", "وال", "فال", "بال", "كال", "لل", "ال",
              "و", "ف", "ب", "ل", "ك", "")
MOVABLE_POS = {"noun", "adj", "adjective"}
FORM_KEYS = ("plural", "dual", "feminine", "fem", "plural2", "feminine_plural")
JUDGE_MODEL = "claude_opus"
_JUDGE_BATCH = 30
_EXAMPLES = 3


def fold_quranic(text: str) -> str:
    """Quranic orthography to standard: dagger alif → alif, wasla → alif,
    Quranic sukun → sukun, small high signs dropped."""
    out = []
    for ch in text or "":
        if ch == "ٰ" or ch == "ٱ":
            out.append("ا")
        elif ch == "ۡ":
            out.append("ْ")
        elif "ۖ" <= ch <= "ۭ":
            continue
        else:
            out.append(ch)
    return "".join(out)


def surface_key(surface: str) -> str:
    """Letters and vowel marks only: the identity a verdict is stored under."""
    return re.sub(r"[^ء-يً-ْ]", "", surface or "")


def _letters(text: str) -> list[tuple[str, str]]:
    """(letter, marks) pairs; wasla is plain alif."""
    out: list[tuple[str, str]] = []
    for ch in text:
        if ch in MARKS:
            if out:
                out[-1] = (out[-1][0], out[-1][1] + ch)
        elif "ء" <= ch <= "ي" or ch == "ٱ":
            out.append(("ا" if ch == "ٱ" else ch, ""))
    return out


def spells_headword(surface: str, headword: str) -> bool:
    """The surface spells the headword after a proclitic: same letters and
    hamza seats, no vowel mark that contradicts it, and its shadda present."""
    surf, head = _letters(surface), _letters(headword)
    if not head:
        return False
    if head[0][0] == "ا":
        head[0] = ("ا", "")
    vocalised = sum(1 for _, marks in surf if marks) >= 2
    bare = "".join(letter for letter, _ in surf)
    for clitic in PROCLITICS:
        if not bare.startswith(clitic):
            continue
        start = len(clitic)
        segment = surf[start:start + len(head)]
        if len(segment) < len(head):
            continue
        ok = True
        for index, ((s_letter, s_marks), (h_letter, h_marks)) in enumerate(zip(segment, head)):
            last = index == len(head) - 1
            if s_letter != h_letter and not (last and h_letter == "ة" and s_letter == "ت"):
                ok = False
                break
            ends_here = last and start + len(head) == len(surf)
            assimilated = index == 0 and clitic.endswith("ل") and SHADDA in s_marks
            if (SHADDA in h_marks) != (SHADDA in s_marks) and (vocalised or ends_here) and not assimilated:
                ok = False
                break
            s_vowel = s_marks.replace(SHADDA, "")
            h_vowel = h_marks.replace(SHADDA, "")
            if not last and s_vowel and h_vowel and s_vowel != h_vowel:
                ok = False
                break
        if ok:
            return True
    return False


def lemma_spellings(lemma) -> list[str]:
    """Headword plus the stored forms a surface may spell."""
    out = [lemma.lemma_ar or ""]
    forms = lemma.forms_json
    if isinstance(forms, str):
        try:
            forms = json.loads(forms)
        except ValueError:
            forms = None
    if isinstance(forms, dict):
        for key in FORM_KEYS:
            v = forms.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    return [h for h in out if h]


def spells_lemma(surface: str, lemma) -> bool:
    return any(spells_headword(surface, h) for h in lemma_spellings(lemma))


@dataclass
class Move:
    table: str
    row_id: int
    owner_id: int  # sentence_id, story_id or verse_id
    surface: str
    stored_lemma_id: int  # as stored (may be a variant)
    old: int  # canonical
    new: int

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.old, self.new, surface_key(self.surface))


def _tables():
    from app.models import QuranicVerseWord, SentenceWord, StoryWord

    return (
        ("sentence_words", SentenceWord, SentenceWord.sentence_id),
        ("story_words", StoryWord, StoryWord.story_id),
        ("quranic_verse_words", QuranicVerseWord, QuranicVerseWord.verse_id),
    )


def find_moves(db) -> tuple[list[Move], dict]:
    """Proposed moves over all three word tables, and the lemma map."""
    from app.models import Lemma
    from app.services.canonical_resolution import resolve_canonical_via_map
    from app.services.sentence_validator import (
        LemmaMatchProvenance,
        build_comprehensive_lemma_lookup,
        lookup_lemma_id,
    )

    lemmas = {lem.lemma_id: lem for lem in db.query(Lemma).all()}
    parents = {lid: lem.canonical_lemma_id for lid, lem in lemmas.items()}
    targets = {lid for lid, lem in lemmas.items()
               if lem.gates_completed_at is not None and lem.canonical_lemma_id is None
               and (lem.pos or "") in MOVABLE_POS}
    lookup = build_comprehensive_lemma_lookup(db)
    cache: dict[str, int | None] = {}

    def resolve(surface: str) -> int | None:
        if surface not in cache:
            provenance = LemmaMatchProvenance()
            found = lookup_lemma_id(surface, lookup, provenance, strict_fallback=True)
            if found is not None and (provenance.is_guess or provenance.homograph_tie):
                found = None
            if found is not None:
                found = resolve_canonical_via_map(found, parents)
            if found not in targets or not spells_lemma(surface, lemmas[found]):
                found = None
            cache[surface] = found
        return cache[surface]

    moves: list[Move] = []
    for name, model, owner_col in _tables():
        rows = (
            db.query(model.id, owner_col, model.surface_form, model.lemma_id)
            .filter(model.lemma_id.isnot(None))
            .yield_per(5000)
        )
        for row_id, owner_id, surface, stored in rows:
            if not surface:
                continue
            text = fold_quranic(surface) if name == "quranic_verse_words" else surface
            target = resolve(text)
            if target is None:
                continue
            old = resolve_canonical_via_map(stored, parents)
            if target == old:
                continue
            current = lemmas.get(old)
            if current is not None and spells_lemma(text, current):
                continue
            moves.append(Move(name, row_id, owner_id, surface, stored, old, target))
    return moves, lemmas


def _contexts(db, moves: list[Move]) -> tuple[dict[tuple[str, int], tuple[str, str]], set[int]]:
    """(table, owner_id) → (Arabic, English) for sentence and Quran rows, and
    the story ids (story sentences are rebuilt per row)."""
    from app.models import QuranicVerse, Sentence

    want: dict[str, set[int]] = defaultdict(set)
    for m in moves:
        want[m.table].add(m.owner_id)
    out: dict[tuple[str, int], tuple[str, str]] = {}
    if want["sentence_words"]:
        for s in db.query(Sentence).filter(Sentence.id.in_(want["sentence_words"])).all():
            out[("sentence_words", s.id)] = (s.arabic_text or "", s.english_translation or "")
    if want["quranic_verse_words"]:
        for v in db.query(QuranicVerse).filter(QuranicVerse.id.in_(want["quranic_verse_words"])).all():
            out[("quranic_verse_words", v.id)] = (v.arabic_text or "", v.english_translation or "")
    return out, want["story_words"]


def _story_sentence(db, row_id: int) -> str:
    from app.models import StoryWord

    sw = db.get(StoryWord, row_id)
    if sw is None:
        return ""
    words = (
        db.query(StoryWord.surface_form)
        .filter(StoryWord.story_id == sw.story_id, StoryWord.sentence_index == sw.sentence_index)
        .order_by(StoryWord.position)
        .all()
    )
    return " ".join(w[0] for w in words)[:400]


_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "word": {"type": "string", "enum": ["new", "old", "neither", "unsure"]},
                    "note": {"type": "string"},
                },
                "required": ["index", "word"],
            },
        }
    },
    "required": ["verdicts"],
}


def judge(items: list[dict]) -> dict[int, dict] | None:
    """items: [{index, surface, old: str, new: str, examples: [str]}] → {index:
    {approved, reason}}; None when the call fails."""
    from app.services.llm import generate_completion

    blocks = []
    for it in items:
        ex = "\n".join(f"    - «{e}»" for e in it["examples"])
        blocks.append(f"ITEM {it['index']}\n  word: {it['surface']}\n  OLD: {it['old']}\n"
                      f"  NEW: {it['new']}\n  sentences:\n{ex}")
    prompt = (
        "An Arabic learning app links each word of its stored texts to a dictionary "
        "entry. For each item, a word is linked to OLD and a newer check proposes NEW. "
        "Read the word in its sentences and answer which entry it is an inflected form of "
        "(with any prefix such as و ف ب ل ك ال and any attached pronoun removed):\n"
        '- "new": the word is NEW in every sentence shown.\n'
        '- "old": the word is OLD (in at least one sentence).\n'
        '- "neither": it is some other word, or a name.\n'
        '- "unsure": the sentences do not settle it (e.g. unvowelled and both readings fit).\n'
        "Judge the word's form and meaning in context, not the entries' spellings alone: "
        "كذلك is ك + ذلك, not a form of ذُلّ; المقال \"the article\" is مَقَال, not the "
        "plural of مِقْلَاة. Give a verdict for every item.\n\n" + "\n\n".join(blocks)
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are an expert in Arabic morphology and lexicography.",
            json_schema=_SCHEMA,
            temperature=0.0,
            model_override=JUDGE_MODEL,
            task_type="lemma_remap_judge",
        )
    except Exception as e:
        logger.warning("lemma remap judge failed: %s", e)
        return None
    rows = res.get("verdicts") if isinstance(res, dict) else None
    if not isinstance(rows, list):
        return None
    wanted = {it["index"] for it in items}
    return {
        r["index"]: {"approved": r.get("word") == "new", "reason": r.get("word"),
                     "note": (r.get("note") or "")[:200]}
        for r in rows
        if isinstance(r, dict) and r.get("index") in wanted
    }


def _entry(lemma) -> str:
    return f"{lemma.lemma_ar} ({lemma.pos or '?'}) — {lemma.gloss_en or ''}"


def judge_moves(moves: list[Move], lemmas: dict, limit: int | None = None) -> dict:
    """Judge the moves without a stored verdict (at most ``limit`` distinct
    moves) and store the verdicts. Returns all verdicts by key."""
    from app.models import LemmaRemapVerdict

    db = SessionLocal()
    try:
        verdicts = {
            (v.old_lemma_id, v.new_lemma_id, v.surface_key): v.approved
            for v in db.query(LemmaRemapVerdict).all()
        }
        by_key: dict[tuple, list[Move]] = defaultdict(list)
        for m in moves:
            if m.key not in verdicts:
                by_key[m.key].append(m)
        keys = list(by_key)[: limit if limit is not None else None]
        examples: dict[tuple, list[str]] = {}
        sample = [m for k in keys for m in by_key[k][:_EXAMPLES]]
        ctx, story_ids = _contexts(db, sample)
        for k in keys:
            texts = []
            for m in by_key[k][:_EXAMPLES]:
                if m.table == "story_words":
                    texts.append(_story_sentence(db, m.row_id))
                else:
                    ar, en = ctx.get((m.table, m.owner_id), ("", ""))
                    texts.append(f"{ar[:400]}" + (f" — {en[:300]}" if en else ""))
            examples[k] = [t for t in texts if t] or [by_key[k][0].surface]
    finally:
        db.close()

    decided: dict[tuple, dict] = {}
    for i in range(0, len(keys), _JUDGE_BATCH):
        batch = keys[i:i + _JUDGE_BATCH]
        items = [{"index": n, "surface": by_key[k][0].surface, "old": _entry(lemmas[k[0]]),
                  "new": _entry(lemmas[k[1]]), "examples": examples[k]} for n, k in enumerate(batch)]
        res = judge(items)
        if res is None:
            continue  # not stored: judged again on the next run
        for n, k in enumerate(batch):
            if n in res:
                decided[k] = res[n]

    if decided:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            for (old, new, skey), d in decided.items():
                db.add(LemmaRemapVerdict(old_lemma_id=old, new_lemma_id=new, surface_key=skey,
                                         approved=d["approved"],
                                         reason=f"{d['reason']}: {d['note']}".strip(": "),
                                         judged_at=now))
            db.commit()
        finally:
            db.close()
        verdicts.update({k: d["approved"] for k, d in decided.items()})
    return verdicts


def apply_moves(moves: list[Move], verdicts: dict, lemmas: dict) -> dict:
    """Apply approved moves compare-and-set. Returns stats with the reviewable
    sentence ids that changed."""
    from app.models import ActivityLog, Sentence
    from app.services.sentence_eligibility import MAPPING_VERIFICATION_MIN_AT

    approved = [m for m in moves if verdicts.get(m.key) is True]
    models = {name: model for name, model, _ in _tables()}
    db = SessionLocal()
    try:
        moved = defaultdict(int)
        by_sentence: dict[int, list[Move]] = defaultdict(list)
        for m in approved:
            model = models[m.table]
            values = {"lemma_id": m.new}
            if m.table == "story_words":
                values["gloss_en"] = lemmas[m.new].gloss_en
            n = (db.query(model)
                 .filter(model.id == m.row_id, model.lemma_id == m.stored_lemma_id)
                 .update(values, synchronize_session=False))
            if n:
                moved[m.table] += 1
                if m.table == "sentence_words":
                    by_sentence[m.owner_id].append(m)
        from app.models import SentenceWord

        sentences = {s.id: s for s in db.query(Sentence).filter(Sentence.id.in_(list(by_sentence))).all()}
        retargeted = 0
        for sid, sms in by_sentence.items():
            sentence = sentences.get(sid)
            if sentence is None or sentence.target_lemma_id is None:
                continue
            followed = {m.new for m in sms if sentence.target_lemma_id in (m.old, m.stored_lemma_id)}
            if len(followed) != 1:
                continue
            still = (db.query(SentenceWord.id)
                     .filter(SentenceWord.sentence_id == sid,
                             SentenceWord.lemma_id == sentence.target_lemma_id).first())
            if still is None:
                sentence.target_lemma_id = followed.pop()
                retargeted += 1
        reviewable = sorted(
            sid for sid, s in sentences.items()
            if s.is_active and s.mappings_verified_at is not None
            and s.mappings_verified_at >= MAPPING_VERIFICATION_MIN_AT
        )
        pairs = defaultdict(int)
        for m in approved:
            pairs[(m.old, m.new)] += 1
        if moved:
            db.add(ActivityLog(
                event_type="word_links_remapped",
                summary=(f"Moved {sum(moved.values())} stored word links onto the lemma they are "
                         f"({dict(moved)}); {retargeted} sentence targets followed"),
                detail_json={"moved": dict(moved), "retargeted": retargeted,
                             "pairs": [[o, n, c] for (o, n), c in sorted(pairs.items(), key=lambda x: -x[1])],
                             "reverify_sentence_ids": reviewable},
            ))
        db.commit()
        return {"moved": dict(moved), "retargeted": retargeted, "reviewable": reviewable}
    finally:
        db.close()


def run(apply: bool = False, judge_limit: int | None = None, reverify: bool = True) -> dict:
    """Find, judge (only unjudged moves), and optionally apply. Model calls run
    with no database session open."""
    db = SessionLocal()
    try:
        moves, lemmas = find_moves(db)
    finally:
        db.close()
    verdicts = judge_moves(moves, lemmas, limit=judge_limit) if moves else {}
    keys = {m.key for m in moves}
    stats = {
        "candidates": len(moves),
        "distinct": len(keys),
        "approved_distinct": sum(1 for k in keys if verdicts.get(k) is True),
        "refused_distinct": sum(1 for k in keys if verdicts.get(k) is False),
        "unjudged_distinct": sum(1 for k in keys if k not in verdicts),
        "approved_rows": sum(1 for m in moves if verdicts.get(m.key) is True),
    }
    if apply and stats["approved_rows"]:
        applied = apply_moves(moves, verdicts, lemmas)
        stats.update(applied)
        if reverify and applied["reviewable"]:
            from app.services.mapping_rescue import reverify_all_active_sentences

            stats["reverify"] = str(reverify_all_active_sentences(sentence_ids=applied["reviewable"]))
    return stats
