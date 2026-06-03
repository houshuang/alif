"""Quranic Arabic Corpus lemma-frequency → Alif lemma mapping.

Computes a genuinely *lemmatized* Quran word-frequency signal and maps it onto
Alif's own lemma rows. The source is the Quranic Arabic Corpus v0.4 morphology
file (Kais Dukes, corpus.quran.com, GPL) — a manually-verified, per-token
morphological annotation where each STEM segment carries a dictionary LEM
feature, so inflected forms are already grouped to a lemma (unlike the
surface-count MSA sources, which conflate homographs onto whichever form the
analyzer emits).

The mapping is the real work, per the recurring "an external list is only as
good as our lemma mapping" lesson:

  1. The LEM feature is Buckwalter-encoded → convert with CAMeL's bw2ar.
  2. QAC encodes the long-ā maddah two ways that standard MSA normalization
     misses — an ASCII caret U+005E (bw2ar leaves it unmapped, e.g. سَمَا^ء)
     and a decomposed hamza+alef ءا (e.g. 'aAyap → آية, 'aAmana → آمن). Both
     must fold to bare ا. normalize_qac_lemma handles this on top of the shared
     normalize_arabic (which already converts dagger-alef U+0670 → ا BEFORE
     stripping diacritics — the documented Quran normalization gotcha).
  3. Homograph disambiguation uses the QAC POS: أَمَرَ (V "to command") and
     أَمْر (N "matter") are distinct LEMs with distinct counts, so each maps to
     the POS-matching Alif lemma instead of one count piling onto whichever
     homograph the lookup happens to return.

Multiple QAC lemmas can resolve to one Alif lemma (e.g. أخرج/خرج → خرج); their
counts are summed, then ranked. We never auto-create lemmas — unmapped QAC
lemmas are reported as honest gaps.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from camel_tools.utils.charmap import CharMapper

from app.models import Lemma
from app.services.sentence_validator import (
    _is_function_word,
    lookup_lemma,
    normalize_arabic,
    strip_diacritics,
)
from app.services.word_selector import _is_noise_lemma

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
QAC_DEFAULT_PATH = DATA_DIR / "frequency_sources" / "quranic-corpus-morphology-0.4.txt"

_BW2AR = CharMapper.builtin_mapper("bw2ar")
_LEM_RE = re.compile(r"LEM:([^|]+)")
_POS_RE = re.compile(r"POS:([^|]+)")
_ROOT_RE = re.compile(r"ROOT:([^|]+)")

# QAC POS tag → acceptable Alif `pos` values (for homograph disambiguation).
_QAC_POS_TO_ALIF = {
    "N": {"noun"},
    "PN": {"noun"},
    "ADJ": {"adj", "adjective"},
    "V": {"verb"},
    "ADV": {"adv", "adverb"},
}


def pos_match(qac_pos: str | None, alif_pos: str | None) -> bool:
    if not qac_pos or not alif_pos:
        return False
    return alif_pos in _QAC_POS_TO_ALIF.get(qac_pos, set())


def normalize_qac_lemma(ar: str) -> str:
    """Normalize a Buckwalter→Arabic QAC lemma to a bare form for Alif lookup."""
    ar = ar.replace("^", "")  # QAC maddah caret (U+005E), unmapped by bw2ar
    bare = normalize_arabic(ar)
    bare = bare.replace("ءا", "ا")  # decomposed madda ءا → ا
    return bare


def parse_qac(path: Path | str = QAC_DEFAULT_PATH) -> dict[str, dict]:
    """Return {lemma_bw: {count, pos, root}} from STEM segments of the QAC file."""
    lemmas: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("("):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 4:
                continue
            feats = parts[3]
            if "STEM" not in feats:
                continue
            m = _LEM_RE.search(feats)
            if not m:
                continue
            lem = m.group(1)
            rec = lemmas.get(lem)
            if rec is None:
                pos = _POS_RE.search(feats)
                root = _ROOT_RE.search(feats)
                rec = {"count": 0, "pos": pos.group(1) if pos else None,
                       "root": root.group(1) if root else None}
                lemmas[lem] = rec
            rec["count"] += 1
    return lemmas


def resolve_qac_lemma(
    lem_bw: str,
    qac_pos: str | None,
    lemma_lookup,
    lemmas_by_id: dict[int, Lemma],
) -> tuple[int | None, bool]:
    """Map one QAC Buckwalter lemma to an Alif lemma_id (None if function/unmapped).

    Shared by map_quran_frequencies (frequency track) and the Quran reading
    backfill (lemmatize_quran_from_qac). Returns (lemma_id, pos_corrected).
    """
    ar = _BW2AR(lem_bw)
    bare = normalize_qac_lemma(ar)
    if not bare or len(bare) < 2:
        return None, False
    if _is_function_word(bare) or _is_function_word(bare.replace("ى", "ي")):
        return None, False

    alts: list[int] = []
    lemma_id = lookup_lemma(
        bare, lemma_lookup, original_bare=strip_diacritics(ar), out_alternatives=alts,
    )
    # POS-aware homograph disambiguation: if the lookup's pick contradicts the
    # QAC POS but a same-POS alternative exists AND is the same bare or a
    # clitic/prefix relation (not a derivationally-distant homograph), switch.
    corrected = False
    if lemma_id is not None and alts:
        chosen = lemmas_by_id.get(lemma_id)
        if chosen is not None and not pos_match(qac_pos, chosen.pos):
            for alt_id in alts:
                alt = lemmas_by_id.get(alt_id)
                if (alt is None or not pos_match(qac_pos, alt.pos)
                        or _is_noise_lemma(alt)):
                    continue
                alt_bare = normalize_arabic(alt.lemma_ar_bare or "")
                distant = (alt_bare != bare
                           and not bare.endswith(alt_bare)
                           and not alt_bare.endswith(bare))
                if distant:
                    continue
                lemma_id = alt_id
                corrected = True
                break
    return lemma_id, corrected


def map_quran_frequencies(
    db,
    *,
    lemma_lookup=None,
    lemmas_by_id: dict[int, Lemma] | None = None,
    path: Path | str = QAC_DEFAULT_PATH,
    collect_report: bool = False,
):
    """Map QAC lemma frequencies onto Alif lemma_ids, ranked by total count.

    Returns ``(ranked, report)`` where ``ranked`` is a list of
    ``(lemma_id, rank, count)`` aggregated by Alif lemma (rank 1 = most
    frequent in the Quran). ``report`` is None unless ``collect_report`` is set,
    in which case it carries mapping diagnostics (match rate, unmapped residue,
    homograph corrections) for the analysis/feasibility pass.
    """
    from app.services.sentence_validator import build_comprehensive_lemma_lookup

    if lemma_lookup is None:
        lemma_lookup = build_comprehensive_lemma_lookup(db)
    if lemmas_by_id is None:
        lemmas_by_id = {l.lemma_id: l for l in db.query(Lemma).all()}

    qac = parse_qac(path)
    by_lemma_id: dict[int, int] = defaultdict(int)
    mapped: list[dict] = []
    unmapped: list[dict] = []
    pos_corrected: list[dict] = []

    for lem_bw, rec in qac.items():
        ar = _BW2AR(lem_bw)
        bare = normalize_qac_lemma(ar)
        lemma_id, corrected = resolve_qac_lemma(
            lem_bw, rec.get("pos"), lemma_lookup, lemmas_by_id,
        )

        if lemma_id is not None:
            by_lemma_id[lemma_id] += rec["count"]
            if collect_report:
                lem = lemmas_by_id.get(lemma_id)
                entry = {"lem_bw": lem_bw, "ar": ar, "bare": bare,
                         "count": rec["count"], "pos": rec.get("pos"),
                         "root": rec.get("root"), "lemma_id": lemma_id,
                         "alif_bare": lem.lemma_ar_bare if lem else None,
                         "alif_gloss": lem.gloss_en if lem else None,
                         "alif_pos": lem.pos if lem else None,
                         "pos_corrected": corrected}
                mapped.append(entry)
                if corrected:
                    pos_corrected.append(entry)
        elif collect_report and bare and len(bare) >= 2 and not (
            _is_function_word(bare) or _is_function_word(bare.replace("ى", "ي"))
        ):
            unmapped.append({"lem_bw": lem_bw, "ar": ar, "bare": bare,
                             "count": rec["count"], "pos": rec.get("pos"),
                             "root": rec.get("root")})

    ranked_ids = sorted(
        by_lemma_id.items(),
        key=lambda kv: (-kv[1], (lemmas_by_id[kv[0]].lemma_ar_bare or "")
                        if kv[0] in lemmas_by_id else ""),
    )
    ranked = [(lemma_id, rank, count)
              for rank, (lemma_id, count) in enumerate(ranked_ids, 1)]

    report = None
    if collect_report:
        mapped_tokens = sum(e["count"] for e in mapped)
        unmapped_tokens = sum(e["count"] for e in unmapped)
        report = {
            "qac_lemmas": len(qac),
            "content_lemmas": len(mapped) + len(unmapped),
            "mapped_qac_lemmas": len(mapped),
            "unmapped_qac_lemmas": len(unmapped),
            "distinct_alif_lemmas": len(by_lemma_id),
            "mapped_tokens": mapped_tokens,
            "unmapped_tokens": unmapped_tokens,
            "pos_corrected": len(pos_corrected),
            "mapped": mapped,
            "unmapped": unmapped,
        }
    return ranked, report
