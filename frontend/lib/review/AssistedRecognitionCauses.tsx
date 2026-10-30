import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { WordFailureCause } from "../types";
import { colors, fontFamily } from "../theme";

const BASE_CAUSES: Array<{ value: WordFailureCause; label: string }> = [
  { value: "retrieval_lapse", label: "Forgot" },
  { value: "mixed_up", label: "Mixed up" },
  { value: "unfamiliar_form", label: "New form" },
];

export function AssistedRecognitionCauses({
  otherWordSurface,
  rating,
  selected,
  missingTashkeelApplicable,
  onToggle,
  children,
}: {
  /** Set when the chips describe a marked word other than the one in the
   *  word panel above, so the row names the word it applies to. */
  otherWordSurface: string | null;
  rating: 1 | 2;
  selected: readonly WordFailureCause[];
  missingTashkeelApplicable: boolean;
  onToggle: (cause: WordFailureCause) => void;
  /** Rendered below the chips — the review screen uses this for the
   *  confusion picker once "Mixed up" is selected. */
  children?: React.ReactNode;
}) {
  const causes = missingTashkeelApplicable
    ? [
        ...BASE_CAUSES,
        { value: "missing_tashkeel" as const, label: "No tashkeel" },
      ]
    : BASE_CAUSES;
  const accent = rating === 1 ? colors.missed : colors.confused;

  return (
    <View style={styles.container}>
      <View style={styles.row}>
        {otherWordSurface ? (
          <Text style={[styles.arabic, { color: accent }]} numberOfLines={1}>
            {otherWordSurface}
          </Text>
        ) : null}
        <Text style={[styles.label, { color: accent }]}>Why?</Text>
        {causes.map((cause) => {
          const active = selected.includes(cause.value);
          return (
            <Pressable
              key={cause.value}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              onPress={() => onToggle(cause.value)}
              style={[
                styles.chip,
                active && { borderColor: accent, backgroundColor: `${accent}29` },
              ]}
            >
              <Text
                style={[styles.chipText, active && { color: accent, fontWeight: "700" }]}
                numberOfLines={1}
              >
                {cause.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
    marginTop: -2,
    marginBottom: 2,
    paddingHorizontal: 2,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
  },
  arabic: {
    fontFamily: fontFamily.arabic,
    fontSize: 18,
    maxWidth: 90,
  },
  label: {
    fontFamily: fontFamily.translitRegular,
    fontSize: 12,
    fontWeight: "600",
    marginRight: 2,
  },
  chip: {
    flex: 1,
    alignItems: "center",
    borderRadius: 999,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceLight,
    paddingVertical: 6,
    paddingHorizontal: 4,
  },
  chipText: {
    color: "#c8c8d8",
    fontFamily: fontFamily.translitRegular,
    fontSize: 12,
    fontWeight: "500",
  },
});
