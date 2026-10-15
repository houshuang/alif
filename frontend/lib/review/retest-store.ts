import AsyncStorage from "@react-native-async-storage/async-storage";
import { RetestEntry, pickReadyRetest } from "./retest";

const KEY = "@alif/missing-word-retries/v3";
let writes: Promise<void> = Promise.resolve();

export async function loadRetests(): Promise<{ queue: RetestEntry[]; lastShownAtMs: number }> {
  try {
    const raw = JSON.parse((await AsyncStorage.getItem(KEY)) ?? "null");
    const entries = Array.isArray(raw?.queue) ? raw.queue.filter((e: RetestEntry) =>
      Number.isInteger(e?.lemmaId) && e.lemmaId > 0 &&
      Number.isFinite(e.failedAtMs) && e.failedAtMs <= Date.now() &&
      Number.isFinite(e.cardsSince) && e.cardsSince >= 0
    ) : [];
    return {
      queue: pickReadyRetest(entries, Date.now()).queue,
      lastShownAtMs: Number.isFinite(raw?.lastShownAtMs) ? raw.lastShownAtMs : 0,
    };
  } catch {
    return { queue: [], lastShownAtMs: 0 };
  }
}

export function saveRetests(queue: RetestEntry[], lastShownAtMs: number): void {
  const snapshot = JSON.stringify({ queue, lastShownAtMs });
  writes = writes.catch(() => {}).then(() => AsyncStorage.setItem(KEY, snapshot));
  void writes.catch(() => {});
}
