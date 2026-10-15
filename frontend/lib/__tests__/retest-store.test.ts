import AsyncStorage from "@react-native-async-storage/async-storage";
import { loadRetests, saveRetests } from "../review/retest-store";
jest.mock("@react-native-async-storage/async-storage", () => ({
  __esModule: true,
  default: { getItem: jest.fn(), setItem: jest.fn().mockResolvedValue(undefined) },
}));
it("restores an overdue retry after a cold start", async () => {
  const e = { lemmaId: 42, failedAtMs: Date.now() - 20 * 60_000, cardsSince: 0 };
  (AsyncStorage.getItem as jest.Mock).mockResolvedValue(JSON.stringify({ queue: [e], lastShownAtMs: 0 }));
  expect((await loadRetests()).queue).toEqual([e]);
});
it("ignores malformed persisted entries", async () => {
  (AsyncStorage.getItem as jest.Mock).mockResolvedValue(JSON.stringify({ queue: [{ lemmaId: null }], lastShownAtMs: null }));
  expect(await loadRetests()).toEqual({ queue: [], lastShownAtMs: 0 });
});
it("serializes saves so an answered retry stays removed", async () => {
  saveRetests([{ lemmaId: 42, failedAtMs: Date.now(), cardsSince: 0 }], 0);
  saveRetests([], 123);
  await new Promise(resolve => setTimeout(resolve, 0));
  const calls = (AsyncStorage.setItem as jest.Mock).mock.calls;
  expect(JSON.parse(calls[calls.length - 1][1])).toEqual({ queue: [], lastShownAtMs: 123 });
});
