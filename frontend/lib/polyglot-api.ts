/**
 * Polyglot API client — separate backend from Alif (different port + DB).
 *
 * Modern Greek, Ancient Greek, and Latin reading-as-mapping. Polyglot runs at
 * `polyglotApiUrl` from expoConfig.extra (defaults to localhost:3001).
 */
import Constants from "expo-constants";

export const POLYGLOT_BASE_URL =
  Constants.expoConfig?.extra?.polyglotApiUrl ?? "http://localhost:3001";

// ─── Types ─────────────────────────────────────────────────────────────────

export type LanguageInfo = {
  code: string;
  name: string;
  script: string;
  direction: string;
  accent_display: string;
  is_active: boolean;
  provider_available: boolean;
};

export type StorySummary = {
  id: number;
  language_code: string;
  title: string | null;
  author: string | null;
  source: string;
  page_count: number | null;
  processed_pages: number;
  total_words: number;
  known_count: number;
  unknown_count: number;
  status: string;
  created_at: string;
};

export type TokenView = {
  position: number;
  surface: string;
  is_punctuation: boolean;
  sentence_index: number;
  lemma_id: number | null;
  lemma_form: string | null;
  lemma_bare: string | null;
  pos: string | null;
  gloss_en: string | null;
  is_function_word: boolean;
  is_known: boolean;
  is_acquiring: boolean;
  is_encountered: boolean;
  is_unknown: boolean;
  is_ignored: boolean;
  is_new: boolean;
  is_oov: boolean;
};

export type PageView = {
  story_id: number;
  page_number: number;
  total_pages: number;
  total_words: number;
  tokens: TokenView[];
};

export type MarkState = "known" | "unknown" | "encountered" | "ignore";

export type CognateInfo = {
  lang: string;
  form: string;
  transparency: "high" | "medium" | "low";
  note?: string;
};

export type LemmaCognates = {
  lemma_id: number;
  lemma_form: string;
  language_code: string;
  cognates: CognateInfo[];
  detected_at: string | null;
  cognate_lemma_id: number | null;
};

// ─── Calls ─────────────────────────────────────────────────────────────────

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${POLYGLOT_BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${POLYGLOT_BASE_URL}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

export async function listLanguages(): Promise<LanguageInfo[]> {
  return get("/api/languages");
}

export async function listStories(): Promise<StorySummary[]> {
  return get("/api/texts");
}

export async function getStory(storyId: number): Promise<StorySummary> {
  return get(`/api/texts/${storyId}`);
}

export async function getPage(storyId: number, pageNumber: number): Promise<PageView> {
  return get(`/api/texts/${storyId}/pages/${pageNumber}`);
}

export async function markWord(
  storyId: number,
  lemmaId: number,
  state: MarkState,
): Promise<{ lemma_id: number; state: string; gloss_en: string | null }> {
  return patch(`/api/texts/${storyId}/mark`, { lemma_id: lemmaId, state });
}

export async function markRemainingKnown(
  storyId: number,
  pageNumber: number,
): Promise<{ page_number: number; newly_known: number }> {
  const res = await fetch(
    `${POLYGLOT_BASE_URL}/api/texts/${storyId}/pages/${pageNumber}/mark_remaining`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`mark_remaining: ${res.status}`);
  return res.json();
}

export async function getLemmaCognates(lemmaId: number): Promise<LemmaCognates> {
  return get(`/api/lemmas/${lemmaId}/cognates`);
}

// ─── Stats ─────────────────────────────────────────────────────────────────

export type LanguageStats = {
  language_code: string;
  total_lemmas: number;
  new: number;
  by_state: {
    known: number;
    acquiring: number;
    encountered: number;
    unknown: number;
    ignored: number;
  };
  stories: { id: number; title: string | null; page_count: number | null; processed_pages: number }[];
};

export async function getLanguageStats(languageCode: string): Promise<LanguageStats> {
  return get(`/api/stats?language_code=${encodeURIComponent(languageCode)}`);
}

// ─── Reviews ───────────────────────────────────────────────────────────────

export type DueLemma = {
  lemma_id: number;
  lemma_form: string;
  lemma_bare: string;
  gloss_en: string | null;
  state: string;
  acquisition_box: number | null;
  next_due: string;
};

export type ReviewRating = 1 | 2 | 3 | 4;

export type ReviewResult = {
  lemma_id: number;
  new_state: string;
  acquisition_box: number | null;
  graduated: boolean | null;
  next_due: string;
  duplicate: boolean;
  leech_suspended: boolean;
};

export type AcquisitionStats = {
  total_acquiring: number;
  box_1: number;
  box_2: number;
  box_3: number;
  due_now: number;
};

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${POLYGLOT_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

export async function getDueLemmas(
  languageCode: string,
  limit: number = 50,
): Promise<DueLemma[]> {
  return get(`/api/reviews/due?language_code=${encodeURIComponent(languageCode)}&limit=${limit}`);
}

export async function submitReview(
  lemmaId: number,
  rating: ReviewRating,
  opts: {
    responseMs?: number;
    sessionId?: string;
    clientReviewId?: string;
    comprehensionSignal?: "understood" | "partial" | "no_idea";
  } = {},
): Promise<ReviewResult> {
  return post("/api/reviews/submit", {
    lemma_id: lemmaId,
    rating,
    response_ms: opts.responseMs,
    session_id: opts.sessionId,
    client_review_id: opts.clientReviewId,
    comprehension_signal: opts.comprehensionSignal,
    review_mode: "reading",
  });
}

export async function getReviewStats(): Promise<AcquisitionStats> {
  return get("/api/reviews/stats");
}
