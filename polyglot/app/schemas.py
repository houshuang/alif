"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
