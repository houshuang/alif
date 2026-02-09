import { useState, useEffect } from "react";
import {
  View,
  Text,
  ScrollView,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { colors, fonts, fontFamily } from "../../lib/theme";
import { getWordDetail } from "../../lib/api";
import { WordDetail, ReviewHistoryEntry } from "../../lib/types";
import AskAI from "../../lib/AskAI";

export default function WordDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [word, setWord] = useState<WordDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

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

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.arabicText}>{word.arabic}</Text>
      <Text style={styles.englishText}>{word.english}</Text>
      <Text style={styles.translitText}>{word.transliteration}</Text>

      <View style={styles.infoGrid}>
        <InfoItem label="Root" value={word.root ?? "—"} />
        <InfoItem label="POS" value={word.pos} />
        <InfoItem label="State" value={word.state} />
        {word.frequency_rank && (
          <InfoItem label="Frequency" value={`#${word.frequency_rank}`} />
        )}
      </View>

      {(word.forms_json || word.grammar_features.length > 0) && (
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
          {word.forms_json && (
            <View style={styles.formsCard}>
              {Object.entries(word.forms_json).map(([key, value]) => (
                <View key={key} style={styles.formRow}>
                  <Text style={styles.formKey}>{key.replace(/_/g, " ")}</Text>
                  <Text style={styles.formVal}>{String(value)}</Text>
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
    <AskAI contextBuilder={buildContext} screen="word_detail" />
    </View>
  );
}

function InfoItem({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.infoItem}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  );
}

function StatBox({ label, value }: { label: string; value: number | string }) {
  return (
    <View style={styles.statBox}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  content: {
    padding: 20,
    alignItems: "center",
  },
  arabicText: {
    fontSize: 40,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlign: "center",
    fontWeight: "600",
    marginTop: 12,
    lineHeight: 60,
  },
  englishText: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "600",
    marginTop: 8,
  },
  translitText: {
    fontSize: 18,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginTop: 4,
  },
  infoGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    marginTop: 24,
    justifyContent: "center",
  },
  infoItem: {
    backgroundColor: colors.surface,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 10,
    minWidth: 80,
    alignItems: "center",
  },
  infoLabel: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    marginBottom: 2,
  },
  infoValue: {
    fontSize: fonts.body,
    color: colors.text,
    fontWeight: "600",
  },
  section: {
    width: "100%",
    maxWidth: 500,
    marginTop: 28,
  },
  sectionTitle: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 12,
  },
  grammarChips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 10,
  },
  grammarChip: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingVertical: 5,
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
  formsCard: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    padding: 12,
    gap: 8,
  },
  formRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  formKey: {
    fontSize: fonts.small,
    color: colors.textSecondary,
  },
  formVal: {
    fontSize: fonts.body,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    marginLeft: 12,
    writingDirection: "rtl",
  },
  statsRow: {
    flexDirection: "row",
    gap: 12,
  },
  statBox: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 10,
    padding: 14,
    alignItems: "center",
  },
  statValue: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "700",
  },
  statLabel: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    marginTop: 4,
  },
  familyRow: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  familyArabic: {
    fontSize: fonts.arabicList,
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
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
  },
  reviewHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  ratingBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  ratingBadgeText: {
    color: "#fff",
    fontSize: fonts.caption,
    fontWeight: "600",
  },
  reviewMeta: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
  reviewDate: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    marginLeft: "auto",
  },
  reviewSentence: {
    fontSize: fonts.arabicList,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    marginTop: 6,
    lineHeight: 28,
  },
  reviewTranslation: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginTop: 4,
  },
  sentenceRow: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
  },
  sentenceStats: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 8,
  },
  sentenceStat: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    backgroundColor: colors.surfaceLight,
    borderRadius: 7,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  sentenceLastReviewed: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    marginTop: 8,
    opacity: 0.8,
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
  },
});
