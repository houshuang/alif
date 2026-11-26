"""Gate for widening a stored gloss: every proposed widening is checked here
before any code path may write it (``preread_service.regloss`` refuses an
unapproved one).

Sense checks (pre-read, Snap taps, Snap chips and page pass, add contexts,
story imports, textbook scans) propose a "broader" gloss when a word is used
in a meaning its stored gloss lacks. Unchecked, those proposals wrote another
word's meaning (حَيّ "alive" → "life; alive", which is حَيَاة), a narrowing
(عَشْرَة → "ten (used in compound numbers 13–19)"), an idiom's meaning
(صَرْف → "disregarding", from بصرف النظر) and book-specific senses
(جَمْعِيَّة → "National Assembly"): 14 of 113 writes on 2026-11-26
(research/snap-prematch-2026-11/review-2026-11-26.md).

Two stages:
1. Rules (no model): plain English, at most 90 characters, an actual change,
   every stored meaning kept, no transliterated phrase in the gloss.
2. An independent judge (Claude Opus, not the model that proposed the gloss)
   sees the lemma, its part of speech and root, the stored and proposed
   gloss, the sentence it came from, the other vocabulary entries on the same
   root and the Wiktionary senses of the headword, and must give a verdict
   for every item. A missing verdict or a failed call means "not approved".
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "wiktionary_index.json.gz"
MAX_GLOSS_CHARS = 90
JUDGE_MODEL = "claude_opus"
_JUDGE_BATCH = 25
_STOP = {"the", "and", "for", "one", "who", "that", "with", "from", "into", "onto", "being",
         "something", "someone", "thing", "etc", "e.g", "also", "used", "very", "make"}
# "(bi-t-tālī)", "(fī-mā)": a transliterated phrase inside the gloss is an idiom.
_TRANSLIT_PHRASE = re.compile(r"\([^)]*\b[a-zʿʾāīūḥṣḍṭẓġš]+-[a-zʿʾāīūḥṣḍṭẓġš]+[^)]*\)", re.I)

REASONS = ("ok", "other_word", "idiom", "specific", "narrowing", "drops_meaning",
           "wrong_pos", "no_value", "wrong_meaning")

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "approve": {"type": "boolean"},
                    "reason": {"type": "string", "enum": list(REASONS)},
                    "note": {"type": "string"},
                },
                "required": ["index", "approve", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}


def bare_key(ar: str) -> str:
    """Letters only, alif/hamza seats and ta marbuta folded, for index lookups."""
    s = unicodedata.normalize("NFC", ar or "")
    s = re.sub(r"[ً-ٰٟـ]", "", s)
    s = re.sub(r"[أإآٱ]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"[^ء-ي]", "", s)


def _senses(gloss: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,/]", gloss or "") if p.strip()]


def _words(text: str) -> set[str]:
    words = set()
    for w in re.findall(r"[a-z][a-z'-]*", (text or "").casefold()):
        if len(w) > 2 and w not in _STOP:
            words.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return words


def _norm_sense(s: str) -> str:
    s = re.sub(r"\([^)]*\)", "", s.casefold())
    s = re.sub(r"^(to|a|an|the)\s+", "", s.strip())
    return re.sub(r"[^a-z ]", "", s).strip()


def _stem_match(word: str, pool: set[str]) -> bool:
    """transform ~ transformation, predominant ~ predominance."""
    return any(w == word or (min(len(w), len(word)) >= 5 and (w.startswith(word[:5]) and word.startswith(w[:5])))
               for w in pool)


def dropped_senses(old: str, new: str) -> list[str]:
    """Stored senses none of whose words (or their stems) survive in the proposal."""
    new_words = _words(new)
    out = []
    for sense in _senses(old):
        sw = _words(sense)
        if sw and not any(_stem_match(w, new_words) for w in sw):
            out.append(sense)
    return out


def rule_check(old: str, new: str) -> str | None:
    """The rule-stage reason a proposal fails, or None when it passes. A
    proposal that drops some stored senses goes to the judge, which is told
    which; one that keeps none of them fails here."""
    new = (new or "").strip()
    if not new or len(new) > MAX_GLOSS_CHARS or re.search(r"[؀-ۿ]", new) or "=" in new:
        return "format"
    if {_norm_sense(s) for s in _senses(old)} == {_norm_sense(s) for s in _senses(new)}:
        return "no_value"
    if new.casefold().strip() == (old or "").casefold().strip():
        return "no_value"
    if _TRANSLIT_PHRASE.search(new) and not _TRANSLIT_PHRASE.search(old or ""):
        return "idiom"
    old_senses = [x for x in _senses(old) if _words(x)]
    if old_senses and len(dropped_senses(old, new)) == len(old_senses):
        return "drops_meaning"
    return None


@lru_cache(maxsize=1)
def _index() -> dict:
    try:
        with gzip.open(INDEX_PATH, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        logger.warning("gloss gate: no Wiktionary index at %s; judging without it", INDEX_PATH)
        return {}


def dictionary_senses(lemma_ar: str, limit: int = 4) -> list[dict]:
    """Wiktionary entries for the headword: exact vocalized matches first."""
    from app.services.preread_service import _vocalized_key

    rows = _index().get(bare_key(lemma_ar)) or []
    key = _vocalized_key(lemma_ar or "")
    rows = sorted(rows, key=lambda r: _vocalized_key(r[0]) != key)
    return [{"headword": r[0], "pos": r[1], "senses": r[2]} for r in rows[:limit]]


def _context(items: list[dict]) -> dict[int, dict]:
    """Root and same-root vocabulary entries per item (short read-only session)."""
    from app.database import SessionLocal
    from app.models import Lemma

    out: dict[int, dict] = {}
    db = SessionLocal()
    try:
        for it in items:
            lemma = db.get(Lemma, it["lemma_id"]) if it.get("lemma_id") else None
            if lemma is None:
                out[it["index"]] = {"root": None, "siblings": []}
                continue
            siblings = []
            if lemma.root_id:
                rows = (
                    db.query(Lemma)
                    .filter(Lemma.root_id == lemma.root_id, Lemma.lemma_id != lemma.lemma_id,
                            Lemma.canonical_lemma_id.is_(None))
                    .order_by(Lemma.frequency_rank.is_(None), Lemma.frequency_rank)
                    .limit(14)
                    .all()
                )
                siblings = [f"{r.lemma_ar} ({r.pos or '?'}) — {r.gloss_en or ''}" for r in rows]
            out[it["index"]] = {"root": lemma.root.root if lemma.root else None,
                                "siblings": siblings}
    finally:
        db.close()
    return out


def _judge(items: list[dict], ctx: dict[int, dict]) -> dict[int, dict] | None:
    from app.services.llm import generate_completion

    blocks = []
    for it in items:
        c = ctx.get(it["index"]) or {}
        lines = [
            f"ITEM {it['index']}",
            f"  lemma: {it['lemma_ar']} ({it.get('pos') or '?'})"
            + (f", root {c['root']}" if c.get("root") else ""),
            f"  stored gloss: {it.get('old_gloss') or ''}",
            f"  proposed gloss: {it['new_gloss']}",
        ]
        dropped = dropped_senses(it.get("old_gloss") or "", it["new_gloss"])
        if dropped:
            lines.append("  stored senses missing from the proposal: " + "; ".join(dropped)
                         + " (reject as drops_meaning unless each is a near-synonym of a kept sense)")
        if it.get("surface") or it.get("context"):
            lines.append(f"  met as: {it.get('surface') or '?'} in «{(it.get('context') or '')[:300]}»")
        if c.get("siblings"):
            lines.append("  other entries on this root: " + "; ".join(c["siblings"]))
        dict_rows = dictionary_senses(it["lemma_ar"])
        if dict_rows:
            lines.append("  Wiktionary: " + " | ".join(
                f"{d['headword']} ({d['pos']}): {'; '.join(d['senses'])}" for d in dict_rows))
        blocks.append("\n".join(lines))
    prompt = (
        "You are checking proposed changes to the English glosses in an adult learner's "
        "Arabic dictionary. A model saw each word in a sentence and proposed widening the "
        "stored gloss to include the meaning used there. The stored gloss is shown for this "
        "lemma on every future card, so it must be the lemma's own general dictionary "
        "meaning. Approve only when ALL of these hold:\n"
        "1. Every added meaning is a standard dictionary sense of THIS lemma with THIS part "
        "of speech. Reject (other_word) a meaning that belongs to another word: another entry "
        "on the root (حياته is حَيَاة \"life\", not the adjective حَيّ), a different verb form, "
        "the elative (أسوأ \"worse\" is not سَيِّئ), a plural, verbal noun or participle, an "
        "adverbial use (خصوصاً), or a homograph (سَمَّى \"to name\" never means \"to poison\").\n"
        "2. The meaning is not from a set phrase or idiom (بصرف النظر \"regardless\" is not a "
        "sense of صَرْف; مثير للاهتمام gives \"interesting\", not مثير). Reject as idiom.\n"
        "3. It is general, not a proper name, a place, one country's institution, a "
        "historical term from this text, or a regional/technical sense put first (specific).\n"
        "4. It is not narrower than the stored gloss (narrowing) and does not drop or distort "
        "a correct stored meaning (drops_meaning); the part of speech stays (wrong_pos).\n"
        "5. It changes something useful (no_value for reorderings, punctuation, a synonym of "
        "what is already there) and the added meaning is correct (wrong_meaning).\n"
        "Use the Wiktionary senses as evidence: a meaning listed for this headword supports "
        "approval; a meaning listed only for another entry is a strong sign of other_word. "
        "Wiktionary is incomplete, so absence alone is not a reason to reject a common sense. "
        "When in doubt, reject: a missing sense costs little, a wrong one teaches an error.\n"
        "Give a verdict for every item, with reason \"ok\" when approving and a short note.\n\n"
        + "\n\n".join(blocks)
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are a meticulous Arabic-English lexicographer.",
            json_schema=_SCHEMA,
            temperature=0.0,
            model_override=JUDGE_MODEL,
            task_type="gloss_gate",
        )
    except Exception as e:
        logger.warning("gloss gate judge failed: %s", e)
        return None
    rows = res.get("verdicts") if isinstance(res, dict) else None
    if not isinstance(rows, list):
        return None
    wanted = {it["index"] for it in items}
    out = {}
    for r in rows:
        if isinstance(r, dict) and r.get("index") in wanted and isinstance(r.get("approve"), bool):
            reason = r.get("reason") if r.get("reason") in REASONS else "wrong_meaning"
            approve = r["approve"] and reason == "ok"
            out[r["index"]] = {"approved": approve, "reason": "ok" if approve else reason,
                               "note": (r.get("note") or "")[:200], "stage": "judge"}
    return out


def review_widenings(items: list[dict]) -> dict[int, dict]:
    """Decide each proposed widening.

    ``items``: [{index, lemma_id?, lemma_ar, pos?, old_gloss, new_gloss,
    surface?, context?}]. Returns {index: {approved, reason, note?, stage}} for
    every item; anything not explicitly approved by both stages is refused."""
    from app.services.interaction_logger import log_interaction

    out: dict[int, dict] = {}
    survivors = []
    for it in items:
        reason = rule_check(it.get("old_gloss") or "", it.get("new_gloss") or "")
        if reason:
            out[it["index"]] = {"approved": False, "reason": reason, "stage": "rule"}
        else:
            survivors.append(it)
    if survivors:
        ctx = _context(survivors)
        for i in range(0, len(survivors), _JUDGE_BATCH):
            batch = survivors[i:i + _JUDGE_BATCH]
            verdicts = _judge(batch, ctx) or {}
            for it in batch:
                out[it["index"]] = verdicts.get(
                    it["index"], {"approved": False, "reason": "unchecked", "stage": "error"})
    for it in items:
        d = out[it["index"]]
        log_interaction(
            event="gloss_gate", lemma_id=it.get("lemma_id"), lemma_ar=it.get("lemma_ar"),
            gloss_from=it.get("old_gloss"), gloss_to=it.get("new_gloss"),
            approved=d["approved"], reason=d["reason"], stage=d["stage"], note=d.get("note"),
        )
    return out
