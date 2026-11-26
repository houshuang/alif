"""Word-panel enrichment: derivation chain, form glosses and set phrases.

The review word panel (frontend ``WordInfoCard`` dock) shows, besides meaning
and root words, three things the lemma row does not hold:

- a derivation chain from the basic word to this one
  (عَلِمَ know → عَلَّمَ teach (II) → مُعَلِّم one who teaches);
- short glosses for the stored forms (مُسْتَخْدِم "user", اِسْتِخْدَام "use");
- two or three set phrases that contain the word (حُرِّيَّةُ التَّنَقُّلِ).

They are written lazily into ``Lemma.word_card_json`` by
``scripts/enrich_word_cards.py``, most useful words first: open misses, then
words being learned, then known words in active sentences by lowest FSRS
stability. Phrase candidates counted from the book corpus
(``scripts/build_phrase_candidates.py``) are shown to the model, and a phrase it
keeps from them is marked ``attested``.

The model output is never trusted as is: a chain must end at this lemma and use
words from its root family or its own forms (one unlisted step allowed), form
glosses are kept only for forms the lemma stores, and a phrase is kept only when
one of its tokens resolves to this lemma or contains one of its forms.

Phases follow the SQLite write-lock rule: read inputs and close the transaction,
call the model, then write each result in a short transaction.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge, WordAddContext
from app.services.sentence_validator import (
    _is_function_word,
    normalize_alef,
    strip_diacritics,
)

logger = logging.getLogger(__name__)

WORD_CARD_VERSION = 1
BATCH_SIZE = 8
MAX_FAMILY_IN_PROMPT = 25
MAX_PHRASES = 3
MAX_PHRASE_WORDS = 5
# Forms the panel shows, in the order it shows them; glosses are asked only for these.
FORM_KEYS = ("present", "masdar", "active_participle", "passive_participle", "plural", "feminine", "elative")
CHAIN_TAGS = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
              "-ing", "doer", "done", "place", "tool", "noun", "adj", "pl"}
PHRASE_CANDIDATES_PATH = Path(__file__).resolve().parents[2] / "data" / "corpora" / "lemma_phrase_candidates.json"

SYSTEM_PROMPT = """You write compact study notes for an advanced learner of Arabic who reads MSA and classical texts.
Be accurate above all: leave a field empty rather than guess. Vocalize every Arabic word fully."""

PROMPT_HEADER = """For each word below, return:

1. chain: how the word is derived, as 2 to 4 steps from the most basic related word to THIS word.
   - The last step must be this word itself (same lemma).
   - Use words from the word's "root family" list or its "forms" wherever possible and give their lemma_id;
     at most one step may be a real word not in those lists (lemma_id null), e.g. a Form X verb that links a place noun.
   - en: a very short gloss that shows the derivation, e.g. "to make know: teach", "place of seeking healing".
   - tag: one of I..X for verbs (the verb form), or -ing (verbal noun), doer (active participle), done (passive participle),
     place, tool, noun, adj, pl.
   - Return an empty chain when the word has no meaningful derivation (a basic Form I verb or a primitive noun).
2. forms: a 1-4 word English gloss for each listed form key (only the keys given).
3. phrases: 2 or 3 fixed, frequent combinations a reader really meets (collocations, idioms, set expressions such as
   وَسَائِلُ التَّوَاصُلِ الاِجْتِمَاعِيِّ "social media"), at most 5 words each.
   - Every phrase must contain this word or one of its forms. Not example sentences.
   - Prefer combinations from "book phrases" (counted in real books) when they are genuine collocations, and set
     from_book true for those. You may add very common MSA collocations not in that list (from_book false).
   - Return fewer, or none, rather than weak ones.

Words:
"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "chain": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ar": {"type": "string"},
                                "en": {"type": "string"},
                                "tag": {"type": "string"},
                                "lemma_id": {"type": ["integer", "null"]},
                            },
                            "required": ["ar", "en", "tag", "lemma_id"],
                            "additionalProperties": False,
                        },
                    },
                    "forms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"key": {"type": "string"}, "en": {"type": "string"}},
                            "required": ["key", "en"],
                            "additionalProperties": False,
                        },
                    },
                    "phrases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ar": {"type": "string"},
                                "en": {"type": "string"},
                                "from_book": {"type": "boolean"},
                            },
                            "required": ["ar", "en", "from_book"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["lemma_id", "chain", "forms", "phrases"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["words"],
    "additionalProperties": False,
}


def _bare(text: str | None) -> str:
    return normalize_alef(strip_diacritics(text or "")).replace("ـ", "").strip()


def _skeleton(text: str | None) -> str:
    """Bare form that keeps shadda, so عَلِمَ and عَلَّمَ stay different words."""
    kept = "".join(ch for ch in (text or "") if ch == "\u0651" or not ("\u064b" <= ch <= "\u0652"))
    return normalize_alef(kept.replace("\u0670", "").replace("ـ", "")).strip()


def _fsrs_stability(ulk: UserLemmaKnowledge) -> float:
    card = ulk.fsrs_card_json
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except ValueError:
            card = None
    if isinstance(card, dict):
        try:
            return float(card.get("stability") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def select_candidates(db: Session, limit: int, now: datetime | None = None) -> list[int]:
    """Lemma ids without a word card, most useful first.

    Order: open misses, then acquiring/learning/lapsed words, then known words by
    lowest FSRS stability. Only gated, canonical, non-name content words that
    appear in at least one active sentence.
    """
    from app.services.missed_word_recovery import open_misses

    now = now or datetime.now(timezone.utc)
    in_sentences = (
        db.query(SentenceWord.lemma_id)
        .join(Sentence, Sentence.id == SentenceWord.sentence_id)
        .filter(Sentence.is_active.is_(True), SentenceWord.lemma_id.isnot(None))
        .distinct()
    )
    rows = (
        db.query(Lemma, UserLemmaKnowledge)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            Lemma.word_card_json.is_(None),
            Lemma.gates_completed_at.isnot(None),
            Lemma.canonical_lemma_id.is_(None),
            Lemma.lemma_id.in_(in_sentences),
            UserLemmaKnowledge.knowledge_state.in_(("acquiring", "learning", "lapsed", "known")),
        )
        .all()
    )
    try:
        missed = set(open_misses(db, now).keys())
    except Exception:  # a ranking aid, not a gate
        logger.exception("word_card: open_misses failed; ranking without it")
        missed = set()

    ranked: list[tuple[tuple, int]] = []
    for lemma, ulk in rows:
        if lemma.word_category in {"proper_name", "onomatopoeia"} or lemma.pos == "particle":
            continue
        if lemma.function_word_override or _is_function_word(_bare(lemma.lemma_ar_bare)):
            continue
        if lemma.lemma_id in missed:
            tier = 0
        elif ulk.knowledge_state in ("acquiring", "learning", "lapsed"):
            tier = 1
        else:
            tier = 2
        ranked.append(((tier, _fsrs_stability(ulk), lemma.lemma_id), lemma.lemma_id))
    ranked.sort(key=lambda item: item[0])
    return [lid for _, lid in ranked[:limit]]


def load_phrase_candidates(path: Path = PHRASE_CANDIDATES_PATH) -> dict[int, list[tuple[str, int]]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): [(p, int(n)) for p, n in v] for k, v in raw.items()}


def gather_inputs(db: Session, lemma_ids: list[int], phrase_candidates: dict[int, list[tuple[str, int]]]) -> list[dict]:
    """Read-only: everything the prompt and the validation need, as plain data."""
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    # Set phrases the learner met the word in (Snap/pre-read adds) count as attested.
    met: dict[int, list[str]] = {}
    for lid, phrase in (
        db.query(WordAddContext.lemma_id, WordAddContext.phrase_json)
        .filter(WordAddContext.lemma_id.in_(lemma_ids), WordAddContext.phrase_json.isnot(None))
    ):
        if isinstance(phrase, dict) and phrase.get("ar"):
            met.setdefault(lid, []).append(phrase["ar"])
    out = []
    for lemma in lemmas:
        family = []
        if lemma.root_id:
            sibs = (
                db.query(Lemma)
                .filter(Lemma.root_id == lemma.root_id, Lemma.lemma_id != lemma.lemma_id,
                        Lemma.canonical_lemma_id.is_(None))
                .limit(MAX_FAMILY_IN_PROMPT)
                .all()
            )
            family = [{"lemma_id": s.lemma_id, "ar": s.lemma_ar, "gloss": s.gloss_en, "pos": s.pos, "wazn": s.wazn}
                      for s in sibs]
        forms_json = lemma.forms_json if isinstance(lemma.forms_json, dict) else {}
        forms = {k: v for k, v in forms_json.items() if k in FORM_KEYS and isinstance(v, str) and v.strip()}
        verb_form = forms_json.get("verb_form") if isinstance(forms_json.get("verb_form"), str) else None
        out.append({
            "lemma_id": lemma.lemma_id,
            "ar": lemma.lemma_ar,
            "bare": lemma.lemma_ar_bare,
            "gloss": lemma.gloss_en,
            "pos": lemma.pos,
            "wazn": lemma.wazn,
            "verb_form": verb_form,
            "root": lemma.root.root if lemma.root else None,
            "root_meaning": lemma.root.core_meaning_en if lemma.root else None,
            "forms": forms,
            "family": family,
            "book_phrases": met.get(lemma.lemma_id, []) + [p for p, _ in phrase_candidates.get(lemma.lemma_id, [])],
        })
    return out


def build_prompt(inputs: list[dict]) -> str:
    blocks = []
    for w in inputs:
        block = {
            "lemma_id": w["lemma_id"],
            "word": w["ar"],
            "gloss": w["gloss"],
            "pos": w["pos"],
            "pattern": w["wazn"],
            "verb_form": w["verb_form"],
            "root": w["root"],
            "root_meaning": w["root_meaning"],
            "forms": w["forms"],
            "root family": [{k: f[k] for k in ("lemma_id", "ar", "gloss", "pos")} for f in w["family"]],
            "book phrases": w["book_phrases"],
        }
        blocks.append(json.dumps(block, ensure_ascii=False))
    return PROMPT_HEADER + "\n".join(blocks)


def _contains_word(phrase_ar: str, inp: dict, resolve) -> bool:
    """A phrase counts only if one of its tokens is this word or one of its forms."""
    targets = {_bare(inp["ar"]), _bare(inp["bare"])} | {_bare(v) for v in inp["forms"].values()}
    targets = {t for t in targets if len(t) >= 2}
    for tok in phrase_ar.split():
        if resolve is not None and resolve(tok) == inp["lemma_id"]:
            return True
        tb = _bare(tok)
        if tb.startswith("ال"):
            tb_noal = tb[2:]
        else:
            tb_noal = tb
        for t in targets:
            # Whole token, or token = clitics + form (+ a case/pronoun ending of at most 3 letters).
            if tb == t or tb_noal == t:
                return True
            idx = tb.find(t)
            if idx != -1 and len(t) >= 3 and idx <= 3 and len(tb) - idx - len(t) <= 3:
                return True
    return False


def validate(inp: dict, raw: dict, resolve=None) -> dict:
    """Turn one model answer into a stored word card, dropping anything unchecked."""
    family_ids = {f["lemma_id"]: f for f in inp["family"]}
    form_bares = {_bare(v) for v in inp["forms"].values()}
    lemma_bare = _bare(inp["ar"])

    chain: list[dict] = []
    raw_chain = raw.get("chain") or []
    if raw_chain and _bare(raw_chain[-1].get("ar")) == lemma_bare:
        unlisted = 0
        ok = True
        for i, node in enumerate(raw_chain[:4]):
            ar, en, tag = (node.get("ar") or "").strip(), (node.get("en") or "").strip(), (node.get("tag") or "").strip()
            if not ar or not en or tag not in CHAIN_TAGS:
                ok = False
                break
            lid = node.get("lemma_id")
            last = i == len(raw_chain[:4]) - 1
            if last:
                lid = inp["lemma_id"]
            elif lid in family_ids and _skeleton(family_ids[lid]["ar"]) == _skeleton(ar):
                pass
            else:
                lid = None
                if _bare(ar) not in form_bares:
                    unlisted += 1
            chain.append({"ar": ar, "en": en[:60], "tag": tag, "lemma_id": lid})
        if not ok or unlisted > 1 or len(chain) < 2:
            chain = []

    forms = {}
    for item in raw.get("forms") or []:
        key, en = item.get("key"), (item.get("en") or "").strip()
        if key in inp["forms"] and en and len(en) <= 40:
            forms[key] = en

    book = {_bare(p) for p in inp["book_phrases"]}
    phrases: list[dict] = []
    seen = set()
    for item in raw.get("phrases") or []:
        ar, en = (item.get("ar") or "").strip(), (item.get("en") or "").strip()
        if not ar or not en or len(ar.split()) > MAX_PHRASE_WORDS or len(ar.split()) < 2:
            continue
        pb = _bare(ar)
        if pb in seen or not _contains_word(ar, inp, resolve):
            continue
        seen.add(pb)
        attested = pb in book or any(b and b in pb for b in book)
        phrases.append({"ar": ar, "en": en[:60], "attested": bool(attested)})
        if len(phrases) >= MAX_PHRASES:
            break

    return {
        "version": WORD_CARD_VERSION,
        "chain": chain,
        "forms": forms,
        "phrases": phrases,
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
    }


def call_model(inputs: list[dict]) -> dict[int, dict] | None:
    """One model call for a batch. None means the call failed: nothing is stored."""
    from app.services.llm import AllProvidersFailed, generate_completion

    try:
        result = generate_completion(
            prompt=build_prompt(inputs),
            system_prompt=SYSTEM_PROMPT,
            json_schema=SCHEMA,
            temperature=0.3,
            timeout=240,
            model_override="claude_haiku",
            task_type="word_card",
        )
    except AllProvidersFailed as exc:
        logger.warning("word_card: all providers failed: %s", exc)
        return None
    words = result.get("words") if isinstance(result, dict) else None
    if not isinstance(words, list):
        return None
    return {w["lemma_id"]: w for w in words if isinstance(w, dict) and isinstance(w.get("lemma_id"), int)}


def enrich_word_cards(db: Session, limit: int, dry_run: bool = False) -> dict[str, int]:
    """Select, call, validate and write. Returns counts for logging."""
    return enrich_word_cards_for(db, select_candidates(db, limit), dry_run=dry_run)


def enrich_word_cards_for(db: Session, ids: list[int], dry_run: bool = False) -> dict[str, int]:
    """Write word cards for exactly these lemma ids (skips ones that have a card)."""
    from app.services.sentence_validator import build_comprehensive_lemma_lookup, lookup_lemma_id
    candidates = load_phrase_candidates()
    lookup = build_comprehensive_lemma_lookup(db)
    db.rollback()  # release the read transaction before any model call

    def resolve(tok: str) -> int | None:
        try:
            return lookup_lemma_id(tok, lookup)
        except Exception:
            return None

    stats = {"selected": len(ids), "written": 0, "failed_batches": 0, "chains": 0, "phrases": 0}
    for start in range(0, len(ids), BATCH_SIZE):
        batch_ids = ids[start:start + BATCH_SIZE]
        inputs = gather_inputs(db, batch_ids, candidates)
        db.rollback()
        answers = call_model(inputs)
        if answers is None:
            stats["failed_batches"] += 1
            continue
        cards = {}
        for inp in inputs:
            raw = answers.get(inp["lemma_id"])
            if raw is None:
                continue  # left for a later run
            cards[inp["lemma_id"]] = validate(inp, raw, resolve)
        if dry_run:
            for lid, card in cards.items():
                print(json.dumps({"lemma_id": lid, **card}, ensure_ascii=False))
        else:
            for lid, card in cards.items():
                db.query(Lemma).filter(Lemma.lemma_id == lid, Lemma.word_card_json.is_(None)).update(
                    {Lemma.word_card_json: card}, synchronize_session=False
                )
            db.commit()
        stats["written"] += len(cards)
        stats["chains"] += sum(1 for c in cards.values() if c["chain"])
        stats["phrases"] += sum(len(c["phrases"]) for c in cards.values())
    return stats
