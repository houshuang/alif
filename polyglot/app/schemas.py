"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ─── Languages ─────────────────────────────────────────────────────────────

class LanguageOut(BaseModel):
    code: str
    name: str
    script: str
    direction: str
    accent_display: str
    is_active: bool
    provider_available: bool

    class Config:
        from_attributes = True


# ─── Stories ───────────────────────────────────────────────────────────────

class PasteImportRequest(BaseModel):
    language_code: str = Field(..., description="e.g. 'el'")
    title: str | None = None
    author: str | None = None
    body: str


class PdfImportRequest(BaseModel):
    language_code: str
    pdf_path: str
    title: str | None = None
    author: str | None = None


class StorySummary(BaseModel):
    id: int
    language_code: str
    title: str | None
    author: str | None
    source: str
    page_count: int | None
    processed_pages: int       # how many pages have been tokenized
    total_words: int
    known_count: int
    unknown_count: int
    status: str
    created_at: datetime
    # First page that looks like actual reading content rather than
    # bureaucratic front-matter (copyright pages, blank dividers, TOC, all-
    # caps title pages). Detected heuristically — see
    # reading_intake.first_content_page_number. When the user opens a story
    # they should land on this page, not page 1.
    first_content_page_number: int = 1


# ─── Page view ─────────────────────────────────────────────────────────────

class TokenView(BaseModel):
    position: int
    surface: str
    is_punctuation: bool
    sentence_index: int
    lemma_id: int | None
    lemma_form: str | None
    lemma_bare: str | None
    pos: str | None
    gloss_en: str | None
    is_function_word: bool
    # True when the quality gate classified this token's sentence as a heading
    # (all-caps + short → chapter/section title or running header). The frontend
    # SHOULD NOT render these inline with body prose — they're meta-text.
    is_heading: bool = False
    is_known: bool
    is_acquiring: bool
    is_encountered: bool
    is_unknown: bool
    is_ignored: bool
    is_new: bool
    is_oov: bool


class PageView(BaseModel):
    story_id: int
    page_number: int
    total_pages: int
    total_words: int
    tokens: list[TokenView]


# ─── Marking ───────────────────────────────────────────────────────────────

class MarkWordRequest(BaseModel):
    lemma_id: int
    state: str = Field(..., description="'known' | 'unknown' | 'encountered' | 'ignore' | 'clear'")


# ─── User profile / cognates ───────────────────────────────────────────────

class UserProfileOut(BaseModel):
    known_languages: list[str]
    native_language: str
    cognate_auto_mark_threshold: str


class UserProfileUpdate(BaseModel):
    known_languages: list[str] | None = None
    native_language: str | None = None
    cognate_auto_mark_threshold: str | None = None


class CognateInfo(BaseModel):
    lang: str
    form: str
    transparency: str
    note: str | None = None


class LemmaCognatesOut(BaseModel):
    lemma_id: int
    lemma_form: str
    language_code: str
    cognates: list[CognateInfo]
    detected_at: datetime | None
    cognate_lemma_id: int | None       # Modern↔Ancient Greek link (if any)


# ─── Lemma enrichment (philology / etymology / diachrony / quotes) ──────────
# Single source of truth for the LemmaEnrichment shape. Both the enrichment
# service (lemma_philology.py) and the frontend (frontend/lib/types.ts) target
# this shape; the corresponding TS interfaces mirror it 1:1 and a Jest test
# loads the same JSON fixture this module emits.
#
# Eras are normalized to a fixed enum so the frontend can color-code stages
# without per-lemma surprises.

# Diachrony eras are language-specific: Greek runs Mycenaean→Modern, Latin runs
# Archaic→Neo-Latin. The enum is enforced at the JSON-schema layer in
# lemma_philology so the enrichment LLM emits a stage label the frontend can
# color-code (the PIE/proto root lives in etymology, not as a diachrony stage).
LEMMA_ERAS_BY_LANG: dict[str, tuple[str, ...]] = {
    "el": ("Mycenaean", "Homeric", "Classical", "Koine", "Byzantine", "Modern"),
    "grc": ("Mycenaean", "Homeric", "Classical", "Koine", "Byzantine"),
    "la": ("Archaic", "Classical", "Late", "Medieval", "Renaissance", "Neo-Latin"),
}
# Back-compat default (Modern Greek) for any caller that doesn't pass a language.
LEMMA_ERA = LEMMA_ERAS_BY_LANG["el"]
LemmaEra = str  # constrained at JSON-schema layer in lemma_philology._gen_schema


def era_enum_for(language_code: str) -> tuple[str, ...]:
    """Era labels valid for a language's diachrony timeline."""
    return LEMMA_ERAS_BY_LANG.get(language_code, LEMMA_ERAS_BY_LANG["el"])


class LemmaEtymology(BaseModel):
    pie_root: str | None = None        # e.g. "*ḱerd- (heart)"
    ancient_form: str | None = None    # polytonic, e.g. "καρδία"
    origin_note: str                   # 1-2 sentence prose origin
    morphology: str | None = None      # e.g. "ἀ- (neg) + λόγος (reason)"


class LemmaDiachronyStage(BaseModel):
    era: str                           # one of LEMMA_ERA
    form: str                          # form at this era (often shifted)
    meaning: str                       # meaning at this era
    note: str | None = None            # one-liner context


class LemmaCognate(BaseModel):
    language: str                      # "English" / "Latin" / "German" / etc.
    form: str
    relation: str                      # see lemma_philology._gen_schema enum
    gloss_en: str | None = None        # for non-English cognates
    note: str | None = None            # false-friend warning, semantic drift


class LemmaQuote(BaseModel):
    text: str                          # ≤25 words
    source: str                        # "Plato, Republic 439d"
    era: str                           # one of LEMMA_ERA
    translation_en: str


class LemmaRegister(BaseModel):
    formality: str | None = None       # "formal" | "neutral" | "colloquial" | "literary"
    collocations: list[str] = Field(default_factory=list)
    false_friends_en: list[str] = Field(default_factory=list)
    usage_note: str | None = None


class LemmaEnrichment(BaseModel):
    """Philological enrichment payload for a single lemma. Carried in
    `Lemma.enrichment_json` and surfaced to the learner in the lookup card +
    lemma detail screen (Modern Editorial design, 2026-05-21)."""
    model_config = ConfigDict(protected_namespaces=())

    version: int = 1
    etymology: LemmaEtymology | None = None
    diachrony: list[LemmaDiachronyStage] = Field(default_factory=list)
    cognates: list[LemmaCognate] = Field(default_factory=list)
    quotes: list[LemmaQuote] = Field(default_factory=list)
    register: LemmaRegister | None = None


class LemmaEnrichmentRequest(BaseModel):
    lemma_ids: list[int]
    language_code: str = "el"


class LemmaEnrichmentBatchResult(BaseModel):
    enriched: int
    failed_lemma_ids: list[int]
    skipped_lemma_ids: list[int]       # glossless / variant / function word
