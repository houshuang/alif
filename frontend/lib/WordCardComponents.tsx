import { useState, useEffect, useRef, useCallback } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { Audio } from "expo-av";
import { colors, fonts, fontFamily } from "./theme";
import { WordForms, GrammarFeatureDetail } from "./types";
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

export function PlayButton({ audioUrl, word }: { audioUrl: string | null; word: string }) {
  const [playing, setPlaying] = useState(false);
  const soundRef = useRef<Audio.Sound | null>(null);

  const play = useCallback(async () => {
    if (playing) return;
    setPlaying(true);
    try {
      const url = audioUrl
        ? `${BASE_URL}${audioUrl}`
        : `${BASE_URL}/api/tts/speak/${encodeURIComponent(word)}`;

      if (soundRef.current) {
        await soundRef.current.unloadAsync();
      }
      const { sound } = await Audio.Sound.createAsync(
        { uri: url },
        { shouldPlay: true }
      );
      soundRef.current = sound;
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          setPlaying(false);
        }
      });
    } catch {
      setPlaying(false);
    }
  }, [audioUrl, word, playing]);

  useEffect(() => {
    return () => {
      soundRef.current?.unloadAsync();
    };
  }, []);

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
