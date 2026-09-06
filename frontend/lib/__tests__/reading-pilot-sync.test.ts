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
