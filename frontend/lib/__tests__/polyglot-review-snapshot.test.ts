/**
 * Regression tests for the per-language review snapshot.
 *
 * Bug fixed 2026-05-25/-26: the review screen used to read a single shared
 * AsyncStorage key (`@polyglot:reviewSnapshot`) and a one-shot mount guard,
 * so switching from Greek to Latin would either rehydrate the Greek session
 * under Latin or not reload at all. The fix is twofold — a per-language key
 * AND a `language` tag inside the payload that the validator checks. These
 * tests pin both so a future refactor can't silently undo either gate.
 */
import {
  REVIEW_SNAPSHOT_TTL_MS,
  isReviewSnapshotValid,
  reviewSnapshotKey,
  type ReviewSnapshot,
} from "../polyglot-review-snapshot";

const baseSnap = (overrides: Partial<ReviewSnapshot> = {}): ReviewSnapshot => ({
  language: "el",
  bundle: { sentences: [], intro_cards: [] },
  // Non-empty slots so the "empty session" guard doesn't kick in. The slot
  // shape is opaque to the validator; cast keeps the fixture minimal.
  slots: [{ type: "sentence", sentenceIndex: 0 } as any],
  stats: null,
  index: 0,
  cardState: "front",
  marks: { missed: [], confused: [] },
  glossWordIdx: null,
  sessionId: "sess-test",
  shownIntroLemmaIds: [],
  savedAt: 1_700_000_000_000,
  ...overrides,
});

describe("reviewSnapshotKey", () => {
  it("returns a per-language key so Greek and Latin can't share storage", () => {
    expect(reviewSnapshotKey("el")).toBe("@polyglot:reviewSnapshot:el");
    expect(reviewSnapshotKey("la")).toBe("@polyglot:reviewSnapshot:la");
    expect(reviewSnapshotKey("el")).not.toBe(reviewSnapshotKey("la"));
  });

  it("never returns the legacy unsuffixed key (would re-introduce cross-language leak)", () => {
    for (const lang of ["el", "la", "grc"]) {
      expect(reviewSnapshotKey(lang)).not.toBe("@polyglot:reviewSnapshot");
    }
  });
});

describe("isReviewSnapshotValid", () => {
  const now = baseSnap().savedAt + 1000; // 1s after savedAt — comfortably fresh

  it("accepts a fresh snapshot whose language tag matches", () => {
    expect(isReviewSnapshotValid(baseSnap(), "el", { now })).toBe(true);
  });

  it("rejects a snapshot whose language tag doesn't match (cross-language guard)", () => {
    // The exact bug we shipped a fix for: Greek snapshot, Latin active.
    expect(isReviewSnapshotValid(baseSnap({ language: "el" }), "la", { now })).toBe(false);
    expect(isReviewSnapshotValid(baseSnap({ language: "la" }), "el", { now })).toBe(false);
  });

  it("rejects an expired snapshot", () => {
    const stale = baseSnap();
    const wayLater = stale.savedAt + REVIEW_SNAPSHOT_TTL_MS + 1;
    expect(isReviewSnapshotValid(stale, "el", { now: wayLater })).toBe(false);
  });

  it("accepts a snapshot exactly at the TTL boundary (inclusive)", () => {
    const snap = baseSnap();
    const atTtl = snap.savedAt + REVIEW_SNAPSHOT_TTL_MS;
    expect(isReviewSnapshotValid(snap, "el", { now: atTtl })).toBe(true);
  });

  it("rejects a snapshot with no slots (the session is already spent)", () => {
    expect(isReviewSnapshotValid(baseSnap({ slots: [] }), "el", { now })).toBe(false);
  });

  it("rejects null / undefined defensively", () => {
    expect(isReviewSnapshotValid(null, "el", { now })).toBe(false);
    expect(isReviewSnapshotValid(undefined, "el", { now })).toBe(false);
  });

  it("rejects a snapshot missing the language tag (legacy unsuffixed payload)", () => {
    // Old persisted shape from before the fix: no `language` field. JSON.parse
    // would surface `language: undefined`, which must never equal the active
    // language string.
    const legacy = { ...baseSnap(), language: undefined as unknown as string };
    expect(isReviewSnapshotValid(legacy as ReviewSnapshot, "el", { now })).toBe(false);
    expect(isReviewSnapshotValid(legacy as ReviewSnapshot, "la", { now })).toBe(false);
  });

  it("honours a caller-supplied TTL override", () => {
    const snap = baseSnap();
    const oneMinTtl = 60 * 1000;
    expect(isReviewSnapshotValid(snap, "el", { now: snap.savedAt + 30_000, ttlMs: oneMinTtl })).toBe(true);
    expect(isReviewSnapshotValid(snap, "el", { now: snap.savedAt + 90_000, ttlMs: oneMinTtl })).toBe(false);
  });
});
