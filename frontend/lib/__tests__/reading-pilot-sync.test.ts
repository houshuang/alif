import AsyncStorage from "@react-native-async-storage/async-storage";
import { enqueueReview, flushQueue, pendingCount } from "../sync-queue";
jest.mock("../api", () => ({ BASE_URL: "http://local.test" }));
jest.mock("../sync-events", () => ({ syncEvents: { emit: jest.fn() } }));
jest.mock("../offline-store", () => ({ invalidateDataCaches: jest.fn(), updateCachedStoryStatus: jest.fn() }));

it("retries the pilot journal endpoint without submitting word reviews", async () => {
  for (const key of Object.keys((AsyncStorage as any)._store)) delete (AsyncStorage as any)._store[key];
  const payload = { client_event_id: "attempt:1", pilot_id: "momo-wings", kind: "open" };
  await enqueueReview("reading_pilot_event", payload, "attempt:1");
  const fetchMock = jest.fn().mockResolvedValueOnce({ ok: false, status: 503 }).mockResolvedValueOnce({ ok: true, status: 200 });
  global.fetch = fetchMock;
  expect(await flushQueue()).toEqual({ synced: 0, failed: 1 });
  expect(await pendingCount()).toBe(1);
  expect(await flushQueue()).toEqual({ synced: 1, failed: 0 });
  expect(await pendingCount()).toBe(0);
  for (const [url, options] of fetchMock.mock.calls) {
    expect(url).toBe("http://local.test/api/books/reading-pilot/events");
    expect(JSON.parse(options.body)).toEqual(payload);
  }
});

it.each([
  ["reading_chapter_event", "events", { client_event_id: "chapter:test:1", kind: "word" }],
  ["reading_chapter_voice", "voice", { event: { client_event_id: "chapter:test:1", kind: "feedback" }, audio_base64: "AA==", mime_type: "audio/mp4", duration_ms: 1000 }],
] as const)("retries %s unchanged through its isolated endpoint", async (type, path, payload) => {
  for (const key of Object.keys((AsyncStorage as any)._store)) delete (AsyncStorage as any)._store[key];
  await enqueueReview(type, payload, "chapter:test:1");
  global.fetch = jest.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ ok: true });
  expect(await flushQueue()).toEqual({ synced: 0, failed: 1 });
  expect(await flushQueue()).toEqual({ synced: 1, failed: 0 });
  expect(await pendingCount()).toBe(0);
  for (const [url, options] of (fetch as jest.Mock).mock.calls) {
    expect(url).toBe(`http://local.test/api/books/chapters/${path}`);
    expect(JSON.parse(options.body)).toEqual(payload);
  }
});
