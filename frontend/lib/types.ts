export interface SentenceData {
  id: number;
  arabic: string;
  english: string;
  transliteration: string;
}

export interface ReviewCard {
  lemma_id: number;
  lemma_ar: string;
  lemma_ar_bare: string;
  gloss_en: string;
  root: string | null;
  pos: string;
  sentence: SentenceData | null;
}

export interface ReviewSession {
  cards: ReviewCard[];
  session_id: string;
  total_due: number;
}

export type ReviewMode = "reading" | "listening";
export type ComprehensionSignal = "understood" | "partial" | "no_idea";

export interface ReviewSubmission {
  lemma_id: number;
  rating: 1 | 3;
  response_ms: number;
  session_id: string;
  missed_words: string[];
  review_mode: ReviewMode;
  comprehension_signal?: ComprehensionSignal;
  missed_word_lemma_ids?: number[];
}

export interface Word {
  id: number;
  arabic: string;
  english: string;
  transliteration: string;
  root: string | null;
  pos: string;
  state: "new" | "learning" | "known";
  due_date: string | null;
}

export interface WordDetail extends Word {
  frequency_rank: number | null;
  times_reviewed: number;
  correct_count: number;
  root_family: { id: number; arabic: string; english: string }[];
}

export interface Stats {
  total_words: number;
  known_words: number;
  learning_words: number;
  new_words: number;
  due_today: number;
  reviews_today: number;
  streak_days: number;
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
}

export interface CEFREstimate {
  level: string;
  sublevel: string;
  known_words: number;
  next_level: string | null;
  words_to_next: number | null;
  reading_coverage_pct: number;
}

export interface LearnCandidate {
  lemma_id: number;
  lemma_ar: string;
  lemma_ar_bare: string;
  gloss_en: string;
  pos: string;
  transliteration: string | null;
  frequency_rank: number | null;
  root: string | null;
  root_meaning: string | null;
  root_id: number | null;
  score: number;
  score_breakdown: {
    frequency: number;
    root_familiarity: number;
    recency_bonus: number;
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
}

export interface SentenceReviewSession {
  session_id: string;
  items: SentenceReviewItem[];
  total_due_words: number;
  covered_due_words: number;
  intro_candidates?: IntroCandidate[];
}

export interface SentenceReviewSubmission {
  sentence_id: number | null;
  primary_lemma_id: number;
  comprehension_signal: ComprehensionSignal;
  missed_lemma_ids: number[];
  response_ms: number;
  session_id: string;
  review_mode: ReviewMode;
}

export interface Analytics {
  stats: {
    total_words: number;
    known: number;
    learning: number;
    new: number;
    due_today: number;
    reviews_today: number;
  };
  pace: LearningPace;
  cefr: CEFREstimate;
  daily_history: DailyStats[];
}
