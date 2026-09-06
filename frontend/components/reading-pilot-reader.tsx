import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ActivityIndicator, AppState, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useNavigation, useRouter } from "expo-router";
import { fontFamily } from "../lib/theme";
import {
  getReadingPilot, loadPilotProgress, nextPilotProgress, pilotEvent,
  PilotEventKind, PilotProgress, ReadingPilot, rereadProgress, savePilotProgress,
} from "../lib/reading-pilot";

const PAPER = "#F3E8D2", INK = "#2B241C", MUTED = "#766956", ACCENT = "#8B4A2B";

export default function ReadingPilotReader() {
  const router = useRouter();
  const navigation = useNavigation();
  const [content, setContent] = useState<ReadingPilot | null>(null);
  const [progress, setProgress] = useState<PilotProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const current = useRef<PilotProgress | null>(null);
  const material = useRef<ReadingPilot | null>(null);
  const saving = useRef(false);
  const openedAttempt = useRef<string | null>(null);
  const mounted = useRef(true);
  const foreground = useRef(AppState.currentState === "active" || AppState.currentState == null);
  const clock = useRef<number | null>(null);
  const scroll = useRef<ScrollView>(null);

  const act = useCallback(async (kind: PilotEventKind,
    change: (p: PilotProgress) => PilotProgress = p => p,
    extra: Record<string, unknown> = {}): Promise<boolean> => {
    const p = current.current;
    const data = material.current;
    if (!p || !data || saving.current || p.finished) return false;
    if (kind === "open" && !foreground.current) return false;
    if (kind === "pause" && openedAttempt.current !== p.attemptId) return false;
    saving.current = true;
    if (mounted.current) setBusy(true);
    const elapsed = clock.current == null ? 0 : Math.max(0, Date.now() - clock.current);
    clock.current = null;
    const timed = {
      ...p, sequence: p.sequence + 1,
      readingMs: Math.min(86_400_000, p.readingMs + (p.phase === "read" ? elapsed : 0)),
      rereadingMs: Math.min(86_400_000, p.rereadingMs + (p.phase === "reread" ? elapsed : 0)),
    };
    const next = change(timed);
    const event = pilotEvent(kind === "complete" ? timed : next, data.sessions[p.index].id, kind, {
      ...extra, followed: next.followed, wanted_more: next.wantedMore,
    });
    // Completion belongs to the portion just read, even though the next draft
    // has a new attempt ID and empty feedback.
    if (kind === "complete") { event.followed = timed.followed; event.wanted_more = timed.wantedMore; }
    try {
      await savePilotProgress(next, event);
      current.current = next;
      if (kind === "open") openedAttempt.current = next.attemptId;
      if (mounted.current) { setProgress(next); setError(null); }
      return true;
    } catch {
      if (mounted.current) setError("Couldn’t save your place on this device. Please try again before leaving.");
      return false;
    } finally {
      clock.current = foreground.current ? Date.now() : null;
      saving.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);

  const load = useCallback(async () => {
    setError(null);
    let loadingProgress = false;
    try {
      const data = await getReadingPilot();
      loadingProgress = true;
      const p = await loadPilotProgress(data.sessions.length);
      if (!mounted.current) return;
      material.current = data;
      current.current = p;
      setContent(data); setProgress(p);
      clock.current = foreground.current ? Date.now() : null;
      if (!p.finished) await act("open");
    } catch {
      if (mounted.current) setError(loadingProgress
        ? "Couldn’t read your saved place on this device. It has been kept so we can recover it."
        : "Couldn’t open the reading. Connect once to download it, then it works offline. Your saved place is kept.");
    }
  }, [act]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; };
  }, [load]);

  useFocusEffect(useCallback(() => {
    foreground.current = AppState.currentState === "active" || AppState.currentState == null;
    clock.current = foreground.current ? Date.now() : null;
    // Tabs keep this screen mounted when returning to the library.
    if (current.current && material.current) void act("open");
    const sub = AppState.addEventListener("change", state => {
      foreground.current = state === "active";
      if (!foreground.current) void act("pause");
      else clock.current = Date.now();
    });
    return () => {
      sub.remove();
      foreground.current = false;
      void act("pause");
    };
  }, [act]));

  const leave = useCallback(async () => {
    if (current.current?.finished || await act("pause")) router.replace("/books");
  }, [act, router]);

  useLayoutEffect(() => {
    navigation.setOptions({
      title: "Momo · A little reading",
      headerStyle: { backgroundColor: PAPER }, headerTintColor: INK, headerShadowVisible: false,
      tabBarStyle: { display: "none" },
      headerLeft: () => <Pressable accessibilityRole="button" accessibilityLabel="Back to library" onPress={leave} style={styles.headerButton}><Text style={styles.link}>‹ Library</Text></Pressable>,
    });
    return () => navigation.setOptions({ tabBarStyle: { backgroundColor: PAPER, borderTopColor: "#D8C4A2" } });
  }, [navigation, leave]);

  async function finish(stay: boolean) {
    if (!content) return;
    if (await act("complete", p => nextPilotProgress(p, content.sessions.length))) {
      scroll.current?.scrollTo({ y: 0, animated: false });
      if (!stay) router.replace("/books");
      else if (!current.current?.finished) await act("open");
    }
  }

  if (!content || !progress) return (
    <View style={styles.center}>
      {error ? <><Text style={styles.body}>{error}</Text><Pressable onPress={load} style={styles.button} accessibilityRole="button"><Text style={styles.buttonText}>Try again</Text></Pressable><Pressable onPress={() => router.replace("/books")} style={styles.control} accessibilityRole="button"><Text style={styles.link}>Back to library</Text></Pressable></> : <ActivityIndicator color={ACCENT} />}
    </View>
  );
  const session = content.sessions[progress.index];
  const last = progress.index === content.sessions.length - 1;

  return (
    <ScrollView ref={scroll} style={styles.container} contentContainerStyle={styles.content} contentInsetAdjustmentBehavior="automatic">
      <Text style={styles.eyebrow}>MOMO · CHAPTER 5 · {progress.index + 1} OF {content.sessions.length}</Text>
      <Text style={styles.title}>{session.title}</Text>
      {!progress.finished && <Text style={styles.body}>{progress.phase === "read" ? session.orientation : "Read it again for the meaning. Help is still here whenever you need it."}</Text>}
      {error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}

      {!progress.finished && <View style={styles.controls}>
        <Pressable disabled={busy} accessibilityRole="button" accessibilityLabel={progress.english ? "Hide English" : "Show English"} onPress={() => act("translation", p => ({ ...p, english: !p.english }), { visible: !progress.english })} style={styles.control}><Text style={styles.link}>{progress.english ? "Hide English" : "Show English"}</Text></Pressable>
        {progress.phase === "reread" && <Pressable accessibilityRole="button" disabled={busy} onPress={() => act("phrase_help", p => ({ ...p, phraseHelp: !p.phraseHelp }), { visible: !progress.phraseHelp })} style={styles.control}><Text style={styles.link}>{progress.phraseHelp ? "Hide phrase help" : "Phrase help"}</Text></Pressable>}
      </View>}

      {session.blocks.map(block => (
        <View key={block.id} style={styles.block}>
          <Text selectable style={styles.arabic}>{block.arabic}</Text>
          {progress.english && !progress.finished && <Text selectable style={styles.translation}>{block.english}</Text>}
          {!progress.finished && (progress.phase === "read" || progress.phraseHelp) && <View style={styles.notes}>
            {block.help.map(note => {
              const open = progress.openHelp.includes(note.id);
              return <View key={note.id}>
                <Pressable disabled={busy} accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityLabel={`Explain ${note.anchor}`} style={styles.phrase} onPress={() => act("help", p => ({ ...p, openHelp: open ? p.openHelp.filter(id => id !== note.id) : [...p.openHelp, note.id] }), { help_id: note.id, visible: !open })}>
                  <Text style={styles.phraseArabic}>{note.anchor}</Text><Text style={styles.hint}>{open ? "−" : "+"}</Text>
                </Pressable>
                {open && <View style={styles.explanation}><Text selectable style={styles.form}>{note.form}</Text><Text selectable style={styles.meaning}>{note.meaning}</Text><Text selectable style={styles.body}>{note.explanation}</Text></View>}
              </View>;
            })}
          </View>}
        </View>
      ))}

      {!progress.finished && <>
        <Pressable disabled={busy} accessibilityRole="button" accessibilityState={{ expanded: progress.simpler }} style={styles.control} onPress={() => act("simpler", p => ({ ...p, simpler: !p.simpler }), { visible: !progress.simpler })}><Text style={styles.link}>{progress.simpler ? "Close simpler retelling" : "Try a simpler Arabic retelling"}</Text></Pressable>
        {progress.simpler && <View style={styles.explanation}><Text style={styles.eyebrow}>SIMPLER RETELLING · ADAPTED FOR THIS READING</Text><Text selectable style={styles.arabic}>{session.simpler_arabic}</Text><Text selectable style={styles.translation}>{session.simpler_english}</Text></View>}

        {progress.phase === "read" ? <>
          <Pressable disabled={busy} accessibilityRole="button" style={styles.button} onPress={async () => { if (await act("reread", rereadProgress)) { scroll.current?.scrollTo({ y: 0, animated: false }); } }}><Text style={styles.buttonText}>Reread Arabic</Text></Pressable>
          <Text style={styles.caption}>One short portion is enough. Use this time in place of some reviews.</Text>
          <Pressable disabled={busy} accessibilityRole="button" onPress={leave} style={styles.control}><Text style={styles.link}>Pause here</Text></Pressable>
        </> : <View style={styles.reflection}>
          <Text style={styles.question}>Could you follow the Arabic this time?</Text>
          <View style={styles.controls}>{([["yes", "Yes"], ["partly", "Partly"], ["no", "Not yet"]] as const).map(([value, label]) => <Pressable key={value} disabled={busy} accessibilityRole="button" accessibilityState={{ selected: progress.followed === value }} onPress={() => act("feedback", p => ({ ...p, followed: value }))} style={[styles.choice, progress.followed === value && styles.selected]}><Text style={styles.link}>{label}</Text></Pressable>)}</View>
          <Text style={styles.question}>Did you want to find out what comes next?</Text>
          <View style={styles.controls}>{([["yes", "Yes"], ["maybe", "Maybe"], ["no", "No"]] as const).map(([value, label]) => <Pressable key={value} disabled={busy} accessibilityRole="button" accessibilityState={{ selected: progress.wantedMore === value }} onPress={() => act("feedback", p => ({ ...p, wantedMore: value }))} style={[styles.choice, progress.wantedMore === value && styles.selected]}><Text style={styles.link}>{label}</Text></Pressable>)}</View>
          <Text style={styles.caption}>Both questions are optional.</Text>
          <Pressable disabled={busy} accessibilityRole="button" style={styles.button} onPress={() => finish(last)}><Text style={styles.buttonText}>{last ? "Finish these first three readings" : "Done for today"}</Text></Pressable>
          {!last && <Pressable disabled={busy} accessibilityRole="button" onPress={() => finish(true)} style={styles.control}><Text style={styles.link}>I’d like a little more</Text></Pressable>}
        </View>}
      </>}
      {progress.finished && <View style={styles.reflection}>
        <Text style={styles.question}>Three readings, one scene.</Text>
        <Text style={styles.body}>This is our first checkpoint. Tell me what became easier and what still interrupted the story, and we’ll shape the next readings around that.</Text>
        <Pressable accessibilityRole="button" onPress={leave} style={styles.button}><Text style={styles.buttonText}>Back to library</Text></Pressable>
      </View>}
      <Text style={styles.source}>Michael Ende · Momo · Arabic source pp. {session.source_pages.join("–")}. English and reading notes prepared for this passage.</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: PAPER },
  content: { padding: 22, paddingBottom: 45, maxWidth: 700, width: "100%", alignSelf: "center", gap: 14 },
  center: { flex: 1, padding: 30, backgroundColor: PAPER, alignItems: "center", justifyContent: "center", gap: 20 },
  headerButton: { padding: 12, minHeight: 44 },
  eyebrow: { fontSize: 11, color: MUTED, letterSpacing: 1.1 },
  title: { fontSize: 27, color: INK, fontWeight: "600", lineHeight: 34 },
  body: { fontSize: 16, lineHeight: 25, color: MUTED },
  arabic: { fontFamily: fontFamily.arabic, fontSize: 30, lineHeight: 53, textAlign: "right", writingDirection: "rtl", color: INK },
  block: { gap: 12, paddingVertical: 12 },
  translation: { fontSize: 17, lineHeight: 27, color: MUTED },
  controls: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  control: { minHeight: 44, justifyContent: "center", paddingVertical: 8, alignSelf: "flex-start" },
  link: { fontSize: 15, color: ACCENT },
  notes: { gap: 3, borderTopWidth: 1, borderTopColor: "#D8C4A2", paddingTop: 6 },
  phrase: { minHeight: 44, flexDirection: "row", justifyContent: "flex-end", alignItems: "center", gap: 12 },
  phraseArabic: { color: ACCENT, fontFamily: fontFamily.arabic, fontSize: 22, writingDirection: "rtl", textAlign: "right", flexShrink: 1 },
  hint: { color: ACCENT, fontSize: 19 },
  explanation: { backgroundColor: "#EBDDCA", padding: 16, borderRadius: 10, gap: 8 },
  form: { fontFamily: fontFamily.arabic, fontSize: 28, lineHeight: 45, color: INK, textAlign: "right", writingDirection: "rtl" },
  meaning: { fontSize: 17, fontWeight: "600", color: INK },
  button: { backgroundColor: ACCENT, minHeight: 50, justifyContent: "center", alignItems: "center", padding: 14, borderRadius: 8, marginTop: 10 },
  buttonText: { fontSize: 16, fontWeight: "600", color: "#FFF9ED", textAlign: "center" },
  caption: { fontSize: 13, lineHeight: 20, color: MUTED },
  reflection: { gap: 14, borderTopWidth: 1, borderTopColor: "#D8C4A2", paddingTop: 24 },
  question: { fontSize: 18, lineHeight: 27, color: INK, fontWeight: "600" },
  choice: { minHeight: 44, minWidth: 65, padding: 12, borderWidth: 1, borderColor: "#D8C4A2", borderRadius: 8, alignItems: "center" },
  selected: { backgroundColor: "#E8D8BA", borderColor: ACCENT },
  source: { fontSize: 11, lineHeight: 17, color: MUTED, marginTop: 20 },
  error: { color: "#A33E32", fontSize: 14, lineHeight: 22 },
});
