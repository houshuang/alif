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
  it("fetches live even when a fresh cache exists — the prefetch is a fallback only", async () => {
    // A prefetched bundle was built before the current session's reviews, so
    // serving it cache-first would re-show the just-finished session. The
    // happy path must always fetch live.
    (AsyncStorage as any)._store[KEY("el")] = JSON.stringify({
      language: "el",
      bundle: bundle("cached"),
      savedAt: Date.now(),
    });
    const fetchMock = mockFetchOnce();

    const result = await getReviewSessionResilient("el");
    expect(result.sentences[0].text).toBe("live");

    // The live fetch happens first (non-prefetch URL), and the stale slot is
    // dropped then refilled with a fresh fallback for the next transition.
    expect((fetchMock.mock.calls[0][0] as string)).not.toContain("prefetch=true");
    await flush();
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

  it("falls back to a prefetched session when the live fetch fails", async () => {
    (AsyncStorage as any)._store[KEY("el")] = JSON.stringify({
      language: "el",
      bundle: bundle("cached"),
      savedAt: Date.now(),
    });
    (global as any).fetch = jest.fn(() =>
      Promise.reject(new Error("Network request failed")),
    );

    const result = await getReviewSessionResilient("el");
    expect(result.sentences[0].text).toBe("cached");
    // The consumed fallback is dropped so a retry doesn't re-serve it blindly.
    expect((AsyncStorage as any)._store[KEY("el")]).toBeUndefined();
  });

  it("forceFresh fetches live", async () => {
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

  it("forceFresh does NOT fall back to a stale cache on failure", async () => {
    (AsyncStorage as any)._store[KEY("el")] = JSON.stringify({
      language: "el",
      bundle: bundle("cached"),
      savedAt: Date.now(),
    });
    (global as any).fetch = jest.fn(() =>
      Promise.reject(new Error("Network request failed")),
    );
    await expect(
      getReviewSessionResilient("el", 15, { forceFresh: true }),
    ).rejects.toThrow();
  });

  it("throws when live fetch fails and no cache is available", async () => {
    (global as any).fetch = jest.fn(() => Promise.reject(new Error("Network request failed")));
    await expect(getReviewSessionResilient("el")).rejects.toThrow();
  });
});

// A fetch mock that fails the session-bundle call (with a chosen error) but lets
// the telemetry beacon through, capturing its parsed body for assertions.
function mockSessionFailingWithBeacon(sessionError: any): { beacons: any[] } {
  const beacons: any[] = [];
  (global as any).fetch = jest.fn((url: string, init?: any) => {
    if (url.includes("session-fetch-event")) {
      beacons.push(JSON.parse(init.body));
      return Promise.resolve({ ok: true, json: async () => ({ logged: true }) });
    }
    return Promise.reject(sessionError);
  });
  return { beacons };
}

describe("getReviewSessionResilient — failure telemetry", () => {
  it("beacons session_fetch_failed with no prefetch, then rethrows", async () => {
    const { beacons } = mockSessionFailingWithBeacon(new Error("Network request failed"));
    await expect(getReviewSessionResilient("la")).rejects.toThrow();
    await flush();

    expect(beacons).toHaveLength(1);
    expect(beacons[0]).toMatchObject({
      outcome: "failed",
      language_code: "la",
      error_kind: "transport",
      had_prefetch: false,
      used_fallback: false,
      force_fresh: false,
    });
  });

  it("classifies an HTTP-status failure as http_<status>", async () => {
    const { beacons } = mockSessionFailingWithBeacon(
      new Error("/api/reviews/session: 502"),
    );
    await expect(getReviewSessionResilient("la")).rejects.toThrow();
    await flush();
    expect(beacons[0].error_kind).toBe("http_502");
  });

  it("classifies an aborted (timeout) fetch as timeout", async () => {
    const abort = new Error("Aborted");
    (abort as any).name = "AbortError";
    const { beacons } = mockSessionFailingWithBeacon(abort);
    await expect(getReviewSessionResilient("la")).rejects.toThrow();
    await flush();
    expect(beacons[0].error_kind).toBe("timeout");
  });

  it("beacons session_fetch_recovered with prefetch age when the fallback is used", async () => {
    (AsyncStorage as any)._store[KEY("la")] = JSON.stringify({
      language: "la",
      bundle: bundle("cached"),
      savedAt: Date.now() - 60_000, // 1 min old — within the 15-min gate
    });
    const { beacons } = mockSessionFailingWithBeacon(new Error("Network request failed"));

    const result = await getReviewSessionResilient("la");
    expect(result.sentences[0].text).toBe("cached");
    await flush();

    expect(beacons).toHaveLength(1);
    expect(beacons[0]).toMatchObject({
      outcome: "recovered",
      language_code: "la",
      had_prefetch: true,
      used_fallback: true,
    });
    expect(beacons[0].prefetch_age_ms).toBeGreaterThanOrEqual(60_000);
  });

  it("a stale prefetch can't rescue the fetch — beacons failed but records had_prefetch + age", async () => {
    (AsyncStorage as any)._store[KEY("la")] = JSON.stringify({
      language: "la",
      bundle: bundle("cached"),
      savedAt: Date.now() - 20 * 60_000, // 20 min old — past the 15-min gate
    });
    const { beacons } = mockSessionFailingWithBeacon(new Error("Network request failed"));

    await expect(getReviewSessionResilient("la")).rejects.toThrow();
    await flush();

    expect(beacons[0]).toMatchObject({
      outcome: "failed",
      had_prefetch: true,
      used_fallback: false,
    });
    expect(beacons[0].prefetch_age_ms).toBeGreaterThanOrEqual(20 * 60_000);
  });
});
