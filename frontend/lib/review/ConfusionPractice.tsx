import React, { useRef, useState, useEffect } from "react";
import { Modal, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily, ltr } from "../theme";
import { ConfusionPair, pairStatusLabel } from "../confusion-learning";
import { recordConfusionAttempt } from "../api";

export default function ConfusionPractice({ pair, sessionId, start = 0, showLesson = start === 0, onClose, onComplete, continueLabel }: {
  pair: ConfusionPair; sessionId: string; start?: number; showLesson?: boolean; continueLabel?: string; onClose: () => void; onComplete: (next: number) => void | Promise<void>;
}) {
  const { height, width } = useWindowDimensions();
  const [stage, setStage] = useState<"lesson" | "exercise">(showLesson ? "lesson" : "exercise");
  const [index, setIndex] = useState(start);
  const [answer, setAnswer] = useState<number | null | undefined>(undefined);
  const [saving, setSaving] = useState(false), [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);
  const attemptId = useRef<string | null>(null);
  const order = useRef(Math.random() < .5 ? [pair.a, pair.b] : [pair.b, pair.a]);
  const q = pair.lesson!.questions[index];
  useEffect(() => {
    // Explicit teaching exposure lets trial analyses account for the co-intervention.
    void recordConfusionAttempt(pair.id, { client_attempt_id: `view-${pair.id}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      session_id: sessionId, question_index: -1, chosen_lemma_id: null, assisted: true }, pair.offline).catch(() => {});
  }, [pair.id, sessionId, start]);
  async function choose(id: number | null) {
    if (saving || answer !== undefined) return;
    setSaving(true); setError(null);
    if (!attemptId.current) attemptId.current = `pair-${pair.id}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try {
      const result = await recordConfusionAttempt(pair.id, { client_attempt_id: attemptId.current,
        session_id: sessionId, question_index: index, chosen_lemma_id: id, assisted: id === null }, pair.offline);
      setQueued(result.queued); setAnswer(id);
    } catch { setError("Your answer was not saved. Tap your choice to retry."); }
    finally { setSaving(false); }
  }
  async function next() {
    if (index + 1 >= Math.min(start + 2, pair.lesson!.questions.length)) { setSaving(true); try { await onComplete(index + 1); } finally { setSaving(false); } return; }
    setIndex(i => i + 1); setAnswer(undefined); attemptId.current = null; setError(null);
  }
  const correctWord = q.answer_lemma_id === pair.a.id ? pair.a : pair.b;
  return <Modal transparent animationType="none" onRequestClose={onClose}>
    <View style={styles.overlay}>
      <View accessibilityViewIsModal style={[styles.card, { maxHeight: height * .9, width: Math.min(560, width - 24) }]}>
        <View style={styles.header}><Text style={styles.heading}>{stage === "lesson" ? "Tell them apart" : "A quick distinction"}</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Return to reading" onPress={onClose} style={styles.close}><Ionicons name="close-outline" size={25} color={colors.textSecondary} /></Pressable></View>
        <ScrollView contentContainerStyle={styles.body}>
          <Text style={styles.meta}>{pairStatusLabel(pair.status)} · {pair.practice_correct} practice answers · {pair.clean_contexts} clean contexts</Text>
          {stage === "lesson" ? <>
            {[pair.a, pair.b].map((w,i) => <View key={w.id} style={styles.word}>
              <Text style={styles.arabic}>{w.arabic}</Text><Text style={styles.meaning}>{ltr(w.gloss)}</Text>
              <Text style={styles.meta}>{w.transliteration}</Text><Text style={styles.text}>{ltr(i === 0 ? pair.lesson!.a_cue : pair.lesson!.b_cue)}</Text>
            </View>)}
            <Text style={styles.explanation}>{ltr(pair.lesson!.distinction)}</Text>
            <Pressable accessibilityRole="button" onPress={() => setStage("exercise")} style={styles.primary}><Text style={styles.primaryText}>Try two short exercises</Text></Pressable>
          </> : <>
            <Text style={styles.meta}>{index - start + 1} of {Math.min(2, pair.lesson!.questions.length - start)}</Text>
            <Text style={q.kind === "cloze" ? styles.sentence : styles.prompt}>{q.kind === "cloze" ? q.prompt : ltr(q.prompt)}</Text>
            <View style={styles.choices}>{order.current.map(w => <Pressable key={w.id} accessibilityRole="button" accessibilityLabel={`Choose ${w.arabic}`}
              disabled={saving || answer !== undefined} onPress={() => choose(w.id)}
              style={[styles.choice, answer === w.id && { borderColor: answer === q.answer_lemma_id ? colors.good : colors.missed }]}>
              <Text style={styles.arabic}>{q.options?.find(o => o.lemma_id === w.id)?.arabic ?? w.arabic}</Text></Pressable>)}</View>
            {answer === undefined ? <Pressable accessibilityRole="button" disabled={saving} onPress={() => choose(null)} style={styles.secondary}><Text style={styles.meta}>{saving ? "Saving answer…" : "Show me the distinction"}</Text></Pressable> : <>
              <Text style={styles.feedback}>{answer === q.answer_lemma_id ? "Yes." : `${correctWord.arabic} — ${correctWord.gloss}`}</Text>
              {!!q.translation && <Text style={styles.text}>{ltr(q.translation)}</Text>}
              <Text style={styles.explanation}>{ltr(q.feedback)}</Text>
              {queued && <Text style={styles.meta}>Saved on this device. Progress will sync when connected.</Text>}
              <Pressable accessibilityRole="button" onPress={next} disabled={saving} style={styles.primary}><Text style={styles.primaryText}>{index + 1 >= Math.min(start + 2, pair.lesson!.questions.length) ? continueLabel ?? "Back to reading" : "Next exercise"}</Text></Pressable>
            </>}
            {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          </>}
          <Text style={styles.footer}>Practice is tracked for this pair. Later unassisted reading checks whether the distinction has stuck.</Text>
        </ScrollView>
      </View>
    </View>
  </Modal>;
}
const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(0,0,0,.55)", alignItems: "center", justifyContent: "center" },
  card: { backgroundColor: colors.surface, borderRadius: 14, borderWidth: 1, borderColor: colors.border, overflow: "hidden" },
  header: { flexDirection: "row", alignItems: "center", paddingLeft: 16, paddingRight: 4, borderBottomWidth: 1, borderBottomColor: colors.border },
  heading: { color: colors.text, fontSize: 18, fontWeight: "600", flex: 1 }, close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  body: { padding: 16 }, meta: { color: colors.textSecondary, fontSize: 12, lineHeight: 18 },
  word: { marginTop: 16 }, arabic: { color: colors.arabic, fontFamily: fontFamily.arabic, fontSize: 32, lineHeight: 46, textAlign: "center", writingDirection: "rtl" },
  meaning: { color: colors.text, fontSize: 17, fontWeight: "600", textAlign: "center", marginBottom: 4 },
  text: { color: colors.text, fontSize: 15, lineHeight: 23, marginTop: 6 },
  explanation: { color: colors.text, fontSize: 15, lineHeight: 23, marginVertical: 16 },
  primary: { minHeight: 48, backgroundColor: colors.accent, borderRadius: 8, alignItems: "center", justifyContent: "center", marginTop: 12, paddingHorizontal: 12 },
  primaryText: { color: colors.bg, fontSize: 15, fontWeight: "600" },
  prompt: { color: colors.text, fontSize: 20, lineHeight: 29, marginVertical: 22 },
  sentence: { color: colors.arabic, fontFamily: fontFamily.arabic, fontSize: 30, lineHeight: 48, writingDirection: "rtl", textAlign: "right", marginVertical: 22 },
  choices: { flexDirection: "row", gap: 12 }, choice: { flex: 1, minHeight: 62, borderWidth: 1, borderColor: colors.border, borderRadius: 8, justifyContent: "center", padding: 4 },
  secondary: { minHeight: 44, alignItems: "center", justifyContent: "center", marginTop: 8 },
  feedback: { color: colors.text, fontSize: 17, marginTop: 16, fontWeight: "600" },
  footer: { color: colors.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 20 }, error: { color: colors.missed, marginTop: 12 },
});
