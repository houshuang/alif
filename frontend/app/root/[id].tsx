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
import { colors, fonts, fontFamily, ltr } from "../../lib/theme";
import { getRootDetail, searchRootByText } from "../../lib/api";
import { RootDetail, RootEnrichment } from "../../lib/types";
import { getCefrColor } from "../../lib/frequency";

function cleanCoreMeaning(s: string | null): string {
  if (!s) return "Unknown meaning";
  return s.replace(/^related to\s+/i, "").replace(/^the concept of\s+/i, "");
}

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

export default function RootDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [root, setRoot] = useState<RootDetail | null>(null);
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
    if (id) loadRoot(Number(id));
  }, [id]);

  async function loadRoot(rootId: number) {
    setLoading(true);
    try {
      const data = await getRootDetail(rootId);
      setRoot(data);
    } catch (e) {
      console.error("Failed to load root:", e);
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

  if (!root) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>Root not found</Text>
      </View>
    );
  }

  const enrichment = root.enrichment as RootEnrichment | null;

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
      <Text style={styles.rootLetters}>{root.root}</Text>
      <Text style={styles.coreMeaning}>
        {cleanCoreMeaning(root.core_meaning_en)}
      </Text>
      <Text style={styles.wordCount}>
        {root.total_words} word{root.total_words !== 1 ? "s" : ""}
      </Text>

      {enrichment && (
        <View style={styles.enrichmentSection}>
          {enrichment.etymology_story && (
            <Text style={styles.etymologyStory}>
              {ltr(enrichment.etymology_story)}
            </Text>
          )}

          {enrichment.cultural_significance && (
            <View style={styles.enrichmentBlock}>
              <Text style={styles.enrichmentLabel}>Cultural Significance</Text>
              <Text style={styles.enrichmentText}>
                {ltr(enrichment.cultural_significance)}
              </Text>
            </View>
          )}

          {enrichment.literary_examples && enrichment.literary_examples.length > 0 && (
            <View style={styles.enrichmentBlock}>
              <Text style={styles.enrichmentLabel}>Literary Examples</Text>
              {enrichment.literary_examples.map((ex, i) => (
                <Text key={i} style={styles.literaryExample}>
                  {ltr(ex)}
                </Text>
              ))}
            </View>
          )}

          {enrichment.fun_facts && enrichment.fun_facts.length > 0 && (
            <View style={styles.enrichmentBlock}>
              <Text style={styles.enrichmentLabel}>Did you know?</Text>
              {enrichment.fun_facts.map((fact, i) => (
                <Text key={i} style={styles.funFact}>
                  {ltr(fact)}
                </Text>
              ))}
            </View>
          )}

          {enrichment.related_roots && enrichment.related_roots.length > 0 && (
            <View style={styles.pillRow}>
              {enrichment.related_roots.map((r, i) => (
                <Pressable
                  key={i}
                  style={styles.pill}
                  onPress={async () => {
                    const result = await searchRootByText(r);
                    if (result) router.replace(`/root/${result.root_id}`);
                  }}
                >
                  <Text style={styles.pillText}>{r}</Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      )}

      {root.patterns.map((pattern, pi) => (
        <View key={pi} style={styles.patternGroup}>
          <Pressable
            style={styles.patternHeader}
            onPress={
              pattern.wazn
                ? () =>
                    router.push(
                      `/pattern/${encodeURIComponent(pattern.wazn!)}`
                    )
                : undefined
            }
          >
            <Text style={styles.patternName}>
              {pattern.wazn || "Unclassified"}
            </Text>
            {pattern.wazn_meaning && (
              <Text style={styles.patternMeaning}>
                {pattern.wazn_meaning}
              </Text>
            )}
            {pattern.wazn && (
              <Ionicons
                name="chevron-forward"
                size={14}
                color={colors.textSecondary}
              />
            )}
          </Pressable>

          {pattern.words.map((word) => (
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
      ))}
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
  rootLetters: {
    fontSize: 52,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontWeight: "600",
    lineHeight: 72,
    writingDirection: "rtl",
  },
  coreMeaning: {
    fontSize: 18,
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
  etymologyStory: {
    fontSize: 15,
    color: colors.text,
    lineHeight: 22,
    marginBottom: 8,
    writingDirection: "ltr" as const,
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
    writingDirection: "ltr" as const,
  },
  literaryExample: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
    fontStyle: "italic",
    marginTop: 4,
    writingDirection: "ltr" as const,
  },
  funFact: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
    marginTop: 4,
    writingDirection: "ltr" as const,
  },
  pillRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 10,
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

  patternGroup: {
    width: "100%",
    maxWidth: 500,
    marginTop: 16,
  },
  patternHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 6,
  },
  patternName: {
    fontSize: 16,
    color: colors.accent,
    fontWeight: "700",
  },
  patternMeaning: {
    fontSize: 13,
    color: colors.textSecondary,
    flex: 1,
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
    fontFamily: fontFamily.translit,
    marginTop: 1,
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
