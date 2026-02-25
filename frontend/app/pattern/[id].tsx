import { useState, useEffect, useLayoutEffect } from "react";
import {
  View,
  Text,
  ScrollView,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useLocalSearchParams, useRouter, useNavigation } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, fontFamily } from "../../lib/theme";
import { getPatternDetail } from "../../lib/api";
import { PatternDetail, PatternEnrichment } from "../../lib/types";
import { getCefrColor } from "../../lib/frequency";

function stateColor(state: string | null): string {
  return (
    ({
      learning: colors.stateLearning,
      known: colors.stateKnown,
      lapsed: colors.missed,
      acquiring: colors.stateAcquiring,
      encountered: colors.stateEncountered,
    } as Record<string, string>)[state || ""] ?? colors.textSecondary
  );
}

const STATE_ORDER: Record<string, number> = {
  known: 0,
  learning: 1,
  acquiring: 2,
  lapsed: 3,
  encountered: 4,
  new: 5,
};

export default function PatternDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [pattern, setPattern] = useState<PatternDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const navigation = useNavigation();

  useLayoutEffect(() => {
    navigation.setOptions({
      headerLeft: () => (
        <Pressable onPress={() => router.canGoBack() ? router.back() : router.replace("/explore")} style={{ paddingLeft: 12 }}>
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </Pressable>
      ),
    });
  }, [navigation, router]);

  useEffect(() => {
    if (id) loadPattern(decodeURIComponent(id));
  }, [id]);

  async function loadPattern(wazn: string) {
    setLoading(true);
    try {
      const data = await getPatternDetail(wazn);
      setPattern(data);
    } catch (e) {
      console.error("Failed to load pattern:", e);
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (!pattern) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>Pattern not found</Text>
      </View>
    );
  }

  const enrichment = pattern.enrichment as PatternEnrichment | null;

  const sortedWords = [...pattern.words].sort((a, b) => {
    const sa = STATE_ORDER[a.knowledge_state || "new"] ?? 5;
    const sb = STATE_ORDER[b.knowledge_state || "new"] ?? 5;
    return sa - sb;
  });

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
      <Text style={styles.patternTitle}>{pattern.wazn}</Text>
      <Text style={styles.patternMeaning}>
        {pattern.wazn_meaning || "—"}
      </Text>
      <Text style={styles.wordCount}>
        {pattern.words.length} word{pattern.words.length !== 1 ? "s" : ""}
      </Text>

      {enrichment && (
        <View style={styles.enrichmentSection}>
          {enrichment.explanation && (
            <Text style={styles.explanation}>{enrichment.explanation}</Text>
          )}

          {enrichment.how_to_recognize && (
            <View style={styles.enrichmentBlock}>
              <Text style={styles.enrichmentLabel}>How to Recognize</Text>
              <Text style={styles.enrichmentText}>
                {enrichment.how_to_recognize}
              </Text>
            </View>
          )}

          {enrichment.semantic_fields && enrichment.semantic_fields.length > 0 && (
            <View style={styles.pillRow}>
              {enrichment.semantic_fields.map((sf, i) => (
                <View key={i} style={styles.semanticPill}>
                  <Text style={styles.semanticPillText}>{sf}</Text>
                </View>
              ))}
            </View>
          )}

          {enrichment.example_derivations && enrichment.example_derivations.length > 0 && (
            <View style={styles.enrichmentBlock}>
              <Text style={styles.enrichmentLabel}>Example Derivations</Text>
              {enrichment.example_derivations.map((ex, i) => (
                <View key={i} style={styles.derivationRow}>
                  <Text style={styles.derivationRoot}>{ex.root}</Text>
                  <Ionicons
                    name="arrow-forward"
                    size={12}
                    color={colors.textSecondary}
                  />
                  <Text style={styles.derivationWord}>{ex.word}</Text>
                  <Text style={styles.derivationGloss}>
                    {ex.gloss} — {ex.explanation}
                  </Text>
                </View>
              ))}
            </View>
          )}

          {enrichment.register_notes && (
            <View style={styles.enrichmentBlock}>
              <Text style={styles.enrichmentLabel}>Register</Text>
              <Text style={styles.enrichmentText}>
                {enrichment.register_notes}
              </Text>
            </View>
          )}

          {enrichment.fun_facts && enrichment.fun_facts.length > 0 && (
            <View style={styles.enrichmentBlock}>
              <Text style={styles.enrichmentLabel}>Did you know?</Text>
              {enrichment.fun_facts.map((fact, i) => (
                <Text key={i} style={styles.funFact}>
                  {fact}
                </Text>
              ))}
            </View>
          )}

          {enrichment.related_patterns && enrichment.related_patterns.length > 0 && (
            <View style={styles.pillRow}>
              {enrichment.related_patterns.map((rp, i) => (
                <Pressable
                  key={i}
                  style={styles.pill}
                  onPress={() => router.replace(`/pattern/${encodeURIComponent(rp)}`)}
                >
                  <Text style={styles.pillText}>{rp}</Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      )}

      <View style={styles.wordList}>
        <Text style={styles.sectionTitle}>Words</Text>
        {sortedWords.map((word) => (
          <Pressable
            key={word.lemma_id}
            style={styles.wordRow}
            onPress={() => router.push(`/word/${word.lemma_id}`)}
          >
            <View style={styles.wordLeft}>
              <View
                style={[
                  styles.stateDot,
                  { backgroundColor: stateColor(word.knowledge_state) },
                ]}
              />
              <View style={{ flex: 1 }}>
                <Text style={styles.wordEnglish} numberOfLines={1}>
                  {word.gloss_en || "—"}
                </Text>
                {word.transliteration && (
                  <Text style={styles.wordTranslit} numberOfLines={1}>
                    {word.transliteration}
                  </Text>
                )}
              </View>
              {word.root && (
                <Text style={styles.wordRoot}>{word.root}</Text>
              )}
              {word.pos && (
                <Text style={styles.wordPos}>{word.pos}</Text>
              )}
              {word.cefr_level && (
                <View style={[styles.cefrDot, { backgroundColor: getCefrColor(word.cefr_level) }]} />
              )}
            </View>
            <Text style={styles.wordArabic}>{word.lemma_ar}</Text>
          </Pressable>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  scroll: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  content: {
    padding: 16,
    alignItems: "center",
  },
  patternTitle: {
    fontSize: 32,
    color: colors.accent,
    fontWeight: "700",
    marginTop: 8,
  },
  patternMeaning: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "600",
    marginTop: 4,
  },
  wordCount: {
    fontSize: 14,
    color: colors.textSecondary,
    marginTop: 4,
  },

  enrichmentSection: {
    width: "100%",
    maxWidth: 500,
    marginTop: 16,
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 14,
  },
  explanation: {
    fontSize: 15,
    color: colors.text,
    lineHeight: 22,
    marginBottom: 4,
  },
  enrichmentBlock: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  enrichmentLabel: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: "600",
    marginBottom: 4,
  },
  enrichmentText: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
  },
  funFact: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
    fontStyle: "italic",
    marginTop: 4,
  },
  pillRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 10,
  },
  semanticPill: {
    backgroundColor: colors.surfaceLight,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  semanticPillText: {
    fontSize: 12,
    color: colors.text,
    fontWeight: "500",
  },
  pill: {
    backgroundColor: colors.accent + "20",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  pillText: {
    fontSize: 13,
    color: colors.accent,
    fontWeight: "500",
  },

  derivationRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 6,
    flexWrap: "wrap",
  },
  derivationRoot: {
    fontSize: 13,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  derivationWord: {
    fontSize: 16,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
  },
  derivationGloss: {
    fontSize: 13,
    color: colors.text,
    flex: 1,
  },

  wordList: {
    width: "100%",
    maxWidth: 500,
    marginTop: 16,
  },
  sectionTitle: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 8,
  },
  wordRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 10,
    marginBottom: 4,
  },
  wordLeft: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  stateDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  wordEnglish: {
    fontSize: 14,
    color: colors.text,
  },
  wordTranslit: {
    fontSize: 11,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginTop: 1,
  },
  wordRoot: {
    fontSize: 12,
    color: colors.accent,
    fontFamily: fontFamily.arabic,
    opacity: 0.7,
  },
  wordPos: {
    fontSize: 11,
    color: colors.textSecondary,
    opacity: 0.7,
  },
  cefrDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    marginLeft: 4,
  },
  wordArabic: {
    fontSize: 22,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
    lineHeight: 30,
    marginLeft: 8,
  },

  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
  },
});
