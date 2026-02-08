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
    source = Column(String(50))
    transliteration_ala_lc = Column(Text)
    audio_url = Column(Text)

    grammar_features_json = Column(JSON, nullable=True)

    root = relationship("Root", back_populates="lemmas")
    knowledge = relationship("UserLemmaKnowledge", back_populates="lemma", uselist=False)
    reviews = relationship("ReviewLog", back_populates="lemma")


class UserLemmaKnowledge(Base):
    __tablename__ = "user_lemma_knowledge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), unique=True, nullable=False)
    knowledge_state = Column(String(20), default="new")  # new/learning/known/lapsed
    fsrs_card_json = Column(JSON)
    last_reviewed = Column(DateTime)
    introduced_at = Column(DateTime, nullable=True)
    times_seen = Column(Integer, default=0)
    times_correct = Column(Integer, default=0)
    total_encounters = Column(Integer, default=0)
    distinct_contexts = Column(Integer, default=0)
    source = Column(String(20), default="study")  # study/import/encountered

    lemma = relationship("Lemma", back_populates="knowledge")


class ReviewLog(Base):
    __tablename__ = "review_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=False)
    rating = Column(Integer, nullable=False)  # 1-4
    reviewed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    response_ms = Column(Integer)
    context = Column(Text)
    session_id = Column(String(50))
    fsrs_log_json = Column(JSON)
    review_mode = Column(String(20), default="reading")  # reading/listening
    comprehension_signal = Column(String(20), nullable=True)  # understood/partial/no_idea
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)
    credit_type = Column(String(20), nullable=True)  # primary/collateral
    client_review_id = Column(String(50), nullable=True, unique=True)

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
    target_lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)

    times_shown = Column(Integer, default=0)
    max_word_count = Column(Integer, nullable=True)
    last_shown_at = Column(DateTime, nullable=True)

    words = relationship("SentenceWord", back_populates="sentence")


class SentenceWord(Base):
    __tablename__ = "sentence_words"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False)
    position = Column(Integer, nullable=False)
    surface_form = Column(Text, nullable=False)
    lemma_id = Column(Integer, ForeignKey("lemmas.lemma_id"), nullable=True)
    is_target_word = Column(Integer, default=0)  # 0/1
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

    sentence = relationship("Sentence")


class GrammarFeature(Base):
    __tablename__ = "grammar_features"

    feature_id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(30), nullable=False)
    feature_key = Column(String(50), unique=True, nullable=False)
    label_en = Column(Text, nullable=False)
    label_ar = Column(Text)
    sort_order = Column(Integer, default=0)

    sentence_features = relationship("SentenceGrammarFeature", back_populates="feature")
    exposures = relationship("UserGrammarExposure", back_populates="feature")


class SentenceGrammarFeature(Base):
    __tablename__ = "sentence_grammar_features"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False)
    feature_id = Column(Integer, ForeignKey("grammar_features.feature_id"), nullable=False)
    is_primary = Column(Boolean, default=False)
    source = Column(String(20))  # llm/rule/manual

    sentence = relationship("Sentence")
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

    feature = relationship("GrammarFeature", back_populates="exposures")
