import AsyncStorage from "@react-native-async-storage/async-storage";
import type {
  SentenceReviewSession,
  ReviewMode,
  StoryListItem,
  StoryDetail,
  WordLookupResult,
} from "./types";

const SESSION_CACHE_VERSION = 2;
const WORD_LOOKUP_CACHE_VERSION = 4;
const WORD_LOOKUP_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

const KEYS = {
  sessions: (mode: ReviewMode) => `@alif/sessions/v${SESSION_CACHE_VERSION}/${mode}`,
  legacySessions: (mode: ReviewMode) => `@alif/sessions/${mode}`,
  reviewed: `@alif/reviewed/v${SESSION_CACHE_VERSION}`,
  legacyReviewed: "@alif/reviewed",
  shownIntros: "@alif/shown-intros",
  words: "@alif/words",
  stats: "@alif/stats",
  analytics: "@alif/analytics",
  storyLookups: (storyId: number) => `@alif/story-lookups/${storyId}`,
  stories: "@alif/stories",
  storyDetail: (id: number) => `@alif/story-detail/${id}`,
  wordLookups: `@alif/word-lookups/v${WORD_LOOKUP_CACHE_VERSION}`,
  wordLookupsLegacy: "@alif/word-lookups",
  lastSessionWords: "@alif/last-session-words",
};

// 24h: short enough that the server's Category 2 rescue card (7-day cooldown)
// can re-fire when we want it to; long enough to cover any plausible session
// resume window after the app is suspended/remounted.
const SHOWN_INTRO_TTL_MS = 24 * 60 * 60 * 1000;

const MAX_CACHED_SESSIONS = 20;
const SESSION_STALENESS_MS = 30 * 60 * 1000; // 30 minutes

interface CachedSessionEntry {
  session: SentenceReviewSession;
  cached_at: number; // Date.now() timestamp
}

let sessionCacheLock: Promise<void> = Promise.resolve();

async function withSessionCacheLock<T>(fn: () => Promise<T>): Promise<T> {
  const previous = sessionCacheLock;
  let release!: () => void;
  sessionCacheLock = new Promise<void>((resolve) => {
    release = resolve;
  });
  await previous;
  try {
    return await fn();
  } finally {
    release();
  }
}

function normalizeSessionEntries(
  raw: CachedSessionEntry[] | SentenceReviewSession[]
): CachedSessionEntry[] {
  return raw.map((item: any) =>
    item.session
      ? (item as CachedSessionEntry)
      : { session: item as SentenceReviewSession, cached_at: 0 }
  );
}

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

function itemSentenceIds(item: {
  sentence_id: number | null;
  sentence_ids?: number[];
  passage_sentences?: { sentence_id: number }[];
}): (number | null)[] {
  const ids = item.sentence_ids?.length
    ? item.sentence_ids
    : item.passage_sentences?.map((s) => s.sentence_id);
  return ids?.length ? ids : [item.sentence_id];
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
  await withSessionCacheLock(async () => {
    const key = KEYS.sessions(mode);
    const existing = normalizeSessionEntries(
      (await getJson<CachedSessionEntry[] | SentenceReviewSession[]>(key)) ?? []
    );
    const existingIds = new Set(existing.map((e) => e.session.session_id));
    const now = Date.now();
    const newEntries: CachedSessionEntry[] = sessions
      .filter((s) => !existingIds.has(s.session_id))
      .map((s) => ({ session: s, cached_at: now }));
    const freshExisting = existing.filter(
      (e) => e.cached_at === 0 || now - e.cached_at <= SESSION_STALENESS_MS
    );
    const combined = [...freshExisting, ...newEntries].slice(-MAX_CACHED_SESSIONS);
    await setJson(key, combined);
  });
}

export async function getCachedSession(
  mode: ReviewMode,
  allowStale: boolean = false,
  minRemaining: number = 1,
): Promise<SentenceReviewSession | null> {
  const key = KEYS.sessions(mode);
  const raw = (await getJson<CachedSessionEntry[] | SentenceReviewSession[]>(key)) ?? [];
  const reviewed = await getReviewedSet();
  const shownIntros = await getShownIntroLemmaIds();
  const now = Date.now();

  const entries = normalizeSessionEntries(raw);

  // Prefer the newest cached session. Older partial sessions are often
  // abandoned tails after the user manually wrapped up.
  for (const entry of [...entries].reverse()) {
    // Skip stale sessions (> 30 min old) unless explicitly allowed (e.g. offline)
    if (!allowStale && entry.cached_at > 0 && now - entry.cached_at > SESSION_STALENESS_MS) {
      continue;
    }

    const session = entry.session;
    const remaining = session.items.filter(
      (item) => !itemSentenceIds(item).some((sentenceId) =>
        reviewed.has(reviewKey(mode, sentenceId, item.primary_lemma_id)) ||
        reviewed.has(legacyReviewKey(session.session_id, sentenceId, item.primary_lemma_id))
      )
    );
    if (remaining.length >= minRemaining) {
      // Strip intros the user already dismissed (24h window). Otherwise a
      // session resumed after iOS suspend/restart will replay them.
      const filteredIntros = (session.experiment_intro_cards ?? []).filter(
        (c) => !shownIntros.has(c.lemma_id),
      );
      return {
        ...session,
        items: remaining,
        experiment_intro_cards: filteredIntros,
      };
    }
  }
  // All sessions exhausted — prune stale reviewed keys in background
  pruneReviewedSet().catch(() => {});
  return null;
}

export async function dropCachedSession(
  mode: ReviewMode,
  sessionId: string,
): Promise<void> {
  await withSessionCacheLock(async () => {
    const key = KEYS.sessions(mode);
    const raw = (await getJson<CachedSessionEntry[] | SentenceReviewSession[]>(key)) ?? [];
    const entries = normalizeSessionEntries(raw);
    const filtered = entries.filter((e) => e.session.session_id !== sessionId);
    await setJson(key, filtered);
  });
}

export async function getCachedSentenceIds(
  mode: ReviewMode
): Promise<Set<number>> {
  const key = KEYS.sessions(mode);
  const raw = (await getJson<CachedSessionEntry[] | SentenceReviewSession[]>(key)) ?? [];
  const entries = normalizeSessionEntries(raw);
  const ids = new Set<number>();
  for (const entry of entries) {
    for (const item of entry.session.items) {
      for (const sentenceId of itemSentenceIds(item)) {
        if (sentenceId != null) ids.add(sentenceId);
      }
    }
  }
  return ids;
}

export async function markReviewed(
  sessionId: string,
  sentenceId: number | null,
  lemmaId: number,
  mode: ReviewMode = "reading",
  sentenceIds?: number[]
): Promise<void> {
  const reviewed = await getReviewedSet();
  const ids = sentenceIds?.length ? sentenceIds : [sentenceId];
  for (const id of ids) {
    reviewed.add(reviewKey(mode, id, lemmaId));
    reviewed.add(legacyReviewKey(sessionId, id, lemmaId));
  }
  await setJson(KEYS.reviewed, Array.from(reviewed));
}

async function getReviewedSet(): Promise<Set<string>> {
  const arr = (await getJson<string[]>(KEYS.reviewed)) ?? [];
  return new Set(arr);
}

// --- Shown intro tracking -----------------------------------------------
// Persisted across app restarts so cached sessions don't re-fire intros the
// user already dismissed. Bug (2026-05-06): a useRef-only dedup reset on
// component remount; iOS app suspend/restore looked like a fresh mount, and
// AsyncStorage replayed the cached session which still had intros for already-
// acked lemmas. Server filters once `experiment_intro_shown_at` is set, but
// only on FRESH responses — cached responses bypass that filter entirely.

export async function markIntroShown(lemmaId: number): Promise<void> {
  const map = await getShownIntroMap();
  map.set(lemmaId, Date.now());
  await persistShownIntroMap(map);
}

export async function getShownIntroLemmaIds(): Promise<Set<number>> {
  const map = await getShownIntroMap();
  return new Set(map.keys());
}

async function getShownIntroMap(): Promise<Map<number, number>> {
  const raw = (await getJson<[number, number][]>(KEYS.shownIntros)) ?? [];
  const cutoff = Date.now() - SHOWN_INTRO_TTL_MS;
  const map = new Map<number, number>();
  for (const [lid, ts] of raw) {
    if (ts >= cutoff) map.set(lid, ts);
  }
  return map;
}

async function persistShownIntroMap(map: Map<number, number>): Promise<void> {
  const cutoff = Date.now() - SHOWN_INTRO_TTL_MS;
  const arr = Array.from(map.entries()).filter(([, ts]) => ts >= cutoff);
  await setJson(KEYS.shownIntros, arr);
}

export async function unmarkReviewed(
  sessionId: string,
  sentenceId: number | null,
  lemmaId: number,
  mode: ReviewMode = "reading",
  sentenceIds?: number[]
): Promise<void> {
  const reviewed = await getReviewedSet();
  const ids = sentenceIds?.length ? sentenceIds : [sentenceId];
  for (const id of ids) {
    reviewed.delete(reviewKey(mode, id, lemmaId));
    reviewed.delete(legacyReviewKey(sessionId, id, lemmaId));
  }
  await setJson(KEYS.reviewed, Array.from(reviewed));
}

const REVIEWED_MAX_SIZE = 1000;

export async function pruneReviewedSet(): Promise<void> {
  const reviewed = await getReviewedSet();
  if (reviewed.size <= REVIEWED_MAX_SIZE) return;

  // Collect all valid keys from cached sessions across all modes
  const validKeys = new Set<string>();
  for (const mode of ["reading", "listening", "quiz"] as ReviewMode[]) {
    const key = KEYS.sessions(mode);
    const raw = (await getJson<CachedSessionEntry[] | SentenceReviewSession[]>(key)) ?? [];
    const entries = normalizeSessionEntries(raw);
    for (const entry of entries) {
      const session = entry.session;
      for (const item of session.items) {
        for (const sentenceId of itemSentenceIds(item)) {
          validKeys.add(reviewKey(mode, sentenceId, item.primary_lemma_id));
          validKeys.add(legacyReviewKey(session.session_id, sentenceId, item.primary_lemma_id));
        }
      }
    }
  }

  // Keep only keys that match cached sessions
  const pruned = new Set<string>();
  for (const k of reviewed) {
    if (validKeys.has(k)) pruned.add(k);
  }
  await setJson(KEYS.reviewed, Array.from(pruned));
}

export async function invalidateSessions(): Promise<void> {
  await AsyncStorage.multiRemove([
    KEYS.sessions("reading"),
    KEYS.sessions("listening"),
    KEYS.sessions("quiz"),
    KEYS.legacySessions("reading"),
    KEYS.legacySessions("listening"),
    KEYS.legacySessions("quiz"),
    KEYS.reviewed,
    KEYS.legacyReviewed,
    KEYS.words,
    KEYS.stats,
    KEYS.analytics,
    KEYS.wordLookups,
    KEYS.wordLookupsLegacy,
  ]);
}

export async function invalidateDataCaches(): Promise<void> {
  await AsyncStorage.multiRemove([
    KEYS.words,
    KEYS.stats,
    KEYS.analytics,
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

// --- Word lookup cache (versioned + TTL) ---

interface CachedWordLookupEntry {
  data: WordLookupResult;
  cached_at: number;
}

export async function cacheWordLookup(lemmaId: number, data: WordLookupResult): Promise<void> {
  const map = (await getJson<Record<string, CachedWordLookupEntry>>(KEYS.wordLookups)) ?? {};
  map[String(lemmaId)] = { data, cached_at: Date.now() };
  await setJson(KEYS.wordLookups, map);
}

export async function getCachedWordLookup(
  lemmaId: number,
  allowStale: boolean = false,
): Promise<WordLookupResult | null> {
  const map = (await getJson<Record<string, CachedWordLookupEntry>>(KEYS.wordLookups)) ?? {};
  const entry = map[String(lemmaId)];
  if (!entry) return null;
  if (!allowStale && Date.now() - entry.cached_at > WORD_LOOKUP_TTL_MS) {
    return null;
  }
  return entry.data;
}

export async function cacheWordLookupBatch(lookups: Record<number, WordLookupResult>): Promise<void> {
  const map = (await getJson<Record<string, CachedWordLookupEntry>>(KEYS.wordLookups)) ?? {};
  const now = Date.now();
  for (const [id, data] of Object.entries(lookups)) {
    map[id] = { data, cached_at: now };
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
