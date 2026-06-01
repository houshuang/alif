"""Confusion analysis for words marked "did not recognize" during review.

Analyzes WHY the user was confused — morphological complexity (clitics/conjugation)
or visual similarity to other known words/forms. All rule-based, no LLM calls.
"""

from sqlalchemy.orm import Session

from app.models import Lemma, Root, UserLemmaKnowledge
from app.services.sentence_validator import (
    PROCLITICS, ENCLITICS, strip_diacritics,
)

# --- Rasm skeleton mapping ---
# Letters that share the same skeletal shape (differ only by dots) map to the same group.
RASM_MAP: dict[str, str] = {}
_RASM_GROUPS = [
    ("ا", "اأإآ"),
    ("ب", "بتثنی"),  # ba/ta/tha/nun/ya share base shape
    ("ج", "جحخ"),
    ("د", "دذ"),
    ("ر", "رز"),
    ("س", "سش"),
    ("ص", "صض"),
    ("ط", "طظ"),
    ("ع", "عغ"),
    ("ف", "فق"),
    ("ك", "ك"),
    ("ل", "ل"),
    ("م", "م"),
    ("ه", "هة"),  # ha and ta marbuta
    ("و", "و"),
]
for _base, _letters in _RASM_GROUPS:
    for _ch in _letters:
        RASM_MAP[_ch] = _base


def to_rasm(text: str) -> str:
    """Convert Arabic text to rasm skeleton (dots removed)."""
    return "".join(RASM_MAP.get(ch, ch) for ch in text)


def edit_distance(a: str, b: str) -> int:
    """Standard Levenshtein distance. Fine for short Arabic words (3-8 chars)."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _is_adjacent_transposition(a: str, b: str) -> bool:
    """True if b is a with exactly one pair of adjacent letters swapped.

    Catches metathesis confusions like جرح (wound) / جحر (burrow) that plain
    Levenshtein scores as distance 2 even though it's the same letters reordered.
    """
    if len(a) != len(b) or a == b:
        return False
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    if len(diffs) != 2:
        return False
    i, j = diffs
    return j == i + 1 and a[i] == b[j] and a[j] == b[i]


def _shares_rime(a: str, b: str, n: int = 2) -> bool:
    """True if two bare forms share their final n letters but differ at the onset.

    Catches rhyme confusions like نام (sleep) / صام (fast) and حرث (plow) /
    ورث (inherit) — same ending, different first letter. Plain edit distance
    ranks these no better than a dot-variant, so they get truncated out of the
    list; the rime signal pulls the cohort up.
    """
    if len(a) < n or len(b) < n or a == b:
        return False
    return a[-n:] == b[-n:] and a[0] != b[0]


def _restore_taa_marbuta(stem: str, target: str) -> bool:
    """Check if stem matches target when accounting for taa marbuta (ة↔ت)."""
    if stem == target:
        return True
    # Try adding taa marbuta to stem end
    if stem + "ة" == target or stem + "ت" == target:
        return True
    # Try swapping final taa marbuta
    if stem and target:
        if stem[-1] in ("ة", "ت") and target[-1] in ("ة", "ت") and stem[:-1] == target[:-1]:
            return True
    return False


def decompose_surface(
    surface_bare: str,
    lemma_bare: str,
    forms_json: dict | None,
) -> dict | None:
    """Try to decompose a surface form into prefix_clitics + stem + suffix_clitics.

    Returns dict with decomposition info, or None if surface ≈ lemma (no clitics/form).
    """
    # If surface is already the lemma (or very close), no decomposition needed
    if surface_bare == lemma_bare:
        return None
    # Also check with al-prefix stripped
    surface_no_al = surface_bare[2:] if surface_bare.startswith("ال") else surface_bare
    lemma_no_al = lemma_bare[2:] if lemma_bare.startswith("ال") else lemma_bare
    if surface_no_al == lemma_no_al and not surface_bare.startswith("ال"):
        return None

    # Collect all known forms from forms_json
    known_forms: dict[str, str] = {}  # bare_form -> form_key
    if forms_json:
        for key, val in forms_json.items():
            if key in ("gender", "verb_form") or not val or not isinstance(val, str):
                continue
            known_forms[strip_diacritics(val)] = key

    best = None
    best_score = -1  # prefer: matched form > lemma match > longer stem

    def _check_stem(prefix: str, stem: str, suffix: str, prefix_labels: list[str], suffix_labels: list[str]):
        nonlocal best, best_score

        # Try matching stem against lemma and known forms
        matched_form_key = None
        matched_form_label = None

        targets_to_check: list[tuple[str, str | None, str | None]] = [
            (lemma_bare, None, "dictionary form"),
            (lemma_no_al, None, "dictionary form"),
        ]
        for form_bare, form_key in known_forms.items():
            form_bare_no_al = form_bare[2:] if form_bare.startswith("ال") else form_bare
            targets_to_check.append((form_bare, form_key, _form_label(form_key)))
            targets_to_check.append((form_bare_no_al, form_key, _form_label(form_key)))

        for target, fk, fl in targets_to_check:
            if _restore_taa_marbuta(stem, target):
                # Score: form match (2) > lemma match (1), longer stem preferred
                score = (2 if fk else 1) * 100 + len(stem)
                if score > best_score:
                    best_score = score
                    matched_form_key = fk
                    matched_form_label = fl
                    best = {
                        "prefix_clitics": [{"text": p, "label": _clitic_label(p), "type": "proclitic"} for p in prefix_labels] if prefix_labels else [],
                        "stem": stem if not fk else (forms_json or {}).get(fk, stem) if isinstance((forms_json or {}).get(fk), str) else stem,
                        "suffix_clitics": [{"text": s, "label": _enclitic_label(s), "type": "enclitic"} for s in suffix_labels] if suffix_labels else [],
                        "matched_form_key": matched_form_key,
                        "matched_form_label": matched_form_label or "dictionary form",
                    }

    # Try all proclitic + enclitic combinations
    prefixes_to_try: list[tuple[str, list[str]]] = [("", [])]
    for pro in PROCLITICS:
        if surface_bare.startswith(pro) and len(surface_bare) > len(pro) + 1:
            # Split compound proclitics like "وال" into ["و", "ال"]
            parts = _split_proclitic(pro)
            prefixes_to_try.append((pro, parts))

    # Also try al-prefix alone
    if surface_bare.startswith("ال") and len(surface_bare) > 3:
        prefixes_to_try.append(("ال", ["ال"]))

    suffixes_to_try: list[tuple[str, list[str]]] = [("", [])]
    for enc in ENCLITICS:
        if surface_bare.endswith(enc) and len(surface_bare) > len(enc) + 1:
            suffixes_to_try.append((enc, [enc]))

    for prefix, prefix_labels in prefixes_to_try:
        for suffix, suffix_labels in suffixes_to_try:
            after_prefix = surface_bare[len(prefix):] if prefix else surface_bare
            stem = after_prefix[:-len(suffix)] if suffix else after_prefix
            if len(stem) < 2:
                continue
            # Only consider if there's actually a clitic
            if not prefix and not suffix:
                continue
            _check_stem(prefix, stem, suffix, prefix_labels, suffix_labels)

    # Also try just form matching (no clitics) if surface differs from lemma
    if not best and known_forms:
        for form_bare, form_key in known_forms.items():
            if _restore_taa_marbuta(surface_bare, form_bare) or _restore_taa_marbuta(surface_no_al, form_bare):
                best = {
                    "prefix_clitics": [],
                    "stem": strip_diacritics((forms_json or {}).get(form_key, surface_bare)) if isinstance((forms_json or {}).get(form_key), str) else surface_bare,
                    "suffix_clitics": [],
                    "matched_form_key": form_key,
                    "matched_form_label": _form_label(form_key),
                }
                break

    return best


def _split_proclitic(pro: str) -> list[str]:
    """Split a compound proclitic like 'وال' into labeled parts ['و', 'ال']."""
    parts = []
    # Compound proclitics in the list: وال, بال, فال, لل, كال
    if pro in ("وال", "بال", "فال", "كال"):
        parts = [pro[0], "ال"]
    elif pro == "لل":
        parts = ["ل", "ال"]
    else:
        parts = [pro]
    return parts


PROCLITIC_LABELS: dict[str, str] = {
    "و": "and",
    "ف": "so/then",
    "ب": "with/by",
    "ل": "for/to",
    "ك": "like/as",
    "ال": "the",
}

ENCLITIC_LABELS: dict[str, str] = {
    "ه": "his/him",
    "ها": "her",
    "هم": "their (m)",
    "هن": "their (f)",
    "هما": "their (dual)",
    "ك": "your (m.s.)",
    "كم": "your (pl)",
    "كن": "your (f.pl)",
    "نا": "our/us",
    "ني": "me",
}

FORM_KEY_LABELS: dict[str, str] = {
    "plural": "plural",
    "feminine": "feminine",
    "elative": "comparative",
    "present": "present tense",
    "masdar": "verbal noun",
    "active_participle": "active participle",
    "passive_participle": "passive participle",
    "imperative": "imperative",
    "past_3fs": "past feminine",
    "past_3p": "past plural",
}


def _clitic_label(text: str) -> str:
    return PROCLITIC_LABELS.get(text, text)


def _enclitic_label(text: str) -> str:
    return ENCLITIC_LABELS.get(text, text)


def _form_label(key: str | None) -> str:
    if not key:
        return "dictionary form"
    return FORM_KEY_LABELS.get(key, key.replace("_", " "))


# --- Prefix disambiguation ---
# Single-letter proclitics that are also common root-initial letters
PREFIX_AMBIGUOUS = {"و", "ف", "ب", "ل", "ك"}


def _build_prefix_hint(
    surface_bare: str,
    lemma_bare: str,
    root: "Root | None",
    decomposition: dict | None,
) -> dict | None:
    """Build a hint when a word's first letter could be confused as a proclitic."""
    if not surface_bare:
        return None

    first_letter = surface_bare[0]
    if first_letter not in PREFIX_AMBIGUOUS:
        return None

    # Did the decomposition find a proclitic starting with this letter?
    has_proclitic = False
    if decomposition and decomposition.get("prefix_clitics"):
        first_clitic_text = decomposition["prefix_clitics"][0]["text"]
        if first_clitic_text == first_letter or first_clitic_text.startswith(first_letter):
            has_proclitic = True

    # Does the root start with this letter?
    root_ar = None
    root_meaning = None
    root_starts_with_letter = False
    if root:
        root_ar = root.root
        root_meaning = root.core_meaning_en
        root_letters = root.root.replace(".", "")
        if root_letters and root_letters[0] == first_letter:
            root_starts_with_letter = True

    lemma_starts_with_letter = lemma_bare and lemma_bare[0] == first_letter
    proclitic_label = PROCLITIC_LABELS.get(first_letter, first_letter)

    if has_proclitic:
        stem_text = decomposition["stem"] if decomposition else lemma_bare
        return {
            "letter": first_letter,
            "is_prefix": True,
            "root_ar": root_ar,
            "root_meaning": root_meaning,
            "hint_text": f"This {first_letter} is \"{proclitic_label}\" — the core word is {stem_text}",
        }
    elif root_starts_with_letter or lemma_starts_with_letter:
        if root_ar:
            return {
                "letter": first_letter,
                "is_prefix": False,
                "root_ar": root_ar,
                "root_meaning": root_meaning,
                "hint_text": f"{first_letter} here is part of root {root_ar}, not \"{proclitic_label}\"",
            }
        else:
            return {
                "letter": first_letter,
                "is_prefix": False,
                "root_ar": None,
                "root_meaning": None,
                "hint_text": f"{first_letter} here is part of the word, not the prefix \"{proclitic_label}\"",
            }

    return None


_STUDIED_STATES = ["encountered", "acquiring", "learning", "known", "lapsed", "suspended"]
_FORM_METADATA_KEYS = {"gender", "verb_form", "pattern", "notes"}
_DISPLAY_FORM_KEYS = (
    "plural",
    "feminine",
    "present",
    "masdar",
    "imperative",
    "past_1s",
    "past_3fs",
    "past_3p",
    "active_participle",
    "passive_participle",
)


def _query_vocabulary(db: Session, lemma_id: int) -> list[tuple]:
    """Query all words the user has been exposed to (non-variant)."""
    return (
        db.query(Lemma, UserLemmaKnowledge.knowledge_state)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            Lemma.lemma_id != lemma_id,
            Lemma.canonical_lemma_id.is_(None),
            UserLemmaKnowledge.knowledge_state.in_(_STUDIED_STATES),
        )
        .all()
    )


def _diff_positions(target: str, candidate: str) -> list[dict]:
    diff_positions = []
    max_len = max(len(target), len(candidate))
    for i in range(max_len):
        ch_a = target[i] if i < len(target) else ""
        ch_b = candidate[i] if i < len(candidate) else ""
        if ch_a != ch_b:
            diff_positions.append({"pos": i, "original": ch_a, "similar": ch_b})
    return diff_positions


def _similarity_forms(lemma: Lemma) -> list[dict]:
    """Return dictionary and known derived forms used for confusion matching."""
    entries: list[dict] = []
    seen: set[str] = set()

    bare = lemma.lemma_ar_bare
    if bare:
        entries.append({
            "bare": bare,
            "display": lemma.lemma_ar,
            "key": None,
            "label": "dictionary form",
        })
        seen.add(bare)

    forms = getattr(lemma, "forms_json", None)
    if isinstance(forms, dict):
        for key, value in forms.items():
            if key in _FORM_METADATA_KEYS or not isinstance(value, str) or not value.strip():
                continue
            form_bare = strip_diacritics(value)
            if not form_bare or form_bare in seen:
                continue
            entries.append({
                "bare": form_bare,
                "display": value,
                "key": key,
                "label": _form_label(key),
            })
            seen.add(form_bare)

    return entries


def _target_forms(
    lemma_bare: str,
    surface_bare: str | None = None,
) -> list[dict]:
    entries = [{
        "bare": lemma_bare,
        "display": lemma_bare,
        "key": None,
        "label": "dictionary form",
        "source": "lemma",
    }]
    if surface_bare and surface_bare != lemma_bare:
        entries.append({
            "bare": surface_bare,
            "display": surface_bare,
            "key": "surface",
            "label": "exposed form",
            "source": "surface",
        })
    return entries


def _same_root(target_root: Root | None, lemma: Lemma) -> bool:
    if not target_root:
        return False
    target_root_id = getattr(target_root, "root_id", None)
    if not isinstance(target_root_id, int):
        return False
    return getattr(lemma, "root_id", None) == target_root_id


def _is_match_eligible(
    edit_dist: int,
    rasm_dist: int,
    len_gap: int,
    is_form_or_surface_match: bool,
    same_root: bool,
    target_pos: str | None,
    candidate_pos: str | None,
    target_bare: str,
    candidate_bare: str,
    transposed: bool = False,
    rime_match: bool = False,
) -> bool:
    if edit_dist == 0:
        return True
    if transposed:
        return True
    if len_gap <= 1 and edit_dist <= 2:
        return True
    if rime_match and len_gap <= 1 and edit_dist <= 3:
        return True
    if is_form_or_surface_match and len_gap <= 2 and edit_dist <= 2:
        return True
    if rasm_dist == 0 and len_gap <= 2 and edit_dist <= 3:
        return True
    if same_root and len_gap <= 3 and (edit_dist <= 3 or rasm_dist <= 2):
        return True

    both_verbs = target_pos == "verb" and candidate_pos == "verb"
    short_neighbor = both_verbs and (len(target_bare) <= 4 or len(candidate_bare) <= 4)
    if short_neighbor and len_gap <= 2 and (edit_dist <= 2 or rasm_dist <= 2):
        return True

    return False


def _match_reason(
    edit_dist: int,
    rasm_dist: int,
    candidate_form_key: str | None,
    target_source: str,
    same_root: bool,
    target_pos: str | None,
    candidate_pos: str | None,
    target_bare: str,
    candidate_bare: str,
    transposed: bool = False,
    rime_match: bool = False,
) -> str:
    if edit_dist == 0:
        if target_source == "surface" or candidate_form_key:
            return "same exposed form"
        return "same spelling"
    if transposed:
        return "letters swapped"
    if same_root:
        return "same root"
    if rime_match:
        return "rhymes"
    if (
        target_pos == "verb"
        and candidate_pos == "verb"
        and (len(target_bare) <= 4 or len(candidate_bare) <= 4)
    ):
        return "short verb neighbor"
    if candidate_form_key or target_source == "surface":
        return "similar exposed form"
    if rasm_dist == 0:
        return "dots only"
    return "similar spelling"


def _score_match(
    edit_dist: int,
    rasm_dist: int,
    len_gap: int,
    candidate_form_key: str | None,
    target_source: str,
    same_root: bool,
    target_pos: str | None,
    candidate_pos: str | None,
    target_bare: str,
    candidate_bare: str,
    transposed: bool = False,
    rime_match: bool = False,
) -> int:
    score = edit_dist * 8 + rasm_dist * 6 + len_gap * 2
    if edit_dist == 0:
        score -= 20
    if transposed:
        # Metathesis (same letters reordered) is a strong confusion signal that
        # plain edit distance underweights — treat it nearly like a dot-variant.
        score -= 12
    if rasm_dist == 0:
        score -= 8
    if same_root:
        score -= 7
    if rime_match:
        # Shared ending, different onset — pulls the rhyme cohort above
        # equidistant dot-variants so the user's near-miss isn't truncated.
        score -= 6
    if candidate_form_key or target_source == "surface":
        score -= 5
    if target_pos and target_pos == candidate_pos:
        score -= 4
    if (
        target_pos == "verb"
        and candidate_pos == "verb"
        and (len(target_bare) <= 4 or len(candidate_bare) <= 4)
    ):
        score -= 6
    return score


def find_similar_words(
    db: Session,
    lemma_id: int,
    lemma_bare: str,
    max_results: int = 5,
    candidates: list[tuple] | None = None,
    surface_bare: str | None = None,
    target_root: Root | None = None,
    target_pos: str | None = None,
) -> list[dict]:
    """Find visually similar words/forms from the user's exposed vocabulary."""
    targets = _target_forms(lemma_bare, surface_bare)

    if candidates is None:
        candidates = _query_vocabulary(db, lemma_id)

    results = []
    for lemma, ks in candidates:
        bare = lemma.lemma_ar_bare
        if not bare:
            continue

        same_root = _same_root(target_root, lemma)
        best: dict | None = None
        for target in targets:
            target_bare = target["bare"]
            target_rasm = to_rasm(target_bare)
            for candidate_form in _similarity_forms(lemma):
                candidate_bare = candidate_form["bare"]
                len_gap = abs(len(candidate_bare) - len(target_bare))
                ed = edit_distance(target_bare, candidate_bare)
                rasm_ed = edit_distance(target_rasm, to_rasm(candidate_bare))
                transposed = _is_adjacent_transposition(target_bare, candidate_bare)
                rime_match = _shares_rime(target_bare, candidate_bare)
                is_form_or_surface_match = bool(candidate_form["key"]) or target["source"] == "surface"
                if not _is_match_eligible(
                    ed,
                    rasm_ed,
                    len_gap,
                    is_form_or_surface_match,
                    same_root,
                    target_pos,
                    lemma.pos,
                    target_bare,
                    candidate_bare,
                    transposed,
                    rime_match,
                ):
                    continue
                score = _score_match(
                    ed,
                    rasm_ed,
                    len_gap,
                    candidate_form["key"],
                    target["source"],
                    same_root,
                    target_pos,
                    lemma.pos,
                    target_bare,
                    candidate_bare,
                    transposed,
                    rime_match,
                )
                match = {
                    "score": score,
                    "edit_distance": ed,
                    "rasm_distance": rasm_ed,
                    "target": target,
                    "candidate_form": candidate_form,
                    "transposed": transposed,
                    "rime_match": rime_match,
                }
                if best is None or (
                    match["score"],
                    match["rasm_distance"],
                    match["edit_distance"],
                ) < (
                    best["score"],
                    best["rasm_distance"],
                    best["edit_distance"],
                ):
                    best = match

        if not best:
            continue

        # Select a few key forms for pattern recognition
        key_forms: dict[str, str] = {}
        if lemma.forms_json and isinstance(lemma.forms_json, dict):
            for k in _DISPLAY_FORM_KEYS:
                v = lemma.forms_json.get(k)
                if v and isinstance(v, str):
                    key_forms[k] = v

        candidate_form = best["candidate_form"]
        target = best["target"]
        matched_form = candidate_form["display"] if candidate_form["key"] else None
        match_reason = _match_reason(
            best["edit_distance"],
            best["rasm_distance"],
            candidate_form["key"],
            target["source"],
            same_root,
            target_pos,
            lemma.pos,
            target["bare"],
            candidate_form["bare"],
            best.get("transposed", False),
            best.get("rime_match", False),
        )

        results.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": bare,
            "gloss_en": lemma.gloss_en,
            "pos": lemma.pos,
            "edit_distance": best["edit_distance"],
            "rasm_distance": best["rasm_distance"],
            "diff_positions": _diff_positions(lemma_bare, bare),
            "knowledge_state": ks,
            "key_forms": key_forms,
            "match_reason": match_reason,
            "matched_form": matched_form,
            "matched_form_key": candidate_form["key"],
            "matched_target": target["display"] if target["source"] == "surface" else None,
            "score": best["score"],
        })

    results.sort(key=lambda r: (r["score"], r["rasm_distance"], r["edit_distance"]))
    return results[:max_results]


# --- Phonetic similarity ---
# Letters that sound similar to Arabic learners, mapped to a common representative.
# Emphatic → plain, pharyngeal → non-pharyngeal, interdental → sibilant.
PHONETIC_MAP: dict[str, str] = {
    "ص": "س", "ض": "د", "ط": "ت", "ظ": "ذ",   # emphatic → plain
    "ح": "ه", "ع": "ا",                          # pharyngeal → non-pharyngeal
    "ث": "س", "ذ": "ز",                          # interdental → sibilant
    "غ": "خ",                                     # voiced → voiceless uvular
    "ن": "م",                                     # nasals
    "ة": "ه",                                     # ta marbuta → ha
    "ى": "ي",                                     # alif maqsura → ya
    "أ": "ا", "إ": "ا", "آ": "ا",               # hamza variants → alif
}

# Human-readable label for each phonetic pair (original → mapped)
PHONETIC_PAIR_LABELS: dict[str, str] = {
    "ص": "ص≈س", "ض": "ض≈د", "ط": "ط≈ت", "ظ": "ظ≈ذ",
    "ح": "ح≈ه", "ع": "ع≈أ",
    "ث": "ث≈س", "ذ": "ذ≈ز",
    "غ": "غ≈خ",
    "ن": "ن≈م",
}


def to_phonetic(text: str) -> str:
    """Convert Arabic text to phonetic skeleton (confusable sounds merged)."""
    return "".join(PHONETIC_MAP.get(ch, ch) for ch in text)


def find_phonetically_similar(
    db: Session,
    lemma_id: int,
    lemma_bare: str,
    visual_ids: set[int],
    max_results: int = 3,
    candidates: list[tuple] | None = None,
) -> list[dict]:
    """Find words that sound similar but look different (emphatic/pharyngeal confusion)."""
    target_phonetic = to_phonetic(lemma_bare)

    if candidates is None:
        candidates = _query_vocabulary(db, lemma_id)

    results = []
    for lemma, ks in candidates:
        if lemma.lemma_id in visual_ids:
            continue
        bare = lemma.lemma_ar_bare
        if not bare:
            continue
        # Length filter on phonetic forms: ±1
        phon_bare = to_phonetic(bare)
        if abs(len(phon_bare) - len(target_phonetic)) > 1:
            continue
        # Phonetic edit distance
        phon_ed = edit_distance(target_phonetic, phon_bare)
        if phon_ed > 2 or phon_ed == 0:
            continue

        # Find which phonetic pairs are responsible for the confusion
        confused_pairs = []
        for ch in bare:
            if ch in PHONETIC_PAIR_LABELS and ch != lemma_bare[0:1]:
                label = PHONETIC_PAIR_LABELS[ch]
                if label not in confused_pairs:
                    confused_pairs.append(label)
        # Also check target word's letters
        for ch in lemma_bare:
            if ch in PHONETIC_PAIR_LABELS:
                label = PHONETIC_PAIR_LABELS[ch]
                if label not in confused_pairs:
                    confused_pairs.append(label)

        results.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "gloss_en": lemma.gloss_en,
            "phonetic_distance": phon_ed,
            "confused_pairs": confused_pairs[:3],
            "knowledge_state": ks,
        })

    results.sort(key=lambda r: r["phonetic_distance"])
    return results[:max_results]


def analyze_confusion(
    db: Session,
    lemma_id: int,
    surface_form: str,
) -> dict:
    """Main entry point: analyze why the user was confused by a word.

    Returns confusion_type ("morphological" | "visual" | "both") with data.
    """
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        return {"confusion_type": None, "error": "Lemma not found"}

    surface_bare = strip_diacritics(surface_form)
    lemma_bare = lemma.lemma_ar_bare or strip_diacritics(lemma.lemma_ar)

    # 1. Morphological analysis
    decomposition = decompose_surface(surface_bare, lemma_bare, lemma.forms_json)

    # 2. Visual + phonetic similarity (share one DB query)
    candidates = _query_vocabulary(db, lemma_id)
    similar_words = find_similar_words(
        db,
        lemma_id,
        lemma_bare,
        candidates=candidates,
        surface_bare=surface_bare,
        target_root=lemma.root,
        target_pos=lemma.pos,
        max_results=8,
    )
    visual_ids = {r["lemma_id"] for r in similar_words}
    phonetic_similar = find_phonetically_similar(
        db, lemma_id, lemma_bare, visual_ids, candidates=candidates,
    )

    # 3. Prefix disambiguation hint
    prefix_hint = _build_prefix_hint(surface_bare, lemma_bare, lemma.root, decomposition)

    # Determine confusion type
    has_morph = decomposition is not None
    has_visual = len(similar_words) > 0

    if has_morph and has_visual:
        confusion_type = "both"
    elif has_morph:
        confusion_type = "morphological"
    elif has_visual:
        confusion_type = "visual"
    else:
        confusion_type = None

    return {
        "confusion_type": confusion_type,
        "surface_form": surface_form,
        "lemma_id": lemma.lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "decomposition": decomposition,
        "similar_words": similar_words,
        "phonetic_similar": phonetic_similar,
        "prefix_hint": prefix_hint,
    }
