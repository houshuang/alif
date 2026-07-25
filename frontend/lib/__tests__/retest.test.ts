import {
  RETEST_EXPIRE_MS,
  RETEST_MIN_CARDS_BETWEEN,
  RETEST_MIN_GAP_MS,
  RetestEntry,
  pickReadyRetest,
  retestArm,
} from "../review/retest";

const T0 = 1_753_400_000_000; // fixed wall-clock origin

function entry(overrides: Partial<RetestEntry> = {}): RetestEntry {
  return { lemmaId: 42, failedAtMs: T0, cardsSince: 0, ...overrides };
}

describe("retestArm", () => {
  it("is deterministic and matches the documented python mirror", () => {
    // Python mirror: h=5381; for c in s: h=(((h*33)&0xFFFFFFFF)^ord(c)); h%2
    // Precomputed for "abc:1": djb2-xor unsigned-32
    const s = "abc:1";
    let h = 5381;
    for (let i = 0; i < s.length; i++) {
      h = (Math.imul(h, 33) ^ s.charCodeAt(i)) >>> 0;
    }
    const expected = h % 2 === 0 ? "treatment" : "control";
    expect(retestArm("abc", 1)).toBe(expected);
    expect(retestArm("abc", 1)).toBe(retestArm("abc", 1));
  });

  it("splits roughly 50/50 over many lemmas", () => {
    let treatment = 0;
    for (let i = 0; i < 1000; i++) {
      if (retestArm("session-xyz", i) === "treatment") treatment++;
    }
    expect(treatment).toBeGreaterThan(400);
    expect(treatment).toBeLessThan(600);
  });
});

describe("pickReadyRetest", () => {
  it("returns null before the minimum wall-clock gap", () => {
    const q = [entry({ cardsSince: RETEST_MIN_CARDS_BETWEEN })];
    const { ready } = pickReadyRetest(q, T0 + RETEST_MIN_GAP_MS - 1);
    expect(ready).toBeNull();
  });

  it("returns null before enough intervening cards", () => {
    const q = [entry({ cardsSince: RETEST_MIN_CARDS_BETWEEN - 1 })];
    const { ready } = pickReadyRetest(q, T0 + RETEST_MIN_GAP_MS + 1);
    expect(ready).toBeNull();
  });

  it("returns the matured entry when both conditions hold", () => {
    const q = [entry({ cardsSince: RETEST_MIN_CARDS_BETWEEN })];
    const { ready } = pickReadyRetest(q, T0 + RETEST_MIN_GAP_MS);
    expect(ready?.lemmaId).toBe(42);
  });

  it("does NOT remove the returned entry — removal happens only after a successful fetch", () => {
    const q = [entry({ cardsSince: 5 })];
    const { queue, ready } = pickReadyRetest(q, T0 + RETEST_MIN_GAP_MS);
    expect(ready).not.toBeNull();
    expect(queue).toHaveLength(1);
  });

  it("prunes expired entries", () => {
    const q = [
      entry({ lemmaId: 1, cardsSince: 5 }),
      entry({ lemmaId: 2, failedAtMs: T0 + 10 * 60_000, cardsSince: 5 }),
    ];
    const { queue, ready } = pickReadyRetest(q, T0 + RETEST_EXPIRE_MS);
    expect(queue.map(e => e.lemmaId)).toEqual([2]);
    expect(ready?.lemmaId).toBe(2);
  });

  it("picks the oldest matured entry first", () => {
    const q = [
      entry({ lemmaId: 1, failedAtMs: T0, cardsSince: 5 }),
      entry({ lemmaId: 2, failedAtMs: T0 + 60_000, cardsSince: 5 }),
    ];
    const { ready } = pickReadyRetest(q, T0 + RETEST_MIN_GAP_MS + 120_000);
    expect(ready?.lemmaId).toBe(1);
  });
});
