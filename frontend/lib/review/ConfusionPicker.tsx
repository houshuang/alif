import React, { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { colors, fontFamily } from "../theme";
import { ConfusionAnalysis, ConfusionCaptureIn } from "../types";

interface ConfusionPickerProps {
  failedLemmaId: number;
  confusionData: ConfusionAnalysis;
  existing?: ConfusionCaptureIn;
  onSave: (capture: ConfusionCaptureIn) => void;
  onClear: () => void;
}

interface Candidate {
  lemma_id: number;
  lemma_ar: string;
  gloss_en: string | null;
}

export function ConfusionPicker({
  failedLemmaId,
  confusionData,
  existing,
  onSave,
  onClear,
}: ConfusionPickerProps) {
  const [expanded, setExpanded] = useState<boolean>(!!existing);
  const [selectedLemmaId, setSelectedLemmaId] = useState<number | null>(
    existing?.capture_method === "suggested_pick" ? existing.confused_with_lemma_id ?? null : null,
  );
  const [freeText, setFreeText] = useState<string>(
    existing?.capture_method === "free_text" ? existing.confused_with_text ?? "" : "",
  );

  const candidates: Candidate[] = useMemo(() => {
    const seen = new Set<number>();
    const out: Candidate[] = [];
    for (const w of confusionData.similar_words ?? []) {
      if (seen.has(w.lemma_id)) continue;
      seen.add(w.lemma_id);
      out.push({ lemma_id: w.lemma_id, lemma_ar: w.lemma_ar, gloss_en: w.gloss_en ?? null });
      if (out.length >= 5) break;
    }
    for (const w of confusionData.phonetic_similar ?? []) {
      if (out.length >= 5) break;
      if (seen.has(w.lemma_id)) continue;
      seen.add(w.lemma_id);
      out.push({ lemma_id: w.lemma_id, lemma_ar: w.lemma_ar, gloss_en: w.gloss_en ?? null });
    }
    return out;
  }, [confusionData]);

  const candidateIds = candidates.map((c) => c.lemma_id);

  const handlePickChip = (lemmaId: number) => {
    setFreeText("");
    setSelectedLemmaId(lemmaId);
    onSave({
      failed_lemma_id: failedLemmaId,
      capture_method: "suggested_pick",
      confused_with_lemma_id: lemmaId,
      candidates_shown: candidateIds,
    });
  };

  const handleSubmitText = () => {
    const text = freeText.trim();
    if (!text) return;
    setSelectedLemmaId(null);
    onSave({
      failed_lemma_id: failedLemmaId,
      capture_method: "free_text",
      confused_with_text: text,
      candidates_shown: candidateIds,
    });
  };

  const handleClear = () => {
    setSelectedLemmaId(null);
    setFreeText("");
    onClear();
  };

  if (!expanded) {
    return (
      <Pressable
        onPress={() => setExpanded(true)}
        style={({ pressed }) => [styles.collapsedLink, pressed && { opacity: 0.5 }]}
        accessibilityRole="button"
      >
        <Text style={styles.collapsedText}>
          {existing ? "✓ confused with something — edit" : "Confused with another word?"}
        </Text>
      </Pressable>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>What were you thinking of?</Text>
        <Pressable onPress={() => setExpanded(false)} hitSlop={8}>
          <Text style={styles.cancelLink}>Close</Text>
        </Pressable>
      </View>

      {candidates.length > 0 && (
        <View style={styles.chipsRow}>
          {candidates.map((c) => {
            const selected = selectedLemmaId === c.lemma_id;
            return (
              <Pressable
                key={c.lemma_id}
                onPress={() => handlePickChip(c.lemma_id)}
                style={({ pressed }) => [
                  styles.chip,
                  selected && styles.chipSelected,
                  pressed && { opacity: 0.7 },
                  freeText.length > 0 && !selected && styles.chipDimmed,
                ]}
              >
                <Text style={[styles.chipArabic, selected && styles.chipArabicSelected]}>
                  {c.lemma_ar}
                </Text>
                {c.gloss_en && (
                  <Text style={[styles.chipGloss, selected && styles.chipGlossSelected]} numberOfLines={1}>
                    {c.gloss_en}
                  </Text>
                )}
              </Pressable>
            );
          })}
        </View>
      )}

      <View style={styles.textRow}>
        <TextInput
          value={freeText}
          onChangeText={(t) => {
            setFreeText(t);
            if (t.length > 0 && selectedLemmaId !== null) setSelectedLemmaId(null);
          }}
          onSubmitEditing={handleSubmitText}
          placeholder="…or type in English"
          placeholderTextColor={colors.textSecondary}
          style={[
            styles.textInput,
            selectedLemmaId !== null && styles.textInputDimmed,
          ]}
          editable={selectedLemmaId === null}
        />
        {freeText.trim().length > 0 && (
          <Pressable onPress={handleSubmitText} style={styles.saveButton}>
            <Text style={styles.saveButtonText}>Save</Text>
          </Pressable>
        )}
      </View>

      {existing && (
        <Pressable onPress={handleClear} style={styles.clearRow}>
          <Text style={styles.clearText}>Clear selection</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  collapsedLink: {
    paddingVertical: 6,
    paddingHorizontal: 4,
    marginTop: 4,
  },
  collapsedText: {
    color: colors.textSecondary,
    fontSize: 13,
    fontFamily: fontFamily.translitRegular,
    textDecorationLine: "underline",
  },
  container: {
    marginTop: 8,
    padding: 10,
    borderRadius: 8,
    backgroundColor: "rgba(243, 156, 18, 0.08)",
    borderWidth: 1,
    borderColor: "rgba(243, 156, 18, 0.25)",
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  title: {
    color: colors.text,
    fontSize: 13,
    fontFamily: fontFamily.translitRegular,
    fontWeight: "600" as const,
  },
  cancelLink: {
    color: colors.textSecondary,
    fontSize: 12,
    fontFamily: fontFamily.translitRegular,
  },
  chipsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 8,
  },
  chip: {
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 14,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  chipSelected: {
    backgroundColor: colors.confused,
    borderColor: colors.confused,
  },
  chipDimmed: {
    opacity: 0.4,
  },
  chipArabic: {
    color: colors.text,
    fontSize: 16,
    fontFamily: fontFamily.arabic,
  },
  chipArabicSelected: {
    color: "#fff",
  },
  chipGloss: {
    color: colors.textSecondary,
    fontSize: 11,
    fontFamily: fontFamily.translitRegular,
    maxWidth: 110,
  },
  chipGlossSelected: {
    color: "#fff",
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
  textInputDimmed: {
    opacity: 0.4,
  },
  saveButton: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    backgroundColor: colors.confused,
    borderRadius: 6,
  },
  saveButtonText: {
    color: "#fff",
    fontSize: 12,
    fontFamily: fontFamily.translitRegular,
    fontWeight: "600" as const,
  },
  clearRow: {
    marginTop: 6,
    alignSelf: "flex-end",
  },
  clearText: {
    color: colors.textSecondary,
    fontSize: 11,
    fontFamily: fontFamily.translitRegular,
    textDecorationLine: "underline",
  },
});
