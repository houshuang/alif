/**
 * Tests for API layer: sentence review, undo, word lookup, story actions,
 * word detail, learn mode, offline fallback, and caching behavior.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";

// Must mock these before importing api
jest.mock("../sync-events", () => ({
  syncEvents: { emit: jest.fn(), on: jest.fn(), off: jest.fn() },
}));
jest.mock("../net-status", () => ({
  netStatus: { isOnline: true, start: jest.fn(), subscribe: jest.fn() },
}));

import {
  getWords,
  getWordDetail,
  getStats,
  getSentenceReviewSession,
  submitSentenceReview,
  undoSentenceReview,
  lookupReviewWord,
  getStories,
  getStoryDetail,
  completeStory,
  lookupStoryWord,
  importStory,
  getNextWords,
  introduceWord,
  submitQuizResult,
  suspendWord,
  flagContent,
  BASE_URL,
} from "../api";

// --- fetch mock ---

const mockFetch = jest.fn();
(global as any).fetch = mockFetch;

function mockJsonResponse(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  });
}

function mockErrorResponse(status: number, body = "error") {
  return Promise.resolve({
    ok: false,
    status,
    json: () => Promise.resolve({ detail: body }),
    text: () => Promise.resolve(body),
  });
}

const store = (AsyncStorage as any)._store;

beforeEach(() => {
  mockFetch.mockReset();
  for (const key of Object.keys(store)) delete store[key];
  jest.clearAllMocks();
});

// ============================================================
// Word list & detail
// ============================================================

describe("getWords", () => {
  it("maps raw API response to Word interface", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse([
        {
          lemma_id: 1,
          lemma_ar: "كِتَاب",
          gloss_en: "book",
          transliteration: "kitāb",
          root: "ك.ت.ب",
          pos: "noun",
          knowledge_state: "learning",
          times_seen: 5,
          times_correct: 3,
          last_reviewed: "2026-02-10T12:00:00Z",
          knowledge_score: 65,
          frequency_rank: 120,
          cefr_level: "A2",
          last_ratings: [3, 3, 1, 3, 3],
        },
      ])
    );

    const words = await getWords();
    expect(words).toHaveLength(1);
    expect(words[0].id).toBe(1);
    expect(words[0].arabic).toBe("كِتَاب");
    expect(words[0].english).toBe("book");
    expect(words[0].state).toBe("learning");
    expect(words[0].knowledge_score).toBe(65);
    expect(words[0].last_ratings).toEqual([3, 3, 1, 3, 3]);
  });

  it("caches words for offline use", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse([
        { lemma_id: 1, lemma_ar: "كتاب", gloss_en: "book", transliteration: "kitāb", root: null, pos: "noun", knowledge_state: "new", times_seen: 0, times_correct: 0, last_reviewed: null, knowledge_score: 0, frequency_rank: null, cefr_level: null },
      ])
    );

    await getWords();
    // Wait for async cache write
    await new Promise((r) => setTimeout(r, 10));

    expect(store["@alif/words"]).toBeDefined();
    const cached = JSON.parse(store["@alif/words"]);
    expect(cached).toHaveLength(1);
    expect(cached[0].arabic).toBe("كتاب");
  });

  it("returns cached words when API fails", async () => {
    // Pre-populate cache
    store["@alif/words"] = JSON.stringify([
      { id: 1, arabic: "كتاب", english: "book", state: "new", times_seen: 0, times_correct: 0, knowledge_score: 0 },
    ]);
    mockFetch.mockReturnValueOnce(Promise.reject(new Error("Network error")));

    const words = await getWords();
    expect(words).toHaveLength(1);
    expect(words[0].arabic).toBe("كتاب");
  });
});

describe("getWordDetail", () => {
  it("maps raw word detail with review history and sentence stats", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({
        lemma_id: 42,
        lemma_ar: "وَلَد",
        gloss_en: "boy",
        transliteration: "walad",
        root: "و.ل.د",
        pos: "noun",
        knowledge_state: "known",
        times_seen: 12,
        times_correct: 10,
        last_reviewed: "2026-02-11T10:00:00Z",
        knowledge_score: 85,
        frequency_rank: 50,
        cefr_level: "A1",
        forms_json: { plural: "أَوْلَاد" },
        grammar_features: [
          { feature_key: "noun_gender", category: "gender", label_en: "Noun Gender", label_ar: "جنس" },
        ],
        root_family: [
          { id: 43, arabic: "وِلَادَة", english: "birth" },
        ],
        review_history: [
          { rating: 3, reviewed_at: "2026-02-10T12:00:00Z", credit_type: "primary", comprehension_signal: "understood", review_mode: "reading" },
        ],
        sentence_stats: [
          { sentence_id: 1, surface_forms: ["الوَلَدُ"], sentence_arabic: "الوَلَدُ يَقْرَأُ", sentence_english: "The boy reads", seen_count: 2, missed_count: 0, confused_count: 0, understood_count: 2, primary_count: 1, collateral_count: 1, accuracy_pct: 100 },
        ],
      })
    );

    const detail = await getWordDetail(42);
    expect(detail.id).toBe(42);
    expect(detail.state).toBe("known");
    expect(detail.forms_json).toEqual({ plural: "أَوْلَاد" });
    expect(detail.grammar_features).toHaveLength(1);
    expect(detail.grammar_features[0].feature_key).toBe("noun_gender");
    expect(detail.root_family).toHaveLength(1);
    expect(detail.review_history).toHaveLength(1);
    expect(detail.review_history[0].credit_type).toBe("primary");
    expect(detail.sentence_stats).toHaveLength(1);
    expect(detail.sentence_stats[0].accuracy_pct).toBe(100);
  });
});

// ============================================================
// Sentence review
// ============================================================

describe("submitSentenceReview", () => {
  it("enqueues review and marks as reviewed", async () => {
    // flushQueue calls fetch — mock it to succeed
    mockFetch.mockReturnValue(mockJsonResponse({ results: [] }));

    const result = await submitSentenceReview({
      sentence_id: 10,
      primary_lemma_id: 42,
      comprehension_signal: "understood",
      missed_lemma_ids: [],
      confused_lemma_ids: [],
      response_ms: 1500,
      session_id: "sess-1",
      review_mode: "reading",
    });

    expect(result.clientReviewId).toBeTruthy();

    // Check review was enqueued
    const queueRaw = store["@alif/sync-queue"];
    const queue = JSON.parse(queueRaw);
    expect(queue.length).toBeGreaterThanOrEqual(1);
    const entry = queue.find((e: any) => e.payload.sentence_id === 10);
    expect(entry).toBeDefined();
    expect(entry.payload.comprehension_signal).toBe("understood");

    // Check marked as reviewed
    const reviewed = JSON.parse(store["@alif/reviewed"]);
    expect(reviewed.some((k: string) => k.includes("42"))).toBe(true);
  });

  it("includes missed and confused lemma ids in payload", async () => {
    mockFetch.mockReturnValue(mockJsonResponse({ results: [] }));

    await submitSentenceReview({
      sentence_id: 10,
      primary_lemma_id: 42,
      comprehension_signal: "partial",
      missed_lemma_ids: [43, 44],
      confused_lemma_ids: [45],
      response_ms: 2000,
      session_id: "sess-1",
      review_mode: "reading",
    });

    const queue = JSON.parse(store["@alif/sync-queue"]);
    const entry = queue.find((e: any) => e.payload.sentence_id === 10);
    expect(entry.payload.missed_lemma_ids).toEqual([43, 44]);
    expect(entry.payload.confused_lemma_ids).toEqual([45]);
  });

  it("accepts explicit client review id", async () => {
    mockFetch.mockReturnValue(mockJsonResponse({ results: [] }));

    const result = await submitSentenceReview(
      {
        sentence_id: 10,
        primary_lemma_id: 42,
        comprehension_signal: "understood",
        missed_lemma_ids: [],
        confused_lemma_ids: [],
        response_ms: 1000,
        session_id: "sess-1",
        review_mode: "reading",
      },
      "explicit-id-123"
    );

    expect(result.clientReviewId).toBe("explicit-id-123");
    const queue = JSON.parse(store["@alif/sync-queue"]);
    expect(queue[0].client_review_id).toBe("explicit-id-123");
  });
});

describe("undoSentenceReview", () => {
  it("removes from queue, unmarks reviewed, and calls backend", async () => {
    // Set up: submit a review first
    mockFetch.mockReturnValue(mockJsonResponse({ results: [] }));
    const { clientReviewId } = await submitSentenceReview({
      sentence_id: 10,
      primary_lemma_id: 42,
      comprehension_signal: "understood",
      missed_lemma_ids: [],
      confused_lemma_ids: [],
      response_ms: 1000,
      session_id: "sess-1",
      review_mode: "reading",
    });

    // Verify it's in the queue
    let queue = JSON.parse(store["@alif/sync-queue"]);
    expect(queue.some((e: any) => e.client_review_id === clientReviewId)).toBe(true);

    // Now undo
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ undone: true, reviews_removed: 2 })
    );
    await undoSentenceReview(clientReviewId, "sess-1", 10, 42, "reading");

    // Queue entry removed
    queue = JSON.parse(store["@alif/sync-queue"]);
    expect(queue.some((e: any) => e.client_review_id === clientReviewId)).toBe(false);

    // Reviewed mark removed
    const reviewed = JSON.parse(store["@alif/reviewed"]);
    expect(reviewed.some((k: string) => k.includes("42"))).toBe(false);

    // Backend called
    const lastCall = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
    expect(lastCall[0]).toContain("/api/review/undo-sentence");
    const body = JSON.parse(lastCall[1].body);
    expect(body.client_review_id).toBe(clientReviewId);
  });

  it("handles backend failure gracefully", async () => {
    mockFetch.mockReturnValue(mockJsonResponse({ results: [] }));
    const { clientReviewId } = await submitSentenceReview({
      sentence_id: 10,
      primary_lemma_id: 42,
      comprehension_signal: "understood",
      missed_lemma_ids: [],
      confused_lemma_ids: [],
      response_ms: 1000,
      session_id: "sess-1",
      review_mode: "reading",
    });

    // Backend undo fails
    mockFetch.mockReturnValueOnce(mockErrorResponse(500, "Internal error"));

    // Should not throw
    await undoSentenceReview(clientReviewId, "sess-1", 10, 42, "reading");

    // Queue still cleaned up locally
    const queue = JSON.parse(store["@alif/sync-queue"]);
    expect(queue.some((e: any) => e.client_review_id === clientReviewId)).toBe(false);
  });
});

// ============================================================
// Session fetching + offline fallback
// ============================================================

describe("getSentenceReviewSession", () => {
  it("returns session from API and caches it", async () => {
    const sessionData = {
      session_id: "api-sess-1",
      items: [
        {
          sentence_id: 1,
          primary_lemma_id: 42,
          words: [{ lemma_id: 42, surface_form: "كتاب", gloss_en: "book" }],
        },
      ],
      total_due_words: 5,
      covered_due_words: 1,
      intro_candidates: [],
    };
    // Main session fetch
    mockFetch.mockReturnValueOnce(mockJsonResponse(sessionData));
    // Prefetch calls
    mockFetch.mockReturnValue(mockJsonResponse(sessionData));

    const session = await getSentenceReviewSession("reading");
    expect(session.session_id).toBe("api-sess-1");
    expect(session.items).toHaveLength(1);
  });

  it("falls back to cache when API fails", async () => {
    // Pre-cache a session
    const cached = {
      session_id: "cached-sess",
      items: [
        { sentence_id: 1, primary_lemma_id: 10, words: [] },
      ],
      total_due_words: 1,
      covered_due_words: 1,
      intro_candidates: [],
    };
    store["@alif/sessions/reading"] = JSON.stringify([cached]);

    mockFetch.mockReturnValueOnce(Promise.reject(new Error("Network error")));

    const session = await getSentenceReviewSession("reading");
    expect(session.session_id).toBe("cached-sess");
  });
});

// ============================================================
// Word lookup (review phase)
// ============================================================

describe("lookupReviewWord", () => {
  const lookupResponse = {
    lemma_id: 42,
    lemma_ar: "كِتَاب",
    gloss_en: "book",
    transliteration: "kitāb",
    root: "ك.ت.ب",
    root_meaning: "writing",
    root_id: 5,
    pos: "noun",
    forms_json: { plural: "كُتُب" },
    is_function_word: false,
    frequency_rank: 120,
    cefr_level: "A2",
    root_family: [
      { lemma_id: 43, lemma_ar: "مَكْتَبَة", gloss_en: "library", pos: "noun", state: "known" },
    ],
  };

  it("fetches word lookup from API", async () => {
    mockFetch.mockReturnValueOnce(mockJsonResponse(lookupResponse));

    const result = await lookupReviewWord(42);
    expect(result.lemma_id).toBe(42);
    expect(result.root).toBe("ك.ت.ب");
    expect(result.root_family).toHaveLength(1);
  });

  it("caches lookup for subsequent calls", async () => {
    mockFetch.mockReturnValueOnce(mockJsonResponse(lookupResponse));

    await lookupReviewWord(42);
    // Wait for cache write
    await new Promise((r) => setTimeout(r, 10));

    // Second call should use cache, not fetch
    mockFetch.mockClear();
    const result = await lookupReviewWord(42);
    expect(result.lemma_id).toBe(42);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

// ============================================================
// Story operations
// ============================================================

describe("getStories", () => {
  it("returns and caches story list", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse([
        { id: 1, title_ar: "قصة", title_en: "Story", status: "active", readiness_pct: 80 },
      ])
    );

    const stories = await getStories();
    expect(stories).toHaveLength(1);
    expect(stories[0].title_en).toBe("Story");

    await new Promise((r) => setTimeout(r, 10));
    expect(store["@alif/stories"]).toBeDefined();
  });
});

describe("getStoryDetail", () => {
  it("returns story with words", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({
        id: 1,
        title_ar: "قصة صغيرة",
        title_en: "A Small Story",
        body_ar: "الولد قرأ الكتاب.",
        status: "active",
        words: [
          { position: 0, surface_form: "الولد", lemma_id: 42, gloss_en: "the boy", is_function_word: false },
          { position: 1, surface_form: "قرأ", lemma_id: 43, gloss_en: "read", is_function_word: false },
        ],
      })
    );

    const detail = await getStoryDetail(1);
    expect(detail.id).toBe(1);
    expect(detail.words).toHaveLength(2);
    expect(detail.words[0].surface_form).toBe("الولد");
  });
});

describe("story actions", () => {
  it("completeStory sends POST with looked-up lemmas", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ status: "completed", word_results: [] })
    );

    await completeStory(1, [42, 43], 5000);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE_URL}/api/stories/1/complete`);
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.looked_up_lemma_ids).toEqual([42, 43]);
    expect(body.reading_time_ms).toBe(5000);
  });

  it("lookupStoryWord sends word position and lemma", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({
        lemma_id: 42,
        surface_form: "الكتاب",
        gloss_en: "the book",
        root: "ك.ت.ب",
        pos: "noun",
      })
    );

    const result = await lookupStoryWord(1, 42, 3);
    expect(result.gloss_en).toBe("the book");

    const [, opts] = mockFetch.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.lemma_id).toBe(42);
    expect(body.position).toBe(3);
  });
});

describe("importStory", () => {
  it("sends arabic text and optional title", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({
        id: 5,
        title_ar: "قصة",
        title_en: "My Story",
        body_ar: "الولد قرأ.",
        status: "active",
        words: [],
      })
    );

    const story = await importStory("الولد قرأ.", "My Story");
    expect(story.id).toBe(5);

    const [, opts] = mockFetch.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.arabic_text).toBe("الولد قرأ.");
    expect(body.title).toBe("My Story");
  });
});

// ============================================================
// Learn mode
// ============================================================

describe("getNextWords", () => {
  it("returns learn candidates with score breakdown", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({
        words: [
          {
            lemma_id: 50,
            lemma_ar: "سيارة",
            lemma_ar_bare: "سيارة",
            gloss_en: "car",
            pos: "noun",
            transliteration: "sayyāra",
            frequency_rank: 85,
            cefr_level: "A2",
            root: "س.ي.ر",
            root_meaning: "to walk",
            root_id: 20,
            score: 0.82,
            score_breakdown: {
              frequency: 0.35,
              root_familiarity: 0.25,
              recency_bonus: 0.12,
              story_bonus: 0.10,
              known_siblings: 2,
              total_siblings: 5,
            },
          },
        ],
      })
    );

    const result = await getNextWords(5);
    expect(result.words).toHaveLength(1);
    expect(result.words[0].score).toBe(0.82);
    expect(result.words[0].score_breakdown.known_siblings).toBe(2);
    expect(result.words[0].frequency_rank).toBe(85);
  });
});

describe("introduceWord", () => {
  it("sends POST with lemma_id", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ lemma_id: 50, state: "learning", sentences_queued: 3 })
    );

    const result = await introduceWord(50);
    expect(result.lemma_id).toBe(50);

    const [, opts] = mockFetch.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.lemma_id).toBe(50);
  });
});

describe("submitQuizResult", () => {
  it("submits got_it=true", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ lemma_id: 50, new_state: "learning", next_due: "2026-02-12T00:00:00Z" })
    );

    const result = await submitQuizResult(50, true);
    expect(result.new_state).toBe("learning");

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.got_it).toBe(true);
  });

  it("submits got_it=false for missed", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ lemma_id: 50, new_state: "learning", next_due: "2026-02-11T12:00:00Z" })
    );

    await submitQuizResult(50, false);

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.got_it).toBe(false);
  });
});

// ============================================================
// Word management
// ============================================================

describe("suspendWord", () => {
  it("sends POST to suspend endpoint", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ lemma_id: 42, state: "suspended" })
    );

    const result = await suspendWord(42);
    expect(result.state).toBe("suspended");

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE_URL}/api/words/42/suspend`);
    expect(opts.method).toBe("POST");
  });
});

describe("flagContent", () => {
  it("flags word gloss for review", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ flag_id: 1, status: "pending" })
    );

    const result = await flagContent({
      content_type: "word_gloss",
      lemma_id: 42,
    });
    expect(result.flag_id).toBe(1);

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.content_type).toBe("word_gloss");
    expect(body.lemma_id).toBe(42);
  });

  it("flags sentence for review", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({ flag_id: 2, status: "pending" })
    );

    await flagContent({
      content_type: "sentence_arabic",
      sentence_id: 10,
    });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.content_type).toBe("sentence_arabic");
    expect(body.sentence_id).toBe(10);
  });
});

// ============================================================
// Stats + offline fallback
// ============================================================

describe("getStats", () => {
  it("maps raw stats and caches", async () => {
    mockFetch.mockReturnValueOnce(
      mockJsonResponse({
        total_words: 500,
        known: 200,
        learning: 150,
        new: 150,
        due_today: 30,
        reviews_today: 15,
        total_reviews: 1200,
        lapsed: 10,
      })
    );

    const stats = await getStats();
    expect(stats.total_words).toBe(500);
    expect(stats.known_words).toBe(200);
    expect(stats.due_today).toBe(30);
    expect(stats.lapsed).toBe(10);
  });

  it("returns cached stats when API fails", async () => {
    store["@alif/stats"] = JSON.stringify({
      total_words: 100,
      known_words: 50,
      learning_words: 30,
      new_words: 20,
      due_today: 10,
      reviews_today: 5,
    });
    mockFetch.mockReturnValueOnce(Promise.reject(new Error("offline")));

    const stats = await getStats();
    expect(stats.total_words).toBe(100);
  });
});
