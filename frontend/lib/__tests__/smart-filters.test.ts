/**
 * Tests for the smart filter functions used in the word list.
 * These functions are defined inline in words.tsx — we replicate them here
 * to test the logic independently.
 */

interface Word {
  id: number;
  state: "new" | "learning" | "known" | "lapsed" | "suspended";
  times_seen: number;
  times_correct: number;
  knowledge_score: number;
  last_ratings?: number[];
}

function isLeech(w: Word): boolean {
  return w.times_seen >= 6 && w.times_correct / w.times_seen < 0.5;
}

function isStruggling(w: Word): boolean {
  const ratings = w.last_ratings || [];
  if (ratings.length < 3) return false;
  const recent = ratings.slice(-4);
  return recent.filter((r) => r < 3).length >= 2;
}

function isRecent(w: Word): boolean {
  return w.state === "learning" && w.times_seen <= 4;
}

function isSolid(w: Word): boolean {
  return w.knowledge_score >= 70;
}

const base: Word = {
  id: 1,
  state: "learning",
  times_seen: 0,
  times_correct: 0,
  knowledge_score: 0,
  last_ratings: [],
};

describe("isLeech", () => {
  it("returns true for high-review low-accuracy word", () => {
    expect(isLeech({ ...base, times_seen: 10, times_correct: 3 })).toBe(true);
  });

  it("returns false for few reviews", () => {
    expect(isLeech({ ...base, times_seen: 5, times_correct: 1 })).toBe(false);
  });

  it("returns false for decent accuracy", () => {
    expect(isLeech({ ...base, times_seen: 10, times_correct: 5 })).toBe(false);
  });

  it("boundary: exactly 6 reviews, 2 correct (33%)", () => {
    expect(isLeech({ ...base, times_seen: 6, times_correct: 2 })).toBe(true);
  });

  it("boundary: exactly 50% accuracy", () => {
    expect(isLeech({ ...base, times_seen: 6, times_correct: 3 })).toBe(false);
  });
});

describe("isStruggling", () => {
  it("returns true for 2+ failures in last 4 ratings", () => {
    expect(
      isStruggling({ ...base, last_ratings: [3, 3, 1, 2] })
    ).toBe(true);
  });

  it("returns false for only 1 failure", () => {
    expect(
      isStruggling({ ...base, last_ratings: [3, 3, 3, 1] })
    ).toBe(false);
  });

  it("returns false with fewer than 3 ratings", () => {
    expect(isStruggling({ ...base, last_ratings: [1, 1] })).toBe(false);
  });

  it("uses only last 4 ratings", () => {
    // Old failures don't count
    expect(
      isStruggling({ ...base, last_ratings: [1, 1, 1, 3, 3, 3, 3] })
    ).toBe(false);
  });

  it("considers recent failures from longer history", () => {
    expect(
      isStruggling({ ...base, last_ratings: [3, 3, 3, 1, 1] })
    ).toBe(true);
  });

  it("returns false with no ratings", () => {
    expect(isStruggling({ ...base })).toBe(false);
  });
});

describe("isRecent", () => {
  it("returns true for learning state with few reviews", () => {
    expect(isRecent({ ...base, state: "learning", times_seen: 2 })).toBe(true);
  });

  it("returns false for known state", () => {
    expect(isRecent({ ...base, state: "known", times_seen: 2 })).toBe(false);
  });

  it("returns false for too many reviews", () => {
    expect(isRecent({ ...base, state: "learning", times_seen: 5 })).toBe(
      false
    );
  });

  it("boundary: exactly 4 reviews", () => {
    expect(isRecent({ ...base, state: "learning", times_seen: 4 })).toBe(true);
  });

  it("returns true for 0 reviews (just introduced)", () => {
    expect(isRecent({ ...base, state: "learning", times_seen: 0 })).toBe(true);
  });
});

describe("isSolid", () => {
  it("returns true for high knowledge score", () => {
    expect(isSolid({ ...base, knowledge_score: 85 })).toBe(true);
  });

  it("returns false for low score", () => {
    expect(isSolid({ ...base, knowledge_score: 50 })).toBe(false);
  });

  it("boundary: exactly 70", () => {
    expect(isSolid({ ...base, knowledge_score: 70 })).toBe(true);
  });

  it("boundary: 69", () => {
    expect(isSolid({ ...base, knowledge_score: 69 })).toBe(false);
  });
});

describe("filter combinations", () => {
  const words: Word[] = [
    { id: 1, state: "learning", times_seen: 10, times_correct: 3, knowledge_score: 25, last_ratings: [1, 1, 1, 1] },
    { id: 2, state: "learning", times_seen: 2, times_correct: 2, knowledge_score: 30, last_ratings: [3, 3] },
    { id: 3, state: "known", times_seen: 20, times_correct: 18, knowledge_score: 90, last_ratings: [3, 3, 3, 3] },
    { id: 4, state: "learning", times_seen: 8, times_correct: 5, knowledge_score: 45, last_ratings: [3, 1, 2, 3] },
    { id: 5, state: "new", times_seen: 0, times_correct: 0, knowledge_score: 0 },
  ];

  it("a word can be both leech and struggling", () => {
    const w = words[0]; // 10 seen, 3 correct, all 1s
    expect(isLeech(w)).toBe(true);
    expect(isStruggling(w)).toBe(true);
  });

  it("recently learned word is not a leech", () => {
    const w = words[1]; // 2 seen, learning
    expect(isRecent(w)).toBe(true);
    expect(isLeech(w)).toBe(false);
  });

  it("solid word is not struggling", () => {
    const w = words[2]; // 90 score, all 3s
    expect(isSolid(w)).toBe(true);
    expect(isStruggling(w)).toBe(false);
  });

  it("filters produce expected counts", () => {
    expect(words.filter(isLeech).length).toBe(1);
    expect(words.filter(isStruggling).length).toBe(2); // id 1 and 4
    expect(words.filter(isRecent).length).toBe(1);
    expect(words.filter(isSolid).length).toBe(1);
  });
});
