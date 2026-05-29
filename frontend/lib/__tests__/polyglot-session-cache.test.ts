import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  prefetchReviewSession,
  getReviewSessionResilient,
} from "../polyglot-api";

// netStatus.isOnline defaults to true (no NetInfo listener attached in tests),
// so prefetch is allowed. expo-constants / async-storage / netinfo are mocked
// via jest.config.js moduleNameMapper.

const KEY = (lang: string) => `@polyglot:nextSession:${lang}`;

const bundle = (tag: string): any => ({
  sentences: [{ sentence_id: 1, target_lemma_id: 9, text: tag, words: [] }],
  intro_cards: [],
});

function mockFetchOnce(): jest.Mock {
  const fn = jest.fn((url: string) =>
    Promise.resolve({
      ok: true,
      json: async () => bundle(url.includes("prefetch=true") ? "prefetch" : "live"),
    }),
  );
  (global as any).fetch = fn;
  return fn;
}

const flush = () => new Promise((r) => setImmediate(r));

beforeEach(() => {
  for (const k of Object.keys((AsyncStorage as any)._store)) {
    delete (AsyncStorage as any)._store[k];
  }
  jest.clearAllMocks();
});

describe("prefetchReviewSession", () => {
  it("caches the next session under a per-language key, fetched with prefetch=true", async () => {
    const fetchMock = mockFetchOnce();
    await prefetchReviewSession("el");

    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("language_code=el");
    expect(calledUrl).toContain("prefetch=true");

    const cached = JSON.parse((AsyncStorage as any)._store[KEY("el")]);
    expect(cached.language).toBe("el");
    expect(cached.bundle.sentences).toHaveLength(1);
    expect(typeof cached.savedAt).toBe("number");
  });

  it("keyed per language — Greek and Latin caches don't collide", async () => {
    mockFetchOnce();
    await prefetchReviewSession("el");
    await prefetchReviewSession("la");
    expect((AsyncStorage as any)._store[KEY("el")]).toBeDefined();
    expect((AsyncStorage as any)._store[KEY("la")]).toBeDefined();
  });
});

describe("getReviewSessionResilient", () => {
  it("serves a cached prefetched session WITHOUT a live session fetch, then refills", async () => {
    // Seed a fresh cache entry.
    (AsyncStorage as any)._store[KEY("el")] = JSON.stringify({
      language: "el",
      bundle: bundle("cached"),
      savedAt: Date.now(),
    });
    const fetchMock = mockFetchOnce();

    const result = await getReviewSessionResilient("el");
    expect(result.sentences[0].text).toBe("cached");
    // The cache slot is consumed...
    expect((AsyncStorage as any)._store[KEY("el")]).toBeUndefined();

    // ...and a background refill prefetch runs (prefetch=true only — no live
    // session fetch was needed for the served session).
    await flush();
    const urls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(urls.every((u) => u.includes("prefetch=true"))).toBe(true);
    expect((AsyncStorage as any)._store[KEY("el")]).toBeDefined();
  });

  it("does a live fetch on a cold cache, then prefetches the following session", async () => {
    const fetchMock = mockFetchOnce();
    const result = await getReviewSessionResilient("el");
    expect(result.sentences[0].text).toBe("live");

    const liveCall = fetchMock.mock.calls[0][0] as string;
    expect(liveCall).not.toContain("prefetch=true");

    await flush();
    expect((AsyncStorage as any)._store[KEY("el")]).toBeDefined();
  });

  it("ignores a stale cache entry and fetches live", async () => {
    (AsyncStorage as any)._store[KEY("el")] = JSON.stringify({
      language: "el",
      bundle: bundle("stale"),
      savedAt: 0, // far in the past → beyond the 15-min recency gate
    });
    mockFetchOnce();
    const result = await getReviewSessionResilient("el");
    expect(result.sentences[0].text).toBe("live");
  });

  it("forceFresh bypasses a fresh cache and fetches live", async () => {
    (AsyncStorage as any)._store[KEY("el")] = JSON.stringify({
      language: "el",
      bundle: bundle("cached"),
      savedAt: Date.now(),
    });
    const fetchMock = mockFetchOnce();
    const result = await getReviewSessionResilient("el", 15, { forceFresh: true });
    expect(result.sentences[0].text).toBe("live");
    expect((fetchMock.mock.calls[0][0] as string)).not.toContain("prefetch=true");
  });

  it("throws when live fetch fails and no cache is available", async () => {
    (global as any).fetch = jest.fn(() => Promise.reject(new Error("Network request failed")));
    await expect(getReviewSessionResilient("el")).rejects.toThrow();
  });
});
