import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Updates from "expo-updates";

import { BASE_URL } from "./api";

const LAST_REPORTED_KEY = "@alif/update-errors-reported-until";
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

/**
 * Send new expo-updates error/fatal log entries to the server (as a
 * `card_shown` interaction with card_type "update_error").
 *
 * When an OTA bundle crashes on launch, expo-updates rolls back to the last
 * working update and the iOS crash report only shows its own abort, not the
 * JS error. The rolled-back bundle can still read the updates log, so this
 * is the one place the real error is recoverable.
 */
export async function reportUpdateErrors(): Promise<void> {
  if (Platform.OS === "web") return;
  try {
    const entries = await Updates.readLogEntriesAsync(MAX_AGE_MS);
    const since = Number(await AsyncStorage.getItem(LAST_REPORTED_KEY)) || 0;
    const fresh = entries.filter(
      (e) => (e.level === "error" || e.level === "fatal") && e.timestamp > since
    );
    if (fresh.length === 0) return;
    const res = await fetch(`${BASE_URL}/api/review/log-card-shown`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        card_type: "update_error",
        detail: {
          running_update_id: Updates.updateId ?? null,
          entries: fresh.slice(-20).map((e) => ({
            ts: e.timestamp,
            level: e.level,
            code: e.code,
            update_id: e.updateId ?? null,
            message: (e.message ?? "").slice(0, 4000),
            stacktrace: (e.stacktrace ?? []).slice(0, 40),
          })),
        },
      }),
    });
    if (res.ok) {
      await AsyncStorage.setItem(
        LAST_REPORTED_KEY,
        String(Math.max(...fresh.map((e) => e.timestamp)))
      );
    }
  } catch {
    // Diagnostics must never affect the app.
  }
}
