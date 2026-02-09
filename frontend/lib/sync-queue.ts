import AsyncStorage from "@react-native-async-storage/async-storage";
import { BASE_URL } from "./api";
import { syncEvents } from "./sync-events";
import { invalidateSessions } from "./offline-store";

const QUEUE_KEY = "@alif/sync-queue";
const MAX_RETRY_ATTEMPTS = 8;

export type QueueEntryType =
  | "sentence"
  | "legacy"
  | "story_complete"
  | "story_skip"
  | "story_too_difficult";

export interface QueueEntry {
  id: string;
  type: QueueEntryType;
  payload: Record<string, unknown>;
  client_review_id: string;
  created_at: string;
  attempts: number;
}

async function getQueue(): Promise<QueueEntry[]> {
  const raw = await AsyncStorage.getItem(QUEUE_KEY);
  return raw ? JSON.parse(raw) : [];
}

async function saveQueue(queue: QueueEntry[]): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
}

export async function enqueueReview(
  type: QueueEntryType,
  payload: Record<string, unknown>,
  clientReviewId: string
): Promise<void> {
  const queue = await getQueue();
  queue.push({
    id: clientReviewId,
    type,
    payload,
    client_review_id: clientReviewId,
    created_at: new Date().toISOString(),
    attempts: 0,
  });
  await saveQueue(queue);
}

const STORY_ACTION_ENDPOINTS: Record<string, string> = {
  story_complete: "complete",
  story_skip: "skip",
  story_too_difficult: "too-difficult",
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

export async function flushQueue(): Promise<{ synced: number; failed: number }> {
  const queue = await getQueue();
  if (queue.length === 0) return { synced: 0, failed: 0 };

  const reviewEntries = queue.filter((e) => e.type === "sentence" || e.type === "legacy");
  const storyEntries = queue.filter((e) => e.type in STORY_ACTION_ENDPOINTS);

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
  const updated: QueueEntry[] = [];
  for (const entry of queue) {
    if (removable.has(entry.client_review_id)) {
      continue;
    }

    if (!attempted.has(entry.client_review_id)) {
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
  await saveQueue(updated);

  if (totalSynced > 0) {
    await invalidateSessions();
    syncEvents.emit("synced");
  }

  return { synced: totalSynced, failed: updated.length };
}

export async function pendingCount(): Promise<number> {
  const queue = await getQueue();
  return queue.length;
}
