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
  selected,
  missingTashkeelApplicable,
  onToggle,
}: {
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
      <Text style={styles.prompt}>Why did it click after reveal? Optional.</Text>
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
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 6,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "rgba(243, 156, 18, 0.28)",
    backgroundColor: "rgba(243, 156, 18, 0.07)",
  },
  prompt: {
    color: colors.textSecondary,
    fontFamily: fontFamily.translitRegular,
    fontSize: 11,
    marginBottom: 7,
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
