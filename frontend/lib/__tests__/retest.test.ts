import { RETEST_EXPIRE_MS, RETEST_MIN_GAP_MS, RETEST_SPACING_MS, RetestEntry, pickReadyRetest } from "../review/retest";
const T0 = 1_753_400_000_000;
function entry(overrides: Partial<RetestEntry> = {}): RetestEntry {
  return { lemmaId: 42, failedAtMs: T0, cardsSince: 0, ...overrides };
}
describe("elapsed-time missing-word retries", () => {
  it("never retries immediately, even after many cards or a batch boundary", () => {
    expect(pickReadyRetest([entry({ cardsSince: 100 })], T0 + RETEST_MIN_GAP_MS - 1).ready).toBeNull();
  });
  it("becomes ready after four minutes with zero intervening cards", () => {
    expect(pickReadyRetest([entry()], T0 + RETEST_MIN_GAP_MS).ready?.lemmaId).toBe(42);
  });
  it("keeps a retry ready after a twenty-minute phone break", () => {
    const result = pickReadyRetest([entry()], T0 + 20 * 60_000);
    expect(result.ready?.lemmaId).toBe(42);
    expect(result.queue).toHaveLength(1);
  });
  it("does not consume a ready entry on fetch failure", () => {
    const result = pickReadyRetest([entry()], T0 + RETEST_MIN_GAP_MS);
    expect(pickReadyRetest(result.queue, T0 + RETEST_MIN_GAP_MS + 1000).ready?.lemmaId).toBe(42);
  });
  it("spaces overdue checks by time even when many are waiting", () => {
    const q = [entry(), entry({ lemmaId: 43 })];
    const last = T0 + 20 * 60_000;
    expect(pickReadyRetest(q, last + RETEST_SPACING_MS - 1, last).ready).toBeNull();
    expect(pickReadyRetest(q, last + RETEST_SPACING_MS, last).ready?.lemmaId).toBe(42);
  });
  it("expires stale retries after a day", () => {
    const q = [entry(), entry({ lemmaId: 43, failedAtMs: T0 + 60_000 })];
    expect(pickReadyRetest(q, T0 + RETEST_EXPIRE_MS).queue.map(e => e.lemmaId)).toEqual([43]);
  });
});
