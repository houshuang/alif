// Missing-word retries: wall-clock eligibility survives batches and app restarts.

export const RETEST_MIN_GAP_MS = 4 * 60 * 1000;
export const RETEST_EXPIRE_MS = 24 * 60 * 60 * 1000;
export const RETEST_SPACING_MS = 60 * 1000;
// With this many checks waiting the one-minute spacing is dropped, so a run of
// dense cards (several misses each) cannot outpace the queue until entries expire.
export const RETEST_BACKLOG_NO_SPACING = 3;
export const RETEST_PROTOCOL_VERSION = 4;

export interface RetestEntry {
  lemmaId: number;
  failedAtMs: number;
  cardsSince: number;
}

/** Return the newest eligible retry without consuming it: the miss closest to
 * the four-minute mark benefits most, and older ones are still inside their
 * 24 hours. The caller removes it on answer; failed requests and cold starts
 * keep the opportunity alive. */
export function pickReadyRetest(queue: RetestEntry[], nowMs: number, lastShownAtMs = 0): {
  queue: RetestEntry[];
  ready: RetestEntry | null;
} {
  const pruned = queue.filter(e => nowMs - e.failedAtMs < RETEST_EXPIRE_MS);
  const eligible = pruned.filter(e => nowMs - e.failedAtMs >= RETEST_MIN_GAP_MS);
  const spaced = eligible.length < RETEST_BACKLOG_NO_SPACING
    && nowMs - lastShownAtMs < RETEST_SPACING_MS;
  const ready = spaced || eligible.length === 0 ? null :
    eligible.reduce((best, e) => (e.failedAtMs > best.failedAtMs ? e : best));
  return { queue: pruned, ready };
}
