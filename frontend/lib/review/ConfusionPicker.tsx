import React, { useState } from "react";
import { Keyboard, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily } from "../theme";
import { ConfusionAnalysis, ConfusionCaptureIn } from "../types";

/** One optional row on red or yellow. Close after save; key by token in parent. */
export function ConfusionPicker({ failedLemmaId, confusionData, existing, onSave, onClear, onOpen }: {
  failedLemmaId: number;
  confusionData: ConfusionAnalysis | null;
  existing?: ConfusionCaptureIn;
  onSave: (capture: ConfusionCaptureIn) => void;
  onClear: () => void;
  onOpen: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [text, setText] = useState(existing?.confused_with_text ?? "");
  const candidates = [...(confusionData?.similar_words ?? []), ...(confusionData?.phonetic_similar ?? [])]
    .filter((w, i, all) => w.lemma_id !== failedLemmaId && all.findIndex(x => x.lemma_id === w.lemma_id) === i);
  const ids = candidates.slice(0, 4).map(w => w.lemma_id);
  const saved = existing?.capture_method === "free_text" ? existing.confused_with_text
    : candidates.find(w => w.lemma_id === existing?.confused_with_lemma_id)?.gloss_en
      ?? (existing ? "word selected" : null);
  const close = () => { setExpanded(false); Keyboard.dismiss(); };
  const save = (capture: ConfusionCaptureIn) => { onSave(capture); close(); };
  const saveText = () => {
    if (text.trim()) save({ failed_lemma_id: failedLemmaId, capture_method: "free_text",
      confused_with_text: text.trim(), candidates_shown: ids });
  };
  return <View style={styles.container}>
    <View style={styles.row}>
      <Pressable accessibilityRole="button" accessibilityLabel={saved ? `Edit confusion: ${saved}` : "Mixed up with another word?"}
        accessibilityState={{ expanded }} onPress={() => {
          if (expanded) close(); else { setExpanded(true); onOpen(); }
        }} style={styles.prompt}>
        <Ionicons name={saved ? "checkmark-outline" : "shuffle-outline"} size={15} color={colors.textSecondary} />
        <Text style={styles.promptText} numberOfLines={1}>{saved ? `Mixed up: ${saved}` : "Mixed up with another word?"}</Text>
      </Pressable>
      {expanded ? <Pressable accessibilityRole="button" accessibilityLabel="Close confusion entry" onPress={close} style={styles.smallButton}>
        <Ionicons name="close-outline" size={20} color={colors.textSecondary} />
      </Pressable> : existing ? <Pressable accessibilityRole="button" accessibilityLabel="Clear confusion" style={styles.smallButton}
        onPress={() => { setText(""); onClear(); }}><Ionicons name="close-outline" size={18} color={colors.textSecondary} /></Pressable> : null}
    </View>
    {expanded && <>
      <View style={styles.row}>
        <TextInput value={text} onChangeText={setText} onSubmitEditing={saveText}
          accessibilityLabel="Word or English meaning you confused this with"
          placeholder="The word or English meaning…" placeholderTextColor={colors.textSecondary}
          style={styles.input} returnKeyType="done" blurOnSubmit />
        <Pressable accessibilityRole="button" accessibilityLabel="Save confusion" onPress={saveText}
          disabled={!text.trim()} style={[styles.save, !text.trim() && { opacity: 0.4 }]}>
          <Ionicons name="checkmark-outline" size={20} color={colors.text} />
        </Pressable>
      </View>
      {candidates.length > 0 && <View style={styles.candidates}>
        {candidates.slice(0, 4).map(w => <Pressable key={w.lemma_id} style={styles.candidate}
          accessibilityRole="button" accessibilityLabel={`Confused with ${w.lemma_ar}: ${w.gloss_en}`}
          onPress={() => save({ failed_lemma_id: failedLemmaId, capture_method: "suggested_pick",
            confused_with_lemma_id: w.lemma_id, candidates_shown: ids })}>
          <Text style={styles.arabic}>{w.lemma_ar}</Text><Text style={styles.gloss} numberOfLines={1}>{w.gloss_en}</Text>
        </Pressable>)}
      </View>}
    </>}
  </View>;
}
const styles = StyleSheet.create({
  container: { marginTop: 2, width: "100%", maxWidth: 500, alignSelf: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  prompt: { flex: 1, minHeight: 44, flexDirection: "row", alignItems: "center", gap: 6 },
  promptText: { color: colors.textSecondary, fontSize: 12, fontFamily: fontFamily.translitRegular, flexShrink: 1 },
  smallButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  input: { flex: 1, minHeight: 44, borderWidth: 1, borderColor: colors.border, borderRadius: 6,
    paddingHorizontal: 8, color: colors.text, fontSize: 13, fontFamily: fontFamily.translitRegular, backgroundColor: colors.surface },
  save: { width: 44, height: 44, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceLight, borderRadius: 6 },
  candidates: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 4 },
  candidate: { flexDirection: "row", alignItems: "center", gap: 5, minHeight: 44, paddingHorizontal: 8,
    maxWidth: "100%", borderWidth: 1, borderColor: colors.border, borderRadius: 6 },
  arabic: { fontFamily: fontFamily.arabic, fontSize: 18, color: colors.arabic },
  gloss: { color: colors.textSecondary, fontSize: 11, flexShrink: 1 },
});
