import AsyncStorage from "@react-native-async-storage/async-storage";
import { BASE_URL } from "./api";
import { syncEvents } from "./sync-events";
import { invalidateSessions } from "./offline-store";

const QUEUE_KEY = "@alif/sync-queue";

export interface QueueEntry {
  id: string;
  type: "sentence" | "legacy";
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
  type: "sentence" | "legacy",
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

export async function flushQueue(): Promise<{ synced: number; failed: number }> {
  const queue = await getQueue();
  if (queue.length === 0) return { synced: 0, failed: 0 };

  try {
    const res = await fetch(`${BASE_URL}/api/review/sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reviews: queue.map((entry) => ({
          type: entry.type,
          payload: entry.payload,
          client_review_id: entry.client_review_id,
        })),
      }),
    });

    if (!res.ok) {
      const updated = queue.map((e) => ({ ...e, attempts: e.attempts + 1 }));
      await saveQueue(updated);
      return { synced: 0, failed: queue.length };
    }

    const data = await res.json();
    const results: { client_review_id: string; status: string }[] = data.results;

    const removable = new Set(
      results
        .filter((r) => r.status === "ok" || r.status === "duplicate")
        .map((r) => r.client_review_id)
    );
    const remaining = queue.filter((e) => !removable.has(e.client_review_id));
    await saveQueue(remaining);

    const synced = removable.size;
    const failed = remaining.length;

    if (synced > 0) {
      await invalidateSessions();
      syncEvents.emit("synced");
    }

    return { synced, failed };
  } catch {
    return { synced: 0, failed: queue.length };
  }
}

export async function pendingCount(): Promise<number> {
  const queue = await getQueue();
  return queue.length;
}
