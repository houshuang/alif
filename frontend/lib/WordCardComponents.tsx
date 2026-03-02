import { useState, useEffect, useRef, useCallback } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { colors, fonts, fontFamily } from "./theme";
import { WordForms, GrammarFeatureDetail, PatternExample } from "./types";
import { BASE_URL } from "./api";

export function posLabel(pos: string | null, forms: WordForms | null): string {
  const p = (pos || "").toLowerCase();
  const parts: string[] = [];

  if (p === "noun") {
    parts.push("noun");
    if (forms?.gender === "m") parts[0] += " (m)";
    else if (forms?.gender === "f") parts[0] += " (f)";
  } else if (p === "verb") {
    if (forms?.verb_form && forms.verb_form !== "I") {
      parts.push(`verb, Form ${forms.verb_form}`);
    } else {
      parts.push("verb");
    }
  } else if (p === "adj" || p === "adjective") {
    parts.push("adj.");
  } else if (p) {
    parts.push(p);
  }

  return parts.join(" ");
}

export function FormsRow({ pos, forms }: { pos: string | null; forms: WordForms | null }) {
  if (!forms) return null;
  const p = (pos || "").toLowerCase();
  const items: { label: string; value: string }[] = [];

  if (p === "verb") {
    if (forms.present) items.push({ label: "present", value: forms.present });
    if (forms.masdar) items.push({ label: "masdar", value: forms.masdar });
    if (forms.active_participle) items.push({ label: "act. part.", value: forms.active_participle });
  } else if (p === "adj" || p === "adjective") {
    if (forms.feminine) items.push({ label: "fem.", value: forms.feminine });
    if (forms.plural) items.push({ label: "pl.", value: forms.plural });
    if (forms.elative) items.push({ label: "comp.", value: forms.elative });
  } else {
    if (forms.plural) items.push({ label: "pl.", value: forms.plural });
    if (forms.feminine) items.push({ label: "fem.", value: forms.feminine });
  }

  if (items.length === 0) return null;

  if (p === "verb" && items.length > 1) {
    return (
      <View style={wcStyles.formsTable}>
        {items.map((item, i) => (
          <View key={i} style={wcStyles.formsTableCell}>
            <Text style={wcStyles.formLabel}>{item.label}</Text>
            <Text style={wcStyles.formValueLarge}>{item.value}</Text>
          </View>
        ))}
      </View>
    );
  }

  return (
    <View style={wcStyles.formsRow}>
      {items.map((item, i) => (
        <Text key={i} style={wcStyles.formItem}>
          <Text style={wcStyles.formLabel}>{item.label} </Text>
          <Text style={wcStyles.formValue}>{item.value}</Text>
        </Text>
      ))}
    </View>
  );
}

export function GrammarRow({
  details,
}: {
  details: { feature_key: string; label_en: string; label_ar: string | null }[] | undefined;
}) {
  const items = details ?? [];
  if (items.length === 0) return null;
  return (
    <View style={wcStyles.grammarSection}>
      <Text style={wcStyles.grammarTitle}>Grammar</Text>
      <View style={wcStyles.grammarChips}>
        {items.map((g) => (
          <View key={g.feature_key} style={wcStyles.grammarChip}>
            <Text style={wcStyles.grammarChipEn}>{g.label_en}</Text>
            {g.label_ar ? (
              <Text style={wcStyles.grammarChipAr}>{g.label_ar}</Text>
            ) : null}
          </View>
        ))}
      </View>
    </View>
  );
}

const FORM_PRIORITY: Record<string, string[]> = {
  verb: ["present", "masdar", "active_participle"],
  noun: ["plural", "feminine"],
  adj: ["feminine", "plural", "elative"],
  adjective: ["feminine", "plural", "elative"],
};

const FORM_LABELS: Record<string, string> = {
  present: "present",
  masdar: "v. noun",
  active_participle: "doer",
  plural: "plural",
  feminine: "feminine",
  elative: "comparative",
};

export function FormsStrip({
  pos,
  forms,
  formsTranslit,
  compact = false,
}: {
  pos: string | null;
  forms: WordForms | null;
  formsTranslit?: Record<string, string> | null;
  compact?: boolean;
}) {
  if (!forms) return null;
  const p = (pos || "").toLowerCase();
  const priority = FORM_PRIORITY[p] || ["plural", "feminine"];

  const items: { key: string; label: string; ar: string; tr: string | null }[] = [];
  for (const key of priority) {
    const val = (forms as Record<string, string | undefined>)[key];
    if (val && typeof val === "string") {
      items.push({
        key,
        label: FORM_LABELS[key] || key,
        ar: val,
        tr: formsTranslit?.[key] || null,
      });
    }
    if (items.length >= 3) break;
  }

  if (items.length === 0) return null;

  const arSize = compact ? 16 : 22;
  const trSize = compact ? 11 : 13;
  const labelSize = compact ? 10 : 12;

  return (
    <View style={wcStyles.formsStripRow}>
      {items.map((item) => (
        <View key={item.key} style={wcStyles.formsStripCell}>
          <Text style={[wcStyles.formsStripAr, { fontSize: arSize }]}>{item.ar}</Text>
          {item.tr ? (
            <Text style={[wcStyles.formsStripTr, { fontSize: trSize }]}>{item.tr}</Text>
          ) : null}
          <Text style={[wcStyles.formsStripLabel, { fontSize: labelSize }]}>{item.label}</Text>
        </View>
      ))}
    </View>
  );
}

export function PatternExamples({
  examples,
  header,
  compact = false,
  onPress,
}: {
  examples?: PatternExample[];
  header?: string | null;
  compact?: boolean;
  onPress?: (lemmaId: number) => void;
}) {
  if (!examples || examples.length === 0) return null;

  const stateColor = (s: string | null) => {
    if (s === "known") return "#4CAF50";
    if (s === "learning" || s === "acquiring") return "#FF9800";
    return "#9E9E9E";
  };

  return (
    <View style={wcStyles.patternExSection}>
      {header ? <Text style={wcStyles.patternExHeader}>{header}</Text> : null}
      {examples.map((ex) => {
        const row = (
          <View key={ex.lemma_id} style={wcStyles.patternExRow}>
            <View style={[wcStyles.patternExDot, { backgroundColor: stateColor(ex.knowledge_state) }]} />
            <Text style={[wcStyles.patternExAr, compact && { fontSize: 15 }]}>{ex.lemma_ar}</Text>
            {ex.transliteration ? (
              <Text style={wcStyles.patternExTr}>{ex.transliteration}</Text>
            ) : null}
            {ex.gloss_en ? (
              <Text style={wcStyles.patternExGloss}>{ex.gloss_en}</Text>
            ) : null}
            {ex.root ? (
              <Text style={wcStyles.patternExRoot}>{ex.root}</Text>
            ) : null}
          </View>
        );

        if (onPress) {
          return (
            <Pressable key={ex.lemma_id} onPress={() => onPress(ex.lemma_id)}>
              {row}
            </Pressable>
          );
        }
        return row;
      })}
    </View>
  );
}

export function PlayButton({ audioUrl, word }: { audioUrl: string | null; word: string }) {
  const url = audioUrl
    ? `${BASE_URL}${audioUrl}`
    : `${BASE_URL}/api/tts/speak/${encodeURIComponent(word)}`;

  const player = useAudioPlayer(url);
  const status = useAudioPlayerStatus(player);
  const playing = status.playing;

  const play = useCallback(() => {
    if (playing) return;
    player.seekTo(0);
    player.play();
  }, [player, playing]);

  return (
    <Pressable style={wcStyles.playButton} onPress={play} disabled={playing}>
      <Text style={[wcStyles.playIcon, playing && { opacity: 0.5 }]}>
        {playing ? "\u23F8" : "\u25B6"}
      </Text>
    </Pressable>
  );
}

export const wcStyles = StyleSheet.create({
  formsRow: {
    flexDirection: "row",
    gap: 16,
    marginBottom: 12,
    flexWrap: "wrap",
    justifyContent: "center",
  },
  formsTable: {
    flexDirection: "row",
    gap: 2,
    marginBottom: 12,
    width: "100%",
    justifyContent: "center",
  },
  formsTableCell: {
    alignItems: "center",
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: colors.surfaceLight,
    borderRadius: 8,
  },
  formItem: {
    fontSize: 18,
  },
  formLabel: {
    color: colors.textSecondary,
    fontSize: 12,
    marginBottom: 2,
  },
  formValue: {
    color: colors.arabic,
    fontSize: 18,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
  },
  formValueLarge: {
    color: colors.arabic,
    fontSize: 20,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    fontWeight: "600",
  },
  grammarSection: {
    marginTop: 2,
    marginBottom: 12,
    alignItems: "center",
    width: "100%",
  },
  grammarTitle: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 6,
  },
  grammarChips: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 6,
  },
  grammarChip: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    paddingVertical: 4,
    paddingHorizontal: 10,
    alignItems: "center",
  },
  grammarChipEn: {
    fontSize: fonts.caption,
    color: colors.text,
    fontWeight: "600",
  },
  grammarChipAr: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    marginTop: 1,
  },
  formsStripRow: {
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    flexWrap: "wrap",
    marginBottom: 12,
  },
  formsStripCell: {
    alignItems: "center",
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: colors.surfaceLight,
    borderRadius: 8,
  },
  formsStripAr: {
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    fontWeight: "600",
  },
  formsStripTr: {
    color: colors.textSecondary,
    fontStyle: "italic",
    marginTop: 1,
  },
  formsStripLabel: {
    color: colors.textSecondary,
    marginTop: 2,
  },
  patternExSection: {
    marginBottom: 12,
    width: "100%",
  },
  patternExHeader: {
    fontSize: 14,
    fontWeight: "600",
    color: colors.text,
    marginBottom: 6,
    textAlign: "center",
  },
  patternExRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: 3,
  },
  patternExDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  patternExAr: {
    fontSize: 17,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
  },
  patternExTr: {
    fontSize: 11,
    color: colors.textSecondary,
    fontStyle: "italic",
  },
  patternExGloss: {
    fontSize: 11,
    color: colors.text,
  },
  patternExRoot: {
    fontSize: 11,
    color: colors.textSecondary,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
  },
  playButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.surfaceLight,
    alignItems: "center",
    justifyContent: "center",
  },
  playIcon: {
    fontSize: 18,
    color: colors.accent,
  },
});
