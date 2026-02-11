import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  markReviewed,
  unmarkReviewed,
  getCachedSession,
  cacheSessions,
  invalidateSessions,
  cacheData,
  getCachedData,
  saveStoryLookups,
  getStoryLookups,
  clearStoryLookups,
  cacheWordLookup,
  getCachedWordLookup,
} from "../offline-store";

const store = (AsyncStorage as any)._store;

beforeEach(async () => {
  for (const key of Object.keys(store)) delete store[key];
  jest.clearAllMocks();
});

describe("markReviewed / unmarkReviewed", () => {
  it("marks a sentence as reviewed", async () => {
    await markReviewed("sess-1", 10, 42, "reading");

    const raw = store["@alif/reviewed"];
    const keys: string[] = JSON.parse(raw);
    expect(keys).toContain("reading:10:42");
    expect(keys).toContain("sess-1:10:42");
  });

  it("marks a word-only card as reviewed", async () => {
    await markReviewed("sess-1", null, 42, "reading");

    const keys: string[] = JSON.parse(store["@alif/reviewed"]);
    expect(keys).toContain("reading:word:42");
  });

  it("unmarks a reviewed sentence", async () => {
    await markReviewed("sess-1", 10, 42, "reading");
    await markReviewed("sess-1", 20, 43, "reading");

    await unmarkReviewed("sess-1", 10, 42, "reading");

    const keys: string[] = JSON.parse(store["@alif/reviewed"]);
    expect(keys).not.toContain("reading:10:42");
    expect(keys).not.toContain("sess-1:10:42");
    // Other entry still there
    expect(keys).toContain("reading:20:43");
  });

  it("unmark is idempotent", async () => {
    await markReviewed("sess-1", 10, 42, "reading");
    await unmarkReviewed("sess-1", 10, 42, "reading");
    await unmarkReviewed("sess-1", 10, 42, "reading");

    const keys: string[] = JSON.parse(store["@alif/reviewed"]);
    expect(keys).not.toContain("reading:10:42");
  });

  it("unmark on empty reviewed set does not error", async () => {
    await unmarkReviewed("sess-1", 10, 42, "reading");
    const raw = store["@alif/reviewed"];
    const keys: string[] = JSON.parse(raw);
    expect(keys).toEqual([]);
  });
});

describe("cacheSessions / getCachedSession", () => {
  const makeSession = (id: string, items: any[]) => ({
    session_id: id,
    items,
    total_due_words: items.length,
    covered_due_words: items.length,
    intro_candidates: [],
  });

  it("caches and retrieves a session", async () => {
    const session = makeSession("s-1", [
      { sentence_id: 1, primary_lemma_id: 10, words: [] },
    ]);
    await cacheSessions("reading", [session]);

    const result = await getCachedSession("reading");
    expect(result).not.toBeNull();
    expect(result!.session_id).toBe("s-1");
    expect(result!.items).toHaveLength(1);
  });

  it("filters out reviewed items from cached session", async () => {
    const session = makeSession("s-1", [
      { sentence_id: 1, primary_lemma_id: 10, words: [] },
      { sentence_id: 2, primary_lemma_id: 20, words: [] },
    ]);
    await cacheSessions("reading", [session]);

    await markReviewed("s-1", 1, 10, "reading");

    const result = await getCachedSession("reading");
    expect(result).not.toBeNull();
    expect(result!.items).toHaveLength(1);
    expect(result!.items[0].primary_lemma_id).toBe(20);
  });

  it("returns null when all items reviewed", async () => {
    const session = makeSession("s-1", [
      { sentence_id: 1, primary_lemma_id: 10, words: [] },
    ]);
    await cacheSessions("reading", [session]);
    await markReviewed("s-1", 1, 10, "reading");

    const result = await getCachedSession("reading");
    expect(result).toBeNull();
  });
});

describe("invalidateSessions", () => {
  it("clears all cached data", async () => {
    await cacheData("words", [{ id: 1 }]);
    await cacheData("stats", { total: 10 });
    await markReviewed("s-1", 1, 10, "reading");

    await invalidateSessions();

    expect(await getCachedData("words")).toBeNull();
    expect(await getCachedData("stats")).toBeNull();
    // reviewed set also cleared
    expect(store["@alif/reviewed"]).toBeUndefined();
  });
});

describe("story lookups", () => {
  it("saves and retrieves lookups", async () => {
    await saveStoryLookups(5, new Set([0, 3, 7]), new Set([10, 20]));

    const result = await getStoryLookups(5);
    expect(result).not.toBeNull();
    expect(result!.positions.has(3)).toBe(true);
    expect(result!.lemmaIds.has(20)).toBe(true);
  });

  it("returns null for uncached story", async () => {
    const result = await getStoryLookups(999);
    expect(result).toBeNull();
  });

  it("clears lookups", async () => {
    await saveStoryLookups(5, new Set([0]), new Set([10]));
    await clearStoryLookups(5);

    expect(await getStoryLookups(5)).toBeNull();
  });
});

describe("word lookup cache", () => {
  it("caches and retrieves word lookups", async () => {
    const lookup = {
      lemma_id: 42,
      lemma_ar: "كتاب",
      gloss_en: "book",
      root: "ك.ت.ب",
    } as any;

    await cacheWordLookup(42, lookup);
    const result = await getCachedWordLookup(42);

    expect(result).not.toBeNull();
    expect(result!.gloss_en).toBe("book");
  });

  it("returns null for uncached lookup", async () => {
    expect(await getCachedWordLookup(999)).toBeNull();
  });
});
