import AsyncStorage from "@react-native-async-storage/async-storage";
import { enqueueReview, removeFromQueue, pendingCount } from "../sync-queue";

// Mock modules that sync-queue imports
jest.mock("../sync-events", () => ({
  syncEvents: { emit: jest.fn(), on: jest.fn(), off: jest.fn() },
}));
jest.mock("../offline-store", () => ({
  invalidateSessions: jest.fn(),
  updateCachedStoryStatus: jest.fn(),
}));

const QUEUE_KEY = "@alif/sync-queue";

beforeEach(async () => {
  (AsyncStorage as any)._store[QUEUE_KEY] = undefined;
  delete (AsyncStorage as any)._store[QUEUE_KEY];
  jest.clearAllMocks();
});

describe("enqueueReview", () => {
  it("adds an entry to the queue", async () => {
    await enqueueReview("sentence", { sentence_id: 1 }, "review-1");

    const raw = (AsyncStorage as any)._store[QUEUE_KEY];
    const queue = JSON.parse(raw);
    expect(queue).toHaveLength(1);
    expect(queue[0].client_review_id).toBe("review-1");
    expect(queue[0].type).toBe("sentence");
    expect(queue[0].payload.sentence_id).toBe(1);
    expect(queue[0].attempts).toBe(0);
  });

  it("appends to existing queue", async () => {
    await enqueueReview("sentence", { sentence_id: 1 }, "review-1");
    await enqueueReview("sentence", { sentence_id: 2 }, "review-2");

    const count = await pendingCount();
    expect(count).toBe(2);
  });
});

describe("removeFromQueue", () => {
  it("removes matching entry and returns true", async () => {
    await enqueueReview("sentence", { sentence_id: 1 }, "review-1");
    await enqueueReview("sentence", { sentence_id: 2 }, "review-2");

    const found = await removeFromQueue("review-1");
    expect(found).toBe(true);

    const count = await pendingCount();
    expect(count).toBe(1);

    const raw = (AsyncStorage as any)._store[QUEUE_KEY];
    const queue = JSON.parse(raw);
    expect(queue[0].client_review_id).toBe("review-2");
  });

  it("returns false when entry not found", async () => {
    await enqueueReview("sentence", { sentence_id: 1 }, "review-1");

    const found = await removeFromQueue("nonexistent");
    expect(found).toBe(false);

    const count = await pendingCount();
    expect(count).toBe(1);
  });

  it("works on empty queue", async () => {
    const found = await removeFromQueue("anything");
    expect(found).toBe(false);
  });
});

describe("pendingCount", () => {
  it("returns 0 for empty queue", async () => {
    expect(await pendingCount()).toBe(0);
  });

  it("returns correct count", async () => {
    await enqueueReview("sentence", {}, "r-1");
    await enqueueReview("sentence", {}, "r-2");
    await enqueueReview("sentence", {}, "r-3");

    expect(await pendingCount()).toBe(3);
  });
});
