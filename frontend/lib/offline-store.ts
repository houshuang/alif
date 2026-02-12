import AsyncStorage from "@react-native-async-storage/async-storage";
import type {
  SentenceReviewSession,
  ReviewMode,
  StoryListItem,
  StoryDetail,
  WordLookupResult,
} from "./types";

const KEYS = {
  sessions: (mode: ReviewMode) => `@alif/sessions/${mode}`,
  reviewed: "@alif/reviewed",
  words: "@alif/words",
  stats: "@alif/stats",
  analytics: "@alif/analytics",
  storyLookups: (storyId: number) => `@alif/story-lookups/${storyId}`,
  stories: "@alif/stories",
  storyDetail: (id: number) => `@alif/story-detail/${id}`,
  wordLookups: "@alif/word-lookups",
  lastSessionWords: "@alif/last-session-words",
};

const MAX_CACHED_SESSIONS = 10;

function reviewKey(
  mode: ReviewMode,
  sentenceId: number | null,
  lemmaId: number
): string {
  return `${mode}:${sentenceId ?? "word"}:${lemmaId}`;
}

function legacyReviewKey(
  sessionId: string,
  sentenceId: number | null,
  lemmaId: number
): string {
  return `${sessionId}:${sentenceId}:${lemmaId}`;
}

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
      (item) =>
        !reviewed.has(reviewKey(mode, item.sentence_id, item.primary_lemma_id)) &&
        !reviewed.has(
          legacyReviewKey(
            session.session_id,
            item.sentence_id,
            item.primary_lemma_id
          )
        )
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
  lemmaId: number,
  mode: ReviewMode = "reading"
): Promise<void> {
  const reviewed = await getReviewedSet();
  reviewed.add(reviewKey(mode, sentenceId, lemmaId));
  reviewed.add(legacyReviewKey(sessionId, sentenceId, lemmaId));
  await setJson(KEYS.reviewed, Array.from(reviewed));
}

async function getReviewedSet(): Promise<Set<string>> {
  const arr = (await getJson<string[]>(KEYS.reviewed)) ?? [];
  return new Set(arr);
}

export async function unmarkReviewed(
  sessionId: string,
  sentenceId: number | null,
  lemmaId: number,
  mode: ReviewMode = "reading"
): Promise<void> {
  const reviewed = await getReviewedSet();
  reviewed.delete(reviewKey(mode, sentenceId, lemmaId));
  reviewed.delete(legacyReviewKey(sessionId, sentenceId, lemmaId));
  await setJson(KEYS.reviewed, Array.from(reviewed));
}

export async function invalidateSessions(): Promise<void> {
  await AsyncStorage.multiRemove([
    KEYS.sessions("reading"),
    KEYS.sessions("listening"),
    KEYS.sessions("quiz"),
    KEYS.reviewed,
    KEYS.words,
    KEYS.stats,
    KEYS.analytics,
    KEYS.wordLookups,
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

// --- Story list cache ---

export async function cacheStories(items: StoryListItem[]): Promise<void> {
  await setJson(KEYS.stories, items);
}

export async function getCachedStories(): Promise<StoryListItem[] | null> {
  return getJson<StoryListItem[]>(KEYS.stories);
}

// --- Story detail cache ---

export async function cacheStoryDetail(story: StoryDetail): Promise<void> {
  await setJson(KEYS.storyDetail(story.id), story);
}

export async function getCachedStoryDetail(id: number): Promise<StoryDetail | null> {
  return getJson<StoryDetail>(KEYS.storyDetail(id));
}

export async function updateCachedStoryStatus(id: number, status: string): Promise<void> {
  const story = await getCachedStoryDetail(id);
  if (story) {
    story.status = status as StoryDetail["status"];
    await cacheStoryDetail(story);
  }
  const stories = await getCachedStories();
  if (stories) {
    const idx = stories.findIndex((s) => s.id === id);
    if (idx >= 0) {
      stories[idx] = { ...stories[idx], status: status as StoryListItem["status"] };
      await cacheStories(stories);
    }
  }
}

// --- Word lookup cache ---

export async function cacheWordLookup(lemmaId: number, data: WordLookupResult): Promise<void> {
  const map = (await getJson<Record<string, WordLookupResult>>(KEYS.wordLookups)) ?? {};
  map[String(lemmaId)] = data;
  await setJson(KEYS.wordLookups, map);
}

export async function getCachedWordLookup(lemmaId: number): Promise<WordLookupResult | null> {
  const map = (await getJson<Record<string, WordLookupResult>>(KEYS.wordLookups)) ?? {};
  return map[String(lemmaId)] ?? null;
}

export async function cacheWordLookupBatch(lookups: Record<number, WordLookupResult>): Promise<void> {
  const map = (await getJson<Record<string, WordLookupResult>>(KEYS.wordLookups)) ?? {};
  for (const [id, data] of Object.entries(lookups)) {
    map[id] = data;
  }
  await setJson(KEYS.wordLookups, map);
}

// --- Last session words (for recap) ---

interface LastSessionWordEntry {
  lemma_id: number;
  rating: number;
  reviewed_at: string;
}

export async function saveLastSessionWord(lemmaId: number, rating: number): Promise<void> {
  const entries = (await getJson<LastSessionWordEntry[]>(KEYS.lastSessionWords)) ?? [];
  entries.push({ lemma_id: lemmaId, rating, reviewed_at: new Date().toISOString() });
  await setJson(KEYS.lastSessionWords, entries);
}

export async function getLastSessionWords(): Promise<LastSessionWordEntry[]> {
  return (await getJson<LastSessionWordEntry[]>(KEYS.lastSessionWords)) ?? [];
}

export async function clearLastSessionWords(): Promise<void> {
  await AsyncStorage.removeItem(KEYS.lastSessionWords);
}
