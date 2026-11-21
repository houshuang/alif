import React, { useState } from "react";
import { Keyboard, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, useWindowDimensions, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily } from "../theme";
import { ConfusionAnalysis, ConfusionCaptureIn } from "../types";

/** Side-icon launched popover; no persistent row in the reading dock. */
export function ConfusionPicker({ failedLemmaId, confusionData, existing, onSave, onClear, visible, onClose }: {
  failedLemmaId: number;
  confusionData: ConfusionAnalysis | null;
  existing?: ConfusionCaptureIn;
  onSave: (capture: ConfusionCaptureIn) => void;
  onClear: () => void;
  visible: boolean;
  onClose: () => void;
}) {
  const { width, height } = useWindowDimensions();
  const [text, setText] = useState(existing?.confused_with_text ?? "");
  const candidates = [...(confusionData?.similar_words ?? []), ...(confusionData?.phonetic_similar ?? [])]
    .filter((w, i, all) => w.lemma_id !== failedLemmaId && all.findIndex(x => x.lemma_id === w.lemma_id) === i);
  const ids = candidates.slice(0, 4).map(w => w.lemma_id);
  const close = () => { onClose(); Keyboard.dismiss(); };
  const save = (capture: ConfusionCaptureIn) => {
    setText(capture.capture_method === "free_text" ? capture.confused_with_text ?? "" : "");
    onSave(capture); close();
  };
  const saveText = () => {
    if (text.trim()) save({ failed_lemma_id: failedLemmaId, capture_method: "free_text",
      confused_with_text: text.trim(), candidates_shown: ids });
  };
  return <Modal visible={visible} transparent animationType="none" onRequestClose={close}>
    <KeyboardAvoidingView style={styles.overlay} behavior={Platform.OS === "ios" ? "padding" : Platform.OS === "android" ? "height" : undefined}>
      <Pressable style={StyleSheet.absoluteFill} accessibilityRole="button" accessibilityLabel="Dismiss confusion popover" onPress={close} />
      <View accessibilityViewIsModal style={[styles.popover, { width: Math.min(380, width - 32), maxHeight: height * 0.7 }]}>
        <View style={styles.row}>
          <Text style={styles.heading}>Mixed up with which word?</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Close confusion entry" onPress={close} style={styles.smallButton}>
            <Ionicons name="close-outline" size={22} color={colors.textSecondary} />
          </Pressable>
        </View>
        <ScrollView keyboardShouldPersistTaps="handled">
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
      {existing && <Pressable accessibilityRole="button" accessibilityLabel="Clear confusion" onPress={() => { setText(""); onClear(); close(); }} style={styles.clear}>
        <Text style={styles.promptText}>Clear this confusion</Text>
      </Pressable>}
      </ScrollView>
      </View>
    </KeyboardAvoidingView>
  </Modal>;
}
const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: "flex-end", alignItems: "center", padding: 16, paddingBottom: 86, backgroundColor: "rgba(0,0,0,0.25)" },
  popover: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: 12 },
  heading: { flex: 1, color: colors.text, fontSize: 16, fontWeight: "600" },
  clear: { minHeight: 44, justifyContent: "center", marginTop: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
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
