// Rapid re-exposure re-test experiment (2026-07-25, conformance v2).
// Pure helpers extracted from app/index.tsx so the queue state machine is
// unit-testable. A rating-1 failure in the treatment arm earns one active
// bare-recall re-test a few minutes later; timing is wall-clock based — the
// <10min massed-practice window is deliberately avoided.

export const RETEST_MIN_GAP_MS = 4 * 60 * 1000;
export const RETEST_EXPIRE_MS = 20 * 60 * 1000;
export const RETEST_MIN_CARDS_BETWEEN = 3;
export const CHECKPOINT_MAX_PER_SESSION = 3;
/** Experiment protocol version. v1 = launch day (pre-conformance); v2 = rating-1-only
 * auto wrap-up, requeue-on-fetch-failure, counter-neutral guarded credit. */
export const RETEST_PROTOCOL_VERSION = 2;

export interface RetestEntry {
  lemmaId: number;
  failedAtMs: number;
  cardsSince: number;
}

/**
 * Deterministic 50/50 arm assignment per (session, lemma) failure event.
 * djb2-xor over `${sessionId}:${lemmaId}`, unsigned-32; even hash → treatment.
 * Python mirror for analysis:
 *   h = 5381
 *   for c in f"{session_id}:{lemma_id}": h = (((h * 33) & 0xFFFFFFFF) ^ ord(c))
 *   arm = "treatment" if h % 2 == 0 else "control"
 */
export function retestArm(sessionId: string, lemmaId: number): "treatment" | "control" {
  const s = `${sessionId}:${lemmaId}`;
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(h, 33) ^ s.charCodeAt(i)) >>> 0;
  }
  return h % 2 === 0 ? "treatment" : "control";
}

/**
 * Prune expired entries in place and return the first matured entry, or null.
 * Does NOT remove the returned entry — the caller removes it only after the
 * card fetch returns a non-empty result (a network failure must not consume
 * the learner's one re-test; conformance fix 2026-07-25).
 */
export function pickReadyRetest(queue: RetestEntry[], nowMs: number): {
  queue: RetestEntry[];
  ready: RetestEntry | null;
} {
  const pruned = queue.filter(e => nowMs - e.failedAtMs < RETEST_EXPIRE_MS);
  const ready =
    pruned.find(
      e =>
        nowMs - e.failedAtMs >= RETEST_MIN_GAP_MS &&
        e.cardsSince >= RETEST_MIN_CARDS_BETWEEN
    ) ?? null;
  return { queue: pruned, ready };
}
