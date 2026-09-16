import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "expo-router";
import { Audio } from "expo-av";
import { VoiceDraft } from "../lib/reading-chapters";

/** Uses the recording module already shipped with Alif. No background recording. */
export default function ReadingVoiceNote({ draft, onDraft, onBusy }: {
  draft: VoiceDraft | null; onDraft: (value: VoiceDraft | null) => Promise<void>; onBusy: (value: boolean) => void;
}) {
  const recording = useRef<Audio.Recording | null>(null);
  const sound = useRef<Audio.Sound | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  const focused = useRef(true);
  const operating = useRef(false);
  const [active, setActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  const [unsaved, setUnsaved] = useState<VoiceDraft | null>(null);
  const persist = useRef(onDraft); persist.current = onDraft;
  const setWorking = (value: boolean) => { operating.current = value; onBusy(value); if (mounted.current) setBusy(value); };

  async function stop() {
    const rec = recording.current;
    if (!rec) return;
    recording.current = null;
    if (timer.current) clearTimeout(timer.current);
    setWorking(true);
    if (mounted.current) { setActive(false); setError(""); }
    try {
      const status = await rec.stopAndUnloadAsync();
      const uri = rec.getURI();
      if (!uri || status.durationMillis < 100) throw new Error("The recording was too short. Please try again.");
      let base64: string, mimeType: VoiceDraft["mimeType"] = "audio/mp4";
      if (Platform.OS === "web") {
        const blob = await (await fetch(uri)).blob();
        mimeType = blob.type.startsWith("audio/mp4") ? "audio/mp4" : blob.type.startsWith("audio/ogg") ? "audio/ogg" : "audio/webm";
        base64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader(); reader.onerror = () => reject(new Error("Couldn’t read the recording"));
          reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.readAsDataURL(blob);
        });
        URL.revokeObjectURL(uri);
      } else {
        const fs = await import("expo-file-system/legacy");
        base64 = await fs.readAsStringAsync(uri, { encoding: fs.EncodingType.Base64 });
        await fs.deleteAsync(uri, { idempotent: true }).catch(() => {});
      }
      if (base64.length > 2_000_000) throw new Error("This note is too large. Please record a shorter one.");
      const value: VoiceDraft = { base64, mimeType, durationMs: Math.min(65000, Math.max(1, Math.round(status.durationMillis))) };
      if (mounted.current) setUnsaved(value);
      await persist.current(value);
      if (mounted.current) setUnsaved(null);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : "Couldn’t save the voice note. Try again or type below.");
    } finally {
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false }).catch(() => {});
      setWorking(false);
    }
  }
  async function start() {
    if (operating.current || recording.current) return;
    setWorking(true); setError("");
    try {
      if (Platform.OS === "web" && (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia)) {
        throw new Error("Microphone recording needs a supported browser and HTTPS. You can type your reflection below.");
      }
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) throw new Error("Microphone access wasn’t allowed. Enable it in settings, or type below.");
      if (!mounted.current || !focused.current) return;
      await sound.current?.unloadAsync(); sound.current = null; setPlaying(false);
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
      const webMime = Platform.OS === "web" ? ["audio/webm", "audio/mp4", "audio/ogg"].find(m => MediaRecorder.isTypeSupported(m)) : "audio/webm";
      if (!webMime) throw new Error("This browser cannot record a supported audio format. Please type below.");
      const { recording: rec } = await Audio.Recording.createAsync({
        ...Audio.RecordingOptionsPresets.HIGH_QUALITY,
        android: { ...Audio.RecordingOptionsPresets.HIGH_QUALITY.android, bitRate: 48000, numberOfChannels: 1 },
        ios: { ...Audio.RecordingOptionsPresets.HIGH_QUALITY.ios, bitRate: 48000, numberOfChannels: 1 },
        web: { mimeType: webMime, bitsPerSecond: 48000 },
      });
      recording.current = rec;
      if (!mounted.current || !focused.current) { await stop(); return; }
      setActive(true); timer.current = setTimeout(() => { void stop(); }, 60_000);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : "Couldn’t start recording. You can type below.");
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false }).catch(() => {});
    } finally { setWorking(false); }
  }
  async function play() {
    if (!draft || operating.current) return;
    setError("");
    try {
      if (playing) { await sound.current?.pauseAsync(); setPlaying(false); return; }
      await sound.current?.unloadAsync();
      let uri = `data:${draft.mimeType};base64,${draft.base64}`;
      if (Platform.OS !== "web") {
        const fs = await import("expo-file-system/legacy");
        uri = `${fs.cacheDirectory}alif-reading-voice-preview.m4a`;
        await fs.writeAsStringAsync(uri, draft.base64, { encoding: fs.EncodingType.Base64 });
      }
      const result = await Audio.Sound.createAsync({ uri }, { shouldPlay: true }, status => {
        if (mounted.current && status.isLoaded && status.didJustFinish) setPlaying(false);
      });
      if (!mounted.current || !focused.current) { await result.sound.unloadAsync(); return; }
      sound.current = result.sound; setPlaying(true);
    } catch { setError("Couldn’t play the note. Your saved recording is still here."); }
  }
  const stopLatest = useRef(stop); stopLatest.current = stop;
  useFocusEffect(useCallback(() => {
    focused.current = true;
    return () => {
      focused.current = false;
      void stopLatest.current(); void sound.current?.unloadAsync();
      sound.current = null;
      if (mounted.current) setPlaying(false);
    };
  }, []));
  useEffect(() => {
    mounted.current = true;
    const sub = AppState.addEventListener("change", state => { if (state !== "active") void stopLatest.current(); });
    return () => {
      mounted.current = false; sub.remove(); if (timer.current) clearTimeout(timer.current);
      void stopLatest.current(); void sound.current?.unloadAsync();
    };
  }, []);
  useEffect(() => { onBusy(active || busy || !!unsaved); }, [active, busy, unsaved, onBusy]);

  return <View style={s.box}>
    <Pressable accessibilityRole="button" disabled={busy || !!unsaved} style={s.button}
      onPress={() => { void (active ? stop() : start()); }}>
      <Text style={s.buttonText}>{busy ? "Saving recording…" : active ? "● Stop recording" : draft ? "Record a replacement" : "◉ Give voice feedback"}</Text>
    </Pressable>
    <Text style={s.hint}>{active ? "Recording · stop to keep your note. Stops automatically after one minute. Speak in any language." : "What flowed? Where did you have to puzzle something out? A short note is enough."}</Text>
    {draft && !active && <View style={s.row}>
      <Pressable accessibilityRole="button" disabled={busy} onPress={() => { void play(); }} style={s.button}><Text style={s.buttonText}>{playing ? "Stop playback" : "Listen to your note"}</Text></Pressable>
      <Pressable accessibilityRole="button" disabled={busy} onPress={() => { void sound.current?.unloadAsync(); setPlaying(false); void onDraft(null).catch(() => setError("Couldn’t remove the note. Please retry.")); }} style={s.button}><Text style={s.buttonText}>Discard</Text></Pressable>
    </View>}
    {unsaved && <Pressable accessibilityRole="button" style={s.button} onPress={() => { void onDraft(unsaved).then(() => { setUnsaved(null); setError(""); }).catch(() => setError("Still unable to save. Please keep this screen open and retry.")); }}><Text style={s.buttonText}>Retry saving recording</Text></Pressable>}
    {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
    {draft && <Text style={s.hint}>Voice draft saved on this device. Use “Save reflection” below to send it.</Text>}
  </View>;
}
const s = StyleSheet.create({
  box: { gap: 10 }, row: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  button: { padding: 13, borderRadius: 9, borderWidth: 1, borderColor: "#BAA689", alignSelf: "flex-start", minHeight: 44 },
  buttonText: { color: "#783F29", fontSize: 15, fontWeight: "600" },
  hint: { color: "#766956", fontSize: 14, lineHeight: 21 }, error: { color: "#9B2828", fontSize: 14 },
});
