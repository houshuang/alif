import React, { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily } from "../theme";
import { ConfusionAnalysis, ConfusionCaptureIn } from "../types";

interface ConfusionPickerProps {
  failedLemmaId: number;
  confusionData: ConfusionAnalysis;
  existing?: ConfusionCaptureIn;
  onSave: (capture: ConfusionCaptureIn) => void;
  onClear: () => void;
}

export function ConfusionPicker({
  failedLemmaId,
  confusionData,
  existing,
  onSave,
  onClear,
}: ConfusionPickerProps) {
  const [freeText, setFreeText] = useState<string>(
    existing?.capture_method === "free_text" ? existing.confused_with_text ?? "" : "",
  );
  const [typing, setTyping] = useState<boolean>(false);

  const candidateIds = useMemo(() => {
    const seen = new Set<number>();
    const out: number[] = [];
    for (const w of confusionData.similar_words ?? []) {
      if (!seen.has(w.lemma_id)) { seen.add(w.lemma_id); out.push(w.lemma_id); }
    }
    for (const w of confusionData.phonetic_similar ?? []) {
      if (!seen.has(w.lemma_id)) { seen.add(w.lemma_id); out.push(w.lemma_id); }
    }
    return out;
  }, [confusionData]);

  const savedSummary = useMemo(() => {
    if (!existing) return null;
    if (existing.capture_method === "suggested_pick") {
      const all = [...(confusionData.similar_words ?? []), ...(confusionData.phonetic_similar ?? [])];
      const match = all.find((w) => w.lemma_id === existing.confused_with_lemma_id);
      return {
        kind: "lemma" as const,
        arabic: match?.lemma_ar ?? `#${existing.confused_with_lemma_id}`,
        gloss: match?.gloss_en ?? null,
      };
    }
    return { kind: "text" as const, text: existing.confused_with_text ?? "" };
  }, [existing, confusionData]);

  const handleSubmitText = () => {
    const text = freeText.trim();
    if (!text) return;
    onSave({
      failed_lemma_id: failedLemmaId,
      capture_method: "free_text",
      confused_with_text: text,
      candidates_shown: candidateIds,
    });
    setTyping(false);
  };

  const handleClearAll = () => {
    setFreeText("");
    setTyping(false);
    onClear();
  };

  if (savedSummary) {
    return (
      <View style={styles.savedRow}>
        <Ionicons name="checkmark-circle" size={16} color={colors.confused} />
        <Text style={styles.savedLabel}>Will record: </Text>
        {savedSummary.kind === "lemma" ? (
          <>
            <Text style={styles.savedArabic}>{savedSummary.arabic}</Text>
            {savedSummary.gloss && (
              <Text style={styles.savedGloss} numberOfLines={1}>
                ({savedSummary.gloss})
              </Text>
            )}
          </>
        ) : (
          <Text style={styles.savedText} numberOfLines={1}>"{savedSummary.text}"</Text>
        )}
        <Pressable onPress={handleClearAll} hitSlop={8} style={styles.clearLinkWrap}>
          <Text style={styles.clearLink}>clear</Text>
        </Pressable>
      </View>
    );
  }

  if (!typing) {
    return (
      <Pressable
        onPress={() => setTyping(true)}
        style={({ pressed }) => [styles.collapsedLink, pressed && { opacity: 0.5 }]}
        accessibilityRole="button"
      >
        <Text style={styles.collapsedText}>
          Or type the word you thought it was ›
        </Text>
      </Pressable>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.textRow}>
        <TextInput
          value={freeText}
          onChangeText={setFreeText}
          onSubmitEditing={handleSubmitText}
          placeholder="what word did you think it was?"
          placeholderTextColor={colors.textSecondary}
          style={styles.textInput}
          autoFocus
          returnKeyType="done"
          blurOnSubmit
        />
        <Pressable
          onPress={handleSubmitText}
          disabled={freeText.trim().length === 0}
          style={[styles.saveButton, freeText.trim().length === 0 && styles.saveButtonDisabled]}
        >
          <Text style={styles.saveButtonText}>Save</Text>
        </Pressable>
        <Pressable onPress={() => { setTyping(false); setFreeText(""); }} hitSlop={8}>
          <Text style={styles.cancelLink}>cancel</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  collapsedLink: {
    paddingVertical: 6,
    paddingHorizontal: 4,
    marginTop: 4,
    alignSelf: "flex-start",
  },
  collapsedText: {
    color: colors.textSecondary,
    fontSize: 13,
    fontFamily: fontFamily.translitRegular,
    textDecorationLine: "underline",
  },
  container: {
    marginTop: 6,
    padding: 8,
    borderRadius: 8,
    backgroundColor: "rgba(243, 156, 18, 0.08)",
    borderWidth: 1,
    borderColor: "rgba(243, 156, 18, 0.25)",
  },
  textRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  textInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 6,
    color: colors.text,
    fontSize: 13,
    fontFamily: fontFamily.translitRegular,
    backgroundColor: colors.surface,
  },
  saveButton: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    backgroundColor: colors.confused,
    borderRadius: 6,
  },
  saveButtonDisabled: {
    opacity: 0.4,
  },
  saveButtonText: {
    color: "#fff",
    fontSize: 12,
    fontFamily: fontFamily.translitRegular,
    fontWeight: "600" as const,
  },
  cancelLink: {
    color: colors.textSecondary,
    fontSize: 11,
    fontFamily: fontFamily.translitRegular,
    paddingHorizontal: 4,
  },
  savedRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 6,
    paddingVertical: 7,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: "rgba(243, 156, 18, 0.12)",
    borderWidth: 1,
    borderColor: "rgba(243, 156, 18, 0.35)",
  },
  savedLabel: {
    color: colors.text,
    fontSize: 12,
    fontFamily: fontFamily.translitRegular,
    fontWeight: "600" as const,
  },
  savedArabic: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 18,
    writingDirection: "rtl",
  },
  savedGloss: {
    color: colors.textSecondary,
    fontSize: 12,
    fontFamily: fontFamily.translitRegular,
    flexShrink: 1,
  },
  savedText: {
    color: colors.text,
    fontSize: 13,
    fontStyle: "italic",
    flexShrink: 1,
  },
  clearLinkWrap: {
    marginLeft: "auto",
  },
  clearLink: {
    color: colors.textSecondary,
    fontSize: 11,
    fontFamily: fontFamily.translitRegular,
    textDecorationLine: "underline",
  },
});
