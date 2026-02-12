import AsyncStorage from "@react-native-async-storage/async-storage";
import { BASE_URL } from "./api";
import { syncEvents } from "./sync-events";
import { invalidateSessions, updateCachedStoryStatus } from "./offline-store";

const QUEUE_KEY = "@alif/sync-queue";
const MAX_RETRY_ATTEMPTS = 8;

export type QueueEntryType =
  | "sentence"
  | "story_complete";

export interface QueueEntry {
  id: string;
  type: QueueEntryType;
  payload: Record<string, unknown>;
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

async function getQueueUnsafe(): Promise<QueueEntry[]> {
  const raw = await AsyncStorage.getItem(QUEUE_KEY);
  return raw ? JSON.parse(raw) : [];
}

async function saveQueueUnsafe(queue: QueueEntry[]): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
}

export async function enqueueReview(
  type: QueueEntryType,
  payload: Record<string, unknown>,
  clientReviewId: string
): Promise<void> {
  await withQueueLock(async () => {
    const queue = await getQueueUnsafe();
    queue.push({
      id: clientReviewId,
      type,
      payload,
      client_review_id: clientReviewId,
      created_at: new Date().toISOString(),
      attempts: 0,
    });
    await saveQueueUnsafe(queue);
  });
}

export async function removeFromQueue(
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

const STORY_ACTION_ENDPOINTS: Record<string, string> = {
  story_complete: "complete",
};

const STORY_ACTION_STATUSES: Record<string, string> = {
  story_complete: "completed",
};

async function flushStoryEntries(
  entries: QueueEntry[]
): Promise<{ synced: Set<string>; attempted: Set<string> }> {
  const synced = new Set<string>();
  const attempted = new Set<string>();
  for (const entry of entries) {
    const action = STORY_ACTION_ENDPOINTS[entry.type];
    if (!action) continue;
    const storyId = entry.payload.story_id;
    try {
      const res = await fetch(`${BASE_URL}/api/stories/${storyId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          looked_up_lemma_ids: entry.payload.looked_up_lemma_ids ?? [],
          reading_time_ms: entry.payload.reading_time_ms,
        }),
      });
      attempted.add(entry.client_review_id);
      if (res.ok || res.status === 409) {
        synced.add(entry.client_review_id);
      }
    } catch {
      // network error — keep in queue
    }
  }
  return { synced, attempted };
}

async function flushQueueInternal(): Promise<{ synced: number; failed: number }> {
  const queue = await withQueueLock(async () => getQueueUnsafe());
  if (queue.length === 0) return { synced: 0, failed: 0 };

  const reviewEntries = queue.filter((e) => e.type === "sentence");
  const storyEntries = queue.filter((e) => e.type in STORY_ACTION_ENDPOINTS);
  const snapshotIds = new Set(queue.map((entry) => entry.client_review_id));

  let totalSynced = 0;
  const removable = new Set<string>();
  const attempted = new Set<string>();

  // Flush review entries as batch
  if (reviewEntries.length > 0) {
    try {
      const res = await fetch(`${BASE_URL}/api/review/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reviews: reviewEntries.map((entry) => ({
            type: entry.type,
            payload: entry.payload,
            client_review_id: entry.client_review_id,
          })),
        }),
      });

      if (res.ok) {
        const data = await res.json();
        const results: { client_review_id: string; status: string }[] = data.results ?? [];
        const resultIds = new Set<string>();
        for (const r of results) {
          attempted.add(r.client_review_id);
          resultIds.add(r.client_review_id);
          if (r.status === "ok" || r.status === "duplicate") {
            removable.add(r.client_review_id);
          }
        }
        // Treat omitted entries as attempted failures so they can retry and eventually drop.
        for (const entry of reviewEntries) {
          if (!resultIds.has(entry.client_review_id)) {
            attempted.add(entry.client_review_id);
          }
        }
      } else {
        for (const entry of reviewEntries) {
          attempted.add(entry.client_review_id);
        }
      }
    } catch {
      // network error — keep all review entries
    }
  }

  // Flush story entries individually
  if (storyEntries.length > 0) {
    const storyResult = await flushStoryEntries(storyEntries);
    const storySynced = storyResult.synced;
    for (const id of storyResult.attempted) {
      attempted.add(id);
    }
    for (const id of storySynced) {
      removable.add(id);
    }
  }

  totalSynced = removable.size;
  let failed = 0;
  await withQueueLock(async () => {
    const currentQueue = await getQueueUnsafe();
    const updated: QueueEntry[] = [];
    for (const entry of currentQueue) {
      const entryId = entry.client_review_id;

      // Preserve entries added while this flush was in progress.
      if (!snapshotIds.has(entryId)) {
        updated.push(entry);
        continue;
      }

      if (removable.has(entryId)) {
        continue;
      }

      if (!attempted.has(entryId)) {
        updated.push(entry);
        continue;
      }

      const nextAttempts = entry.attempts + 1;
      if (nextAttempts >= MAX_RETRY_ATTEMPTS) {
        console.warn(
          "dropping sync queue entry after max attempts:",
          entry.type,
          entry.client_review_id
        );
        continue;
      }
      updated.push({ ...entry, attempts: nextAttempts });
    }
    failed = updated.length;
    await saveQueueUnsafe(updated);
  });

  if (totalSynced > 0) {
    for (const entry of storyEntries) {
      if (!removable.has(entry.client_review_id)) continue;
      const storyId = Number(entry.payload.story_id);
      const nextStatus = STORY_ACTION_STATUSES[entry.type];
      if (!Number.isFinite(storyId) || !nextStatus) continue;
      await updateCachedStoryStatus(storyId, nextStatus).catch(() => {});
    }
    await invalidateSessions();
    syncEvents.emit("synced");
  }

  return { synced: totalSynced, failed };
}

export async function flushQueue(): Promise<{ synced: number; failed: number }> {
  if (flushInFlight) {
    return flushInFlight;
  }
  flushInFlight = flushQueueInternal().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}

export async function pendingCount(): Promise<number> {
  const queue = await withQueueLock(async () => getQueueUnsafe());
  return queue.length;
}
