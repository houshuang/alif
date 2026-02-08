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
} from "./types";
import { netStatus } from "./net-status";
import {
  cacheSessions,
  getCachedSession,
  markReviewed,
  cacheData,
  getCachedData,
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
    flushQueue().catch(() => {});
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
    frequency_rank: w.frequency_rank,
    times_reviewed: w.times_seen || 0,
    correct_count: w.times_correct || 0,
    root_family: (w.root_family || []).map((f: any) => ({
      id: f.id,
      arabic: f.arabic,
      english: f.english,
    })),
    review_history: (w.review_history || []),
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

export async function getSentenceReviewSession(
  mode: ReviewMode = "reading"
): Promise<SentenceReviewSession> {
  try {
    const data = await fetchApi<any>(
      `/api/review/next-sentences?limit=10&mode=${mode}`
    );
    const session = { ...data, session_id: generateSessionId() };
    cacheSessions(mode, [session]).catch(() => {});
    prefetchSessions(mode).catch(() => {});
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

  await markReviewed(
    submission.session_id,
    submission.sentence_id,
    submission.primary_lemma_id
  );

  await enqueueReview("sentence", {
    sentence_id: submission.sentence_id,
    primary_lemma_id: submission.primary_lemma_id,
    comprehension_signal: submission.comprehension_signal,
    missed_lemma_ids: submission.missed_lemma_ids,
    response_ms: submission.response_ms,
    session_id: submission.session_id,
    review_mode: submission.review_mode,
  }, clientReviewId);

  if (netStatus.isOnline) {
    flushQueue().catch(() => {});
  }

  return { word_results: [] };
}

export async function prefetchSessions(mode: ReviewMode): Promise<void> {
  try {
    const data = await fetchApi<any>(
      `/api/review/next-sentences?limit=10&mode=${mode}`
    );
    const session = { ...data, session_id: generateSessionId() };
    await cacheSessions(mode, [session]);
  } catch {}
}
