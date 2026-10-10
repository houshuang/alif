import { useCallback, useRef, useState } from "react";
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Tabs, useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors, fontFamily } from "../lib/theme";
import { arabicFor, changeParallelProgress, currentParallelProgress, currentParallelText,
  loadParallelJournal, parallelLibrary, ParallelAction, ParallelJournal, SupportLanguage,
  updateParallelJournal } from "../lib/parallel-reading";

const SUPPORT: { code: SupportLanguage; name: string }[] = [
  { code: "grc", name: "Greek" }, { code: "la", name: "Latin" }, { code: "ru", name: "Russian" },
];
const INK = "#2B241C", PAPER = "#F3E8D2", ACCENT = "#8B4A2B", MUTED = "#766956";

export default function ParallelReader() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [journal, setJournal] = useState<ParallelJournal | null>(null);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [clues, setClues] = useState<string[]>([]); const [about, setAbout] = useState(false);
  const [effortSaved, setEffortSaved] = useState(false);
  const operation = useRef(false), active = useRef(false);
  const scroll = useRef<ScrollView>(null);
  useFocusEffect(useCallback(() => {
    active.current = true;
    loadParallelJournal().then(() => updateParallelJournal(j => j, "open"))
      .then(j => { if (active.current) { setJournal(j); setError(""); } })
      .catch(() => { if (active.current) setError("Couldn’t open saved reading. Your bookmark has been preserved. Retry to continue."); });
    return () => { active.current = false; };
  }, []));
  async function act(change: (j: ParallelJournal) => ParallelJournal, kind: ParallelAction,
    extra: Record<string, unknown> = {}, resetScroll = false): Promise<boolean> {
    if (operation.current) return false;
    operation.current = true; setBusy(true);
    try {
      const j = await updateParallelJournal(change, kind, extra);
      if (active.current) {
        setJournal(j); setError("");
        if (resetScroll) { setClues([]); setAbout(false); setEffortSaved(false); scroll.current?.scrollTo({ y: 0, animated: false }); }
      }
      return true;
    } catch { if (active.current) setError("Couldn’t save your place on this device. Retry before leaving; your previous bookmark is safe."); return false; }
    finally { operation.current = false; if (active.current) setBusy(false); }
  }
  const button = (label: string, press: () => void, selected = false, disabled = false) => <Pressable
    accessibilityRole="button" accessibilityState={{ selected, disabled: disabled || busy }}
    disabled={disabled || busy} onPress={press} style={[s.button, selected && s.selected, (disabled || busy) && { opacity: .45 }]}>
    <Text style={[s.buttonText, selected && { color: "#FFF9ED" }]}>{label}</Text>
  </Pressable>;
  if (!journal) return <View style={[s.loading, { paddingTop: insets.top + 24 }]}>
    {error ? <><Text accessibilityRole="alert" style={s.error}>{error}</Text>{button("Retry", () => {
      loadParallelJournal().then(setJournal).then(() => setError("")).catch(() => setError("Saved reading is still unavailable; it has not been reset."));
    })}</> : <ActivityIndicator color={ACCENT} />}
  </View>;
  const text = currentParallelText(journal), progress = currentParallelProgress(journal);
  const paragraph = text.paragraphs[progress.paragraph];
  function go(index: number) { void act(j => changeParallelProgress(j, { paragraph: index, reread: false }), "passage", {}, true); }
  const arabic = (p: typeof paragraph) => <Text key={p.id} selectable style={[s.arabic,
    { fontSize: 30 * journal.size, lineHeight: 49 * journal.size }]}>{arabicFor(p, journal.vowels)}</Text>;
  const support = (code: SupportLanguage) => <View key={code} style={s.card}>
    <Text style={s.label}>{code === "grc" ? "Ancient Greek" : SUPPORT.find(l => l.code === code)?.name} · {text.versions[code]}</Text>
    <Text selectable style={[s.support, { fontSize: 23 * journal.size, lineHeight: 35 * journal.size }]}>{paragraph[code]}</Text>
  </View>;
  return <ScrollView ref={scroll} style={s.screen} contentContainerStyle={[s.content, { paddingTop: insets.top + 18, paddingBottom: insets.bottom + 32 }]}>
    <Tabs.Screen options={{ tabBarStyle: journal.library ? { backgroundColor: colors.surface, borderTopColor: colors.border } : { display: "none" } }} />
    {error ? <View><Text accessibilityRole="alert" style={s.error}>{error}</Text>{button("Retry saving", () => { void act(j => j, "open"); })}</View> : null}
    <View style={s.top}><Text style={s.eyebrow}>PARALLEL READING</Text>{!journal.library && button("Alif", () => { void act(j => j, "leave").then(ok => { if (ok) router.push("/"); }); })}{!journal.library && button("Library", () => { void act(j => ({ ...j, library: true }), "library", {}, true); })}</View>
    {journal.library ? <>
      <Text style={s.title}>Read your way into Arabic.</Text>
      <Text style={s.body}>Let Greek, Latin and Russian give you clues. Return to the Arabic and see what you can now recognise.</Text>
      {journal.progress[journal.textId] && button(`Continue · ${text.title}`, () => { void act(j => ({ ...j, library: false }), "select", {}, true); }, true)}
      {parallelLibrary.texts.map(t => {
        const saved = journal.progress[t.id];
        const count = t.paragraphs.reduce((n, p) => n + p.ar.split(/\s+/).length, 0);
        return <Pressable accessibilityRole="button" disabled={busy} key={t.id} style={s.card} onPress={() => {
          void act(j => changeParallelProgress({ ...j, textId: t.id, library: false }, {}), "select", {}, true);
        }}>
          <Text style={s.label}>{t.author} · {count} Arabic words</Text>
          <Text style={s.cardTitle}>{t.title}</Text><Text style={s.body}>{t.description}</Text>
          <Text style={s.link}>{saved?.completed ? "Read · open again →" : saved ? `Continue · passage ${saved.paragraph + 1} of ${t.paragraphs.length} →` : `Begin · ${t.paragraphs.length} passages →`}</Text>
        </Pressable>;
      })}
      <Text style={s.small}>Your place and settings stay on this device. Reading here creates no vocabulary-review obligations.</Text>
    </> : <>
      <Text style={s.label}>{text.author}</Text><Text style={s.title}>{text.title}</Text>
      <View style={s.row}>{button("A−", () => { void act(j => ({ ...j, size: Math.max(.85, +(j.size - .1).toFixed(2)) }), "size"); }, false, journal.size <= .85)}
        {button("A+", () => { void act(j => ({ ...j, size: Math.min(1.4, +(j.size + .1).toFixed(2)) }), "size"); }, false, journal.size >= 1.4)}
        {button(journal.vowels ? "Hide marks" : "Vowel marks", () => { void act(j => ({ ...j, vowels: !j.vowels }), "vowels"); }, journal.vowels)}
      </View>
      <Text style={s.small}>{progress.reread ? "Reread · Arabic alone" : `Passage ${progress.paragraph + 1} of ${text.paragraphs.length}`}</Text>
      <View style={s.card}><Text style={s.label}>Arabic · {text.versions.ar}</Text>
        {progress.reread ? text.paragraphs.map(arabic) : arabic(paragraph)}
        {journal.vowels && !paragraph.ar_vowelled && <Text style={s.small}>Only the existing selective vowel marks are available for this text.</Text>}
      </View>
      {!progress.reread && <>
        <View style={s.row}>{SUPPORT.map(l => <View key={l.code}>{button(l.name, () => { void act(j => ({ ...j, support: l.code, revealed: true }), "support"); }, journal.support === l.code)}</View>)}</View>
        <View style={s.row}>{button(journal.revealed ? "Hide support" : `Reveal ${SUPPORT.find(l => l.code === journal.support)?.name}`, () => { void act(j => ({ ...j, revealed: !j.revealed, all: false }), "reveal"); })}
          {button(journal.all ? "Show a pair" : "All four", () => { void act(j => ({ ...j, all: !j.all }), "all"); }, journal.all)}</View>
        {journal.all ? SUPPORT.map(l => support(l.code)) : journal.revealed ? support(journal.support) : <Text style={s.small}>Choose a language when you want a clue.</Text>}
        <View style={s.row}>{button("← Previous", () => go(progress.paragraph - 1), false, progress.paragraph === 0)}
          {progress.paragraph < text.paragraphs.length - 1 ? button("Next passage →", () => go(progress.paragraph + 1), true)
            : button("Reread Arabic →", () => { void act(j => changeParallelProgress(j, { reread: true }), "reread", {}, true); }, true)}</View>
        <Text style={s.label}>Word clues · optional English</Text>
        {paragraph.clues.map(c => <View key={c.id}>
          {button(`${clues.includes(c.id) ? "−" : "+"} ${c.form}`, () => {
            const visible = !clues.includes(c.id);
            void act(j => j, "clue", { clue_id: c.id, visible }).then(ok => { if (ok) setClues(v => visible ? [...v, c.id] : v.filter(id => id !== c.id)); });
          }, clues.includes(c.id))}
          {clues.includes(c.id) && <View style={s.clue}><Text selectable style={s.body}>{c.meaning}</Text><Text selectable style={s.small}>{c.bridge}</Text></View>}
        </View>)}
        <View style={s.row}>{text.paragraphs.map((p, i) => <View key={p.id}>{button(`${i + 1}`, () => go(i), i === progress.paragraph)}</View>)}</View>
      </>}
      {progress.reread && <>
        <Text style={s.body}>Which words can you recognise from the Arabic now? You can return to the parallel passages whenever you want.</Text>
        <View style={s.row}>{button("Back to parallel", () => { void act(j => changeParallelProgress(j, { reread: false }), "reread", {}, true); })}
          {button(progress.completed ? "Read again from start" : "Finish reading", () => {
            void act(j => changeParallelProgress(j, progress.completed ? { paragraph: 0, reread: false } : { completed: true }), progress.completed ? "reread" : "complete", {}, progress.completed);
          }, true)}</View>
        {progress.completed && <>
          <Text style={s.cardTitle}>Reading finished.</Text><Text style={s.small}>Optional: how did it feel?</Text>
          <View style={s.row}>{([ ["smooth", "Smooth"], ["some-work", "Some work"], ["tiring", "Tiring"] ] as const).map(([effort, name]) => <View key={effort}>{button(name, () => {
            void act(j => j, "reflection", { effort }).then(ok => { if (ok) setEffortSaved(true); });
          })}</View>)}</View>{effortSaved && <Text style={s.small}>Reflection saved.</Text>}
          {button("Choose another reading →", () => { void act(j => ({ ...j, library: true }), "library", {}, true); }, true)}
        </>}
      </>}
      {button(about ? "Hide text sources" : "About this text & its versions", () => {
        void act(j => j, "about", { visible: !about }).then(ok => { if (ok) setAbout(!about); });
      })}
      {about && <View style={s.card}><Text style={s.body}>{text.source_note}</Text>
        <Text style={s.small}>New translations are AI-generated learning aids, reviewed during preparation but not independently checked by a language specialist.</Text>
        {button("Open original source ↗", () => { void act(j => j, "about", { visible: true }).then(ok => { if (ok) void Linking.openURL(text.source_url).catch(() => setError("Couldn’t open the source link.")); }); })}
      </View>}
    </>}
  </ScrollView>;
}
const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: PAPER }, content: { paddingHorizontal: 20, gap: 14, width: "100%", maxWidth: 720, alignSelf: "center" },
  loading: { flex: 1, padding: 24, backgroundColor: PAPER, justifyContent: "center", gap: 16 },
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 },
  eyebrow: { color: ACCENT, fontSize: 11, letterSpacing: 1.5, fontWeight: "700" },
  title: { color: INK, fontFamily: "EBGaramond_400Regular", fontSize: 36, lineHeight: 40 },
  cardTitle: { color: INK, fontFamily: "EBGaramond_400Regular", fontSize: 27, lineHeight: 32 },
  body: { color: INK, fontSize: 15, lineHeight: 24 }, small: { color: MUTED, fontSize: 12, lineHeight: 20 },
  label: { color: MUTED, fontSize: 12, lineHeight: 19, fontWeight: "600" },
  card: { backgroundColor: "#FFF9ED", borderWidth: 1, borderColor: "#D8C4A2", borderRadius: 16, padding: 18, gap: 12 },
  arabic: { color: INK, fontFamily: fontFamily.arabic, writingDirection: "rtl", textAlign: "right" },
  support: { color: INK, fontFamily: "EBGaramond_400Regular", writingDirection: "ltr", textAlign: "left" },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8 }, button: { minHeight: 44, justifyContent: "center", alignItems: "center", paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: "#CDB796", borderRadius: 10 },
  buttonText: { color: ACCENT, fontSize: 14, textAlign: "center" }, selected: { backgroundColor: ACCENT, borderColor: ACCENT },
  clue: { padding: 14, gap: 6, backgroundColor: "#E8DCC5", borderRadius: 10 }, link: { color: ACCENT, fontSize: 14 },
  error: { color: "#9D2525", fontSize: 14, lineHeight: 22 },
});
