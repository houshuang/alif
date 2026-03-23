import React from "react";
import { View, Text, StyleSheet } from "react-native";
import type { GraduatedWord } from "./types";
import { colors, fontFamily } from "./theme";

const SOURCE_COLORS: Record<string, string> = {
  "Auto": colors.accent,
  "Learn": colors.gotIt,
  "OCR": colors.noIdea,
  "Reintro": colors.missed,
  "Book": "#9b59b6",
  "Story": "#9b59b6",
  "Duolingo": colors.gotIt,
  "Review": colors.textSecondary,
};

function formatStarted(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return "0d";
  if (diffDays === 1) return "1d";
  if (diffDays < 7) return `${diffDays}d`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w`;
  return `${Math.floor(diffDays / 30)}mo`;
}

export function GraduatedWordsTable({ words }: { words: GraduatedWord[] }) {
  if (words.length === 0) return null;

  return (
    <View style={s.container}>
      <Text style={s.title}>
        {words.length} {words.length === 1 ? "word" : "words"} graduated
      </Text>
      <View style={s.table}>
        {words.map((w) => {
          const badgeColor = SOURCE_COLORS[w.source || ""] || colors.textSecondary;
          return (
            <View key={w.lemma_id} style={s.row}>
              <Text style={[s.source, { color: badgeColor }]} numberOfLines={1}>{w.source || ""}</Text>
              <Text style={s.arabic}>{w.lemma_ar}</Text>
              <Text style={s.english} numberOfLines={1}>{w.gloss_en || ""}</Text>
              <Text style={s.started}>{formatStarted(w.started_at ?? null)}</Text>
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
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 7,
    paddingHorizontal: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    gap: 6,
  },
  source: {
    fontSize: 10,
    fontWeight: "600",
    width: 42,
  },
  arabic: {
    fontSize: 20,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
    minWidth: 50,
    textAlign: "right",
  },
  english: {
    fontSize: 13,
    color: colors.text,
    flex: 1,
  },
  started: {
    fontSize: 10,
    color: colors.textSecondary,
    width: 24,
    textAlign: "right",
  },
});
