import { useState, useCallback, useMemo } from "react";
import {
  View,
  Text,
  FlatList,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { colors, fonts, fontFamily } from "../lib/theme";
import { getWords } from "../lib/api";
import { Word } from "../lib/types";

type FilterValue = "all" | "new" | "learning" | "known" | "reviewed";

function reviewCategory(w: Word): "failed" | "passed" | "unseen" {
  if (w.times_seen === 0) return "unseen";
  if (w.times_correct < w.times_seen) return "failed";
  return "passed";
}

const sortOrder = { failed: 0, passed: 1, unseen: 2 };

export default function WordsScreen() {
  const [words, setWords] = useState<Word[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FilterValue>("all");
  const router = useRouter();

  useFocusEffect(
    useCallback(() => {
      loadWords();
    }, [])
  );

  async function loadWords() {
    setLoading(true);
    try {
      const data = await getWords();
      setWords(data);
      setError(null);
    } catch (e) {
      console.error("Failed to load words:", e);
      setError("Failed to load words");
    } finally {
      setLoading(false);
    }
  }

  const filtered = useMemo(() => {
    let result = words;
    if (filter === "reviewed") {
      result = result.filter((w) => w.times_seen > 0);
    } else if (filter !== "all") {
      result = result.filter((w) => w.state === filter);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (w) =>
          w.arabic.includes(q) ||
          w.english.toLowerCase().includes(q) ||
          w.transliteration.toLowerCase().includes(q)
      );
    }
    return [...result].sort(
      (a, b) => sortOrder[reviewCategory(a)] - sortOrder[reviewCategory(b)]
    );
  }, [words, search, filter]);

  const reviewedCount = useMemo(
    () => words.filter((w) => w.times_seen > 0).length,
    [words]
  );

  function stateBadgeColor(state: Word["state"]): string {
    return {
      new: colors.stateNew,
      learning: colors.stateLearning,
      known: colors.stateKnown,
    }[state];
  }

  function renderWord({ item }: { item: Word }) {
    const cat = reviewCategory(item);
    const failed = item.times_seen - item.times_correct;

    const rowBg =
      cat === "failed"
        ? "rgba(231, 76, 60, 0.10)"
        : cat === "passed"
          ? "rgba(46, 204, 113, 0.08)"
          : colors.surface;

    const borderColor =
      cat === "failed"
        ? colors.missed
        : cat === "passed"
          ? colors.good
          : "transparent";

    return (
      <Pressable
        style={[
          styles.wordRow,
          {
            backgroundColor: rowBg,
            borderLeftWidth: 3,
            borderLeftColor: borderColor,
          },
        ]}
        onPress={() => router.push(`/word/${item.id}`)}
      >
        <View style={styles.wordLeft}>
          <Text style={styles.wordArabic}>{item.arabic}</Text>
          <Text style={styles.wordEnglish}>{item.english}</Text>
          {item.times_seen > 0 ? (
            <Text style={styles.wordReviewStats}>
              Seen {item.times_seen}x{" "}
              <Text style={{ color: colors.good }}>
                {item.times_correct} correct
              </Text>
              {failed > 0 && (
                <Text style={{ color: colors.missed }}>
                  {" "}
                  · {failed} failed
                </Text>
              )}
            </Text>
          ) : (
            <Text style={styles.wordReviewStats}>{item.pos}</Text>
          )}
        </View>
        <View style={styles.wordRight}>
          {item.knowledge_score > 0 && (
            <Text style={styles.scoreText}>{item.knowledge_score}</Text>
          )}
          <View
            style={[
              styles.stateBadge,
              { backgroundColor: stateBadgeColor(item.state) },
            ]}
          >
            <Text style={styles.stateBadgeText}>{item.state}</Text>
          </View>
        </View>
      </Pressable>
    );
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (error && words.length === 0) {
    return (
      <View style={styles.errorContainer}>
        <Text style={styles.errorText}>{error}</Text>
        <Pressable style={styles.retryButton} onPress={loadWords}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  const filters: FilterValue[] = [
    "all",
    "new",
    "learning",
    "known",
    "reviewed",
  ];

  function filterLabel(f: FilterValue): string {
    const label = f.charAt(0).toUpperCase() + f.slice(1);
    if (f === "all") return label;
    if (f === "reviewed") return `${label} (${reviewedCount})`;
    return `${label} (${words.filter((w) => w.state === f).length})`;
  }

  return (
    <View style={styles.container}>
      <TextInput
        style={styles.searchInput}
        placeholder="Search Arabic, English, or transliteration..."
        placeholderTextColor={colors.textSecondary}
        value={search}
        onChangeText={setSearch}
      />
      <View style={styles.filterRow}>
        {filters.map((f) => (
          <Pressable
            key={f}
            style={[
              styles.filterChip,
              filter === f && styles.filterChipActive,
            ]}
            onPress={() => setFilter(f)}
          >
            <Text
              style={[
                styles.filterChipText,
                filter === f && styles.filterChipTextActive,
              ]}
            >
              {filterLabel(f)}
            </Text>
          </Pressable>
        ))}
      </View>
      <FlatList
        data={filtered}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderWord}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Text style={styles.emptyText}>No words found</Text>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  searchInput: {
    backgroundColor: colors.surface,
    color: colors.text,
    fontSize: fonts.body,
    paddingHorizontal: 16,
    paddingVertical: 12,
    margin: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterRow: {
    flexDirection: "row",
    paddingHorizontal: 12,
    gap: 8,
    marginBottom: 8,
    flexWrap: "wrap",
  },
  filterChip: {
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterChipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  filterChipText: {
    color: colors.textSecondary,
    fontSize: fonts.small,
  },
  filterChipTextActive: {
    color: "#fff",
    fontWeight: "600",
  },
  list: {
    paddingHorizontal: 12,
    paddingBottom: 20,
  },
  wordRow: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    padding: 14,
    marginBottom: 8,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  wordLeft: {
    flex: 1,
  },
  wordArabic: {
    fontSize: fonts.arabicList,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    marginBottom: 2,
  },
  wordEnglish: {
    fontSize: fonts.small,
    color: colors.textSecondary,
  },
  wordReviewStats: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    marginTop: 3,
  },
  wordRight: {
    alignItems: "flex-end",
    marginLeft: 12,
  },
  scoreText: {
    fontSize: 20,
    fontWeight: "700",
    color: colors.accent,
    marginBottom: 4,
  },
  stateBadge: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 10,
  },
  stateBadgeText: {
    color: "#fff",
    fontSize: fonts.caption,
    fontWeight: "600",
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    textAlign: "center",
    marginTop: 40,
  },
  errorContainer: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
    marginBottom: 16,
  },
  retryButton: {
    backgroundColor: colors.accent,
    paddingVertical: 10,
    paddingHorizontal: 24,
    borderRadius: 10,
  },
  retryText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "600",
  },
});
