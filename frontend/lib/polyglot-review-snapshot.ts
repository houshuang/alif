/**
 * Polyglot review-session snapshot — pure helpers, no React or AsyncStorage.
 *
 * The review screen (polyglot-review.tsx) persists an in-flight session to
 * AsyncStorage so a remount (e.g. round-tripping into the lemma-detail screen,
 * or a fast app reload) can rehydrate exactly where the learner left off.
 * Greek and Latin share the same screen, so:
 *
 *   1. `reviewSnapshotKey(lang)` is per-language. Switching languages reads
 *      the OTHER language's snapshot (or none) instead of the wrong language's
 *      sentences. Old single-key snapshots written by pre-2026-05-26 builds
 *      are orphaned at `@polyglot:reviewSnapshot` (unsuffixed) and ignored.
 *   2. The snapshot ALSO carries its own `language` tag on the inside, and
 *      `isReviewSnapshotValid` rejects a tag mismatch. Defense in depth: a
 *      manual storage edit, a stale legacy unsuffixed snapshot, or a future
 *      key-scheme bug can't accidentally cross-pollinate.
 *
 * Regression context (2026-05-25 → -26): previously this was a single
 * language-blind key + a one-shot mount guard, so switching el↔la rehydrated
 * the previous language's session. See research/experiment-log.md and the
 * "Language-switch correctness" note in polyglot/CLAUDE.md.
 */
import type {
  AcquisitionStats,
  ReviewSessionBundle,
} from "./polyglot-api";
import type { SessionSlot } from "./polyglot-review-helpers";

export const REVIEW_SNAPSHOT_TTL_MS = 15 * 60 * 1000;

export const reviewSnapshotKey = (lang: string): string =>
  `@polyglot:reviewSnapshot:${lang}`;

export type ReviewSnapshotCardState = "front" | "back";

export type ReviewSnapshot = {
  language: string;
  bundle: ReviewSessionBundle;
  slots: SessionSlot[];
  stats: AcquisitionStats | null;
  index: number;
  cardState: ReviewSnapshotCardState;
  // `Set` isn't JSON-serialisable, so marks ride as arrays in the snapshot and
  // are re-hydrated into Sets in the component.
  marks: { missed: number[]; confused: number[] };
  glossWordIdx: number | null;
  sessionId: string;
  shownIntroLemmaIds: number[];
  savedAt: number;
};

/**
 * True when `snap` may be rehydrated for `language` right now.
 *
 * Conservative: a null snapshot, language mismatch, expired timestamp, or
 * empty session all return false. Callers should fall through to
 * loadSession() on false.
 */
export function isReviewSnapshotValid(
  snap: ReviewSnapshot | null | undefined,
  language: string,
  opts: { now?: number; ttlMs?: number } = {},
): boolean {
  if (!snap) return false;
  if (snap.language !== language) return false;
  const now = opts.now ?? Date.now();
  const ttl = opts.ttlMs ?? REVIEW_SNAPSHOT_TTL_MS;
  if (now - (snap.savedAt ?? 0) > ttl) return false;
  if (!snap.slots || snap.slots.length === 0) return false;
  return true;
}
