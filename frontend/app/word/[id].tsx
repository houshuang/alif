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
import { colors, fonts } from "../../lib/theme";
import { getWordDetail } from "../../lib/api";
import { WordDetail } from "../../lib/types";

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

  return (
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

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Review History</Text>
        <View style={styles.statsRow}>
          <StatBox label="Times Reviewed" value={word.times_reviewed} />
          <StatBox label="Correct" value={word.correct_count} />
          <StatBox label="Accuracy" value={`${accuracy}%`} />
        </View>
      </View>

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
    </ScrollView>
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
  },
  familyEnglish: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginLeft: 12,
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
  },
});
