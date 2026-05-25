import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  enqueuePageReview,
  removeFromPolyglotQueue,
  pendingPolyglotCount,
  flushPolyglotQueue,
  type PageReviewPayload,
} from "../polyglot-sync-queue";

jest.mock("../sync-events", () => ({
  syncEvents: { emit: jest.fn(), on: jest.fn(), off: jest.fn() },
}));
jest.mock("../polyglot-api", () => ({
  POLYGLOT_BASE_URL: "http://test.local/polyglot",
}));

const QUEUE_KEY = "@polyglot/sync-queue";

const PAGE: PageReviewPayload = {
  story_id: 7,
  page_number: 3,
  unknown_lemma_ids: [11, 12],
  encountered_lemma_ids: [13],
};

beforeEach(async () => {
  delete (AsyncStorage as any)._store[QUEUE_KEY];
  jest.clearAllMocks();
  (global as any).fetch = jest.fn();
});

describe("enqueuePageReview", () => {
  it("stores a self-contained page_review entry", async () => {
    await enqueuePageReview(PAGE, "crid-1");

    const queue = JSON.parse((AsyncStorage as any)._store[QUEUE_KEY]);
    expect(queue).toHaveLength(1);
    expect(queue[0].type).toBe("page_review");
    expect(queue[0].client_review_id).toBe("crid-1");
    expect(queue[0].payload.unknown_lemma_ids).toEqual([11, 12]);
    expect(queue[0].payload.encountered_lemma_ids).toEqual([13]);
    expect(queue[0].attempts).toBe(0);
  });

  it("replaces an existing same-id entry with the latest marks", async () => {
    // Deterministic per-page id: re-advancing the same page (back-then-forward,
    // or editing marks offline before the first send) must REPLACE the queued
    // entry so the freshest red/yellow taps are the ones that replay.
    await enqueuePageReview(PAGE, "pr:7:3");
    await enqueuePageReview(
      { ...PAGE, unknown_lemma_ids: [11, 12, 99], encountered_lemma_ids: [] },
      "pr:7:3",
    );

    expect(await pendingPolyglotCount()).toBe(1);
    const queue = JSON.parse((AsyncStorage as any)._store[QUEUE_KEY]);
    expect(queue[0].payload.unknown_lemma_ids).toEqual([11, 12, 99]);
    expect(queue[0].payload.encountered_lemma_ids).toEqual([]);
  });

  it("keeps distinct-id entries side by side", async () => {
    await enqueuePageReview(PAGE, "pr:7:3");
    await enqueuePageReview({ ...PAGE, page_number: 4 }, "pr:7:4");
    expect(await pendingPolyglotCount()).toBe(2);
  });
});

describe("removeFromPolyglotQueue", () => {
  it("removes a matching entry", async () => {
    await enqueuePageReview(PAGE, "crid-1");
    await enqueuePageReview(PAGE, "crid-2");
    expect(await removeFromPolyglotQueue("crid-1")).toBe(true);
    expect(await pendingPolyglotCount()).toBe(1);
  });

  it("returns false when not found", async () => {
    expect(await removeFromPolyglotQueue("nope")).toBe(false);
  });
});

describe("flushPolyglotQueue", () => {
  it("posts the split red/yellow lists + client_review_id and drains on 200", async () => {
    await enqueuePageReview(PAGE, "crid-1");
    (global.fetch as jest.Mock).mockResolvedValue({ ok: true, status: 200 });

    const result = await flushPolyglotQueue();

    expect(result.synced).toBe(1);
    expect(await pendingPolyglotCount()).toBe(0);

    const [url, opts] = (global.fetch as jest.Mock).mock.calls[0];
    expect(url).toBe("http://test.local/polyglot/api/texts/7/pages/3/mark_remaining");
    const body = JSON.parse(opts.body);
    expect(body.unknown_lemma_ids).toEqual([11, 12]);
    expect(body.encountered_lemma_ids).toEqual([13]);
    expect(body.client_review_id).toBe("crid-1");
  });

  it("treats a duplicate replay (200) as synced", async () => {
    await enqueuePageReview(PAGE, "crid-1");
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ duplicate: true }),
    });

    const result = await flushPolyglotQueue();
    expect(result.synced).toBe(1);
    expect(await pendingPolyglotCount()).toBe(0);
  });

  it("keeps the entry queued on network error", async () => {
    await enqueuePageReview(PAGE, "crid-1");
    (global.fetch as jest.Mock).mockRejectedValue(new Error("offline"));

    const result = await flushPolyglotQueue();
    expect(result.synced).toBe(0);
    expect(await pendingPolyglotCount()).toBe(1);

    // attempts not incremented on a pure network error.
    const queue = JSON.parse((AsyncStorage as any)._store[QUEUE_KEY]);
    expect(queue[0].attempts).toBe(0);
  });

  it("increments attempts on a server error (4xx/5xx)", async () => {
    await enqueuePageReview(PAGE, "crid-1");
    (global.fetch as jest.Mock).mockResolvedValue({ ok: false, status: 500 });

    await flushPolyglotQueue();
    expect(await pendingPolyglotCount()).toBe(1);
    const queue = JSON.parse((AsyncStorage as any)._store[QUEUE_KEY]);
    expect(queue[0].attempts).toBe(1);
  });
});
