import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { WordFailureCause } from "../types";
import { colors, fontFamily } from "../theme";

const BASE_CAUSES: Array<{ value: WordFailureCause; label: string }> = [
  { value: "retrieval_lapse", label: "Just forgot" },
  { value: "mixed_up", label: "Mixed it up" },
  { value: "unfamiliar_form", label: "Unfamiliar form" },
];

export function AssistedRecognitionCauses({
  surfaceForm,
  assistedWordCount,
  selected,
  missingTashkeelApplicable,
  onToggle,
}: {
  surfaceForm: string;
  assistedWordCount: number;
  selected: readonly WordFailureCause[];
  missingTashkeelApplicable: boolean;
  onToggle: (cause: WordFailureCause) => void;
}) {
  const causes = missingTashkeelApplicable
    ? [
        ...BASE_CAUSES,
        { value: "missing_tashkeel" as const, label: "No tashkeel" },
      ]
    : BASE_CAUSES;

  return (
    <View style={styles.container}>
      <View style={styles.heading}>
        <Text style={styles.arabic}>{surfaceForm}</Text>
        <Text style={styles.prompt}>
          You recognized this after reveal. Why? (optional)
        </Text>
      </View>
      <View style={styles.chips}>
        {causes.map((cause) => {
          const active = selected.includes(cause.value);
          return (
            <Pressable
              key={cause.value}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              onPress={() => onToggle(cause.value)}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>
                {cause.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
      {selected.includes("missing_tashkeel") && (
        <Text style={styles.detail}>I knew it once the vowels appeared.</Text>
      )}
      {assistedWordCount > 1 && (
        <Text style={styles.detail}>
          Use the word arrows to switch between yellow words.
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 8,
    marginHorizontal: 2,
    paddingVertical: 11,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "rgba(243, 156, 18, 0.55)",
    backgroundColor: "rgba(243, 156, 18, 0.12)",
  },
  heading: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 8,
    marginBottom: 9,
  },
  arabic: {
    color: colors.confused,
    fontFamily: fontFamily.arabic,
    fontSize: 19,
    fontWeight: "700",
  },
  prompt: {
    color: colors.text,
    fontFamily: fontFamily.translitRegular,
    fontSize: 12,
    fontWeight: "600",
    flexShrink: 1,
  },
  chips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  chip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    paddingVertical: 5,
    paddingHorizontal: 9,
  },
  chipActive: {
    borderColor: colors.confused,
    backgroundColor: "rgba(243, 156, 18, 0.16)",
  },
  chipText: {
    color: colors.textSecondary,
    fontFamily: fontFamily.translitRegular,
    fontSize: 11,
    fontWeight: "500",
  },
  chipTextActive: {
    color: colors.confused,
    fontWeight: "700",
  },
  detail: {
    color: colors.textSecondary,
    fontFamily: fontFamily.translitRegular,
    fontSize: 10,
    marginTop: 6,
  },
});
