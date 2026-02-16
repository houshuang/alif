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
import { colors, fonts, fontFamily } from "../lib/theme";
import { getBookPageDetail } from "../lib/api";
import { BookPageDetail, BookPageWord, BookPageSentence } from "../lib/types";

const STATE_COLORS: Record<string, string> = {
  known: colors.stateKnown,
  learning: colors.stateLearning,
  acquiring: colors.stateAcquiring,
  encountered: colors.stateEncountered,
  new: colors.stateNew,
  lapsed: colors.missed,
};

const STATE_LABELS: Record<string, string> = {
  known: "Known",
  learning: "Learning",
  acquiring: "Acquiring",
  encountered: "Encountered",
  lapsed: "Lapsed",
};

export default function BookPageScreen() {
  const { storyId, page } = useLocalSearchParams<{
    storyId: string;
    page: string;
  }>();
  const [data, setData] = useState<BookPageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const navigation = useNavigation();

  useLayoutEffect(() => {
    navigation.setOptions({
      headerLeft: () => (
        <Pressable onPress={() => router.push("/stories")} style={{ paddingLeft: 12 }}>
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </Pressable>
      ),
      title: `Page ${page || ""}`,
      headerStyle: { backgroundColor: colors.bg },
      headerTintColor: colors.text,
    });
  }, [navigation, page]);

  useEffect(() => {
    if (storyId && page) {
      setLoading(true);
      getBookPageDetail(Number(storyId), Number(page))
        .then(setData)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [storyId, page]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (!data) {
    return (
      <View style={styles.center}>
        <Text style={styles.emptyText}>Page not found</Text>
      </View>
    );
  }

  const newNotStarted = data.words.filter(
    (w) => w.is_new && !["acquiring", "learning", "known", "lapsed"].includes(w.knowledge_state || "")
  );
  const newLearning = data.words.filter(
    (w) => w.is_new && ["acquiring", "learning", "known", "lapsed"].includes(w.knowledge_state || "")
  );
  const existingWords = data.words.filter((w) => !w.is_new);
  const seenCount = data.sentences.filter((s) => s.seen).length;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {data.story_title_en && (
        <Text style={styles.subtitle}>{data.story_title_en}</Text>
      )}

      <View style={styles.statsRow}>
        <View style={styles.statBox}>
          <Text style={styles.statValue}>{data.new_not_started}</Text>
          <Text style={styles.statLabel}>Not started</Text>
        </View>
        <View style={styles.statBox}>
          <Text style={[styles.statValue, data.new_learning > 0 && { color: colors.stateLearning }]}>
            {data.new_learning}
          </Text>
          <Text style={styles.statLabel}>Learning</Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statValue}>{data.known_count}</Text>
          <Text style={styles.statLabel}>Known at import</Text>
        </View>
      </View>

      {newLearning.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Learning ({newLearning.length})
          </Text>
          {newLearning.map((w) => (
            <WordRow key={w.lemma_id} word={w} onPress={() => router.push(`/word/${w.lemma_id}`)} />
          ))}
        </View>
      )}

      {newNotStarted.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Not Started ({newNotStarted.length})
          </Text>
          {newNotStarted.map((w) => (
            <WordRow key={w.lemma_id} word={w} onPress={() => router.push(`/word/${w.lemma_id}`)} />
          ))}
        </View>
      )}

      {existingWords.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Known at Import ({existingWords.length})
          </Text>
          {existingWords.map((w) => (
            <WordRow key={w.lemma_id} word={w} onPress={() => router.push(`/word/${w.lemma_id}`)} />
          ))}
        </View>
      )}

      {data.sentences.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Sentences ({seenCount}/{data.sentences.length} seen)
          </Text>
          {data.sentences.map((s) => (
            <SentenceRow key={s.id} sentence={s} />
          ))}
        </View>
      )}
    </ScrollView>
  );
}

function WordRow({ word, onPress }: { word: BookPageWord; onPress: () => void }) {
  const stateColor = STATE_COLORS[word.knowledge_state || ""] || colors.textSecondary;
  const stateLabel = STATE_LABELS[word.knowledge_state || ""] || word.knowledge_state || "—";

  return (
    <Pressable style={styles.wordRow} onPress={onPress}>
      <View style={styles.wordLeft}>
        <Text style={styles.wordArabic}>{word.arabic}</Text>
        {word.transliteration && (
          <Text style={styles.wordTranslit}>{word.transliteration}</Text>
        )}
      </View>
      <View style={styles.wordRight}>
        <Text style={styles.wordGloss} numberOfLines={1}>
          {word.gloss_en || "—"}
        </Text>
        <View style={[styles.statePill, { backgroundColor: stateColor + "25" }]}>
          <Text style={[styles.statePillText, { color: stateColor }]}>
            {stateLabel}
          </Text>
        </View>
      </View>
      <Ionicons name="chevron-forward" size={14} color={colors.textSecondary} />
    </Pressable>
  );
}

function SentenceRow({ sentence }: { sentence: BookPageSentence }) {
  return (
    <View style={styles.sentenceRow}>
      <View style={styles.sentenceContent}>
        <Text style={styles.sentenceArabic}>{sentence.arabic_diacritized}</Text>
        {sentence.english_translation && (
          <Text style={styles.sentenceEnglish}>
            {sentence.english_translation}
          </Text>
        )}
      </View>
      <Ionicons
        name={sentence.seen ? "checkmark-circle" : "ellipse-outline"}
        size={20}
        color={sentence.seen ? colors.gotIt : colors.textSecondary}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, paddingBottom: 40 },
  center: {
    flex: 1,
    backgroundColor: colors.bg,
    justifyContent: "center",
    alignItems: "center",
  },
  emptyText: { color: colors.textSecondary, fontSize: fonts.body },
  subtitle: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginBottom: 12,
  },
  statsRow: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 20,
  },
  statBox: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 10,
    padding: 12,
    alignItems: "center",
  },
  statValue: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "700",
  },
  statLabel: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    marginTop: 2,
  },
  section: { marginBottom: 20 },
  sectionTitle: {
    color: colors.text,
    fontSize: fonts.body,
    fontWeight: "600",
    marginBottom: 8,
  },
  wordRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 12,
    marginBottom: 6,
  },
  wordLeft: { flex: 1 },
  wordArabic: {
    color: colors.arabic,
    fontSize: fonts.arabicList,
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    writingDirection: "rtl",
  },
  wordTranslit: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    textAlign: "right",
    marginTop: 2,
  },
  wordRight: {
    alignItems: "flex-end",
    marginLeft: 12,
    marginRight: 8,
  },
  wordGloss: {
    color: colors.text,
    fontSize: fonts.small,
    maxWidth: 120,
  },
  statePill: {
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
    marginTop: 4,
  },
  statePillText: {
    fontSize: 10,
    fontWeight: "600",
  },
  sentenceRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 12,
    marginBottom: 6,
  },
  sentenceContent: { flex: 1, marginRight: 8 },
  sentenceArabic: {
    color: colors.arabic,
    fontSize: fonts.arabicList,
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    writingDirection: "rtl",
    lineHeight: 32,
  },
  sentenceEnglish: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginTop: 4,
  },
});
