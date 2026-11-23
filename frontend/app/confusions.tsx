import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Stack } from "expo-router";
import { colors, fontFamily } from "../lib/theme";
import { ConfusionLibrary, ConfusionPair, pairStatusLabel } from "../lib/confusion-learning";
import { listConfusions, prepareConfusion, refreshConfusions } from "../lib/api";
import ManualMatch from "../lib/review/ManualConfusionMatch";
import ConfusionPractice from "../lib/review/ConfusionPractice";


export default function ConfusionsScreen() {
  const [data, setData] = useState<ConfusionLibrary | null>(null), [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true), [active, setActive] = useState<ConfusionPair | null>(null);
  const [tab, setTab] = useState<"pairs" | "manual">("pairs");
  const [showProgress, setShowProgress] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const [start, setStart] = useState(0);
  const [session] = useState(`pair-study-${Date.now()}`);
  const reload = useCallback(async () => {
    setError(null); setLoading(true);
    try { setData(await refreshConfusions()); } catch { setError("Your confusion pairs could not load. Check your connection and retry."); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    if (!data?.pairs.some(p => p.generation_state === "working")) return;
    const timer = setInterval(() => { void listConfusions().then(setData).catch(() => {}); }, 3000);
    return () => clearInterval(timer);
  }, [data?.pairs.some(p => p.generation_state === "working")]);
  async function study(pair: ConfusionPair) {
    if (pair.lesson) { setStart(0); setActive({...pair, offline: data?.offline}); return; }
    setError(null);
    try { await prepareConfusion(pair.id); await reload(); } catch { setError("The lesson could not be prepared. Please retry."); }
  }
  return <View style={styles.container}><Stack.Screen options={{ title: "Confusion pairs" }} />
    <ScrollView contentContainerStyle={styles.content}>
      <Text style={styles.title}>Learn the difference</Text>
      <Text style={styles.text}>Short contrasts for words you have mixed up. Practice success starts recovery; later unassisted reading confirms it.</Text>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded: showProgress }} onPress={() => setShowProgress(!showProgress)} style={styles.action}><Text style={styles.link}>How progress works</Text></Pressable>
      {showProgress && <Text style={styles.meta}>Improving means successful practice with both words. Resolved means the distinction also holds in later unassisted reading: four practice successes, then three different clean sentence contexts across two days, starting after 24 hours. Reporting confusion again reopens the pair.</Text>}
      {loading && !data && <ActivityIndicator color={colors.accent} />}
      {!!error && <View><Text accessibilityRole="alert" style={styles.error}>{error}</Text><Pressable accessibilityRole="button" onPress={reload} style={styles.action}><Text style={styles.link}>Retry</Text></Pressable></View>}
      {data?.offline && <Text style={styles.meta}>Offline: saved lessons are available. New matches and lessons need a connection.</Text>}
      {data && data.pairs.length === 0 && data.unresolved.length === 0 && <Text style={styles.text}>When you name a word you confused with another, it will appear here.</Text>}
      {data && <View style={styles.tabs}>
        <Pressable accessibilityRole="button" accessibilityState={{ selected: tab === "pairs" }} onPress={() => setTab("pairs")} style={styles.action}><Text style={tab === "pairs" ? styles.link : styles.text}>Pairs ({data.pairs.length})</Text></Pressable>
        <Pressable accessibilityRole="button" accessibilityState={{ selected: tab === "manual" }} onPress={() => setTab("manual")} style={styles.action}><Text style={tab === "manual" ? styles.link : styles.text}>Needs matching ({data.unresolved.filter(c => c.state !== "not_a_word").length})</Text></Pressable>
      </View>}
      {tab === "pairs" && data?.pairs.slice().sort((a,b) => Number(!!b.lesson) - Number(!!a.lesson)).map(p => <View key={p.id} style={styles.row}>
        <Text style={styles.arabic}>{p.a.arabic} · {p.b.arabic}</Text>
        <Text style={styles.text}>{p.a.gloss} / {p.b.gloss}</Text>
        <Text style={styles.status}>{pairStatusLabel(p.status)}</Text>
        <Text style={styles.meta}>Exercises: {p.practice_total_correct}/{p.practice_attempts} correct · {p.clean_contexts} clean reading contexts · {p.clean_days} days</Text>
        {p.status === "resolved" && <Text style={styles.meta}>The distinction has held in later reading.</Text>}
        {p.generation_state === "working" ? <Text style={styles.meta}>Preparing and checking a contrast lesson…</Text> : <Pressable accessibilityRole="button" onPress={() => study(p)} style={styles.action}><Text style={styles.link}>{p.lesson ? "Study and practise" : p.generation_state === "failed" ? "Retry lesson preparation" : "Prepare a contrast lesson"}</Text></Pressable>}
        {p.generation_state === "failed" && <Text style={styles.meta}>The lesson did not pass preparation. Your pair and history are saved.</Text>}
      </View>)}
      {tab === "manual" && <>
        <Text style={styles.heading}>Manually entered confusors</Text>
        {data?.unresolved.filter(c => c.state !== "not_a_word").sort((a,b) => b.capture_id - a.capture_id).map(c => <ManualMatch key={c.capture_id} capture={c} onSaved={reload} />)}
        <Pressable accessibilityRole="button" accessibilityState={{ expanded: showNotes }} onPress={() => setShowNotes(!showNotes)} style={styles.action}><Text style={styles.link}>{showNotes ? "Hide" : "Show"} forgetting notes</Text></Pressable>
        {showNotes && data?.unresolved.filter(c => c.state === "not_a_word").map(c => <ManualMatch key={c.capture_id} capture={c} onSaved={reload} />)}
      </>}
    </ScrollView>
    {active?.lesson && <ConfusionPractice key={`${active.id}:${start}`} pair={active} start={start} sessionId={session} continueLabel={start + 2 < active.lesson.questions.length ? "Try sentence exercises" : "Finish practice"} onClose={() => { setActive(null); void reload(); }} onComplete={async next => { if (next < active.lesson!.questions.length) {
        try { const updated = await listConfusions(); setData(updated); setActive(updated.pairs.find(p => p.id === active.id) ?? active); } catch { /* Keep the cached lesson available offline. */ }
        setStart(next);
      } else { setActive(null); void reload(); } }} />}
  </View>;
}
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg }, content: { padding: 20, paddingBottom: 40, maxWidth: 620, alignSelf: "center", width: "100%" },
  title: { color: colors.text, fontSize: 24, fontWeight: "600", marginBottom: 10 }, heading: { color: colors.text, fontSize: 19, fontWeight: "600", marginTop: 28 },
  text: { color: colors.text, fontSize: 15, lineHeight: 23 }, meta: { color: colors.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 5 },
  tabs: { flexDirection: "row", flexWrap: "wrap", gap: 20, marginTop: 12 },
  row: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 14, marginTop: 20 },
  arabic: { color: colors.arabic, fontSize: 30, fontFamily: fontFamily.arabic, lineHeight: 46, textAlign: "right", writingDirection: "rtl" },
  arabicSmall: { color: colors.arabic, fontSize: 25, fontFamily: fontFamily.arabic, lineHeight: 40, writingDirection: "rtl" },
  status: { color: colors.text, fontSize: 14, fontWeight: "600", marginTop: 10 }, link: { color: colors.accent, fontSize: 15 },
  action: { minHeight: 44, justifyContent: "center" }, error: { color: colors.missed, marginTop: 12 },
  search: { flexDirection: "row", gap: 12, marginTop: 10 }, input: { flex: 1, minHeight: 44, paddingHorizontal: 10, color: colors.text, borderWidth: 1, borderColor: colors.border, borderRadius: 6 },
  option: { minHeight: 48, flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap" },
});
