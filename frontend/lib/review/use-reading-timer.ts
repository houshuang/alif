import { useEffect, useRef, useState } from "react";
import { AppState, Platform } from "react-native";
import { useIsFocused } from "@react-navigation/native";
import { ReadingTimer } from "./reading-timer";

export function useReadingTimer(key: string | null, visible: boolean) {
  const focused = useIsFocused();
  const ref = useRef<{ key: string | null; timer: ReadingTimer }>({ key: null, timer: new ReadingTimer() });
  const [ignored, setIgnored] = useState(false);
  useEffect(() => {
    const timer = new ReadingTimer();
    ref.current = { key, timer }; setIgnored(false);
  }, [key]);
  useEffect(() => {
    const timer = ref.current.timer;
    if (!key || !focused || !visible) return;
    // Start only after the visible card has had a render opportunity, excluding
    // cache/network/grammar screens. An immediate answer before this is unmeasured.
    const frame = requestAnimationFrame(() => {
      timer.start();
      if (AppState.currentState !== "active") timer.exclude("background");
      if (Platform.OS === "web" && (document.hidden || !document.hasFocus())) timer.exclude("background");
    });
    const pulse = setInterval(() => timer.heartbeat(), 1000);
    const change = AppState.addEventListener("change", state => {
      if (state !== "active") { timer.interrupt("background"); setIgnored(true); }
    });
    const blur = Platform.OS === "android" ? AppState.addEventListener("blur", () => {
      timer.interrupt("background"); setIgnored(true);
    }) : null;
    const onBlur = () => { timer.interrupt("background"); setIgnored(true); };
    const hidden = () => { if (document.hidden) onBlur(); };
    if (Platform.OS === "web") {
      document.addEventListener("visibilitychange", hidden); window.addEventListener("blur", onBlur);
    }
    return () => {
      cancelAnimationFrame(frame); clearInterval(pulse); change.remove(); blur?.remove();
      timer.interrupt("navigation");
      if (Platform.OS === "web") {
        document.removeEventListener("visibilitychange", hidden); window.removeEventListener("blur", onBlur);
      }
    };
  }, [key, focused, visible]);
  const current = () => ref.current.key === key ? ref.current.timer : null;
  return {
    ignored,
    ignore: () => { current()?.exclude("manual"); setIgnored(true); },
    assist: () => current()?.exclude("assistance"),
    mark: () => current()?.exclude("marked_word"),
    finish: () => current()?.finish() ?? { version: 1 as const, elapsed_ms: null, exclusions: ["not_started" as const] },
  };
}
