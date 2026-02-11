import Constants from "expo-constants";
import {
  ReviewSession,
  ReviewSubmission,
  ReviewMode,
  ReviewCard,
  SentenceReviewSession,
  SentenceReviewSubmission,
  Word,
  WordDetail,
  Stats,
  Analytics,
  LearnCandidate,
  IntroduceResult,
  StoryListItem,
  StoryDetail,
  StoryLookupResult,
  WordLookupResult,
  AskAIResponse,
  ConversationSummary,
  ConversationDetail,
  GrammarLesson,
  GrammarProgress,
  DeepAnalytics,
  BatchUploadResult,
  BatchSummary,
} from "./types";
import { netStatus } from "./net-status";
import {
  cacheSessions,
  getCachedSession,
  markReviewed,
  cacheData,
  getCachedData,
  cacheStories,
  getCachedStories,
  cacheStoryDetail,
  getCachedStoryDetail,
  updateCachedStoryStatus,
  cacheWordLookup,
  getCachedWordLookup,
  cacheWordLookupBatch,
} from "./offline-store";
import { enqueueReview, flushQueue } from "./sync-queue";

export const BASE_URL =
  Constants.expoConfig?.extra?.apiUrl ?? "http://localhost:8000";

function generateSessionId(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function generateUuid(): string {
  return generateSessionId();
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export async function getReviewSession(
  mode: ReviewMode = "reading"
): Promise<ReviewSession> {
  const endpoint =
    mode === "listening" ? "/api/review/next-listening" : "/api/review/next";
  const raw = await fetchApi<any[]>(endpoint);

  const cards: ReviewCard[] = raw.map((c) => ({
    lemma_id: c.lemma_id,
    lemma_ar: c.lemma_ar,
    lemma_ar_bare: c.lemma_ar_bare,
    gloss_en: c.gloss_en || "",
    root: null,
    pos: c.knowledge_state || "",
    sentence: null,
  }));

  return {
    cards,
    session_id: generateSessionId(),
    total_due: cards.length,
  };
}

export async function submitReview(submission: ReviewSubmission): Promise<void> {
  const clientReviewId = submission.client_review_id || generateUuid();
  await enqueueReview("legacy", {
    lemma_id: submission.lemma_id,
    rating: submission.rating,
    response_ms: submission.response_ms,
    session_id: submission.session_id,
    review_mode: submission.review_mode,
    comprehension_signal: submission.comprehension_signal,
  }, clientReviewId);

  if (netStatus.isOnline) {
    flushQueue().catch((e) => console.warn("sync flush failed:", e));
  }
}

export async function getWords(): Promise<Word[]> {
  try {
    const raw = await fetchApi<any[]>("/api/words?limit=200");
    const words = raw.map((w) => ({
      id: w.lemma_id,
      arabic: w.lemma_ar,
      english: w.gloss_en || "",
      transliteration: w.transliteration || "",
      root: w.root,
      pos: w.pos || "",
      state: w.knowledge_state || "new",
      due_date: null,
      times_seen: w.times_seen || 0,
      times_correct: w.times_correct || 0,
      last_reviewed: w.last_reviewed || null,
      knowledge_score: w.knowledge_score || 0,
      frequency_rank: w.frequency_rank ?? null,
      cefr_level: w.cefr_level ?? null,
    }));
    cacheData("words", words).catch(() => {});
    return words;
  } catch (e) {
    const cached = await getCachedData<Word[]>("words");
    if (cached) return cached;
    throw e;
  }
}

export async function getWordDetail(id: number): Promise<WordDetail> {
  const w = await fetchApi<any>(`/api/words/${id}`);
  return {
    id: w.lemma_id,
    arabic: w.lemma_ar,
    english: w.gloss_en || "",
    transliteration: w.transliteration || "",
    root: w.root,
    pos: w.pos || "",
    state: w.knowledge_state || "new",
    due_date: null,
    times_seen: w.times_seen || 0,
    times_correct: w.times_correct || 0,
    last_reviewed: w.last_reviewed || null,
    knowledge_score: w.knowledge_score || 0,
    frequency_rank: w.frequency_rank ?? null,
    cefr_level: w.cefr_level ?? null,
    times_reviewed: w.times_seen || 0,
    correct_count: w.times_correct || 0,
    forms_json: w.forms_json || null,
    grammar_features: (w.grammar_features || []).map((g: any) => ({
      feature_key: g.feature_key,
      category: g.category ?? null,
      label_en: g.label_en || g.feature_key,
      label_ar: g.label_ar ?? null,
    })),
    root_family: (w.root_family || []).map((f: any) => ({
      id: f.id,
      arabic: f.arabic,
      english: f.english,
    })),
    review_history: (w.review_history || []),
    sentence_stats: (w.sentence_stats || []).map((s: any) => ({
      sentence_id: s.sentence_id,
      surface_forms: s.surface_forms || [],
      sentence_arabic: s.sentence_arabic || "",
      sentence_english: s.sentence_english ?? null,
      sentence_transliteration: s.sentence_transliteration ?? null,
      seen_count: s.seen_count || 0,
      missed_count: s.missed_count || 0,
      confused_count: s.confused_count || 0,
      understood_count: s.understood_count || 0,
      primary_count: s.primary_count || 0,
      collateral_count: s.collateral_count || 0,
      accuracy_pct: s.accuracy_pct ?? null,
      last_reviewed_at: s.last_reviewed_at ?? null,
    })),
  };
}

export async function getStats(): Promise<Stats> {
  try {
    const raw = await fetchApi<any>("/api/stats");
    const stats = {
      total_words: raw.total_words,
      known_words: raw.known,
      learning_words: raw.learning,
      new_words: raw.new,
      due_today: raw.due_today,
      reviews_today: raw.reviews_today,
      streak_days: 0,
      total_reviews: raw.total_reviews ?? 0,
      lapsed: raw.lapsed ?? 0,
    };
    cacheData("stats", stats).catch(() => {});
    return stats;
  } catch (e) {
    const cached = await getCachedData<Stats>("stats");
    if (cached) return cached;
    throw e;
  }
}

export async function getAnalytics(): Promise<Analytics> {
  try {
    const data = await fetchApi<Analytics>("/api/stats/analytics");
    cacheData("analytics", data).catch(() => {});
    return data;
  } catch (e) {
    const cached = await getCachedData<Analytics>("analytics");
    if (cached) return cached;
    throw e;
  }
}

export async function getNextWords(
  count: number = 3
): Promise<LearnCandidate[]> {
  const data = await fetchApi<{ words: LearnCandidate[] }>(
    `/api/learn/next-words?count=${count}`
  );
  return data.words;
}

export async function introduceWord(
  lemmaId: number
): Promise<IntroduceResult> {
  return fetchApi<IntroduceResult>("/api/learn/introduce", {
    method: "POST",
    body: JSON.stringify({ lemma_id: lemmaId }),
  });
}

export async function submitQuizResult(
  lemmaId: number,
  gotIt: boolean
): Promise<{ lemma_id: number; new_state: string; next_due: string }> {
  return fetchApi("/api/learn/quiz-result", {
    method: "POST",
    body: JSON.stringify({ lemma_id: lemmaId, got_it: gotIt }),
  });
}

export async function suspendWord(lemmaId: number): Promise<{ lemma_id: number; state: string }> {
  return fetchApi("/api/learn/suspend", {
    method: "POST",
    body: JSON.stringify({ lemma_id: lemmaId }),
  });
}

export async function getLemmaSentence(
  lemmaId: number
): Promise<{ ready: boolean; sentence: any | null }> {
  return fetchApi(`/api/learn/sentences/${lemmaId}`);
}

export async function getSentenceReviewSession(
  mode: ReviewMode = "reading"
): Promise<SentenceReviewSession> {
  try {
    const data = await fetchApi<any>(
      `/api/review/next-sentences?limit=10&mode=${mode}`
    );
    const session = { ...data, session_id: data.session_id || generateSessionId() };
    cacheSessions(mode, [session]).catch(() => {});
    deepPrefetchSessions(mode).catch(() => {});
    prefetchWordLookupsForSession(session).catch(() => {});
    return session;
  } catch (e) {
    const cached = await getCachedSession(mode);
    if (cached) return cached;
    throw e;
  }
}

export async function submitSentenceReview(
  submission: SentenceReviewSubmission
): Promise<{ word_results: any[] }> {
  const clientReviewId = submission.client_review_id || generateUuid();

  await enqueueReview("sentence", {
    sentence_id: submission.sentence_id,
    primary_lemma_id: submission.primary_lemma_id,
    comprehension_signal: submission.comprehension_signal,
    missed_lemma_ids: submission.missed_lemma_ids,
    confused_lemma_ids: submission.confused_lemma_ids,
    response_ms: submission.response_ms,
    session_id: submission.session_id,
    review_mode: submission.review_mode,
    audio_play_count: submission.audio_play_count,
    lookup_count: submission.lookup_count,
  }, clientReviewId);

  await markReviewed(
    submission.session_id,
    submission.sentence_id,
    submission.primary_lemma_id,
    submission.review_mode
  );

  if (netStatus.isOnline) {
    flushQueue().catch((e) => console.warn("sync flush failed:", e));
  }

  return { word_results: [] };
}

export async function prefetchSessions(mode: ReviewMode): Promise<void> {
  try {
    const data = await fetchApi<any>(
      `/api/review/next-sentences?limit=10&mode=${mode}&prefetch=true`
    );
    const session = { ...data, session_id: data.session_id || generateSessionId() };
    await cacheSessions(mode, [session]);
  } catch {}
}

export async function deepPrefetchSessions(mode: ReviewMode, count: number = 3): Promise<void> {
  for (let i = 0; i < count; i++) {
    try {
      const data = await fetchApi<any>(
        `/api/review/next-sentences?limit=10&mode=${mode}&prefetch=true`
      );
      const session = { ...data, session_id: data.session_id || generateSessionId() };
      await cacheSessions(mode, [session]);
      prefetchWordLookupsForSession(session).catch(() => {});
    } catch {
      break;
    }
  }
}

export async function prefetchWordLookupsForSession(
  session: SentenceReviewSession
): Promise<void> {
  const lemmaIds = new Set<number>();
  for (const item of session.items) {
    for (const word of item.words) {
      if (word.lemma_id != null) {
        lemmaIds.add(word.lemma_id);
      }
    }
  }

  const ids = Array.from(lemmaIds);
  const BATCH_SIZE = 5;
  const results: Record<number, WordLookupResult> = {};

  for (let i = 0; i < ids.length; i += BATCH_SIZE) {
    const batch = ids.slice(i, i + BATCH_SIZE);
    const promises = batch.map(async (id) => {
      const cached = await getCachedWordLookup(id);
      if (cached) return;
      try {
        const result = await fetchApi<WordLookupResult>(`/api/review/word-lookup/${id}`);
        results[id] = result;
      } catch {}
    });
    await Promise.all(promises);
  }

  if (Object.keys(results).length > 0) {
    await cacheWordLookupBatch(results);
  }
}

// --- Stories ---

export async function getStories(): Promise<StoryListItem[]> {
  try {
    const data = await fetchApi<StoryListItem[]>("/api/stories");
    cacheStories(data).catch(() => {});
    return data;
  } catch (e) {
    const cached = await getCachedStories();
    if (cached) return cached;
    throw e;
  }
}

export async function getStoryDetail(id: number): Promise<StoryDetail> {
  try {
    const data = await fetchApi<StoryDetail>(`/api/stories/${id}`);
    cacheStoryDetail(data).catch(() => {});
    return data;
  } catch (e) {
    const cached = await getCachedStoryDetail(id);
    if (cached) return cached;
    throw e;
  }
}

export async function generateStory(opts?: {
  difficulty?: string;
  length?: string;
  topic?: string;
}): Promise<StoryDetail> {
  return fetchApi<StoryDetail>("/api/stories/generate", {
    method: "POST",
    body: JSON.stringify({
      difficulty: opts?.difficulty || "beginner",
      length: opts?.length || "medium",
      topic: opts?.topic || null,
    }),
  });
}

export async function importStory(arabicText: string, title?: string): Promise<StoryDetail> {
  return fetchApi<StoryDetail>("/api/stories/import", {
    method: "POST",
    body: JSON.stringify({ arabic_text: arabicText, title }),
  });
}

function isLikelyNetworkError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const msg = error.message.toLowerCase();
  return (
    msg.includes("network request failed") ||
    msg.includes("failed to fetch") ||
    msg.includes("network error") ||
    msg.includes("request timed out")
  );
}

type StoryAction = "complete" | "skip" | "too-difficult";
type StoryQueueType = "story_complete" | "story_skip" | "story_too_difficult";

async function postStoryAction(
  storyId: number,
  action: StoryAction,
  queueType: StoryQueueType,
  lookedUpLemmaIds: number[],
  readingTimeMs?: number
): Promise<"synced" | "queued"> {
  const body = {
    looked_up_lemma_ids: lookedUpLemmaIds,
    reading_time_ms: readingTimeMs,
  };

  try {
    const res = await fetch(`${BASE_URL}/api/stories/${storyId}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok || res.status === 409) {
      return "synced";
    }
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`API error ${res.status}: ${text}`);
  } catch (e) {
    if (!isLikelyNetworkError(e)) {
      throw e;
    }
    await enqueueReview(queueType, {
      story_id: storyId,
      looked_up_lemma_ids: lookedUpLemmaIds,
      reading_time_ms: readingTimeMs,
    }, generateUuid());
    if (netStatus.isOnline) {
      flushQueue().catch(() => {});
    }
    return "queued";
  }
}

export async function completeStory(storyId: number, lookedUpLemmaIds: number[], readingTimeMs?: number): Promise<void> {
  const result = await postStoryAction(
    storyId,
    "complete",
    "story_complete",
    lookedUpLemmaIds,
    readingTimeMs
  );
  if (result === "synced") {
    updateCachedStoryStatus(storyId, "completed").catch(() => {});
  }
}

export async function skipStory(storyId: number, lookedUpLemmaIds: number[], readingTimeMs?: number): Promise<void> {
  const result = await postStoryAction(
    storyId,
    "skip",
    "story_skip",
    lookedUpLemmaIds,
    readingTimeMs
  );
  if (result === "synced") {
    updateCachedStoryStatus(storyId, "skipped").catch(() => {});
  }
}

export async function tooDifficultStory(storyId: number, lookedUpLemmaIds: number[], readingTimeMs?: number): Promise<void> {
  const result = await postStoryAction(
    storyId,
    "too-difficult",
    "story_too_difficult",
    lookedUpLemmaIds,
    readingTimeMs
  );
  if (result === "synced") {
    updateCachedStoryStatus(storyId, "too_difficult").catch(() => {});
  }
}

export async function deleteStory(storyId: number): Promise<void> {
  await fetchApi(`/api/stories/${storyId}`, { method: "DELETE" });
}

export async function lookupStoryWord(storyId: number, lemmaId: number, position: number): Promise<StoryLookupResult> {
  return fetchApi<StoryLookupResult>(`/api/stories/${storyId}/lookup`, {
    method: "POST",
    body: JSON.stringify({ lemma_id: lemmaId, position }),
  });
}

export async function getStoryReadiness(storyId: number): Promise<{ readiness_pct: number; unknown_count: number }> {
  return fetchApi(`/api/stories/${storyId}/readiness`);
}

export async function prefetchStoryDetails(stories: StoryListItem[]): Promise<void> {
  for (const story of stories) {
    try {
      const data = await fetchApi<StoryDetail>(`/api/stories/${story.id}`);
      await cacheStoryDetail(data);
    } catch {
      break;
    }
  }
}

// --- Review word lookup (with cache) ---

export async function lookupReviewWord(lemmaId: number): Promise<WordLookupResult> {
  const cached = await getCachedWordLookup(lemmaId);
  if (cached) return cached;
  try {
    const result = await fetchApi<WordLookupResult>(`/api/review/word-lookup/${lemmaId}`);
    cacheWordLookup(lemmaId, result).catch(() => {});
    return result;
  } catch (e) {
    if (cached) return cached;
    throw e;
  }
}

// --- Chat ---

export async function askAI(
  question: string,
  context: string,
  screen: string,
  conversationId?: string
): Promise<AskAIResponse> {
  return fetchApi<AskAIResponse>("/api/chat/ask", {
    method: "POST",
    body: JSON.stringify({ question, context, screen, conversation_id: conversationId }),
  });
}

export async function getChatConversations(): Promise<ConversationSummary[]> {
  return fetchApi<ConversationSummary[]>("/api/chat/conversations");
}

export async function getChatConversation(conversationId: string): Promise<ConversationDetail> {
  return fetchApi<ConversationDetail>(`/api/chat/conversations/${conversationId}`);
}

// --- Grammar ---

export async function getGrammarLesson(featureKey: string): Promise<GrammarLesson> {
  return fetchApi<GrammarLesson>(`/api/grammar/lesson/${featureKey}`);
}

export async function introduceGrammarFeature(featureKey: string): Promise<{ feature_key: string; introduced_at: string }> {
  return fetchApi("/api/grammar/introduce", {
    method: "POST",
    body: JSON.stringify({ feature_key: featureKey }),
  });
}

export async function getConfusedGrammarFeatures(): Promise<{ features: GrammarLesson[] }> {
  return fetchApi("/api/grammar/confused");
}

export async function getGrammarProgress(): Promise<GrammarProgress[]> {
  const data = await fetchApi<{ progress: GrammarProgress[] }>("/api/grammar/progress");
  return data.progress;
}

export async function getDeepAnalytics(): Promise<DeepAnalytics> {
  return fetchApi<DeepAnalytics>("/api/stats/deep-analytics");
}

export async function submitReintroResult(
  lemmaId: number,
  result: "remember" | "show_again",
  sessionId?: string,
  clientReviewId?: string,
): Promise<{ status: string; result: string; lemma_id: number }> {
  return fetchApi("/api/review/reintro-result", {
    method: "POST",
    body: JSON.stringify({
      lemma_id: lemmaId,
      result,
      session_id: sessionId,
      client_review_id: clientReviewId || generateUuid(),
    }),
  });
}

// --- OCR / Textbook Scanner ---

export async function scanTextbookPages(imageUris: string[]): Promise<BatchUploadResult> {
  const formData = new FormData();
  for (const uri of imageUris) {
    const filename = uri.split("/").pop() || "page.jpg";
    const match = /\.(\w+)$/.exec(filename);
    const type = match ? `image/${match[1]}` : "image/jpeg";
    formData.append("files", { uri, name: filename, type } as any);
  }

  const res = await fetch(`${BASE_URL}/api/ocr/scan-pages`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export async function getBatchStatus(batchId: string): Promise<BatchUploadResult> {
  return fetchApi<BatchUploadResult>(`/api/ocr/batch/${batchId}`);
}

export async function getUploadHistory(): Promise<{ batches: BatchSummary[] }> {
  return fetchApi(`/api/ocr/uploads`);
}

export async function extractTextFromImage(imageUri: string): Promise<string> {
  const formData = new FormData();
  const filename = imageUri.split("/").pop() || "image.jpg";
  const match = /\.(\w+)$/.exec(filename);
  const type = match ? `image/${match[1]}` : "image/jpeg";
  formData.append("file", { uri: imageUri, name: filename, type } as any);

  const res = await fetch(`${BASE_URL}/api/ocr/extract-text`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`API error ${res.status}: ${text}`);
  }
  const data = await res.json();
  return data.extracted_text;
}

// --- Word management ---

export async function suspendWord(lemmaId: number): Promise<{ lemma_id: number; state: string }> {
  return fetchApi(`/api/words/${lemmaId}/suspend`, { method: "POST" });
}

export async function unsuspendWord(lemmaId: number): Promise<{ lemma_id: number; state: string }> {
  return fetchApi(`/api/words/${lemmaId}/unsuspend`, { method: "POST" });
}

// --- Content flags ---

export async function flagContent(data: {
  content_type: string;
  lemma_id?: number;
  sentence_id?: number;
}): Promise<{ flag_id: number; status: string }> {
  return fetchApi("/api/flags", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function getFlags(status?: string): Promise<any[]> {
  const params = status ? `?status=${status}` : "";
  return fetchApi(`/api/flags${params}`);
}

// --- Activity log ---

export interface ActivityEntry {
  id: number;
  event_type: string;
  summary: string;
  detail_json: any;
  created_at: string;
}

export async function getActivity(limit: number = 20): Promise<ActivityEntry[]> {
  return fetchApi<ActivityEntry[]>(`/api/activity?limit=${limit}`);
}
