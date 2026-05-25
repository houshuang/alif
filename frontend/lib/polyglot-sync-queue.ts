/**
 * Offline queue for the Polyglot reader — the page-advance equivalent of
 * Alif's `sync-queue.ts`.
 *
 * Why a separate queue: Alif's `sync-queue.ts` is hardwired to Alif's
 * `BASE_URL` and Alif endpoints, so it can't carry a polyglot action. This one
 * posts to `POLYGLOT_BASE_URL` and queues exactly one entry type — `page_review`
 * (advancing a page). The page submit is the authoritative, self-contained
 * record of a page outcome: it carries the red/yellow taps inline, so one
 * queued entry fully describes the page (`apply_page_review` applies the taps on
 * replay) and is idempotent on `client_review_id` (polyglot Hard Invariant 11).
 *
 * Per-tap `markWord` stays a best-effort online call (live gloss + instant SRS
 * enrolment); it is NOT queued — the page submit carries the same taps and is
 * the source of truth once the page is advanced.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { POLYGLOT_BASE_URL } from "./polyglot-api";
import { syncEvents } from "./sync-events";

const QUEUE_KEY = "@polyglot/sync-queue";
const MAX_RETRY_ATTEMPTS = 8;

export interface PageReviewPayload {
  story_id: number;
  page_number: number;
  unknown_lemma_ids: number[];
  encountered_lemma_ids: number[];
  session_id?: string | null;
}

export interface PolyglotQueueEntry {
  id: string;
  type: "page_review";
  payload: PageReviewPayload;
  client_review_id: string;
  created_at: string;
  attempts: number;
}

let queueLock: Promise<void> = Promise.resolve();
let flushInFlight: Promise<{ synced: number; failed: number }> | null = null;

async function withQueueLock<T>(fn: () => Promise<T>): Promise<T> {
  const previous = queueLock;
  let release!: () => void;
  queueLock = new Promise<void>((resolve) => {
    release = resolve;
  });
  await previous;
  try {
    return await fn();
  } finally {
    release();
  }
}

async function getQueueUnsafe(): Promise<PolyglotQueueEntry[]> {
  const raw = await AsyncStorage.getItem(QUEUE_KEY);
  return raw ? JSON.parse(raw) : [];
}

async function saveQueueUnsafe(queue: PolyglotQueueEntry[]): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
}

export async function enqueuePageReview(
  payload: PageReviewPayload,
  clientReviewId: string
): Promise<void> {
  await withQueueLock(async () => {
    const queue = await getQueueUnsafe();
    // `clientReviewId` is deterministic per (story, page) — see
    // polyglot-review-helpers.pageReviewClientId. Re-advancing a page (the
    // learner went back then forward, or edited marks offline before the first
    // send) must REPLACE the queued entry so the latest red/yellow taps are the
    // ones that replay, not the stale first attempt. An already-sent entry is no
    // longer in the queue, so this is a plain push in the common case; the
    // server is idempotent on the same id anyway (page_review_log).
    const next = queue.filter((e) => e.client_review_id !== clientReviewId);
    next.push({
      id: clientReviewId,
      type: "page_review",
      payload,
      client_review_id: clientReviewId,
      created_at: new Date().toISOString(),
      attempts: 0,
    });
    await saveQueueUnsafe(next);
  });
}

export async function removeFromPolyglotQueue(
  clientReviewId: string
): Promise<boolean> {
  let found = false;
  await withQueueLock(async () => {
    const queue = await getQueueUnsafe();
    const idx = queue.findIndex((e) => e.client_review_id === clientReviewId);
    if (idx >= 0) {
      queue.splice(idx, 1);
      await saveQueueUnsafe(queue);
      found = true;
    }
  });
  return found;
}

// "ok" → drain; "http_error" → real server response, count an attempt and
// eventually drop; "network_error" → offline, keep the entry WITHOUT burning a
// retry (being offline a while must not silently drop a page outcome).
async function postEntry(
  entry: PolyglotQueueEntry
): Promise<"ok" | "http_error" | "network_error"> {
  const { story_id, page_number } = entry.payload;
  try {
    const res = await fetch(
      `${POLYGLOT_BASE_URL}/api/texts/${story_id}/pages/${page_number}/mark_remaining`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          unknown_lemma_ids: entry.payload.unknown_lemma_ids,
          encountered_lemma_ids: entry.payload.encountered_lemma_ids,
          client_review_id: entry.client_review_id,
          session_id: entry.payload.session_id ?? null,
        }),
      }
    );
    // 200 covers both a fresh apply and an idempotent replay (the backend
    // returns `duplicate: true` in the body, still 200). 409 is accepted for
    // parity with Alif's queue even though polyglot doesn't currently emit it.
    if (res.ok || res.status === 409) return "ok";
    return "http_error";
  } catch {
    return "network_error";
  }
}

async function flushQueueInternal(): Promise<{ synced: number; failed: number }> {
  const snapshot = await withQueueLock(async () => getQueueUnsafe());
  if (snapshot.length === 0) return { synced: 0, failed: 0 };

  const removable = new Set<string>();
  const attempted = new Set<string>();
  const snapshotIds = new Set(snapshot.map((e) => e.client_review_id));

  for (const entry of snapshot) {
    const result = await postEntry(entry);
    if (result === "ok") {
      removable.add(entry.client_review_id);
    } else if (result === "http_error") {
      attempted.add(entry.client_review_id);
    }
    // network_error: leave untouched — neither drained nor counted as an attempt.
  }

  let failed = 0;
  await withQueueLock(async () => {
    const current = await getQueueUnsafe();
    const updated: PolyglotQueueEntry[] = [];
    for (const entry of current) {
      const id = entry.client_review_id;
      // Preserve entries added while this flush was in progress.
      if (!snapshotIds.has(id)) {
        updated.push(entry);
        continue;
      }
      if (removable.has(id)) continue;
      if (!attempted.has(id)) {
        updated.push(entry);
        continue;
      }
      const nextAttempts = entry.attempts + 1;
      if (nextAttempts >= MAX_RETRY_ATTEMPTS) {
        console.warn("dropping polyglot page_review after max attempts:", id);
        continue;
      }
      updated.push({ ...entry, attempts: nextAttempts });
    }
    failed = updated.length;
    await saveQueueUnsafe(updated);
  });

  const synced = removable.size;
  if (synced > 0) syncEvents.emit("polyglot-synced");
  return { synced, failed };
}

export async function flushPolyglotQueue(): Promise<{ synced: number; failed: number }> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = flushQueueInternal().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}

export async function pendingPolyglotCount(): Promise<number> {
  const queue = await withQueueLock(async () => getQueueUnsafe());
  return queue.length;
}
