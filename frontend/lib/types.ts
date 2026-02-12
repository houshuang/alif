export type ReviewMode = "reading" | "listening" | "quiz";
export type ComprehensionSignal = "understood" | "partial" | "no_idea" | "grammar_confused";

export interface Word {
  id: number;
  arabic: string;
  english: string;
  transliteration: string;
  root: string | null;
  pos: string;
  state: "new" | "learning" | "known" | "lapsed" | "suspended" | "acquiring" | "encountered";
  due_date: string | null;
  times_seen: number;
  times_correct: number;
  last_reviewed: string | null;
  knowledge_score: number;
  frequency_rank: number | null;
  cefr_level: string | null;
  last_ratings?: number[];
  last_review_gaps?: (number | null)[];
}

export interface ReviewHistoryEntry {
  rating: number;
  reviewed_at: string | null;
  response_ms: number | null;
  credit_type: string | null;
  comprehension_signal: string | null;
  review_mode: string | null;
  sentence_arabic?: string;
  sentence_english?: string;
}

export interface GrammarFeatureDetail {
  feature_key: string;
  category: string | null;
  label_en: string;
  label_ar: string | null;
}

export interface WordSentenceStat {
  sentence_id: number;
  surface_forms: string[];
  sentence_arabic: string;
  sentence_english: string | null;
  sentence_transliteration: string | null;
  seen_count: number;
  missed_count: number;
  confused_count: number;
  understood_count: number;
  primary_count: number;
  collateral_count: number;
  accuracy_pct: number | null;
  last_reviewed_at: string | null;
}

export interface SourceInfo {
  type: string;
  story_id?: number;
  story_title?: string;
}

export interface WordDetail extends Word {
  times_reviewed: number;
  correct_count: number;
  forms_json?: WordForms | null;
  grammar_features: GrammarFeatureDetail[];
  root_family: { id: number; arabic: string; english: string }[];
  review_history: ReviewHistoryEntry[];
  sentence_stats: WordSentenceStat[];
  source_info?: SourceInfo | null;
  etymology_json?: EtymologyData | null;
  acquisition_box?: number | null;
}

export interface Stats {
  total_words: number;
  known_words: number;
  learning_words: number;
  new_words: number;
  due_today: number;
  reviews_today: number;
  streak_days: number;
  total_reviews: number;
  lapsed: number;
  acquiring: number;
  encountered: number;
}

export interface DailyStats {
  date: string;
  reviews: number;
  words_learned: number;
  cumulative_known: number;
  accuracy: number | null;
}

export interface LearningPace {
  words_per_day_7d: number;
  words_per_day_30d: number;
  reviews_per_day_7d: number;
  reviews_per_day_30d: number;
  total_study_days: number;
  current_streak: number;
  longest_streak: number;
  accuracy_7d: number | null;
  study_days_7d: number;
}

export interface CEFREstimate {
  level: string;
  sublevel: string;
  known_words: number;
  next_level: string | null;
  words_to_next: number | null;
  reading_coverage_pct: number;
}

export interface EtymologyData {
  root_meaning: string | null;
  pattern: string | null;
  pattern_meaning: string | null;
  derivation: string | null;
  semantic_field: string | null;
  related_loanwords: string[];
  cultural_note: string | null;
}

export interface WrapUpCard {
  lemma_id: number;
  lemma_ar: string;
  lemma_ar_bare: string;
  gloss_en: string | null;
  transliteration: string | null;
  pos: string | null;
  forms_json: WordForms | null;
  root: string | null;
  root_meaning: string | null;
  etymology_json: EtymologyData | null;
}

export interface RecapItem {
  sentence_id: number;
  arabic_text: string;
  english_translation: string;
  transliteration: string | null;
  audio_url: string | null;
  primary_lemma_id: number;
  words: SentenceWordMeta[];
  is_recap: boolean;
}

export interface WordForms {
  gender?: string;
  plural?: string;
  present?: string;
  masdar?: string;
  active_participle?: string;
  verb_form?: string;
  feminine?: string;
  elative?: string;
}

export interface LearnCandidate {
  lemma_id: number;
  lemma_ar: string;
  lemma_ar_bare: string;
  gloss_en: string;
  pos: string;
  transliteration: string | null;
  frequency_rank: number | null;
  cefr_level: string | null;
  root: string | null;
  root_meaning: string | null;
  root_id: number | null;
  forms_json: WordForms | null;
  audio_url: string | null;
  example_ar: string | null;
  example_en: string | null;
  grammar_features?: string[];
  grammar_details?: GrammarFeatureDetail[];
  score: number;
  etymology_json?: EtymologyData | null;
  story_title?: string | null;
  score_breakdown: {
    frequency: number;
    root_familiarity: number;
    recency_bonus: number;
    story_bonus: number;
    encountered_bonus: number;
    known_siblings: number;
    total_siblings: number;
  };
}

export interface RootFamilyWord {
  lemma_id: number;
  lemma_ar: string;
  lemma_ar_bare: string;
  gloss_en: string;
  pos: string;
  transliteration: string | null;
  state: string;
}

export interface IntroduceResult {
  lemma_id: number;
  lemma_ar?: string;
  gloss_en?: string;
  state: string;
  already_known: boolean;
  root?: string | null;
  root_meaning?: string | null;
  root_family?: RootFamilyWord[];
}

export interface SentenceWordMeta {
  lemma_id: number | null;
  surface_form: string;
  gloss_en: string | null;
  stability: number | null;
  is_due: boolean;
  is_function_word: boolean;
  knowledge_state: string;
  root: string | null;
  root_meaning: string | null;
  root_id: number | null;
  frequency_rank: number | null;
  cefr_level: string | null;
  grammar_tags?: string[];
}

export interface WordLookupResult {
  lemma_id: number;
  lemma_ar: string;
  gloss_en: string | null;
  transliteration: string | null;
  root: string | null;
  root_meaning: string | null;
  root_id: number | null;
  pos: string | null;
  forms_json: WordForms | null;
  example_ar: string | null;
  example_en: string | null;
  grammar_details: GrammarFeatureDetail[];
  is_function_word?: boolean;
  frequency_rank: number | null;
  cefr_level: string | null;
  root_family: {
    lemma_id: number;
    lemma_ar: string;
    gloss_en: string | null;
    pos: string | null;
    transliteration: string | null;
    state: string;
  }[];
}

export interface SentenceReviewItem {
  sentence_id: number | null;
  arabic_text: string;
  arabic_diacritized: string | null;
  english_translation: string;
  transliteration: string | null;
  audio_url: string | null;
  primary_lemma_id: number;
  primary_lemma_ar: string;
  primary_gloss_en: string;
  words: SentenceWordMeta[];
  grammar_features?: string[];
}

export interface IntroCandidate {
  lemma_id: number;
  lemma_ar: string;
  gloss_en: string | null;
  pos: string | null;
  transliteration: string | null;
  root: string | null;
  root_meaning: string | null;
  root_id: number | null;
  insert_at: number;
  forms_json: WordForms | null;
  example_ar: string | null;
  example_en: string | null;
  audio_url: string | null;
  grammar_features: string[];
  grammar_details: GrammarFeatureDetail[];
  story_title?: string | null;
  root_family: {
    lemma_id: number;
    lemma_ar: string;
    gloss_en: string | null;
    pos: string | null;
    transliteration: string | null;
    state: string;
  }[];
}

export interface ReintroCard {
  lemma_id: number;
  lemma_ar: string;
  gloss_en: string | null;
  pos: string | null;
  transliteration: string | null;
  root: string | null;
  root_meaning: string | null;
  root_id: number | null;
  forms_json: WordForms | null;
  example_ar: string | null;
  example_en: string | null;
  audio_url: string | null;
  grammar_features: string[];
  grammar_details: GrammarFeatureDetail[];
  times_seen: number;
  root_family: {
    lemma_id: number;
    lemma_ar: string;
    gloss_en: string | null;
    pos: string | null;
    transliteration: string | null;
    state: string;
  }[];
}

export interface SentenceReviewSession {
  session_id: string;
  items: SentenceReviewItem[];
  total_due_words: number;
  covered_due_words: number;
  intro_candidates?: IntroCandidate[];
  reintro_cards?: ReintroCard[];
  grammar_intro_needed?: string[];
  grammar_refresher_needed?: string[];
}

export interface GrammarProgress {
  feature_key: string;
  category: string;
  label_en: string;
  times_seen: number;
  times_correct: number;
  comfort_score: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface GrammarLesson {
  feature_key: string;
  label_en: string;
  label_ar: string | null;
  category: string;
  form_change_type: string | null;
  explanation: string;
  examples: { ar: string; en: string }[];
  tip: string | null;
  introduced_at: string | null;
  times_seen: number;
  times_confused: number;
  comfort_score: number;
  confusion_rate?: number;
  is_refresher?: boolean;
}

export interface SentenceReviewSubmission {
  sentence_id: number | null;
  primary_lemma_id: number;
  comprehension_signal: ComprehensionSignal;
  missed_lemma_ids: number[];
  confused_lemma_ids?: number[];
  response_ms: number;
  session_id: string;
  review_mode: ReviewMode;
  client_review_id?: string;
  audio_play_count?: number;
  lookup_count?: number;
}

export interface Analytics {
  stats: {
    total_words: number;
    known: number;
    learning: number;
    new: number;
    due_today: number;
    reviews_today: number;
    total_reviews: number;
    lapsed: number;
    acquiring: number;
    encountered: number;
  };
  pace: LearningPace;
  cefr: CEFREstimate;
  daily_history: DailyStats[];
}

// Deep Analytics types
export interface StabilityBucket {
  label: string;
  count: number;
  min_days: number;
  max_days: number | null;
}

export interface RetentionStats {
  period_days: number;
  total_reviews: number;
  correct_reviews: number;
  retention_pct: number | null;
}

export interface StateTransitions {
  period: string;
  new_to_learning: number;
  learning_to_known: number;
  known_to_lapsed: number;
  lapsed_to_learning: number;
}

export interface ComprehensionBreakdown {
  period_days: number;
  understood: number;
  partial: number;
  no_idea: number;
  total: number;
}

export interface StrugglingWord {
  lemma_id: number;
  lemma_ar: string;
  gloss_en: string | null;
  times_seen: number;
  total_encounters: number;
}

export interface RootCoverageData {
  total_roots: number;
  roots_with_known: number;
  roots_fully_mastered: number;
  top_partial_roots: { root: string; root_meaning: string | null; known: number; total: number }[];
}

export interface SessionDetail {
  session_id: string;
  reviewed_at: string;
  sentence_count: number;
  comprehension: Record<string, number>;
  avg_response_ms: number | null;
}

export interface DeepAnalytics {
  stability_distribution: StabilityBucket[];
  retention_7d: RetentionStats;
  retention_30d: RetentionStats;
  transitions_today: StateTransitions;
  transitions_7d: StateTransitions;
  transitions_30d: StateTransitions;
  comprehension_7d: ComprehensionBreakdown;
  comprehension_30d: ComprehensionBreakdown;
  struggling_words: StrugglingWord[];
  root_coverage: RootCoverageData;
  recent_sessions: SessionDetail[];
}

export interface StoryWordMeta {
  position: number;
  surface_form: string;
  lemma_id: number | null;
  gloss_en: string | null;
  is_known: boolean;
  is_function_word: boolean;
  name_type: "personal" | "place" | null;
  sentence_index: number;
}

export interface StoryListItem {
  id: number;
  title_ar: string | null;
  title_en: string | null;
  source: "generated" | "imported";
  status: "active" | "completed" | "suspended";
  readiness_pct: number;
  unknown_count: number;
  total_words: number;
  created_at: string;
}

export interface StoryDetail extends StoryListItem {
  body_ar: string;
  body_en: string | null;
  transliteration: string | null;
  known_count: number;
  words: StoryWordMeta[];
}

export interface StoryLookupResult {
  lemma_id: number;
  gloss_en: string | null;
  transliteration: string | null;
  root: string | null;
  pos: string | null;
}

export interface AskAIResponse {
  answer: string;
  conversation_id: string;
}

export interface ChatMessageItem {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ConversationSummary {
  conversation_id: string;
  screen: string;
  preview: string;
  created_at: string;
  message_count: number;
}

export interface ConversationDetail {
  conversation_id: string;
  screen: string;
  context_summary: string | null;
  messages: ChatMessageItem[];
}

// --- OCR / Textbook Scanner types ---

export interface ExtractedWord {
  arabic: string;
  arabic_bare: string;
  english: string | null;
  status: "new" | "existing" | "existing_new_card";
  lemma_id: number;
  knowledge_state: string;
  root?: string | null;
  pos?: string | null;
}

export interface PageUploadResult {
  id: number;
  batch_id: string;
  filename: string | null;
  status: "pending" | "processing" | "completed" | "failed";
  new_words: number;
  existing_words: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  extracted_words: ExtractedWord[];
}

export interface BatchUploadResult {
  batch_id: string;
  pages: PageUploadResult[];
  total_new: number;
  total_existing: number;
}

export interface BatchSummary {
  batch_id: string;
  page_count: number;
  status: "processing" | "completed" | "failed";
  total_new: number;
  total_existing: number;
  created_at: string;
  pages: PageUploadResult[];
}

// --- Topic / Settings types ---

export interface TopicSettings {
  active_topic: string | null;
  topic_started_at: string | null;
  words_introduced_in_topic: number;
  max_topic_batch: number;
}

export interface TopicInfo {
  domain: string;
  label: string;
  available_words: number;
  learned_words: number;
  eligible: boolean;
}
