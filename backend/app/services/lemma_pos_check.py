"""Part-of-speech check for lemmas.

The mapping verifier is shown each lemma's ``pos`` and told to flag a wrong
part of speech, so a lemma stored with the wrong label (a verb stored as a
noun) gets correctly mapped sentences rejected. ``check_new_lemmas`` runs as
a quality gate on every import; ``scripts/audit_lemma_pos.py`` runs the same
check over the whole table.
"""

import logging
from collections import Counter

from app.models import Lemma

logger = logging.getLogger(__name__)

BATCH_SIZE = 60
OPEN_CLASSES = {"verb", "noun", "adj", "adv"}
POS_VALUES = [
    "verb", "noun", "adj", "adv", "prep", "pron", "conj", "particle",
    "num", "interj", "expr", "noun_prop",
]
LABEL_SPELLINGS = {
    "adjective": "adj",
    "adverb": "adv",
    "preposition": "prep",
    "conjunction": "conj",
    "v": "verb",
    "passive participle": "adj",
}
SKIP_CATEGORIES = {"proper_name", "onomatopoeia", "practiced_name"}
SKIP_POS = {"noun_prop", "proper_noun", "name", "foreign"}

SYSTEM_PROMPT = (
    "You are an Arabic lexicographer checking part-of-speech labels in a "
    "learner's dictionary."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "pos": {"type": "string", "enum": POS_VALUES},
                    "conflict": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["lemma_id", "pos", "conflict", "note"],
            },
        }
    },
    "required": ["entries"],
}


def canonical_label(pos: str | None) -> str | None:
    if not pos:
        return None
    p = pos.strip().lower()
    return LABEL_SPELLINGS.get(p, p)


def build_prompt(rows: list[dict]) -> str:
    lines = "\n".join(
        f"- lemma_id={r['lemma_id']}; word={r['lemma_ar']}; gloss={r['gloss_en']}"
        for r in rows
    )
    return f"""For each dictionary headword below, give its part of speech as a dictionary would list that headword.

Rules:
- Judge from the vocalized Arabic form together with the gloss.
- A verbal noun (masdar) is a noun. A person, trade or role (مُدِير, عَامِل, مُتَقَاعِد) is a noun.
- A participle or nisba used mainly to describe (مُزْدَحِم, ثَمِين, نَبَاتِيّ) is an adj.
- A fixed phrase of several words is expr.
- Set conflict=true when the vocalized Arabic form and the English gloss cannot be the same word in the same part of speech, for example the noun form وَصْل with the gloss "to arrive", or a singular noun glossed as a plural or as a verb. In that case still give the part of speech of the Arabic form, and say in the note what disagrees.
- Otherwise conflict=false and note is an empty string.

Return one entry for every lemma_id.

{lines}"""


def audit_rows(rows: list[dict]) -> list[dict]:
    """One LLM call. ``rows`` need lemma_id, lemma_ar, gloss_en, pos.

    Returns the rows the model answered, each with proposed_pos, conflict, note.
    Raises on provider failure.
    """
    from app.services.llm import generate_completion

    by_id = {r["lemma_id"]: r for r in rows}
    result = generate_completion(
        prompt=build_prompt(rows),
        system_prompt=SYSTEM_PROMPT,
        json_schema=SCHEMA,
        timeout=300,
        model_override="claude_haiku",
        task_type="lemma_pos_audit",
    )
    entries = result.get("entries", []) if isinstance(result, dict) else []
    out: list[dict] = []
    for e in entries:
        row = by_id.pop(e.get("lemma_id"), None) if isinstance(e, dict) else None
        if row is None or e.get("pos") not in POS_VALUES:
            continue
        out.append({
            **row,
            "proposed_pos": e["pos"],
            "conflict": bool(e.get("conflict")),
            "note": str(e.get("note") or "")[:200],
        })
    return out


def auditable(lemma) -> bool:
    return bool(
        lemma.gloss_en
        and lemma.word_category not in SKIP_CATEGORIES
        and (lemma.pos or "").lower() not in SKIP_POS
    )


def plan_changes(proposals: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (changes, left_alone) from audit proposals."""
    changes: list[dict] = []
    left: list[dict] = []
    for p in proposals:
        current = p.get("pos")
        tidy = canonical_label(current)
        proposed = p["proposed_pos"]
        if tidy == proposed:
            if tidy != current:
                changes.append({**p, "new_pos": tidy, "kind": "label_spelling"})
            continue
        if p.get("conflict"):
            if tidy != current:
                changes.append({**p, "new_pos": tidy, "kind": "label_spelling"})
            left.append({**p, "why": "form_gloss_conflict"})
            continue
        if proposed in OPEN_CLASSES and (tidy is None or tidy in OPEN_CLASSES):
            changes.append({**p, "new_pos": proposed, "kind": "corrected"})
            continue
        if tidy != current:
            changes.append({**p, "new_pos": tidy, "kind": "label_spelling"})
        left.append({**p, "why": "outside_open_classes"})
    return changes, left


def check_new_lemmas(db, lemma_ids: list[int]) -> dict:
    """Quality gate: correct the label of newly created lemmas.

    Reads the rows, releases the session, makes the LLM calls, then writes.
    A provider failure leaves the labels as imported.
    """
    rows = [
        {"lemma_id": l.lemma_id, "lemma_ar": l.lemma_ar, "gloss_en": l.gloss_en, "pos": l.pos}
        for l in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
        if auditable(l)
    ]
    db.commit()
    summary = {"checked": 0, "changed": 0, "conflicts": 0}
    if not rows:
        return summary

    proposals: list[dict] = []
    for start in range(0, len(rows), BATCH_SIZE):
        proposals.extend(audit_rows(rows[start:start + BATCH_SIZE]))
    summary["checked"] = len(proposals)

    changes, left = plan_changes(proposals)
    for c in changes:
        lemma = db.get(Lemma, c["lemma_id"])
        if lemma is None or lemma.pos != c["pos"] or lemma.gloss_en != c["gloss_en"]:
            continue
        lemma.pos = c["new_pos"]
        summary["changed"] += 1
    db.commit()

    conflicts = [x for x in left if x["why"] == "form_gloss_conflict"]
    summary["conflicts"] = len(conflicts)
    if conflicts:
        from app.services.activity_log import log_activity

        log_activity(
            db,
            event_type="import_form_gloss_conflict",
            summary=(
                f"{len(conflicts)} new lemmas whose Arabic form and gloss "
                "describe different words"
            ),
            detail={"lemmas": [
                {"lemma_id": x["lemma_id"], "lemma_ar": x["lemma_ar"],
                 "gloss_en": x["gloss_en"], "note": x["note"]}
                for x in conflicts
            ]},
        )
    return summary
