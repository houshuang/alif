import AsyncStorage from "@react-native-async-storage/async-storage";
import { enqueueReview, flushQueue } from "../sync-queue";
import { freshPilotProgress, getReadingPilot, loadPilotProgress, nextPilotProgress, parsePilotProgress,
  pilotEvent, PILOT_STORAGE_KEY, rereadProgress, savePilotProgress } from "../reading-pilot";
jest.mock("../sync-queue", () => ({ enqueueReview: jest.fn(), flushQueue: jest.fn() }));
jest.mock("../api", () => ({ BASE_URL: "http://local.test" }));
const enqueue = enqueueReview as jest.Mock, flush = flushQueue as jest.Mock;
beforeEach(() => {
  for (const key of Object.keys((AsyncStorage as any)._store)) delete (AsyncStorage as any)._store[key];
  enqueue.mockReset().mockResolvedValue(undefined);
  flush.mockReset().mockResolvedValue({ synced: 0, failed: 0 });
});
it("reread hides support but keeps time and location", () => {
  const p = { ...freshPilotProgress(1), readingMs: 170000, openHelp: ["2-1-1"], simpler: true };
  expect(rereadProgress(p)).toMatchObject({ index: 1, phase: "reread", english: false,
    openHelp: [], simpler: false, readingMs: 170000, attemptId: p.attemptId });
});
it("continues with fresh support and stops at the checkpoint", () => {
  const p = rereadProgress(freshPilotProgress());
  const next = nextPilotProgress(p, 3);
  expect(next).toMatchObject({ index: 1, phase: "read", english: true, finished: false });
  expect(next.attemptId).not.toBe(p.attemptId);
  expect(nextPilotProgress(freshPilotProgress(2), 3)).toMatchObject({ index: 2, finished: true });
});
it("preserves offline events with continuation and retries unchanged IDs", async () => {
  enqueue.mockRejectedValue(new Error("storage unavailable"));
  const p = { ...freshPilotProgress(1), sequence: 2, readingMs: 40000 };
  const event = pilotEvent(p, "wings-2", "open");
  await savePilotProgress(p, event);
  const saved = JSON.parse((AsyncStorage as any)._store[PILOT_STORAGE_KEY]);
  expect(saved.progress).toEqual(p); expect(saved.outbox).toEqual([event]);
  enqueue.mockResolvedValue(undefined);
  expect(await loadPilotProgress(3)).toEqual(p);
  expect(enqueue).toHaveBeenLastCalledWith("reading_pilot_event", event, event.client_event_id);
  expect(JSON.parse((AsyncStorage as any)._store[PILOT_STORAGE_KEY]).outbox).toEqual([]);
});
it("serializes saves so earlier unsent events survive", async () => {
  enqueue.mockRejectedValue(new Error("storage error"));
  const p = freshPilotProgress();
  await Promise.all([1, 2, 3].map(sequence => {
    const next = { ...p, sequence };
    return savePilotProgress(next, pilotEvent(next, "wings-1", "open"));
  }));
  const saved = JSON.parse((AsyncStorage as any)._store[PILOT_STORAGE_KEY]);
  expect(saved.progress.sequence).toBe(3);
  expect(saved.outbox.map((e: any) => e.client_event_id)).toEqual([1, 2, 3].map(i => `${p.attemptId}:${i}`));
});
it("does not erase corrupt progress", async () => {
  const raw = JSON.stringify({ progress: { index: 999 }, outbox: [] });
  await AsyncStorage.setItem(PILOT_STORAGE_KEY, raw);
  await expect(loadPilotProgress(3)).rejects.toThrow();
  expect((AsyncStorage as any)._store[PILOT_STORAGE_KEY]).toBe(raw);
  expect(parsePilotProgress(null, 3)).toBeNull();
});
it("opens cached content without a network request", async () => {
  const cached = { id: "momo-wings", version: 1, sessions: [1, 2, 3].map(() => ({ blocks: [{}] })) };
  await AsyncStorage.setItem("@alif:reading-pilot:content:v1", JSON.stringify(cached));
  global.fetch = jest.fn().mockRejectedValue(new Error("offline"));
  expect(await getReadingPilot()).toEqual(cached);
  expect(global.fetch).not.toHaveBeenCalled();
});
