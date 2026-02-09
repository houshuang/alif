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
    client_review_id: str | None = None


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
    total_reviews: int = 0
    lapsed: int = 0


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
    accuracy_7d: Optional[float] = None
    study_days_7d: int = 0


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
    knowledge_state: str = "new"
    root: str | None = None
    root_meaning: str | None = None
    root_id: int | None = None


class SentenceReviewItem(BaseModel):
    sentence_id: int | None
    arabic_text: str
    arabic_diacritized: str | None = None
    english_translation: str
    transliteration: str | None = None
    audio_url: str | None = None
    primary_lemma_id: int
    primary_lemma_ar: str
    primary_gloss_en: str
    words: list[SentenceWordMeta]


class IntroCandidateOut(BaseModel):
    lemma_id: int
    lemma_ar: str
    gloss_en: str | None = None
    pos: str | None = None
    transliteration: str | None = None
    root: str | None = None
    root_meaning: str | None = None
    root_id: int | None = None
    insert_at: int = 0


class SentenceSessionOut(BaseModel):
    session_id: str
    items: list[SentenceReviewItem]
    total_due_words: int
    covered_due_words: int
    intro_candidates: list[IntroCandidateOut] = []


class SentenceReviewSubmitIn(BaseModel):
    sentence_id: int | None = None
    primary_lemma_id: int
    comprehension_signal: str  # understood/partial/no_idea
    missed_lemma_ids: list[int] = []
    confused_lemma_ids: list[int] = []
    response_ms: int | None = None
    session_id: str | None = None
    review_mode: str = "reading"
    client_review_id: str | None = None
    audio_play_count: int | None = None
    lookup_count: int | None = None


class SentenceReviewSubmitOut(BaseModel):
    word_results: list[dict]


class BulkSyncItem(BaseModel):
    type: str  # "sentence" or "legacy"
    payload: dict
    client_review_id: str


class BulkSyncIn(BaseModel):
    reviews: list[BulkSyncItem]


class BulkSyncItemResult(BaseModel):
    client_review_id: str
    status: str  # "ok" | "duplicate" | "error"
    error: str | None = None


class BulkSyncOut(BaseModel):
    results: list[BulkSyncItemResult]


# --- Story schemas ---

class StoryWordMetaOut(BaseModel):
    position: int
    surface_form: str
    lemma_id: int | None = None
    gloss_en: str | None = None
    is_known: bool = False
    is_function_word: bool = False
    sentence_index: int = 0


class StoryOut(BaseModel):
    id: int
    title_ar: str | None = None
    title_en: str | None = None
    source: str
    status: str
    readiness_pct: float
    unknown_count: int
    total_words: int
    difficulty_level: str | None = None
    created_at: str
    model_config = {"from_attributes": True}


class StoryDetailOut(BaseModel):
    id: int
    title_ar: str | None = None
    title_en: str | None = None
    body_ar: str
    body_en: str | None = None
    transliteration: str | None = None
    source: str
    status: str
    readiness_pct: float
    unknown_count: int
    total_words: int
    known_count: int
    difficulty_level: str | None = None
    completed_at: str | None = None
    created_at: str
    words: list[StoryWordMetaOut]


class StoryGenerateIn(BaseModel):
    difficulty: str = "beginner"
    max_sentences: int = 6
    length: str = "medium"  # short/medium/long
    topic: str | None = None


class StoryImportIn(BaseModel):
    arabic_text: str
    title: str | None = None


class StoryCompleteIn(BaseModel):
    looked_up_lemma_ids: list[int] = []
    reading_time_ms: int | None = None


class StoryLookupIn(BaseModel):
    lemma_id: int
    position: int


class StoryLookupOut(BaseModel):
    lemma_id: int
    gloss_en: str | None = None
    transliteration: str | None = None
    root: str | None = None
    pos: str | None = None


class StoryReadinessOut(BaseModel):
    readiness_pct: float
    unknown_count: int
    unknown_words: list[dict]


# --- Chat schemas ---

class AskQuestionIn(BaseModel):
    question: str
    context: str = ""
    screen: str = ""
    conversation_id: str | None = None


class AskQuestionOut(BaseModel):
    answer: str
    conversation_id: str


class ChatMessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime
    model_config = {"from_attributes": True}


class ConversationSummary(BaseModel):
    conversation_id: str
    screen: str
    preview: str
    created_at: datetime
    message_count: int


class ConversationDetail(BaseModel):
    conversation_id: str
    screen: str
    context_summary: str | None
    messages: list[ChatMessageOut]
