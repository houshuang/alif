import { useCallback, useRef, useState } from "react";
import { ActivityIndicator, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Tabs, useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors, fontFamily } from "../lib/theme";
import { changeParallelProgress, currentParallelProgress, currentParallelText,
  loadParallelJournal, parallelLibrary, parallelWordRuns, ParallelAction, ParallelJournal,
  ParallelParagraph, ParallelToken, SupportLanguage, updateParallelJournal } from "../lib/parallel-reading";

const SUPPORT: { code: SupportLanguage; name: string; full: string }[] = [
  { code: "grc", name: "Greek", full: "Ancient Greek" }, { code: "la", name: "Latin", full: "Latin" },
  { code: "ru", name: "Russian", full: "Russian" }, { code: "en", name: "English", full: "English" },
];
const INK = "#2B241C", PAPER = "#F3E8D2", ACCENT = "#8B4A2B", MUTED = "#766956";
type Lookup = { paragraph: ParallelParagraph; token: ParallelToken; display: string };

export default function ParallelReader() {
  const insets = useSafeAreaInsets(), router = useRouter();
  const [journal, setJournal] = useState<ParallelJournal | null>(null);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [clues, setClues] = useState<string[]>([]); const [showClues, setShowClues] = useState(false);
  const [about, setAbout] = useState(false); const [displayOpen, setDisplayOpen] = useState(false);
  const [effortSaved, setEffortSaved] = useState(false); const [lookup, setLookup] = useState<Lookup | null>(null);
  const operation = useRef(false), active = useRef(false), scroll = useRef<ScrollView>(null);
  useFocusEffect(useCallback(() => {
    let alive = true; active.current = true;
    loadParallelJournal().then(() => updateParallelJournal(j => j, "open"))
      .then(j => { if (alive) { setJournal(j); setError(""); } })
      .catch(() => { if (alive) setError("Couldn’t open saved reading. Your bookmark has been preserved. Retry to continue."); });
    return () => { alive = false; active.current = false; };
  }, []));
  async function act(change: (j: ParallelJournal) => ParallelJournal, kind: ParallelAction,
    extra: Record<string, unknown> = {}, resetScroll = false): Promise<boolean> {
    if (operation.current) return false;
    operation.current = true; setBusy(true);
    try {
      const j = await updateParallelJournal(change, kind, extra);
      if (active.current) {
        setJournal(j); setError("");
        if (resetScroll) {
          setClues([]); setShowClues(false); setAbout(false); setLookup(null); setEffortSaved(false);
          scroll.current?.scrollTo({ y: 0, animated: false });
        }
      }
      return true;
    } catch { if (active.current) setError("Couldn’t save your place. Your previous bookmark is safe; please retry."); return false; }
    finally { operation.current = false; if (active.current) setBusy(false); }
  }
  const button = (label: string, press: () => void, primary = false, disabled = false, a11y?: string) => <Pressable
    accessibilityRole="button" accessibilityLabel={a11y || label} accessibilityState={{ disabled: disabled || busy }}
    disabled={disabled || busy} onPress={press} style={[s.button, primary && s.primary, (disabled || busy) && s.disabled]}>
    <Text style={[s.buttonText, primary && s.primaryText]}>{label}</Text>
  </Pressable>;
  const link = (label: string, press: () => void, expanded?: boolean) => <Pressable accessibilityRole="button"
    accessibilityState={{ disabled: busy, expanded }} disabled={busy} onPress={press} style={s.linkButton}>
    <Text style={s.linkText}>{label}</Text>
  </Pressable>;
  const retry = () => {
    loadParallelJournal().then(j => { setJournal(j); setError(""); }).catch(() => setError("Saved reading is still unavailable; it has not been reset."));
  };
  if (!journal) return <View style={s.loading}>{error ? <><Text accessibilityRole="alert" style={s.error}>{error}</Text>{button("Retry", retry)}</> : <ActivityIndicator color={ACCENT} />}</View>;
  const text = currentParallelText(journal), progress = currentParallelProgress(journal);
  const paragraph = text.paragraphs[progress.paragraph];
  function go(index: number) { void act(j => changeParallelProgress(j, { paragraph: index, reread: false }), "passage", {}, true); }
  async function openWord(p: ParallelParagraph, token: ParallelToken, display: string) {
    if (await act(j => j, "word", { paragraph_id: p.id, token_id: token.id, visible: true })) setLookup({ paragraph: p, token, display });
  }
  async function closeWord() {
    if (lookup && await act(j => j, "word", { paragraph_id: lookup.paragraph.id, token_id: lookup.token.id, visible: false })) setLookup(null);
  }
  const arabic = (p: ParallelParagraph) => <Text key={p.id} style={[s.arabic, { fontSize: 30 * journal.size, lineHeight: 49 * journal.size }]}>
    {parallelWordRuns(p, journal.vowels).map((run, i) => run.token ? <Text key={i} accessibilityRole="button"
      accessibilityLabel={`Word help: ${run.text}`} accessibilityState={{ disabled: busy }} suppressHighlighting
      onPress={() => { if (!busy) void openWord(p, run.token!, run.text); }}
      style={lookup?.paragraph.id === p.id && lookup.token.id === run.token.id ? s.highlight : undefined}>{run.text}</Text> : run.text)}
  </Text>;
  const support = (code: SupportLanguage) => <View key={code} style={s.translation}>
    <Text style={s.label}>{SUPPORT.find(l => l.code === code)?.full} · {text.versions[code]}</Text>
    <Text selectable style={[s.support, { fontSize: 22 * journal.size, lineHeight: 33 * journal.size }]}>{paragraph[code]}</Text>
  </View>;
  return <View style={s.screen}>
    <Tabs.Screen options={{ tabBarStyle: journal.library ? { backgroundColor: colors.surface, borderTopColor: colors.border } : { display: "none" } }} />
    <ScrollView ref={scroll} contentContainerStyle={[s.content, { paddingTop: insets.top + 12 }]}>
      <View style={s.top}>
        {journal.library ? <Text style={s.eyebrow}>PARALLEL READING</Text> : link("‹ Library", () => { void act(j => ({ ...j, library: true }), "library", {}, true); })}
        <View style={s.row}>
          {!journal.library && link("Aa", () => {
            void act(j => j, "display", { panel: "appearance", visible: !displayOpen }).then(ok => { if (ok) setDisplayOpen(!displayOpen); });
          }, displayOpen)}
          {link("Alif", () => { void act(j => j, "leave").then(ok => { if (ok) router.push("/"); }); })}
        </View>
      </View>
      {error && <View><Text accessibilityRole="alert" style={s.error}>{error}</Text>{button("Retry", retry)}</View>}
      {journal.library ? <>
        <Text style={s.title}>Read your way into Arabic.</Text>
        <Text style={s.body}>Begin with Arabic. Let another language give you a clue, then return to the words.</Text>
        {journal.progress[journal.textId] && button(`Continue · ${text.title}`, () => { void act(j => ({ ...j, library: false }), "select", {}, true); }, true)}
        {parallelLibrary.texts.map(t => {
          const saved = journal.progress[t.id];
          return <Pressable accessibilityRole="button" disabled={busy} key={t.id} style={s.card} onPress={() => {
            void act(j => changeParallelProgress({ ...j, textId: t.id, library: false }, {}), "select", {}, true);
          }}>
            <Text style={s.label}>{t.author}</Text><Text style={s.cardTitle}>{t.title}</Text><Text style={s.body}>{t.description}</Text>
            <Text style={s.linkText}>{saved?.completed ? "Read · open again →" : saved ? `Continue · passage ${saved.paragraph + 1} of ${t.paragraphs.length} →` : `${t.paragraphs.length} passages · begin →`}</Text>
          </Pressable>;
        })}
        <Text style={s.small}>Your place stays on this device. Word help creates no review obligations.</Text>
      </> : <>
        <Text style={s.label}>{text.author}</Text><Text style={s.title}>{text.title}</Text>
        {displayOpen && <View style={s.displayPanel}>
          <View style={s.row}>{button("A−", () => { void act(j => ({ ...j, size: Math.max(.85, +(j.size - .1).toFixed(2)) }), "size"); }, false, journal.size <= .85, "Decrease text size")}
            {button("A+", () => { void act(j => ({ ...j, size: Math.min(1.4, +(j.size + .1).toFixed(2)) }), "size"); }, false, journal.size >= 1.4, "Increase text size")}
            {button(journal.vowels ? "Hide vowel marks" : "Vowel marks", () => { void act(j => ({ ...j, vowels: !j.vowels }), "vowels"); })}</View>
          {!paragraph.ar_vowelled && <Text style={s.small}>This text has selective vowel marks, rather than full vocalization.</Text>}
        </View>}
        <View style={s.arabicCard}><Text style={s.label}>{progress.reread ? "Arabic-only reread · tap a word for help" : "Arabic · tap a word for help"}</Text>
          {progress.reread ? text.paragraphs.map(arabic) : arabic(paragraph)}
        </View>
        {!progress.reread && <>
          <View style={s.segments}>{SUPPORT.map(l => <Pressable key={l.code} accessibilityRole="button"
            accessibilityState={{ selected: journal.support === l.code && !journal.all, disabled: busy }} disabled={busy}
            onPress={() => { void act(j => ({ ...j, support: l.code, revealed: true, all: false }), "support"); }}
            style={[s.segment, journal.support === l.code && !journal.all && s.segmentActive]}>
            <Text style={[s.segmentText, journal.support === l.code && !journal.all && s.segmentTextActive]}>{l.name}</Text>
          </Pressable>)}</View>
          <View style={s.top}>
            {link(journal.revealed || journal.all ? "Hide translation" : "Reveal translation", () => { void act(j => ({ ...j, revealed: !(j.revealed || j.all), all: false }), "reveal"); }, journal.revealed || journal.all)}
            {link(journal.all ? "Show a pair" : "Compare all", () => { void act(j => ({ ...j, all: !j.all }), "all"); }, journal.all)}
          </View>
          {journal.all ? SUPPORT.map(l => support(l.code)) : journal.revealed ? support(journal.support) : <Text style={s.small}>Choose a language whenever you want a clue.</Text>}
          <View style={s.details}>
            {link(showClues ? "− Phrase clues" : "+ Phrase clues", () => {
              void act(j => j, "display", { panel: "phrases", visible: !showClues }).then(ok => { if (ok) setShowClues(!showClues); });
            }, showClues)}
            {showClues && paragraph.clues.map(c => <View key={c.id}>
              {link(`${clues.includes(c.id) ? "−" : "+"} ${c.form}`, () => {
                const visible = !clues.includes(c.id);
                void act(j => j, "clue", { clue_id: c.id, visible }).then(ok => { if (ok) setClues(v => visible ? [...v, c.id] : v.filter(id => id !== c.id)); });
              }, clues.includes(c.id))}
              {clues.includes(c.id) && <View style={s.clue}><Text style={s.body}>{c.meaning}</Text><Text style={s.small}>{c.bridge}</Text></View>}
            </View>)}
          </View>
        </>}
        {progress.reread && <>
          <Text style={s.body}>Which words can you now recognise from the Arabic?</Text>
          {progress.completed && <>
            <Text style={s.cardTitle}>Reading finished.</Text><Text style={s.small}>Optional: how did it feel?</Text>
            <View style={s.row}>{([ ["smooth", "Smooth"], ["some-work", "Some work"], ["tiring", "Tiring"] ] as const).map(([effort, name]) => <View key={effort}>{button(name, () => {
              void act(j => j, "reflection", { effort }).then(ok => { if (ok) setEffortSaved(true); });
            })}</View>)}</View>{effortSaved && <Text style={s.small}>Reflection saved.</Text>}
            {button("Choose another reading →", () => { void act(j => ({ ...j, library: true }), "library", {}, true); }, true)}
          </>}
        </>}
        <View style={s.details}>
          {link(about ? "− Text & sources" : "+ Text & sources", () => {
            void act(j => j, "about", { visible: !about }).then(ok => { if (ok) setAbout(!about); });
          }, about)}
          {about && <View style={s.sourcePanel}><Text style={s.body}>{text.source_note}</Text>
            {Object.entries(text.versions).map(([code, provenance]) => <Text key={code} style={s.small}>{code === "ar" ? "Arabic" : SUPPORT.find(l => l.code === code)?.full}: {provenance}</Text>)}
            <Text style={s.small}>New translations and word meanings are prepared learning aids, not independently checked by a language specialist.</Text>
            {link("Original source ↗", () => { void act(j => j, "about", { visible: true }).then(ok => { if (ok) void Linking.openURL(text.source_url).catch(() => setError("Couldn’t open the source link.")); }); })}
          </View>}
        </View>
        {!progress.reread && <View style={s.row}>{text.paragraphs.map((p, i) => <View key={p.id}>{button(`${i + 1}`, () => go(i), i === progress.paragraph, false, `Go to passage ${i + 1}`)}</View>)}</View>}
      </>}
    </ScrollView>
    {!journal.library && <View style={[s.footer, { paddingBottom: Math.max(insets.bottom, 12) }]}>
      {progress.reread ? <>
        {button("Parallel", () => { void act(j => changeParallelProgress(j, { reread: false }), "reread", {}, true); })}
        {button(progress.completed ? "Read again" : "Finish reading", () => {
          void act(j => changeParallelProgress(j, progress.completed ? { paragraph: 0, reread: false } : { completed: true }), progress.completed ? "reread" : "complete", {}, progress.completed);
        }, true)}
      </> : <>
        {button("←", () => go(progress.paragraph - 1), false, progress.paragraph === 0, "Previous passage")}
        <Text style={s.small}>{progress.paragraph + 1} / {text.paragraphs.length}</Text>
        {progress.paragraph < text.paragraphs.length - 1 ? button("Next →", () => go(progress.paragraph + 1), true, false, "Next passage")
          : button("Reread Arabic →", () => { void act(j => changeParallelProgress(j, { reread: true }), "reread", {}, true); }, true)}
      </>}
    </View>}
    <Modal visible={!!lookup} transparent animationType="none" onRequestClose={() => { void closeWord(); }}>
      <View style={s.modalBackdrop}>
        <Pressable accessibilityRole="button" accessibilityLabel="Close word help" style={StyleSheet.absoluteFill} onPress={() => { void closeWord(); }} />
        <View accessibilityViewIsModal style={[s.wordCard, { marginBottom: insets.bottom + 12 }]}>
          <ScrollView contentContainerStyle={s.wordContent}>
            <Text style={s.label}>WORD IN THIS PASSAGE</Text><Text selectable style={s.lookupArabic}>{lookup?.display}</Text>
            <Text selectable style={s.wordMeaning}>{lookup?.token.gloss}</Text>
            {error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
            {button("Back to reading", () => { void closeWord(); }, true)}
          </ScrollView>
        </View>
      </View>
    </Modal>
  </View>;
}
const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: PAPER }, content: { paddingHorizontal: 20, paddingBottom: 24, gap: 12, width: "100%", maxWidth: 720, alignSelf: "center" },
  loading: { flex: 1, padding: 24, backgroundColor: PAPER, justifyContent: "center", gap: 16 },
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 },
  eyebrow: { color: ACCENT, fontSize: 11, letterSpacing: 1.5, fontWeight: "700" },
  title: { color: INK, fontFamily: "EBGaramond_400Regular", fontSize: 32, lineHeight: 36 },
  cardTitle: { color: INK, fontFamily: "EBGaramond_400Regular", fontSize: 27, lineHeight: 32 },
  body: { color: INK, fontSize: 15, lineHeight: 24 }, small: { color: MUTED, fontSize: 12, lineHeight: 20 },
  label: { color: MUTED, fontSize: 12, lineHeight: 19, fontWeight: "600" },
  card: { backgroundColor: "#FFF9ED", borderWidth: 1, borderColor: "#D8C4A2", borderRadius: 12, padding: 18, gap: 12 },
  arabicCard: { backgroundColor: "#FFF9ED", borderRadius: 12, padding: 18, gap: 12 },
  arabic: { color: INK, fontFamily: fontFamily.arabic, writingDirection: "rtl", textAlign: "right" }, highlight: { backgroundColor: "#E4D0AF" },
  translation: { padding: 16, backgroundColor: "#ECDEC4", borderRadius: 10, gap: 10 },
  support: { color: INK, fontFamily: "EBGaramond_400Regular", writingDirection: "ltr", textAlign: "left" },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8 }, button: { minHeight: 44, justifyContent: "center", alignItems: "center", paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: "#CDB796", borderRadius: 9 },
  buttonText: { color: ACCENT, fontSize: 14, textAlign: "center" }, primary: { backgroundColor: ACCENT, borderColor: ACCENT }, primaryText: { color: "#FFF9ED" }, disabled: { opacity: .45 },
  linkButton: { minHeight: 44, justifyContent: "center", paddingVertical: 10, paddingHorizontal: 4 }, linkText: { color: ACCENT, fontSize: 14 },
  segments: { flexDirection: "row", gap: 4, borderBottomWidth: 1, borderBottomColor: "#D8C4A2" },
  segment: { flex: 1, minHeight: 44, paddingVertical: 12, paddingHorizontal: 2, alignItems: "center", justifyContent: "center", borderBottomWidth: 2, borderBottomColor: "transparent" },
  segmentActive: { borderBottomColor: ACCENT }, segmentText: { color: MUTED, fontSize: 13 }, segmentTextActive: { color: ACCENT, fontWeight: "700" },
  details: { borderTopWidth: 1, borderTopColor: "#D8C4A2" }, sourcePanel: { gap: 12 },
  displayPanel: { padding: 12, backgroundColor: "#E8DCC5", borderRadius: 10, gap: 10 }, clue: { padding: 14, gap: 6 },
  footer: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingTop: 10, paddingHorizontal: 20, gap: 12, backgroundColor: PAPER, borderTopWidth: 1, borderTopColor: "#D8C4A2" },
  error: { color: "#9D2525", fontSize: 14, lineHeight: 22 },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(35,28,18,.35)", justifyContent: "flex-end", padding: 18 },
  wordCard: { backgroundColor: "#FFF7E8", borderRadius: 16, maxHeight: "65%", maxWidth: 640, width: "100%", alignSelf: "center" },
  wordContent: { padding: 22, gap: 14 }, lookupArabic: { color: INK, fontFamily: fontFamily.arabic, fontSize: 34, lineHeight: 56, textAlign: "right", writingDirection: "rtl" },
  wordMeaning: { color: INK, fontSize: 20, lineHeight: 29 },
});
