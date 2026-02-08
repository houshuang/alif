import AsyncStorage from "@react-native-async-storage/async-storage";
import type { SentenceReviewSession, ReviewMode } from "./types";

const KEYS = {
  sessions: (mode: ReviewMode) => `@alif/sessions/${mode}`,
  reviewed: "@alif/reviewed",
  words: "@alif/words",
  stats: "@alif/stats",
  analytics: "@alif/analytics",
  storyLookups: (storyId: number) => `@alif/story-lookups/${storyId}`,
};

const MAX_CACHED_SESSIONS = 3;

async function getJson<T>(key: string): Promise<T | null> {
  const raw = await AsyncStorage.getItem(key);
  return raw ? JSON.parse(raw) : null;
}

async function setJson(key: string, value: unknown): Promise<void> {
  await AsyncStorage.setItem(key, JSON.stringify(value));
}

export async function cacheSessions(
  mode: ReviewMode,
  sessions: SentenceReviewSession[]
): Promise<void> {
  const key = KEYS.sessions(mode);
  const existing = (await getJson<SentenceReviewSession[]>(key)) ?? [];
  const existingIds = new Set(existing.map((s) => s.session_id));
  const newSessions = sessions.filter((s) => !existingIds.has(s.session_id));
  const combined = [...existing, ...newSessions].slice(-MAX_CACHED_SESSIONS);
  await setJson(key, combined);
}

export async function getCachedSession(
  mode: ReviewMode
): Promise<SentenceReviewSession | null> {
  const key = KEYS.sessions(mode);
  const sessions = (await getJson<SentenceReviewSession[]>(key)) ?? [];
  const reviewed = await getReviewedSet();

  for (const session of sessions) {
    const remaining = session.items.filter(
      (item) => !reviewed.has(`${session.session_id}:${item.sentence_id}:${item.primary_lemma_id}`)
    );
    if (remaining.length > 0) {
      return { ...session, items: remaining };
    }
  }
  return null;
}

export async function markReviewed(
  sessionId: string,
  sentenceId: number | null,
  lemmaId: number
): Promise<void> {
  const reviewed = await getReviewedSet();
  reviewed.add(`${sessionId}:${sentenceId}:${lemmaId}`);
  await setJson(KEYS.reviewed, Array.from(reviewed));
}

async function getReviewedSet(): Promise<Set<string>> {
  const arr = (await getJson<string[]>(KEYS.reviewed)) ?? [];
  return new Set(arr);
}

export async function invalidateSessions(): Promise<void> {
  await AsyncStorage.multiRemove([
    KEYS.sessions("reading"),
    KEYS.sessions("listening"),
    KEYS.reviewed,
  ]);
}

export async function cacheData(
  type: "words" | "stats" | "analytics",
  data: unknown
): Promise<void> {
  await setJson(KEYS[type], data);
}

export async function getCachedData<T>(
  type: "words" | "stats" | "analytics"
): Promise<T | null> {
  return getJson<T>(KEYS[type]);
}

interface StoryLookupState {
  positions: number[];
  lemmaIds: number[];
}

export async function saveStoryLookups(
  storyId: number,
  positions: Set<number>,
  lemmaIds: Set<number>,
): Promise<void> {
  await setJson(KEYS.storyLookups(storyId), {
    positions: Array.from(positions),
    lemmaIds: Array.from(lemmaIds),
  });
}

export async function getStoryLookups(
  storyId: number,
): Promise<{ positions: Set<number>; lemmaIds: Set<number> } | null> {
  const data = await getJson<StoryLookupState>(KEYS.storyLookups(storyId));
  if (!data) return null;
  return {
    positions: new Set(data.positions),
    lemmaIds: new Set(data.lemmaIds),
  };
}

export async function clearStoryLookups(storyId: number): Promise<void> {
  await AsyncStorage.removeItem(KEYS.storyLookups(storyId));
}
