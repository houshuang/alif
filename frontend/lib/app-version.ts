import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import * as Updates from "expo-updates";

const SEEN_UPDATE_KEY = "@alif/seen-update-id";

export interface VersionInfo {
  /** app.json "version" (== runtimeVersion under the appVersion policy) */
  appVersion: string;
  /** Full EAS update UUID, or null when running the embedded bundle / dev / web */
  updateId: string | null;
  /** First 8 chars of the update UUID — enough to match against `eas update:list` */
  shortUpdateId: string | null;
  /** When the running update was published (null for embedded/dev/web) */
  publishedAt: Date | null;
  /** True when running the JS bundle compiled into the binary */
  isEmbedded: boolean;
}

export function getVersionInfo(): VersionInfo {
  let updateId: string | null = null;
  let publishedAt: Date | null = null;
  let isEmbedded = true;
  try {
    updateId = Updates.updateId ?? null;
    publishedAt = Updates.createdAt ?? null;
    isEmbedded = Updates.isEmbeddedLaunch;
  } catch {
    // expo-updates is inert on web and in dev clients — treat as embedded
  }
  return {
    appVersion: Constants.expoConfig?.version ?? "?",
    updateId,
    shortUpdateId: updateId ? updateId.slice(0, 8) : null,
    publishedAt,
    isEmbedded,
  };
}

/** One-line description for the More screen, e.g.
 *  "v1.0.0 · update 019f98d2 · Jul 25, 14:33" or "v1.0.0 · embedded bundle" */
export function versionLabel(info: VersionInfo = getVersionInfo()): string {
  const parts = [`v${info.appVersion}`];
  if (info.updateId) {
    parts.push(`update ${info.shortUpdateId}`);
    if (info.publishedAt) {
      parts.push(
        info.publishedAt.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      );
    }
  } else {
    parts.push("embedded bundle");
  }
  return parts.join(" · ");
}

/**
 * Returns VersionInfo once per newly-applied OTA update (the first launch that
 * runs a new updateId), null otherwise. Embedded launches and fresh installs
 * record their identity silently — the toast is only for OTA arrivals.
 */
export async function detectNewlyAppliedUpdate(): Promise<VersionInfo | null> {
  const info = getVersionInfo();
  const identity = info.updateId ?? `embedded:${info.appVersion}`;
  try {
    const seen = await AsyncStorage.getItem(SEEN_UPDATE_KEY);
    if (seen === identity) return null;
    await AsyncStorage.setItem(SEEN_UPDATE_KEY, identity);
    // First run after install (or after a native rebuild) is not an OTA event.
    if (!info.updateId) return null;
    return info;
  } catch {
    return null;
  }
}
