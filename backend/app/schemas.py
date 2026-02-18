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
    cefr_level: Optional[str] = None
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
    acquiring: int = 0
    encountered: int = 0


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
    acquiring_known: int = 0
    next_level: Optional[str] = None
    words_to_next: Optional[int] = None
    reading_coverage_pct: float
    days_to_next_weekly_pace: Optional[int] = None
    days_to_next_today_pace: Optional[int] = None


class GraduatedWord(BaseModel):
    lemma_id: int
    lemma_ar: str
    gloss_en: str | None


class IntroducedBySource(BaseModel):
    source: str
    count: int


class AnalyticsOut(BaseModel):
    stats: StatsOut
    pace: LearningPaceOut
    cefr: CEFREstimate
    daily_history: list[DailyStatsPoint]
    comprehension_today: Optional["ComprehensionBreakdown"] = None
    graduated_today: list[GraduatedWord] = []
    introduced_today: list[IntroducedBySource] = []
    calibration_signal: str = "not_enough_data"
    total_words_reviewed_7d: int = 0
    total_words_reviewed_alltime: int = 0
    unique_words_recognized_7d: int = 0
    unique_words_recognized_prior_7d: int = 0


class StabilityBucket(BaseModel):
    label: str
    count: int
    min_days: float
    max_days: float | None


class RetentionStats(BaseModel):
    period_days: int
    total_reviews: int
    correct_reviews: int
    retention_pct: float | None


class StateTransitions(BaseModel):
    period: str  # "today" / "7d" / "30d"
    new_to_learning: int = 0
    learning_to_known: int = 0
    known_to_lapsed: int = 0
    lapsed_to_learning: int = 0


class ComprehensionBreakdown(BaseModel):
    period_days: int
    understood: int = 0
    partial: int = 0
    no_idea: int = 0
    total: int = 0


class StrugglingWord(BaseModel):
    lemma_id: int
    lemma_ar: str
    gloss_en: str | None
    times_seen: int
    total_encounters: int


class RootCoverage(BaseModel):
    total_roots: int
    roots_with_known: int
    roots_fully_mastered: int
    top_partial_roots: list[dict]  # [{root, root_meaning, known, total}]


class SessionDetail(BaseModel):
    session_id: str
    reviewed_at: str
    sentence_count: int
    comprehension: dict  # {understood: N, partial: N, no_idea: N}
    avg_response_ms: float | None


class AcquisitionWord(BaseModel):
    lemma_id: int
    lemma_ar: str
    gloss_en: str | None
    acquisition_box: int
    times_seen: int
    times_correct: int


class RecentGraduation(BaseModel):
    lemma_id: int
    lemma_ar: str
    gloss_en: str | None
    graduated_at: str


class AcquisitionPipeline(BaseModel):
    box_1: list[AcquisitionWord]
    box_2: list[AcquisitionWord]
    box_3: list[AcquisitionWord]
    box_1_count: int
    box_2_count: int
    box_3_count: int
    box_1_due: int = 0
    box_2_due: int = 0
    box_3_due: int = 0
    recent_graduations: list[RecentGraduation]
    flow_history: list[dict] = []  # [{date, entered, graduated}] last 7 days


class InsightsOut(BaseModel):
    avg_encounters_to_graduation: float | None = None
    graduation_rate_pct: float | None = None
    best_weekday: dict | None = None  # {day_name, accuracy_pct, review_count}
    dark_horse_root: dict | None = None  # {root, meaning, known, total}
    unique_sentences_reviewed: int = 0
    total_sentence_reviews: int = 0
    forgetting_forecast: dict = {}  # {skip_1d: N, skip_3d: N, skip_7d: N}
    record_intro_day: dict | None = None  # {date, count}
    record_graduation_day: dict | None = None  # {date, count}


class DeepAnalyticsOut(BaseModel):
    stability_distribution: list[StabilityBucket]
    retention_7d: RetentionStats
    retention_30d: RetentionStats
    transitions_today: StateTransitions
    transitions_7d: StateTransitions
    transitions_30d: StateTransitions
    comprehension_7d: ComprehensionBreakdown
    comprehension_30d: ComprehensionBreakdown
    struggling_words: list[StrugglingWord]
    root_coverage: RootCoverage
    recent_sessions: list[SessionDetail]
    acquisition_pipeline: Optional[AcquisitionPipeline] = None
    insights: Optional[InsightsOut] = None


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
    frequency_rank: int | None = None
    cefr_level: str | None = None


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
    grammar_features: list[str] = []


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
    forms_json: dict | None = None
    example_ar: str | None = None
    example_en: str | None = None
    audio_url: str | None = None
    grammar_features: list[str] = []
    grammar_details: list[dict] = []
    root_family: list[dict] = []
    story_title: str | None = None


class ReintroCardOut(BaseModel):
    lemma_id: int
    lemma_ar: str
    gloss_en: str | None = None
    pos: str | None = None
    transliteration: str | None = None
    root: str | None = None
    root_meaning: str | None = None
    root_id: int | None = None
    forms_json: dict | None = None
    example_ar: str | None = None
    example_en: str | None = None
    audio_url: str | None = None
    grammar_features: list[str] = []
    grammar_details: list[dict] = []
    times_seen: int = 0
    root_family: list[dict] = []


class ReintroResultIn(BaseModel):
    lemma_id: int
    result: str  # "remember" or "show_again"
    session_id: str | None = None
    client_review_id: str | None = None


class SentenceSessionOut(BaseModel):
    session_id: str
    items: list[SentenceReviewItem]
    total_due_words: int
    covered_due_words: int
    intro_candidates: list[IntroCandidateOut] = []
    reintro_cards: list[ReintroCardOut] = []
    grammar_intro_needed: list[str] = []
    grammar_refresher_needed: list[str] = []


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
    type: str  # "sentence"
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


# --- Wrap-up and Recap schemas ---

class WrapUpIn(BaseModel):
    seen_lemma_ids: list[int]
    missed_lemma_ids: list[int] = []
    session_id: str | None = None


class WrapUpCardOut(BaseModel):
    lemma_id: int
    lemma_ar: str
    lemma_ar_bare: str
    gloss_en: str | None = None
    transliteration: str | None = None
    pos: str | None = None
    forms_json: dict | list | None = None
    root: str | None = None
    root_meaning: str | None = None
    etymology_json: dict | None = None
    memory_hooks_json: dict | None = None
    is_acquiring: bool = False


class WrapUpOut(BaseModel):
    cards: list[WrapUpCardOut]


class RecapIn(BaseModel):
    last_session_lemma_ids: list[int]


# --- Story schemas ---

class StoryWordMetaOut(BaseModel):
    position: int
    surface_form: str
    lemma_id: int | None = None
    gloss_en: str | None = None
    is_known: bool = False
    is_function_word: bool = False
    name_type: str | None = None
    sentence_index: int = 0


class PageReadiness(BaseModel):
    page: int
    new_words: int
    learned_words: int
    unlocked: bool


class BookPageWordOut(BaseModel):
    lemma_id: int
    arabic: str
    gloss_en: str | None = None
    transliteration: str | None = None
    knowledge_state: str | None = None
    is_new: bool = False


class BookPageSentenceOut(BaseModel):
    id: int
    arabic_diacritized: str
    english_translation: str | None = None
    seen: bool = False


class BookPageDetailOut(BaseModel):
    story_id: int
    page_number: int
    story_title_en: str | None = None
    known_count: int = 0
    new_not_started: int = 0
    new_learning: int = 0
    words: list[BookPageWordOut] = []
    sentences: list[BookPageSentenceOut] = []


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
    page_count: int | None = None
    sentence_count: int | None = None
    sentences_seen: int | None = None
    page_readiness: list[PageReadiness] | None = None
    new_total: int | None = None
    new_learning: int | None = None
    created_at: str
    estimated_days_to_ready: int | None = None
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
    page_count: int | None = None
    sentence_count: int | None = None
    sentences_seen: int | None = None
    page_readiness: list[PageReadiness] | None = None
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


# --- OCR / Textbook Scanner schemas ---

class PageUploadOut(BaseModel):
    id: int
    batch_id: str
    filename: str | None = None
    status: str
    new_words: int
    existing_words: int
    error_message: str | None = None
    created_at: str
    completed_at: str | None = None
    extracted_words: list[dict] = []
    model_config = {"from_attributes": True}


class BatchUploadOut(BaseModel):
    batch_id: str
    pages: list[PageUploadOut]
    total_new: int
    total_existing: int


class OCRStoryImportOut(BaseModel):
    extracted_text: str
