import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, AppState, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { fontFamily, ltr } from "../lib/theme";
import ReadingVoiceNote from "../components/reading-voice-note";
import { ChapterBook, ChapterEventKind, ChapterProgress, ChapterToken, getChapterBook,
  loadChapterProgress, updateChapterProgress, withoutVowels } from "../lib/reading-chapters";

const PAPER = "#F3E8D2", INK = "#2B241C", ACCENT = "#8B4A2B", MUTED = "#766956";

export default function ChapterReader() {
  const router = useRouter(); const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ chapter?: string }>();
  const [book, setBook] = useState<ChapterBook | null>(null);
  const [chapterId, setChapterId] = useState("");
  const [progress, setProgress] = useState<ChapterProgress | null>(null);
  const [error, setError] = useState(""); const [retry, setRetry] = useState(0);
  const [lookup, setLookup] = useState<ChapterToken | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [busy, setBusy] = useState(false); const [voiceBusy, setVoiceBusy] = useState(false);
  const [text, setText] = useState("");
  const scroll = useRef<ScrollView>(null); const scrollY = useRef(0);
  const positions = useRef<Record<string, number>>({});
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const restoreY = useRef<number | null>(null);
  const mounted = useRef(true); const selected = useRef("");
  const operation = useRef(false);
  const chapter = book?.chapters.find(c => c.id === chapterId);

  useEffect(() => {
    let alive = true; mounted.current = true; setError(""); setProgress(null);
    Promise.all([getChapterBook(), loadChapterProgress()]).then(async ([data, journal]) => {
      if (!alive) return;
      const id = typeof params.chapter === "string" ? params.chapter : journal.lastChapter;
      if (!data.chapters.some(c => c.id === id)) throw new Error("This chapter doesn’t exist. Open the chapter list below.");
      selected.current = id; setBook(data); setChapterId(id); setLookup(null); setShowPreview(false);
      const saved = await updateChapterProgress(id, p => p, "open");
      if (!alive) return;
      scrollY.current = saved.scrollY; restoreY.current = saved.scrollY;
      positions.current[id] = saved.scrollY;
      setProgress(saved); setText(saved.text);
    }).catch(e => { if (alive) setError(e instanceof Error ? e.message : "Couldn’t open your reading. Please retry."); });
    return () => { alive = false; mounted.current = false; };
  }, [params.chapter, retry]);

  const act = useCallback(async (change: (p: ChapterProgress) => ChapterProgress,
    kind?: ChapterEventKind, extra: Record<string, unknown> = {}, sendVoice = false): Promise<boolean> => {
    const id = selected.current;
    if (!id) return false;
    try {
      const saved = await updateChapterProgress(id, change, kind, extra, sendVoice);
      if (mounted.current && selected.current === id) { setProgress(saved); setError(""); }
      return true;
    } catch {
      if (mounted.current) setError("Couldn’t save on this device. Your reading and drafts are still here; please retry before leaving.");
      return false;
    }
  }, []);
  const transition = async (change: (p: ChapterProgress) => ChapterProgress, kind: ChapterEventKind) => {
    if (operation.current || voiceBusy) return;
    operation.current = true; setBusy(true);
    if (await act(change, kind)) {
      scrollY.current = 0; positions.current[selected.current] = 0;
      restoreY.current = null; scroll.current?.scrollTo({ y: 0, animated: false });
    }
    operation.current = false; setBusy(false);
  };
  useEffect(() => {
    if (!chapterId) return;
    const id = chapterId;
    const checkpoint = () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      const y = positions.current[id] ?? 0;
      void updateChapterProgress(id, p => ({ ...p, scrollY: y }), "pause").catch(() => {
        if (mounted.current) setError("Couldn’t save your place. Please retry before leaving.");
      });
    };
    const subscription = AppState.addEventListener("change", state => { if (state !== "active") checkpoint(); });
    return () => { subscription.remove(); checkpoint(); };
  }, [chapterId]);

  function saveScroll(y: number) {
    scrollY.current = Math.max(0, Math.round(y));
    if (restoreY.current !== null) return;
    positions.current[chapterId] = scrollY.current;
    if (saveTimer.current) return;
    const id = chapterId;
    saveTimer.current = setTimeout(() => {
      saveTimer.current = null;
      void updateChapterProgress(id, p => ({ ...p, scrollY: positions.current[id] ?? 0 })).catch(() => {
        if (mounted.current) setError("Couldn’t save your place. Please retry before leaving.");
      });
    }, 800);
  }
  const button = (label: string, onPress: () => void, primary = false, disabled = false) => <Pressable
    accessibilityRole="button" disabled={disabled} onPress={onPress}
    style={[s.button, primary && s.primary, disabled && { opacity: 0.5 }]}>
    <Text style={[s.buttonText, primary && { color: "#FFF9ED" }]}>{label}</Text>
  </Pressable>;
  const preview = () => <View style={s.preview}>
    <Text style={s.sectionTitle}>A few words for this chapter</Text>
    <Text style={s.small}>Just a reminder before you read. No test, and nothing to memorize.</Text>
    {chapter?.preview.map(w => <View key={w.form} style={s.previewRow}>
      <Text selectable style={s.previewArabic}>{w.form}</Text>
      <View style={{ flex: 1 }}><Text style={s.body}>{w.meaning}</Text>{!!w.note && <Text style={s.small}>{w.note}</Text>}</View>
    </View>)}
  </View>;

  if (!book || !chapter || !progress) return <View style={[s.loading, { paddingTop: insets.top + 24 }]}>
    {error ? <><Text accessibilityRole="alert" style={s.error}>{error}</Text>{button("Retry", () => setRetry(n => n + 1))}{button("Open first chapter", () => router.replace("/read?chapter=drawing"))}</> : <><ActivityIndicator color={ACCENT} /><Text style={s.small}>Opening your chapter…</Text></>}
  </View>;

  const index = book.chapters.findIndex(c => c.id === chapter.id);
  return <View style={[s.screen, { paddingTop: insets.top }]}>
    <ScrollView ref={scroll} keyboardShouldPersistTaps="handled" style={s.screen}
      contentContainerStyle={[s.content, { paddingBottom: insets.bottom + 40 }]}
      scrollEventThrottle={200} onScroll={e => saveScroll(e.nativeEvent.contentOffset.y)}
      onContentSizeChange={(_, height) => {
        if (restoreY.current !== null && height > 0) {
          const y = Math.min(restoreY.current, height); restoreY.current = null; scroll.current?.scrollTo({ y, animated: false });
        }
      }}>
      <View style={s.top}>
        {button("‹ Library", () => router.push("/books"), false, voiceBusy)}
        <Text style={s.small}>SUPPORTED READING</Text>
      </View>
      <Text style={s.bookTitle}>{book.title}</Text>
      <View style={s.chapterTabs}>{book.chapters.map((c, i) => <Pressable key={c.id}
        accessibilityRole="button" accessibilityState={{ selected: c.id === chapterId }} disabled={voiceBusy || busy}
        style={[s.chapterTab, c.id === chapterId && s.selectedTab]} onPress={() => router.replace(`/read?chapter=${c.id}`)}>
        <Text style={[s.small, c.id === chapterId && { color: ACCENT, fontWeight: "700" }]}>Chapter {i + 1}</Text>
      </Pressable>)}</View>
      <Text style={s.title}>{chapter.title}</Text>
      <Text style={s.small}>{chapter.word_count} Arabic words · one short chapter</Text>
      {!!error && <View style={s.errorBox}><Text accessibilityRole="alert" style={s.error}>{error}</Text>{button("Retry saving", () => { void act(p => ({ ...p, text, scrollY: scrollY.current })); })}</View>}
      {progress.stage === "preview" ? <>
        <Text style={s.orientation}>{chapter.orientation}</Text>
        {preview()}
        {button("Read the chapter", () => { void transition(p => ({ ...p, stage: "reading", scrollY: 0 }), "start"); }, true, busy)}
        <Text style={s.small}>Scroll at your own pace. Tap any Arabic word for help, or reveal a paragraph’s English. Your place is saved on this device.</Text>
      </> : <>
        <View style={s.tools}>
          {button(progress.vowels ? "Hide vowel marks" : "Show vowel marks", () => { void act(p => ({ ...p, vowels: !p.vowels }), "vowels", { visible: !progress.vowels }); })}
          {button(showPreview ? "Close word reminders" : "Word reminders", () => { setShowPreview(v => !v); void act(p => p, "preview", { visible: !showPreview }); })}
        </View>
        <Text style={s.small}>Tap a word whenever it helps. English stays hidden until you ask.</Text>
        {showPreview && preview()}
        {chapter.paragraphs.map((paragraph, i) => <View key={paragraph.id} style={s.paragraph}>
          <Text style={s.paragraphNumber}>{i + 1}</Text>
          <Text style={s.arabic} accessibilityLanguage="ar">
            {paragraph.tokens.map((token, n) => <Text key={token.id} accessibilityRole="button"
              accessibilityLabel={`Look up ${token.surface}`} onPress={() => { setLookup(token); void act(p => p, "word", { paragraph_id: paragraph.id, token_id: token.id }); }}>
              {(n ? " " : "") + (progress.vowels ? token.surface : withoutVowels(token.surface))}
            </Text>)}
          </Text>
          {button(progress.translations.includes(paragraph.id) ? "Hide English" : "Read English", () => {
            const visible = !progress.translations.includes(paragraph.id);
            void act(p => ({ ...p, translations: visible ? [...new Set([...p.translations, paragraph.id])] : p.translations.filter(id => id !== paragraph.id) }), "translation", { paragraph_id: paragraph.id, visible });
          })}
          {progress.translations.includes(paragraph.id) && <Text selectable style={s.english}>{paragraph.english}</Text>}
        </View>)}
        <View style={s.ending}>
          <Text style={s.endMark}>◆</Text>
          {progress.stage !== "finished" ? button("Finish chapter", () => { void act(p => ({ ...p, stage: "finished" }), "complete"); }, true, voiceBusy || busy)
            : <Text style={s.sectionTitle}>Chapter finished</Text>}
          <Text style={s.small}>A good place to stop. You can also leave a reflection whenever you like.</Text>
        </View>
        <View style={s.reflection}>
          <Text style={s.sectionTitle}>How did this reading feel?</Text>
          <Text style={s.small}>Optional. This helps shape the next chapters.</Text>
          <View style={s.tools}>{([
            ["smooth", "Flowed easily"], ["some-work", "Some puzzling out"], ["tiring", "Tiring"],
          ] as const).map(([value, label]) => <Pressable key={value} accessibilityRole="button"
            accessibilityState={{ selected: progress.effort === value }}
            style={[s.button, progress.effort === value && s.selectedTab]}
            onPress={() => { void act(p => ({ ...p, effort: p.effort === value ? null : value, feedbackSaved: false })); }}><Text style={s.buttonText}>{label}</Text></Pressable>)}</View>
          <ReadingVoiceNote key={chapter.id} draft={progress.voice} onBusy={setVoiceBusy}
            onDraft={async voice => { const id = chapter.id; const saved = await updateChapterProgress(id, p => ({ ...p, voice, feedbackSaved: false })); if (mounted.current && selected.current === id) setProgress(saved); }} />
          <TextInput accessibilityLabel="Reading reflection" placeholder="Or type a quick reflection…"
            placeholderTextColor={MUTED} multiline maxLength={4000} value={text} style={s.input}
            onChangeText={value => { setText(value); void act(p => ({ ...p, text: value, feedbackSaved: false })); }} />
          {button("Save reflection", () => { void act(p => ({ ...p, text }), "feedback", {}, true); }, true,
            voiceBusy || progress.feedbackSaved || (!text.trim() && !progress.voice && !progress.effort))}
          {progress.feedbackSaved && <Text style={s.small}>Reflection saved. It will sync when connected.</Text>}
          <Text style={s.small}>Voice notes are saved as recordings for later review.</Text>
        </View>
        {progress.stage === "finished" && <View style={s.tools}>
          {index + 1 < book.chapters.length && button("Next chapter →", () => router.replace(`/read?chapter=${book.chapters[index + 1].id}`), true, voiceBusy)}
          {button("Done for today", () => router.push("/books"), false, voiceBusy)}
          {button("Read this chapter again", () => {
            void transition(p => ({ ...p, stage: "reading", scrollY: 0, translations: [] }), "reread");
          }, false, voiceBusy || busy)}
        </View>}
      </>}
    </ScrollView>
    <Modal visible={!!lookup} transparent animationType="fade" onRequestClose={() => setLookup(null)}>
      <View style={s.modalBackdrop}>
        <Pressable accessibilityRole="button" accessibilityLabel="Close word help" style={StyleSheet.absoluteFill} onPress={() => setLookup(null)} />
        <View style={[s.wordCard, { marginBottom: insets.bottom + 20 }]}>
          <Text selectable style={s.lookupArabic}>{lookup?.surface}</Text>
          <Text selectable style={s.wordMeaning}>{lookup?.gloss}</Text>
          {!!lookup?.note && <Text style={s.body}>{ltr(lookup.note)}</Text>}
          {button("Back to the story", () => setLookup(null))}
        </View>
      </View>
    </Modal>
  </View>;
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: PAPER }, loading: { flex: 1, backgroundColor: PAPER, padding: 24, gap: 20 },
  content: { padding: 22, gap: 16, maxWidth: 740, width: "100%", alignSelf: "center" },
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
  bookTitle: { color: MUTED, fontSize: 15 }, title: { color: INK, fontSize: 29, fontWeight: "600" },
  orientation: { color: INK, fontSize: 17, lineHeight: 27 }, small: { color: MUTED, fontSize: 14, lineHeight: 21 },
  body: { color: INK, fontSize: 16, lineHeight: 25 }, sectionTitle: { color: INK, fontSize: 20, fontWeight: "600" },
  button: { alignSelf: "flex-start", borderWidth: 1, borderColor: "#C8B599", paddingVertical: 11, paddingHorizontal: 15, borderRadius: 9, minHeight: 44, justifyContent: "center" },
  buttonText: { color: ACCENT, fontSize: 15, fontWeight: "600" }, primary: { backgroundColor: ACCENT, borderColor: ACCENT },
  tools: { flexDirection: "row", flexWrap: "wrap", gap: 10 }, chapterTabs: { flexDirection: "row", gap: 10 },
  chapterTab: { padding: 12, borderRadius: 8, borderWidth: 1, borderColor: "#C8B599", minHeight: 44 }, selectedTab: { backgroundColor: "#E4D0AF", borderColor: ACCENT },
  preview: { gap: 14, paddingVertical: 18 }, previewRow: { flexDirection: "row", gap: 18, alignItems: "center" },
  previewArabic: { color: INK, fontFamily: fontFamily.arabic, fontSize: 25, lineHeight: 42, textAlign: "right", writingDirection: "rtl", width: "43%" },
  paragraph: { gap: 12, paddingVertical: 18 }, paragraphNumber: { color: MUTED, fontSize: 12, alignSelf: "center" },
  arabic: { color: INK, fontFamily: fontFamily.arabic, fontSize: 30, lineHeight: 55, textAlign: "right", writingDirection: "rtl" },
  english: { color: "#564936", fontSize: 17, lineHeight: 28, padding: 16, backgroundColor: "#ECDEC4", borderRadius: 8 },
  ending: { alignItems: "flex-start", gap: 15, paddingVertical: 20, borderTopWidth: 1, borderTopColor: "#D8C4A2" }, endMark: { color: ACCENT, alignSelf: "center" },
  reflection: { gap: 17, paddingVertical: 22, borderTopWidth: 1, borderTopColor: "#D8C4A2" },
  input: { color: INK, fontSize: 16, lineHeight: 25, borderWidth: 1, borderColor: "#C8B599", borderRadius: 9, padding: 14, minHeight: 100, textAlignVertical: "top" },
  errorBox: { gap: 12 }, error: { color: "#9B2828", fontSize: 15, lineHeight: 23 },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(35,28,18,0.35)", justifyContent: "flex-end", padding: 18 },
  wordCard: { backgroundColor: "#FFF7E8", borderRadius: 16, padding: 24, gap: 16, maxWidth: 640, width: "100%", alignSelf: "center" },
  lookupArabic: { color: INK, fontFamily: fontFamily.arabic, fontSize: 34, lineHeight: 56, textAlign: "right", writingDirection: "rtl" },
  wordMeaning: { color: INK, fontSize: 21, lineHeight: 30 },
});
