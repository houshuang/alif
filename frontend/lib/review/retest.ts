// Missing-word retries: wall-clock eligibility survives batches and app restarts.

export const RETEST_MIN_GAP_MS = 4 * 60 * 1000;
export const RETEST_EXPIRE_MS = 24 * 60 * 60 * 1000;
export const RETEST_SPACING_MS = 60 * 1000;
export const RETEST_PROTOCOL_VERSION = 3;

export interface RetestEntry {
  lemmaId: number;
  failedAtMs: number;
  cardsSince: number;
}

/** Return the oldest eligible retry without consuming it. The caller removes
 * it on answer; failed requests and cold starts keep the opportunity alive. */
export function pickReadyRetest(queue: RetestEntry[], nowMs: number, lastShownAtMs = 0): {
  queue: RetestEntry[];
  ready: RetestEntry | null;
} {
  const pruned = queue.filter(e => nowMs - e.failedAtMs < RETEST_EXPIRE_MS);
  const ready = nowMs - lastShownAtMs < RETEST_SPACING_MS ? null :
    pruned.find(
      e =>
        nowMs - e.failedAtMs >= RETEST_MIN_GAP_MS
    ) ?? null;
  return { queue: pruned, ready };
}
