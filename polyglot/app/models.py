from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON, Boolean,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Language(Base):
    """Per-language configuration. Seeded at startup.

    `accent_display` controls UI rendering choices: monotonic (Modern Greek),
    polytonic (Ancient Greek), macrons_on/off (Latin). NLP provider lookup
    happens via `code` against the in-process registry in services/languages.
    """
    __tablename__ = "languages"

    code = Column(String(8), primary_key=True)         # 'el', 'grc', 'la'
    name = Column(String(40), nullable=False)          # 'Modern Greek'
    script = Column(String(20), nullable=False)        # 'greek' / 'latin'
    direction = Column(String(3), nullable=False, default="ltr")
    accent_display = Column(String(20), nullable=False)  # monotonic/polytonic/macrons_on/macrons_off
    is_active = Column(Boolean, default=True)
    config_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Lemma(Base):
    """A canonical word entry. Per-language; cross-language cognates link via
    `cognate_lemma_id` (e.g. Modern φιλία ↔ Ancient φιλία).

    No language-specific columns: Arabic-style `root_id` / `wazn` / `tashkeel`
    live entirely in Alif's models. Per-language morphology metadata goes in
    `forms_json` (shape determined by the language's NLP provider).
    """
    __tablename__ = "lemmas"

    lemma_id = Column(Integer, primary_key=True, autoincrement=True)
    language_code = Column(String(8), ForeignKey("languages.code"), nullable=False, index=True)
    lemma_form = Column(Text, nullable=False)          # display form, with accents/diacritics
    lemma_bare = Column(Text, nullable=False)          # normalized for lookup (per provider rules)
    pos = Column(String(20))
    gloss_en = Column(Text)                            # English gloss (UI language for now)
    frequency_rank = Column(Integer)
    cefr_level = Column(String(2))
    source = Column(String(40))                        # reading_intake/manual/llm/frequency_list/import
    forms_json = Column(JSON, nullable=True)           # paradigm — per-language shape
    example_src = Column(Text, nullable=True)          # source-language example sentence
    example_en = Column(Text, nullable=True)
    canonical_lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)  # variant chains
    cognate_lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)    # cross-language (MG↔AG)
    word_category = Column(String(20), nullable=True)  # NULL=standard, proper_name, function_word
    gates_completed_at = Column(DateTime, nullable=True)
    notes_json = Column(JSON, nullable=True)
    # External (L1) cognates: [{"lang": "en", "form": "philosophy", "transparency": "high|medium|low", "note": "..."}]
    # NULL = never checked; [] = checked, none found.
    cognates_json = Column(JSON, nullable=True)
    cognates_detected_at = Column(DateTime, nullable=True)
    enrichment_json = Column(JSON, nullable=True)
    enrichment_status = Column(String(20), nullable=True)
    enriched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    language = relationship("Language")
    canonical_lemma = relationship("Lemma", remote_side="Lemma.lemma_id", foreign_keys=[canonical_lemma_id])
    cognate_lemma = relationship("Lemma", remote_side="Lemma.lemma_id", foreign_keys=[cognate_lemma_id])
    knowledge = relationship("UserLemmaKnowledge", back_populates="lemma", uselist=False)
    reviews = relationship("ReviewLog", back_populates="lemma")

    __table_args__ = (
        Index("ix_lemmas_lang_bare", "language_code", "lemma_bare"),
    )


class UserLemmaKnowledge(Base):
    """One row per known/encountered lemma. FSRS state lives in `fsrs_card_json`
    so we can drop in py-fsrs v6 unchanged. Acquisition Leitner fields mirror
    Alif's exactly so the eventual extraction is mechanical.
    """
    __tablename__ = "user_lemma_knowledge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), unique=True, nullable=False)
    knowledge_state = Column(String(20), default="new", index=True)
    fsrs_card_json = Column(JSON)
    last_reviewed = Column(DateTime)
    introduced_at = Column(DateTime, nullable=True)
    times_seen = Column(Integer, default=0)
    times_correct = Column(Integer, default=0)
    total_encounters = Column(Integer, default=0)
    distinct_contexts = Column(Integer, default=0)
    source = Column(String(30), default="reading_intake")  # reading_intake/manual/encountered/auto_intro/...
    knowledge_origin = Column(String(30), nullable=True, index=True)
    first_failed_at = Column(DateTime, nullable=True, index=True)
    last_failed_at = Column(DateTime, nullable=True)
    failure_count = Column(Integer, default=0, server_default="0")
    first_correct_after_failure_at = Column(DateTime, nullable=True, index=True)

    acquisition_box = Column(Integer, nullable=True)
    acquisition_next_due = Column(DateTime, nullable=True)
    acquisition_started_at = Column(DateTime, nullable=True)
    graduated_at = Column(DateTime, nullable=True, index=True)
    entered_acquiring_at = Column(DateTime, nullable=True)
    leech_suspended_at = Column(DateTime, nullable=True)
    leech_count = Column(Integer, default=0, server_default="0")
    experiment_intro_shown_at = Column(DateTime, nullable=True)

    # Scaffold confirmation. An assumed-known word (knowledge_state='known' with
    # no FSRS card — bulk-marked / cognate-known) earns verification evidence
    # when it survives collateral exposure in a shown sentence (green, not
    # missed). confirmed_at is stamped on the first such exposure; clean_exposures
    # counts them. This does NOT create an FSRS card — confirmed scaffold stays
    # out of the review rotation until a future red miss lapses it into
    # acquisition. See polyglot CLAUDE.md Hard Invariant 6.
    clean_exposures = Column(Integer, default=0, server_default="0")
    confirmed_at = Column(DateTime, nullable=True, index=True)

    lemma = relationship("Lemma", back_populates="knowledge")


class FrequencyEntry(Base):
    """Multi-language frequency table. Source-tagged so we can mix SUBTLEX-GR,
    Perseus, Dickinson Core, etc. without collision.
    """
    __tablename__ = "frequency_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    language_code = Column(String(8), ForeignKey("languages.code"), nullable=False, index=True)
    source = Column(String(30), nullable=False)        # subtlex_gr/perseus/dickinson_core/...
    rank = Column(Integer, nullable=False)
    lemma_key = Column(Text, nullable=False)           # normalized form for matching
    display_form = Column(Text, nullable=False)
    gloss_en = Column(Text, nullable=True)
    pos = Column(String(20), nullable=True)
    count = Column(Integer, nullable=True)
    score = Column(Float, nullable=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("language_code", "source", "rank", name="uq_freq_lang_source_rank"),
        Index("ix_freq_lang_key", "language_code", "lemma_key"),
    )


class Sentence(Base):
    """A sentence-level review item. Generated by LLM or pulled from imported
    texts. Mappings to lemmas live in SentenceWord.
    """
    __tablename__ = "sentences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    language_code = Column(String(8), ForeignKey("languages.code"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    translation_en = Column(Text)
    transliteration = Column(Text, nullable=True)
    source = Column(String(20))                        # llm/manual/import/story/textbook
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=True, index=True)
    target_lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True, index=True)

    # For sentences harvested from textbook pages: provenance + idempotency key.
    # NULL for LLM-generated or pasted sentences.
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=True, index=True)
    sentence_index_in_page = Column(Integer, nullable=True)

    difficulty_score = Column(Float, nullable=True)
    audio_url = Column(Text, nullable=True)
    times_shown = Column(Integer, default=0)
    last_reading_shown_at = Column(DateTime, nullable=True)
    last_reading_comprehension = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True, server_default="1")
    mappings_verified_at = Column(DateTime, nullable=True)
    quality_reviewed_at = Column(DateTime, nullable=True)
    quality_natural = Column(Boolean, nullable=True)
    quality_translation_correct = Column(Boolean, nullable=True)
    quality_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    story = relationship("Story", foreign_keys=[story_id])
    page = relationship("Page", foreign_keys=[page_id])
    words = relationship("SentenceWord", back_populates="sentence", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("page_id", "sentence_index_in_page", name="uq_sentences_page_sidx"),
    )


class SentenceWord(Base):
    __tablename__ = "sentence_words"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    surface_form = Column(Text, nullable=False)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)
    is_target_word = Column(Boolean, default=False)
    grammar_role_json = Column(JSON, nullable=True)

    sentence = relationship("Sentence", back_populates="words")


class SentenceReviewLog(Base):
    """One row per sentence-level submission. Per-word ReviewLog rows still
    exist alongside this — they carry the FSRS state changes; this row carries
    the sentence-shaped signal (comprehension, mode, response time).
    """
    __tablename__ = "sentence_review_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False, index=True)
    session_id = Column(String(50), nullable=True, index=True)
    reviewed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    comprehension = Column(String(20), nullable=False)  # understood/partial/no_idea
    response_ms = Column(Integer, nullable=True)
    review_mode = Column(String(20), default="reading")
    client_review_id = Column(String(50), nullable=True, unique=True, index=True)
    # Per-word detail persisted in the DB. Previously these lived only in the
    # interaction JSONL, which ages out — leaving old partial reviews
    # un-reconstructable (which words were red). Storing them here lets the DB
    # alone rebuild any review for audit/backfill without the log files.
    missed_lemma_ids = Column(JSON, nullable=True)
    confused_lemma_ids = Column(JSON, nullable=True)


class ReviewLog(Base):
    __tablename__ = "review_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=False, index=True)
    rating = Column(Integer, nullable=False)
    reviewed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    response_ms = Column(Integer)
    context = Column(Text)
    session_id = Column(String(50), index=True)
    fsrs_log_json = Column(JSON)
    review_mode = Column(String(20), default="reading")
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)
    is_acquisition = Column(Boolean, default=False, server_default="0")
    # Offline idempotency: client supplies a UUID; if the request arrives twice
    # we return the existing review without applying it again. Mirrors Alif's
    # `client_review_id` field used by the React Native offline queue.
    client_review_id = Column(String(50), nullable=True, unique=True, index=True)
    # Free-text learner self-assessment: understood / partial / no_idea. Optional —
    # the FSRS rating is the load-bearing signal, but this captures the user's
    # subjective state for later analysis.
    comprehension_signal = Column(String(20), nullable=True)
    # Sentence-review pipeline metadata. credit_type distinguishes the lemma the
    # sentence was selected for ("primary") from the surrounding scaffold
    # ("collateral"). was_confused captures the partial→rating-2 audit case
    # where the learner knew the word but didn't recognize it in context.
    credit_type = Column(String(20), nullable=True)
    was_confused = Column(Boolean, default=False, server_default="0")

    lemma = relationship("Lemma", back_populates="reviews")


class Story(Base):
    """A book/text the user wants to read. PDFs are split into Page rows at
    import time; each Page is tokenized lazily on first view, not upfront.

    `body_src` may be NULL for PDF imports (the text lives in pages); paste
    imports store the whole body here and create a single Page.
    """
    __tablename__ = "stories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    language_code = Column(String(8), ForeignKey("languages.code"), nullable=False, index=True)
    title = Column(Text, nullable=True)
    author = Column(Text, nullable=True)
    body_src = Column(Text, nullable=True)             # nullable: PDFs put text in pages
    source = Column(String(30), nullable=False)        # paste/pdf
    source_path = Column(Text, nullable=True)          # original file path for PDFs
    status = Column(String(20), default="active", index=True)
    page_count = Column(Integer, nullable=True)
    total_words = Column(Integer, default=0)           # filled in once pages are processed
    known_count = Column(Integer, default=0)
    unknown_count = Column(Integer, default=0)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    pages = relationship("Page", back_populates="story", order_by="Page.page_number", cascade="all, delete-orphan")


class Page(Base):
    """One page of a Story. Tokenized lazily — `processed_at IS NULL` means
    we have raw text but no PageWord rows yet. First view of the page
    triggers tokenization + lemmatization.
    """
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    body_src = Column(Text, nullable=False)
    body_clean = Column(Text, nullable=True)               # Haiku-cleaned prose; tokenizer reads from here when set
    processed_at = Column(DateTime, nullable=True)         # tokenized + simplemma lemmatized
    mappings_verified_at = Column(DateTime, nullable=True) # quality gate (LLM-in-context) pass
    viewed_at = Column(DateTime, nullable=True)            # last time user opened the page
    quality_gate_failures = Column(Integer, default=0)     # tokens the gate left as 'unclear'
    total_words = Column(Integer, default=0)
    known_count = Column(Integer, default=0)
    unknown_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    story = relationship("Story", back_populates="pages")
    words = relationship("PageWord", back_populates="page", order_by="PageWord.position", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("story_id", "page_number", name="uq_pages_story_page"),
    )


class PageWord(Base):
    __tablename__ = "page_words"

    id = Column(Integer, primary_key=True, autoincrement=True)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    surface_form = Column(Text, nullable=False)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)
    sentence_index = Column(Integer, default=0)
    is_function_word = Column(Boolean, default=False)
    name_type = Column(String(20), nullable=True)
    # Quality-gate audit fields. `verified_at` set when the LLM checked this
    # mapping in context. `original_lemma_id` keeps the pre-correction guess
    # for audit; NULL when the original was accepted.
    verified_at = Column(DateTime, nullable=True)
    original_lemma_id = Column(Integer, nullable=True)
    quality_note = Column(Text, nullable=True)             # LLM's reason on correction/unclear

    page = relationship("Page", back_populates="words")
    lemma = relationship("Lemma")


class MaterialJob(Base):
    __tablename__ = "material_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(40), nullable=False, index=True)
    language_code = Column(String(8), ForeignKey("languages.code"), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    priority = Column(Integer, nullable=False, default=100, index=True)
    dedupe_key = Column(String(200), nullable=True, index=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    not_before = Column(DateTime, nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at = Column(DateTime, nullable=True)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)
    language_code = Column(String(8), nullable=True, index=True)
    summary = Column(Text, nullable=False)
    detail_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserProfile(Base):
    """Singleton — single-user app. Drives cognate detection and personalised
    sentence generation hints.

    `known_languages` is an ordered list of BCP-47-ish codes the user can read
    fluently enough to spot transparent cognates. Default reflects the
    European reader baseline; adjust via PATCH /api/profile.
    """
    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True, autoincrement=True)
    known_languages = Column(JSON, nullable=False, default=lambda: ["en", "no", "de", "fr", "it", "es"])
    native_language = Column(String(8), nullable=False, default="no")
    cognate_auto_mark_threshold = Column(String(10), default="high")  # high/medium/low/never
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(50), nullable=False, index=True)
    screen = Column(String(50), nullable=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    context_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentFlag(Base):
    __tablename__ = "content_flags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content_type = Column(String(30), nullable=False, index=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)
    status = Column(String(20), default="pending", index=True)
    original_value = Column(Text, nullable=True)
    corrected_value = Column(Text, nullable=True)
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)
