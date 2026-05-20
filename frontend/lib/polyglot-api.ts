/**
 * Polyglot API client — separate backend from Alif (different DB + process).
 *
 * Modern Greek, Ancient Greek, and Latin reading-as-mapping. The polyglot
 * service lives on its own port (3002 in prod) but is fronted by a
 * transparent reverse proxy mounted at `/polyglot` on the alif backend
 * — see `backend/app/routers/polyglot_proxy.py`. The client therefore
 * uses `apiUrl + /polyglot` so iOS / web only needs to know ONE host:port.
 *
 * `polyglotApiUrl` in expoConfig.extra remains as an explicit override for
 * dev setups where alif isn't running (e.g. talking directly to a local
 * polyglot uvicorn on :3001).
 */
import Constants from "expo-constants";

const ALIF_API_URL: string =
  Constants.expoConfig?.extra?.apiUrl ?? "http://localhost:3000";
const POLYGLOT_OVERRIDE: string | undefined =
  Constants.expoConfig?.extra?.polyglotApiUrl;

export const POLYGLOT_BASE_URL = POLYGLOT_OVERRIDE ?? `${ALIF_API_URL}/polyglot`;

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
  /**
   * First page whose body_src passes the "looks like real reading content"
   * heuristic — skips copyright pages, blank dividers, all-caps title pages.
   * When the user opens a story, the reader should land here, not at page 1.
   */
  first_content_page_number: number;
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
  /** True iff the quality gate classified this token's sentence as a
   *  heading (chapter/section title or running header). UI MUST NOT render
   *  these inline with body prose. */
  is_heading: boolean;
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

export type MarkState = "known" | "unknown" | "encountered" | "ignore" | "clear";

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
export type ComprehensionSignal = "understood" | "partial" | "no_idea";

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
    comprehensionSignal?: ComprehensionSignal;
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

// ─── Sentence review ───────────────────────────────────────────────────────
// Mirrors Alif's sentence-review API (see frontend/lib/api.ts). Fields that
// polyglot's POST /api/reviews/submit-sentence does not accept are intentionally
// dropped (no audio_play_count, no lookup_count, no confusion_candidate_lemma_ids,
// no parent_card_type) per `polyglot/CLAUDE.md` § "Ground design and code in Alif".

export type WordRender = {
  position: number;
  surface_form: string;
  lemma_id: number | null;
  lemma_form: string | null;
  gloss_en: string | null;
  is_target: boolean;
  is_function_word: boolean;
  is_proper_name: boolean;
  knowledge_state: string;
};

export type SentencePayload = {
  sentence_id: number;
  text: string;
  translation_en: string | null;
  target_lemma_id: number;
  source: string | null;
  page_id: number | null;
  words: WordRender[];
  selection_reason: string;
  score: number;
};

export type SentenceReviewSubmission = {
  sentence_id: number;
  primary_lemma_id?: number | null;
  comprehension_signal: ComprehensionSignal;
  missed_lemma_ids: number[];
  confused_lemma_ids?: number[];
  response_ms: number;
  session_id: string;
  review_mode?: string;
  client_review_id: string;
};

export type WordReviewResult = {
  lemma_id: number;
  rating: number;
  credit_type: string;
  new_state: string;
  next_due: string;
};

export type SentenceReviewResult = {
  word_results: WordReviewResult[];
  duplicate: boolean;
  leech_suspended_lemma_ids: number[];
};

export type IntroCard = {
  lemma_id: number;
  lemma_form: string;
  lemma_bare: string;
  gloss_en: string | null;
  pos: string | null;
  intro_kind: "new" | "rescue";
  times_seen: number;
  cognate_lemma_id: number | null;
  cognate_lemma_form: string | null;
};

export type ReviewSessionBundle = {
  sentences: SentencePayload[];
  intro_cards: IntroCard[];
};

export async function getReviewSession(
  languageCode: string,
  limit: number = 15,
): Promise<ReviewSessionBundle> {
  return get(
    `/api/reviews/session?language_code=${encodeURIComponent(languageCode)}&limit=${limit}`,
  );
}

export async function ackExperimentIntro(
  lemmaId: number,
  sessionId?: string,
): Promise<{ lemma_id: number; stamped: boolean }> {
  return post("/api/reviews/experiment-intro-ack", {
    lemma_id: lemmaId,
    session_id: sessionId ?? null,
  });
}

export async function getNextSentence(
  lemmaId: number,
  languageCode: string,
): Promise<SentencePayload | null> {
  const res = await fetch(
    `${POLYGLOT_BASE_URL}/api/reviews/next-sentence?lemma_id=${lemmaId}&language_code=${encodeURIComponent(languageCode)}`,
  );
  if (!res.ok) throw new Error(`/api/reviews/next-sentence: ${res.status}`);
  const body = await res.text();
  if (!body || body === "null") return null;
  return JSON.parse(body);
}

export async function submitSentenceReview(
  submission: SentenceReviewSubmission,
): Promise<SentenceReviewResult> {
  return post("/api/reviews/submit-sentence", {
    sentence_id: submission.sentence_id,
    primary_lemma_id: submission.primary_lemma_id ?? null,
    comprehension_signal: submission.comprehension_signal,
    missed_lemma_ids: submission.missed_lemma_ids,
    confused_lemma_ids: submission.confused_lemma_ids ?? [],
    response_ms: submission.response_ms,
    session_id: submission.session_id,
    review_mode: submission.review_mode ?? "reading",
    client_review_id: submission.client_review_id,
  });
}

export async function undoSentenceReview(clientReviewId: string): Promise<{ undone: boolean; reviews_removed: number }> {
  return post("/api/reviews/undo-sentence", { client_review_id: clientReviewId });
}
