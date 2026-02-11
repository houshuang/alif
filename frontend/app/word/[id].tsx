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
import { getWordDetail } from "../../lib/api";
import { WordDetail, ReviewHistoryEntry } from "../../lib/types";
import { getCefrColor } from "../../lib/frequency";
import ActionMenu from "../../lib/review/ActionMenu";

export default function WordDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [word, setWord] = useState<WordDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const navigation = useNavigation();

  useLayoutEffect(() => {
    navigation.setOptions({
      headerLeft: () => (
        <Pressable onPress={() => router.back()} style={{ paddingLeft: 12 }}>
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </Pressable>
      ),
    });
  }, [navigation, router]);

  useEffect(() => {
    if (id) loadWord(Number(id));
  }, [id]);

  async function loadWord(wordId: number) {
    setLoading(true);
    try {
      const data = await getWordDetail(wordId);
      setWord(data);
    } catch (e) {
      console.error("Failed to load word:", e);
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

  if (!word) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>Word not found</Text>
      </View>
    );
  }

  const accuracy =
    word.times_reviewed > 0
      ? Math.round((word.correct_count / word.times_reviewed) * 100)
      : 0;

  const w = word;
  const grammarSummary = w.grammar_features.map((g) => g.label_en).join(", ");
  function buildContext(): string {
    const parts = [
      `Word: ${w.arabic} (${w.english})`,
      `POS: ${w.pos}`,
      `State: ${w.state}`,
    ];
    if (w.root) parts.push(`Root: ${w.root}`);
    if (w.transliteration) parts.push(`Transliteration: ${w.transliteration}`);
    if (grammarSummary) parts.push(`Grammar: ${grammarSummary}`);
    if (w.root_family.length > 0) {
      const family = w.root_family.map((f) => `${f.arabic} (${f.english})`).join(", ");
      parts.push(`Root family: ${family}`);
    }
    if (w.sentence_stats.length > 0) {
      parts.push(`Sentence contexts: ${w.sentence_stats.length}`);
    }
    if (w.review_history.length > 0) {
      const recent = w.review_history.slice(0, 5).map((r) =>
        `${r.rating >= 3 ? "Pass" : "Fail"}${r.review_mode ? ` (${r.review_mode})` : ""}`
      ).join(", ");
      parts.push(`Recent reviews: ${recent}`);
    }
    return parts.join("\n");
  }

  const infoParts = [
    word.root,
    word.pos,
    word.state,
    word.frequency_rank ? `#${word.frequency_rank.toLocaleString()}` : null,
    word.cefr_level,
  ].filter(Boolean) as string[];

  // Filter redundant forms (gender shown as grammar chip)
  const displayForms = word.forms_json
    ? Object.entries(word.forms_json).filter(([key]) => key !== "gender")
    : [];

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.headerRow}>
        <Text style={styles.arabicText}>{word.arabic}</Text>
        <Text style={styles.englishText}>{word.english}</Text>
      </View>
      {word.transliteration ? (
        <Text style={styles.translitText}>{word.transliteration}</Text>
      ) : null}

      <Text style={styles.infoLine}>
        {infoParts.map((part, i) => (
          <Text key={i}>
            {i > 0 ? " · " : ""}
            {part === word.cefr_level ? (
              <Text style={{ color: getCefrColor(word.cefr_level!) }}>{part}</Text>
            ) : (
              part
            )}
          </Text>
        ))}
      </Text>

      {(displayForms.length > 0 || word.grammar_features.length > 0) && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Grammar</Text>
          {word.grammar_features.length > 0 && (
            <View style={styles.grammarChips}>
              {word.grammar_features.map((g) => (
                <View key={g.feature_key} style={styles.grammarChip}>
                  <Text style={styles.grammarChipEn}>{g.label_en}</Text>
                  {g.label_ar ? (
                    <Text style={styles.grammarChipAr}>{g.label_ar}</Text>
                  ) : null}
                </View>
              ))}
            </View>
          )}
          {displayForms.length > 0 && (
            <View style={styles.formsRow}>
              {displayForms.map(([key, value]) => (
                <View key={key} style={styles.formChip}>
                  <Text style={styles.formVal}>{String(value)}</Text>
                  <Text style={styles.formKey}>{key.replace(/_/g, " ")}</Text>
                </View>
              ))}
            </View>
          )}
        </View>
      )}

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Review History</Text>
        <View style={styles.statsRow}>
          <StatBox label="Score" value={word.knowledge_score} />
          <StatBox label="Reviewed" value={word.times_reviewed} />
          <StatBox label="Correct" value={word.correct_count} />
          <StatBox label="Accuracy" value={`${accuracy}%`} />
        </View>
      </View>

      {word.review_history && word.review_history.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Reviews</Text>
          {word.review_history.map((r: ReviewHistoryEntry, i: number) => (
            <View key={i} style={styles.reviewRow}>
              <View style={styles.reviewHeader}>
                <View
                  style={[
                    styles.ratingBadge,
                    { backgroundColor: r.rating >= 3 ? colors.good : colors.missed },
                  ]}
                >
                  <Text style={styles.ratingBadgeText}>
                    {r.rating >= 3 ? "Pass" : "Fail"}
                  </Text>
                </View>
                {r.credit_type && (
                  <Text style={styles.reviewMeta}>{r.credit_type}</Text>
                )}
                {r.review_mode && (
                  <Text style={styles.reviewMeta}>{r.review_mode}</Text>
                )}
                {r.reviewed_at && (
                  <Text style={styles.reviewDate}>
                    {new Date(r.reviewed_at).toLocaleDateString()}
                  </Text>
                )}
              </View>
              {r.sentence_arabic && (
                <Text style={styles.reviewSentence}>{r.sentence_arabic}</Text>
              )}
              {r.sentence_english && (
                <Text style={styles.reviewTranslation}>{r.sentence_english}</Text>
              )}
            </View>
          ))}
        </View>
      )}

      {word.root_family.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Root Family ({word.root})
          </Text>
          {word.root_family.map((f) => (
            <Pressable
              key={f.id}
              style={styles.familyRow}
              onPress={() => router.push(`/word/${f.id}`)}
            >
              <Text style={styles.familyArabic}>{f.arabic}</Text>
              <Text style={styles.familyEnglish}>{f.english}</Text>
            </Pressable>
          ))}
        </View>
      )}

      {word.sentence_stats.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Sentence Contexts ({word.sentence_stats.length})
          </Text>
          {word.sentence_stats.map((s) => (
            <View key={s.sentence_id} style={styles.sentenceRow}>
              <Text style={styles.reviewSentence}>{s.sentence_arabic}</Text>
              {s.sentence_english ? (
                <Text style={styles.reviewTranslation}>{s.sentence_english}</Text>
              ) : null}
              <View style={styles.sentenceStats}>
                <Text style={styles.sentenceStat}>Seen {s.seen_count}</Text>
                <Text style={styles.sentenceStat}>Missed {s.missed_count}</Text>
                <Text style={styles.sentenceStat}>Confused {s.confused_count}</Text>
                <Text style={styles.sentenceStat}>Understood {s.understood_count}</Text>
                {s.accuracy_pct !== null ? (
                  <Text style={styles.sentenceStat}>Accuracy {s.accuracy_pct}%</Text>
                ) : null}
              </View>
              {s.last_reviewed_at ? (
                <Text style={styles.sentenceLastReviewed}>
                  Last reviewed {new Date(s.last_reviewed_at).toLocaleDateString()}
                </Text>
              ) : (
                <Text style={styles.sentenceLastReviewed}>Not reviewed yet</Text>
              )}
            </View>
          ))}
        </View>
      )}
    </ScrollView>
    <ActionMenu
      focusedLemmaId={word.id}
      focusedLemmaAr={word.arabic}
      sentenceId={null}
      askAIContextBuilder={buildContext}
      askAIScreen="word_detail"
    />
    </View>
  );
}

function StatBox({ label, value }: { label: string; value: number | string }) {
  return (
    <View style={styles.statBox}>
      <Text style={styles.statValue} numberOfLines={1} adjustsFontSizeToFit>{value}</Text>
      <Text style={styles.statLabel} numberOfLines={1} adjustsFontSizeToFit>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  content: {
    padding: 16,
    alignItems: "center",
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginTop: 4,
  },
  arabicText: {
    fontSize: 52,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    fontWeight: "600",
    lineHeight: 72,
  },
  englishText: {
    fontSize: 16,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  translitText: {
    fontSize: 14,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginTop: 2,
  },
  infoLine: {
    fontSize: 14,
    color: colors.textSecondary,
    marginTop: 8,
  },
  section: {
    width: "100%",
    maxWidth: 500,
    marginTop: 16,
  },
  sectionTitle: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 6,
  },
  grammarChips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 8,
  },
  grammarChip: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    paddingVertical: 4,
    paddingHorizontal: 8,
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
  formsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  formChip: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingVertical: 6,
    paddingHorizontal: 12,
    alignItems: "center",
  },
  formKey: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    marginTop: 2,
  },
  formVal: {
    fontSize: 24,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
    lineHeight: 34,
  },
  statsRow: {
    flexDirection: "row",
    gap: 8,
  },
  statBox: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 8,
    alignItems: "center",
  },
  statValue: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "700",
  },
  statLabel: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
  },
  familyRow: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 10,
    marginBottom: 6,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  familyArabic: {
    fontSize: fonts.arabicMedium,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
  },
  familyEnglish: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginLeft: 12,
  },
  reviewRow: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 10,
    marginBottom: 6,
  },
  reviewHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 4,
  },
  ratingBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
  },
  ratingBadgeText: {
    color: "#fff",
    fontSize: 11,
    fontWeight: "600",
  },
  reviewMeta: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  reviewDate: {
    fontSize: 11,
    color: colors.textSecondary,
    marginLeft: "auto",
  },
  reviewSentence: {
    fontSize: fonts.arabicList,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    marginTop: 4,
    lineHeight: 28,
  },
  reviewTranslation: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginTop: 3,
  },
  sentenceRow: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 10,
    marginBottom: 6,
  },
  sentenceStats: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 6,
  },
  sentenceStat: {
    fontSize: 11,
    color: colors.textSecondary,
    backgroundColor: colors.surfaceLight,
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  sentenceLastReviewed: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 6,
    opacity: 0.8,
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
  },
});
