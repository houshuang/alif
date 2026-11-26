import React, { useState, useRef, useEffect } from "react";
import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { WordFailureCause } from "../types";
import { colors, fontFamily } from "../theme";

const BASE_CAUSES: Array<{ value: WordFailureCause; label: string }> = [
  { value: "retrieval_lapse", label: "Forgot" },
  { value: "mixed_up", label: "Mixed" },
  { value: "unfamiliar_form", label: "Form" },
];

export function AssistedRecognitionCauses({
  compact = false,
  otherWordSurface,
  rating,
  selected,
  missingTashkeelApplicable,
  onToggle,
  onMixedUpPress,
  mixedUpExpanded = false,
  children,
}: {
  /** Set when the chips describe a marked word other than the one in the
   *  word panel above, so the row names the word it applies to. */
  compact?: boolean;
  otherWordSurface: string | null;
  rating: 1 | 2;
  selected: readonly WordFailureCause[];
  missingTashkeelApplicable: boolean;
  onToggle: (cause: WordFailureCause) => void;
  onMixedUpPress?: () => void;
  mixedUpExpanded?: boolean;
  /** Rendered below the chips — the review screen uses this for the
   *  confusion picker once "Mixed" is selected. */
  children?: React.ReactNode;
}) {
  const legendTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressed = useRef(false);
  useEffect(() => () => { if (legendTimer.current) clearTimeout(legendTimer.current); }, []);
  const [legend, setLegend] = useState<WordFailureCause | null>(null);
  const legends: Record<WordFailureCause, string> = { retrieval_lapse: "Forgot — I could not recall this word", mixed_up: "Mixed — I mistook this for another word", unfamiliar_form: "Form — familiar word, unfamiliar form", missing_tashkeel: "Vowels — missing vowel marks made it hard" };
  const causes = missingTashkeelApplicable
    ? [
        ...BASE_CAUSES,
        { value: "missing_tashkeel" as const, label: "Vowels" },
      ]
    : BASE_CAUSES;
  const accent = rating === 1 ? colors.missed : colors.confused;

  return (
    <View style={[styles.container, compact && styles.compactContainer]}>
      {compact && legend && <View pointerEvents="none" style={styles.legend}>
        <Text style={styles.legendText}>{legends[legend]}</Text>
      </View>}
      <View style={[styles.row, compact && styles.compactRow]}>
        {otherWordSurface ? (
          <Text style={[styles.arabic, { color: accent }]} numberOfLines={1}>
            {otherWordSurface}
          </Text>
        ) : null}
        {!compact && <Text style={[styles.label, { color: accent }]}>Why?</Text>}
        {causes.map((cause) => {
          const active = selected.includes(cause.value);
          return (
            <Pressable
              key={cause.value}
              accessibilityRole="checkbox"
              accessibilityLabel={`${cause.label}${otherWordSurface ? `: ${otherWordSurface}` : ""}`}
              accessibilityHint="Optional reason this word was hard. Hold to show its explanation."
              accessibilityState={cause.value === "mixed_up" && onMixedUpPress ? { checked: active, expanded: mixedUpExpanded } : { checked: active }}
              aria-checked={active}
              onHoverIn={() => setLegend(cause.value)}
              onHoverOut={() => setLegend(null)}
              onFocus={() => setLegend(cause.value)}
              onBlur={() => setLegend(null)}
              delayLongPress={350}
              onPressIn={() => { longPressed.current = false; }}
              onLongPress={() => {
                longPressed.current = true; setLegend(cause.value);
                if (legendTimer.current) clearTimeout(legendTimer.current);
                legendTimer.current = setTimeout(() => setLegend(null), 2500);
              }}
              onPress={() => { if (longPressed.current) { longPressed.current = false; return; } setLegend(null); cause.value === "mixed_up" && onMixedUpPress ? onMixedUpPress() : onToggle(cause.value); }}
              style={[
                styles.chip,
                compact && styles.iconButton,
                active && { borderColor: accent, backgroundColor: `${accent}29` },
              ]}
            >
              {compact ? <View style={styles.namedIcon}><Ionicons
                name={cause.value === "retrieval_lapse" ? "refresh-outline"
                  : cause.value === "mixed_up" ? "shuffle-outline"
                  : cause.value === "unfamiliar_form" ? "layers-outline" : "text-outline"}
                size={18} color={active ? accent : colors.textSecondary} /><Text style={[styles.iconName, active && { color: accent, fontWeight: "700" }]}>{cause.label}</Text></View> : <Text
                style={[styles.chipText, active && { color: accent, fontWeight: "700" }]}
                numberOfLines={1}
              >
                {cause.label}
              </Text>}
            </Pressable>
          );
        })}
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  // Compact: one full-width row under the word panel, icon and name side by side.
  namedIcon: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5 },
  iconName: { fontSize: 13, color: colors.textSecondary },
  legend: { position: "absolute", left: 0, right: 0, bottom: 48, padding: 9, borderRadius: 6,
    backgroundColor: colors.surfaceLight, borderWidth: 1, borderColor: colors.border, zIndex: 100 },
  legendText: { color: colors.text, fontSize: 12, lineHeight: 17, textAlign: "center" },
  compactContainer: { marginTop: 6, marginBottom: 0, paddingHorizontal: 0 },
  compactRow: { gap: 6 },
  iconButton: { flex: 1, height: 44, paddingVertical: 0, paddingHorizontal: 2, justifyContent: "center", borderRadius: 12 },
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
