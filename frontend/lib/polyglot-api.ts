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
import AsyncStorage from "@react-native-async-storage/async-storage";
import { netStatus } from "./net-status";

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

export type SentenceTranslation = {
  /** Matches TokenView.sentence_index — group tokens by this to render pairs. */
  sentence_index_in_page: number;
  /** Null when the LLM call failed for this sentence; the row stays untranslated. */
  translation_en: string | null;
};

export type PageTranslation = {
  story_id: number;
  page_number: number;
  /** Full-page English. null = LLM failed, retry; "" = blank/punctuation page.
   *  When `sentences` is non-empty this is the concatenation of those, kept for
   *  backward compat. The reader's Reveal renders `sentences` directly. */
  translation_en: string | null;
  generated: boolean;
  /** One entry per harvested Sentence row, keyed by sentence_index_in_page.
   *  Empty for legacy / un-harvested pages — Reveal then falls back to the
   *  page-level translation_en above. */
  sentences: SentenceTranslation[];
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

export async function listStories(languageCode?: string): Promise<StorySummary[]> {
  const qs = languageCode ? `?language_code=${encodeURIComponent(languageCode)}` : "";
  return get(`/api/texts${qs}`);
}

export async function getStory(storyId: number): Promise<StorySummary> {
  return get(`/api/texts/${storyId}`);
}

export async function getPage(storyId: number, pageNumber: number): Promise<PageView> {
  return get(`/api/texts/${storyId}/pages/${pageNumber}`);
}

// Lazy full-page English translation for the reader's "Show English" reveal.
// Generated + cached server-side on first request; the reader prefetches it in
// the background on page load so the reveal is instant.
export async function getPageTranslation(
  storyId: number,
  pageNumber: number,
): Promise<PageTranslation> {
  return get(`/api/texts/${storyId}/pages/${pageNumber}/translation`);
}

export async function markWord(
  storyId: number,
  lemmaId: number,
  state: MarkState,
): Promise<{ lemma_id: number; state: string; gloss_en: string | null }> {
  return patch(`/api/texts/${storyId}/mark`, { lemma_id: lemmaId, state });
}

// Advancing a page is a self-contained, idempotent action queued via
// `polyglot-sync-queue.ts` (offline-safe, auto-sent on reconnect) rather than a
// direct call here. Request shape: see the backend's MarkRemainingRequest.

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
    learning: number;
    lapsed: number;
    acquiring: number;        // legacy: acquiring + learning
    acquiring_only: number;
    encountered: number;
    unknown: number;
    ignored: number;
    suspended: number;
  };
  leitner: {
    total_acquiring: number;
    box_1: number;
    box_2: number;
    box_3: number;
    due_now: number;
  };
  fsrs: {
    tracked: number;
    stability_buckets: { label: string; count: number }[];
  };
  recovery: {
    pre_known: number;
    cognate_known: number;
    ever_failed: number;
    recovered_once: number;
    graduated_after_failure: number;
    stable_after_failure_7d: number;
    stable_after_failure_21d: number;
    stable_after_failure_60d: number;
    currently_known_after_failure: number;
    learning_after_failure: number;
    still_acquiring_after_failure: number;
    lapsed_after_failure: number;
    failed_not_yet_recovered: number;
  };
  known_summary: {
    total: number;
    pre_known: number;
    cognate_known: number;
    fsrs_known: number;
    assumed_known: number;
    exposure_confirmed: number;
    assumed_unconfirmed: number;
    judged_known: number;
    unjudged_known: number;
    lapsed_from_assumed_known: number;
    lapsed_from_assumed_known_to_learn: number;
  };
  judged_progress: {
    total: number;
    to_learn: number;
    learnt: number;
    pending: number;
    ever_red: number;
    ever_green: number;
    yellow_only: number;
    lapsed_from_known: number;
    lapsed_from_known_to_learn: number;
    pipeline: {
      acquiring: number;
      box_1: number;
      box_2: number;
      box_3: number;
      acquisition_due_now: number;
      learning: number;
      known: number;
      lapsed: number;
      suspended: number;
      fsrs_tracked: number;
      fsrs_due_now: number;
    };
  };
  today: {
    reviews: number;
    sentence_reviews: number;
    pages_read: number;
    new_lemmas: number;
    graduated: number;
    marked_unknown: number;
    streak: number;
  };
  history_14d: {
    date: string;
    reviews: number;
    pages_read: number;
    new_lemmas: number;
  }[];
  flow_history: {
    week_start: string;
    confirmed: number;
    gaps_discovered: number;
    graduated: number;
    new_lemmas: number;
  }[];
  frequency: {
    source: string;
    total_entries: number;
    bands: {
      top_n: number;
      learned: number;
      acquiring: number;
      encountered: number;
      unmapped: number;
      new: number;
      coverage_pct: number;
    }[];
  } | null;
  stories: {
    id: number;
    title: string | null;
    page_count: number | null;
    processed_pages: number;
    viewed_pages: number;
    total_words: number;
    known_count: number;
    unknown_count: number;
  }[];
  activity: {
    event_type: string;
    summary: string;
    created_at: string | null;
  }[];
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

export async function getReviewStats(languageCode?: string): Promise<AcquisitionStats> {
  const q = languageCode ? `?language_code=${encodeURIComponent(languageCode)}` : "";
  return get(`/api/reviews/stats${q}`);
}

// ─── Chat + reports ───────────────────────────────────────────────────────

export type PolyglotAskAIResponse = {
  answer: string;
  conversation_id: string;
};

export async function askPolyglotAI(
  question: string,
  context: string,
  screen: string,
  conversationId?: string,
): Promise<PolyglotAskAIResponse> {
  const res = await fetch(`${POLYGLOT_BASE_URL}/api/chat/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, context, screen, conversation_id: conversationId }),
  });
  if (!res.ok) throw new Error(`/api/chat/ask: ${res.status}`);
  return res.json();
}

export async function flagPolyglotContent(data: {
  content_type: string;
  lemma_id?: number;
  sentence_id?: number;
}): Promise<{ flag_id: number; status: string }> {
  return post("/api/flags", data);
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
  is_punctuation?: boolean;
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

// ─── Lemma philology enrichment ─────────────────────────────────────────────
// Mirrors `polyglot/app/schemas.py::LemmaEnrichment` 1:1. Shape parity is
// enforced by frontend/lib/__tests__/polyglot-enrichment-shape.test.ts which
// loads a backend-emitted fixture and asserts the TS interface accepts it.
// When changing this shape: edit schemas.py FIRST, regen the fixture, then
// update here.

export type LemmaEra =
  | "Mycenaean"
  | "Homeric"
  | "Classical"
  | "Koine"
  | "Byzantine"
  | "Modern";

export type LemmaCognateRelation =
  | "loanword-from-greek"
  | "shared-pie-root"
  | "calque"
  | "descendant"
  | "borrowed-via-latin";

export type LemmaFormality = "formal" | "neutral" | "colloquial" | "literary";

export type LemmaEtymology = {
  pie_root: string | null;
  ancient_form: string | null;
  origin_note: string;
  morphology: string | null;
};

export type LemmaDiachronyStage = {
  era: LemmaEra;
  form: string;
  meaning: string;
  note: string | null;
};

export type LemmaCognate = {
  language: string;
  form: string;
  relation: LemmaCognateRelation;
  gloss_en: string | null;
  note: string | null;
};

export type LemmaQuote = {
  text: string;
  source: string;
  era: LemmaEra;
  translation_en: string;
};

export type LemmaRegister = {
  formality: LemmaFormality | null;
  collocations: string[];
  false_friends_en: string[];
  usage_note: string | null;
};

export type LemmaEnrichment = {
  version: number;
  etymology: LemmaEtymology | null;
  diachrony: LemmaDiachronyStage[];
  cognates: LemmaCognate[];
  quotes: LemmaQuote[];
  register: LemmaRegister | null;
};

// Mirrors the `/api/lemmas/{lemma_id}/detail` response (profile.py).
export type LemmaDetail = {
  lemma_id: number;
  language_code: string;
  lemma_form: string;
  lemma_bare: string;
  pos: string | null;
  gloss_en: string | null;
  frequency_rank: number | null;
  cefr_level: string | null;
  word_category: string | null;
  cognate_lemma_id: number | null;
  cognate_lemma_form: string | null;
  external_cognates: { lang: string; form: string; transparency: string; note?: string | null }[];
  enrichment: LemmaEnrichment | null;
  enrichment_status: string | null;
  enriched_at: string | null;
  knowledge_state: string | null;
  times_seen: number;
};

export async function getLemmaDetail(lemmaId: number): Promise<LemmaDetail> {
  return get(`/api/lemmas/${lemmaId}/detail`);
}

export type ReviewSessionBundle = {
  sentences: SentencePayload[];
  intro_cards: IntroCard[];
};

// ─── Review session loading (prefetch + resilient fetch) ────────────────────
//
// Mirrors Alif's session cache + background prefetch (lib/api.ts), per
// polyglot/CLAUDE.md § "Ground design and code in Alif". The problem this
// solves: the review screen used to do a single cold fetch for the *next*
// session at the exact moment the current one finished. On an iOS dev build
// over mobile data, a transient failure there surfaces as a bare "Network
// request failed". The fix is to build the next session in the background while
// the learner is still reviewing, so the transition is a cache hit.
//
// Divergence from Alif: a single-slot cache (one session ahead), keyed per
// language because Greek and Latin share the review screen — that's all the
// transition needs. Bump to a queue if deep offline use is ever required.

const nextSessionKey = (lang: string) => `@polyglot:nextSession:${lang}`;

// Don't serve a prefetched session older than this — the learner may have
// reviewed words since it was built, drifting it out of date. 15 min matches
// the review-snapshot recency gate in polyglot-review.tsx.
const NEXT_SESSION_MAX_AGE_MS = 15 * 60_000;

// build_session is DB-only and <1s (mirrors Alif's "no LLM in session build"
// invariant), so a generous timeout here only catches genuine transport
// stalls. NOT applied to the shared get() helper — the reader's page-view /
// gloss endpoints can legitimately spend 2-3 min in the LLM quality gate.
const SESSION_TIMEOUT_MS = 12_000;

type CachedNextSession = {
  language: string;
  bundle: ReviewSessionBundle;
  savedAt: number;
};

async function fetchWithTimeout(url: string, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function fetchSessionBundle(
  languageCode: string,
  limit: number,
  prefetch: boolean,
): Promise<ReviewSessionBundle> {
  const url =
    `${POLYGLOT_BASE_URL}/api/reviews/session` +
    `?language_code=${encodeURIComponent(languageCode)}&limit=${limit}` +
    (prefetch ? "&prefetch=true" : "");
  const res = await fetchWithTimeout(url, SESSION_TIMEOUT_MS);
  if (!res.ok) throw new Error(`/api/reviews/session: ${res.status}`);
  return res.json();
}

async function readCachedNextSession(
  languageCode: string,
): Promise<ReviewSessionBundle | null> {
  try {
    const raw = await AsyncStorage.getItem(nextSessionKey(languageCode));
    if (!raw) return null;
    const cached: CachedNextSession = JSON.parse(raw);
    if (cached.language !== languageCode) return null;
    if (Date.now() - cached.savedAt > NEXT_SESSION_MAX_AGE_MS) return null;
    return cached.bundle;
  } catch {
    return null;
  }
}

async function clearCachedNextSession(languageCode: string): Promise<void> {
  await AsyncStorage.removeItem(nextSessionKey(languageCode)).catch(() => {});
}

// Build the next session ahead of time and stash it. Best-effort: any failure
// just leaves the previous cache (if any) in place, and the next transition
// falls back to a live fetch. `prefetch=true` suppresses the backend's
// session_built log so a prefetch that's never shown isn't counted as a
// session (mirror of Alif's prefetch flag).
export async function prefetchReviewSession(
  languageCode: string,
  limit: number = 15,
): Promise<void> {
  if (!netStatus.isOnline) return;
  try {
    const bundle = await fetchSessionBundle(languageCode, limit, true);
    const entry: CachedNextSession = {
      language: languageCode,
      bundle,
      savedAt: Date.now(),
    };
    await AsyncStorage.setItem(nextSessionKey(languageCode), JSON.stringify(entry));
  } catch {
    // keep any existing cache
  }
}

// Primary session loader for the review screen. Serves a background-prefetched
// session instantly when one is cached (the common session→session case),
// else fetches live with a timeout and falls back to the cache on failure.
// Either way it kicks off a prefetch of the *following* session so the next
// transition is a cache hit too. `forceFresh` (the "Refresh session" action)
// bypasses the cache and always pulls live.
export async function getReviewSessionResilient(
  languageCode: string,
  limit: number = 15,
  opts: { forceFresh?: boolean } = {},
): Promise<ReviewSessionBundle> {
  if (opts.forceFresh) {
    await clearCachedNextSession(languageCode);
  } else {
    const cached = await readCachedNextSession(languageCode);
    if (cached) {
      // Consume the slot, then refill it in the background.
      await clearCachedNextSession(languageCode);
      void prefetchReviewSession(languageCode, limit);
      return cached;
    }
  }

  try {
    const bundle = await fetchSessionBundle(languageCode, limit, false);
    void prefetchReviewSession(languageCode, limit);
    return bundle;
  } catch (e) {
    // Live fetch failed (timeout / transport). A prefetch kicked during the
    // previous session may have landed in the meantime — a slightly-stale
    // session beats a hard "Network request failed" wall. forceFresh skips
    // this: the user explicitly asked for new material.
    if (!opts.forceFresh) {
      const stale = await readCachedNextSession(languageCode);
      if (stale) return stale;
    }
    throw e;
  }
}

// Raw single-fetch session loader. Retained for callers/tests that want a
// direct fetch without the cache layer; the review screen uses
// getReviewSessionResilient.
export async function getReviewSession(
  languageCode: string,
  limit: number = 15,
): Promise<ReviewSessionBundle> {
  return fetchSessionBundle(languageCode, limit, false);
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
