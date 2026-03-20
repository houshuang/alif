import React from "react";
import { View, Text, StyleSheet } from "react-native";
import type { GraduatedWord } from "./types";
import { colors, fontFamily } from "./theme";

const SOURCE_COLORS: Record<string, string> = {
  "Auto": colors.accent,
  "Learn mode": colors.gotIt,
  "Textbook OCR": colors.noIdea,
  "Reintroduced": colors.missed,
  "Book": "#9b59b6",
  "Story": "#9b59b6",
  "Duolingo": colors.gotIt,
  "Collateral": colors.textSecondary,
};

function formatStarted(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "yesterday";
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
  return `${Math.floor(diffDays / 30)}mo ago`;
}

export function GraduatedWordsTable({ words }: { words: GraduatedWord[] }) {
  if (words.length === 0) return null;

  return (
    <View style={s.container}>
      <Text style={s.title}>
        {words.length} {words.length === 1 ? "word" : "words"} graduated
      </Text>
      <View style={s.table}>
        <View style={s.headerRow}>
          <Text style={[s.headerCell, { width: 90 }]}>Source</Text>
          <Text style={[s.headerCell, { minWidth: 55, textAlign: "right" }]}>Arabic</Text>
          <Text style={[s.headerCell, { flex: 1 }]}>Translit.</Text>
          <Text style={[s.headerCell, { flex: 1, textAlign: "right" }]}>English</Text>
          <Text style={[s.headerCell, { width: 52, textAlign: "right" }]}>Started</Text>
        </View>
        {words.map((w) => {
          const badgeColor = SOURCE_COLORS[w.source || ""] || colors.textSecondary;
          return (
            <View key={w.lemma_id} style={s.row}>
              <View style={s.sourceCell}>
                <View style={[s.sourceDot, { backgroundColor: badgeColor }]} />
                <Text style={[s.sourceText, { color: badgeColor }]} numberOfLines={1}>{w.source || ""}</Text>
              </View>
              <Text style={s.arabic}>{w.lemma_ar}</Text>
              {w.transliteration ? (
                <Text style={s.translit} numberOfLines={1}>{w.transliteration}</Text>
              ) : (
                <View style={s.translitPlaceholder} />
              )}
              <Text style={s.english} numberOfLines={1}>{w.gloss_en || ""}</Text>
              <Text style={s.started}>{formatStarted(w.started_at)}</Text>
            </View>
          );
        })}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  container: {
    width: "100%",
    maxWidth: 420,
  },
  title: {
    fontSize: 13,
    color: colors.gotIt,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 8,
    textAlign: "center",
  },
  table: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    overflow: "hidden",
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 6,
    paddingHorizontal: 12,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  headerCell: {
    fontSize: 10,
    color: colors.textSecondary,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    gap: 8,
  },
  sourceCell: {
    flexDirection: "row",
    alignItems: "center",
    width: 90,
    gap: 5,
  },
  sourceDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  sourceText: {
    fontSize: 11,
    fontWeight: "600",
  },
  arabic: {
    fontSize: 20,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
    minWidth: 55,
    textAlign: "right",
  },
  translit: {
    fontSize: 12,
    fontFamily: fontFamily.translit,
    color: colors.textSecondary,
    flex: 1,
    minWidth: 40,
  },
  translitPlaceholder: {
    flex: 1,
    minWidth: 40,
  },
  english: {
    fontSize: 13,
    color: colors.text,
    flex: 1,
    textAlign: "right",
  },
  started: {
    fontSize: 10,
    color: colors.textSecondary,
    width: 52,
    textAlign: "right",
  },
});
