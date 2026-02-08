from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class RootOut(BaseModel):
    root_id: int
    root: str
    core_meaning_en: Optional[str] = None
    model_config = {"from_attributes": True}


class LemmaOut(BaseModel):
    lemma_id: int
    lemma_ar: str
    lemma_ar_bare: str
    root_id: Optional[int] = None
    pos: Optional[str] = None
    gloss_en: Optional[str] = None
    frequency_rank: Optional[int] = None
    source: Optional[str] = None
    transliteration_ala_lc: Optional[str] = None
    audio_url: Optional[str] = None
    model_config = {"from_attributes": True}


class LemmaDetailOut(LemmaOut):
    root: Optional[RootOut] = None
    knowledge_state: Optional[str] = None
    model_config = {"from_attributes": True}


class KnowledgeOut(BaseModel):
    id: int
    lemma_id: int
    knowledge_state: str
    last_reviewed: Optional[datetime] = None
    times_seen: int
    times_correct: int
    source: str
    model_config = {"from_attributes": True}


class ReviewCardOut(BaseModel):
    lemma_id: int
    lemma_ar: str
    lemma_ar_bare: str
    gloss_en: Optional[str] = None
    audio_url: Optional[str] = None
    knowledge_state: str
    due: Optional[str] = None


class ReviewSubmitIn(BaseModel):
    lemma_id: int
    rating: int  # 1=Again, 2=Hard, 3=Good, 4=Easy
    response_ms: Optional[int] = None
    session_id: Optional[str] = None
    review_mode: str = "reading"  # reading/listening
    comprehension_signal: Optional[str] = None  # understood/partial/no_idea
    missed_word_lemma_ids: Optional[list[int]] = None


class ReviewSubmitOut(BaseModel):
    lemma_id: int
    new_state: str
    next_due: str


class AnalyzeWordIn(BaseModel):
    word: str


class AnalyzeWordOut(BaseModel):
    word: str
    lemma: Optional[str] = None
    root: Optional[str] = None
    pos: Optional[str] = None
    gloss_en: Optional[str] = None
    source: str = "mock"


class AnalyzeSentenceIn(BaseModel):
    sentence: str


class AnalyzeSentenceOut(BaseModel):
    sentence: str
    words: list[AnalyzeWordOut]
    source: str = "mock"


class StatsOut(BaseModel):
    total_words: int
    known: int
    learning: int
    new: int
    due_today: int
    reviews_today: int


class DailyStatsPoint(BaseModel):
    date: str
    reviews: int
    words_learned: int
    cumulative_known: int
    accuracy: Optional[float] = None


class LearningPaceOut(BaseModel):
    words_per_day_7d: float
    words_per_day_30d: float
    reviews_per_day_7d: float
    reviews_per_day_30d: float
    total_study_days: int
    current_streak: int
    longest_streak: int


class CEFREstimate(BaseModel):
    level: str
    sublevel: str
    known_words: int
    next_level: Optional[str] = None
    words_to_next: Optional[int] = None
    reading_coverage_pct: float


class AnalyticsOut(BaseModel):
    stats: StatsOut
    pace: LearningPaceOut
    cefr: CEFREstimate
    daily_history: list[DailyStatsPoint]


class ImportResultOut(BaseModel):
    imported: int
    skipped_names: int
    skipped_phrases: int
    roots_found: int


class SentenceWordMeta(BaseModel):
    lemma_id: int | None
    surface_form: str
    gloss_en: str | None = None
    stability: float | None = None
    is_due: bool = False
    is_function_word: bool = False


class SentenceReviewItem(BaseModel):
    sentence_id: int | None
    arabic_text: str
    arabic_diacritized: str | None = None
    english_translation: str
    transliteration: str | None = None
    primary_lemma_id: int
    primary_lemma_ar: str
    primary_gloss_en: str
    words: list[SentenceWordMeta]


class SentenceSessionOut(BaseModel):
    session_id: str
    items: list[SentenceReviewItem]
    total_due_words: int
    covered_due_words: int


class SentenceReviewSubmitIn(BaseModel):
    sentence_id: int | None = None
    primary_lemma_id: int
    comprehension_signal: str  # understood/partial/no_idea
    missed_lemma_ids: list[int] = []
    response_ms: int | None = None
    session_id: str | None = None
    review_mode: str = "reading"


class SentenceReviewSubmitOut(BaseModel):
    word_results: list[dict]
