from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON, Boolean
)
from sqlalchemy.orm import relationship

from app.database import Base


class Root(Base):
    __tablename__ = "roots"

    root_id = Column(Integer, primary_key=True, autoincrement=True)
    root = Column(Text, unique=True, nullable=False)  # e.g. "ك.ت.ب"
    core_meaning_en = Column(Text)
    productivity_score = Column(Integer, default=0)

    lemmas = relationship("Lemma", back_populates="root")


class Lemma(Base):
    __tablename__ = "lemmas"

    lemma_id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_ar = Column(Text, nullable=False)        # diacritized
    lemma_ar_bare = Column(Text, nullable=False)    # stripped
    root_id = Column(Integer, ForeignKey("roots.root_id"), nullable=True)
    pos = Column(String(20))
    gloss_en = Column(Text)
    frequency_rank = Column(Integer)
    cefr_level = Column(String(2))
    source = Column(String(50))
    transliteration_ala_lc = Column(Text)
    audio_url = Column(Text)

    grammar_features_json = Column(JSON, nullable=True)
    forms_json = Column(JSON, nullable=True)
    example_ar = Column(Text, nullable=True)
    example_en = Column(Text, nullable=True)
    canonical_lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)
    source_story_id = Column(Integer, ForeignKey("stories.id"), nullable=True)
    thematic_domain = Column(String(30), nullable=True)
    etymology_json = Column(JSON, nullable=True)

    root = relationship("Root", back_populates="lemmas")
    canonical_lemma = relationship("Lemma", remote_side="Lemma.lemma_id", foreign_keys=[canonical_lemma_id])
    source_story = relationship("Story", foreign_keys=[source_story_id])
    knowledge = relationship("UserLemmaKnowledge", back_populates="lemma", uselist=False)
    reviews = relationship("ReviewLog", back_populates="lemma")
    story_words = relationship("StoryWord", back_populates="lemma")


class UserLemmaKnowledge(Base):
    __tablename__ = "user_lemma_knowledge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), unique=True, nullable=False)
    knowledge_state = Column(String(20), default="new", index=True)  # new/encountered/acquiring/learning/known/lapsed/suspended
    fsrs_card_json = Column(JSON)
    last_reviewed = Column(DateTime)
    introduced_at = Column(DateTime, nullable=True)
    times_seen = Column(Integer, default=0)
    times_correct = Column(Integer, default=0)
    total_encounters = Column(Integer, default=0)
    distinct_contexts = Column(Integer, default=0)
    source = Column(String(20), default="study")  # study/duolingo/encountered/textbook_scan/story_import/auto_intro/collocate
    variant_stats_json = Column(JSON, nullable=True)

    # Acquisition (Leitner 3-box) fields
    acquisition_box = Column(Integer, nullable=True)  # 1/2/3, NULL = not acquiring
    acquisition_next_due = Column(DateTime, nullable=True)
    acquisition_started_at = Column(DateTime, nullable=True)
    graduated_at = Column(DateTime, nullable=True)
    leech_suspended_at = Column(DateTime, nullable=True)

    lemma = relationship("Lemma", back_populates="knowledge")


class ReviewLog(Base):
    __tablename__ = "review_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=False)
    rating = Column(Integer, nullable=False)  # 1-4
    reviewed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    response_ms = Column(Integer)
    context = Column(Text)
    session_id = Column(String(50))
    fsrs_log_json = Column(JSON)
    review_mode = Column(String(20), default="reading")  # reading/listening
    comprehension_signal = Column(String(20), nullable=True)  # understood/partial/no_idea
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)
    credit_type = Column(String(20), nullable=True)  # primary/collateral
    client_review_id = Column(String(50), nullable=True, unique=True)
    is_acquisition = Column(Boolean, default=False, server_default="0")

    lemma = relationship("Lemma", back_populates="reviews")


class Sentence(Base):
    __tablename__ = "sentences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    arabic_text = Column(Text, nullable=False)
    arabic_diacritized = Column(Text)
    english_translation = Column(Text)
    transliteration = Column(Text)
    source = Column(String(20))  # llm/tatoeba/manual
    difficulty_score = Column(Float)
    audio_url = Column(Text)
    target_lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True, index=True)

    times_shown = Column(Integer, default=0)
    max_word_count = Column(Integer, nullable=True)
    last_reading_shown_at = Column(DateTime, nullable=True)
    last_reading_comprehension = Column(String(20), nullable=True)
    last_listening_shown_at = Column(DateTime, nullable=True)
    last_listening_comprehension = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True, server_default="1")
    created_at = Column(DateTime, nullable=True)

    words = relationship("SentenceWord", back_populates="sentence")
    review_logs = relationship("SentenceReviewLog", back_populates="sentence")
    grammar_features = relationship("SentenceGrammarFeature", back_populates="sentence")


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
    __tablename__ = "sentence_review_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False)
    session_id = Column(String(50), nullable=True)
    reviewed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    comprehension = Column(String(20), nullable=False)  # understood/partial/no_idea
    response_ms = Column(Integer, nullable=True)
    review_mode = Column(String(20), default="reading")
    client_review_id = Column(String(50), nullable=True, unique=True)

    sentence = relationship("Sentence", back_populates="review_logs")


class GrammarFeature(Base):
    __tablename__ = "grammar_features"

    feature_id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(30), nullable=False)
    feature_key = Column(String(50), unique=True, nullable=False)
    label_en = Column(Text, nullable=False)
    label_ar = Column(Text)
    sort_order = Column(Integer, default=0)
    form_change_type = Column(String(20), nullable=True)  # form_changing / structural

    sentence_features = relationship("SentenceGrammarFeature", back_populates="feature")
    exposures = relationship("UserGrammarExposure", back_populates="feature")


class SentenceGrammarFeature(Base):
    __tablename__ = "sentence_grammar_features"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False)
    feature_id = Column(Integer, ForeignKey("grammar_features.feature_id"), nullable=False)
    is_primary = Column(Boolean, default=False)
    source = Column(String(20))  # llm/rule/manual

    sentence = relationship("Sentence", back_populates="grammar_features")
    feature = relationship("GrammarFeature", back_populates="sentence_features")


class UserGrammarExposure(Base):
    __tablename__ = "user_grammar_exposure"

    id = Column(Integer, primary_key=True, autoincrement=True)
    feature_id = Column(Integer, ForeignKey("grammar_features.feature_id"), unique=True, nullable=False)
    times_seen = Column(Integer, default=0)
    times_correct = Column(Integer, default=0)
    first_seen_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    comfort_score = Column(Float, default=0.0)
    introduced_at = Column(DateTime, nullable=True)
    times_confused = Column(Integer, default=0)

    feature = relationship("GrammarFeature", back_populates="exposures")


class Story(Base):
    __tablename__ = "stories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title_ar = Column(Text, nullable=True)
    title_en = Column(Text, nullable=True)
    body_ar = Column(Text, nullable=False)
    body_en = Column(Text, nullable=True)
    transliteration = Column(Text, nullable=True)
    source = Column(String(20), nullable=False)  # generated/imported
    status = Column(String(20), default="active", index=True)  # active/completed/too_difficult/skipped/suspended
    total_words = Column(Integer, default=0)
    known_count = Column(Integer, default=0)
    unknown_count = Column(Integer, default=0)
    readiness_pct = Column(Float, default=0.0)
    difficulty_level = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    words = relationship("StoryWord", back_populates="story", order_by="StoryWord.position")


class StoryWord(Base):
    __tablename__ = "story_words"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    surface_form = Column(Text, nullable=False)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)
    sentence_index = Column(Integer, default=0)
    gloss_en = Column(Text, nullable=True)
    is_known_at_creation = Column(Boolean, default=False)
    is_function_word = Column(Boolean, default=False)
    name_type = Column(String(20), nullable=True)  # "personal" or "place" for proper nouns

    story = relationship("Story", back_populates="words")
    lemma = relationship("Lemma", back_populates="story_words")


class PageUpload(Base):
    __tablename__ = "page_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(50), nullable=False, index=True)
    filename = Column(Text, nullable=True)
    status = Column(String(20), default="pending", index=True)  # pending/processing/completed/failed
    extracted_words_json = Column(JSON, nullable=True)
    new_words = Column(Integer, default=0)
    existing_words = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(50), nullable=False, index=True)
    screen = Column(String(50), nullable=True)
    role = Column(String(20), nullable=False)  # user/assistant
    content = Column(Text, nullable=False)
    context_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentFlag(Base):
    __tablename__ = "content_flags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content_type = Column(String(30), nullable=False, index=True)  # word_gloss, sentence_arabic, sentence_english, sentence_transliteration
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)
    status = Column(String(20), default="pending", index=True)  # pending/reviewing/fixed/dismissed
    original_value = Column(Text, nullable=True)
    corrected_value = Column(Text, nullable=True)
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)

    lemma = relationship("Lemma")
    sentence = relationship("Sentence")


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)  # flag_resolved, sentences_generated, backfill_completed, etc.
    summary = Column(Text, nullable=False)
    detail_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class VariantDecision(Base):
    __tablename__ = "variant_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    word_bare = Column(Text, nullable=False, index=True)
    base_bare = Column(Text, nullable=False, index=True)
    is_variant = Column(Boolean, nullable=False)
    reason = Column(Text, nullable=True)
    decided_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LearnerSettings(Base):
    __tablename__ = "learner_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    active_topic = Column(String(30), nullable=True)
    topic_started_at = Column(DateTime, nullable=True)
    words_introduced_in_topic = Column(Integer, default=0)
    topic_history_json = Column(JSON, nullable=True)
