import { useState, useCallback, useMemo } from "react";
import {
  View,
  Text,
  FlatList,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
} from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, fontFamily } from "../lib/theme";
import { getWords } from "../lib/api";
import { Word } from "../lib/types";
import { getCefrColor } from "../lib/frequency";

type FilterValue =
  | "all"
  | "new"
  | "learning"
  | "known"
  | "lapsed"
  | "suspended"
  | "reviewed";

function reviewCategory(w: Word): "failed" | "passed" | "unseen" {
  if (w.times_seen === 0) return "unseen";
  if (w.times_correct < w.times_seen) return "failed";
  return "passed";
}

const sortOrder = { failed: 0, passed: 1, unseen: 2 };

const STATE_LABELS: Record<string, string> = {
  new: "New",
  learning: "Learning",
  known: "Known",
  lapsed: "Lapsed",
  suspended: "Suspended",
};

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
      lapsed: colors.missed,
      suspended: colors.textSecondary,
    }[state];
  }

  function renderWord({ item }: { item: Word }) {
    const cat = reviewCategory(item);
    const failed = item.times_seen - item.times_correct;
    const badgeColor = stateBadgeColor(item.state);

    const rowBg =
      cat === "failed"
        ? "rgba(231, 76, 60, 0.08)"
        : cat === "passed"
          ? "rgba(46, 204, 113, 0.06)"
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
          <Text style={styles.wordEnglish} numberOfLines={1}>{item.english}</Text>
          <View style={styles.wordMeta}>
            {item.pos ? (
              <Text style={styles.wordPos}>{item.pos}</Text>
            ) : null}
            {item.times_seen > 0 ? (
              <Text style={styles.wordReviewStats}>
                {item.times_seen} reviews
                {" · "}
                <Text style={{ color: colors.good }}>{item.times_correct} ok</Text>
                {failed > 0 && (
                  <Text style={{ color: colors.missed }}> · {failed} missed</Text>
                )}
              </Text>
            ) : null}
          </View>
        </View>
        <View style={styles.wordRight}>
          {item.cefr_level && (
            <View style={[styles.cefrBadge, { backgroundColor: getCefrColor(item.cefr_level) }]}>
              <Text style={styles.cefrText}>{item.cefr_level}</Text>
            </View>
          )}
          {item.knowledge_score > 0 && (
            <Text style={styles.scoreText}>{item.knowledge_score}</Text>
          )}
          <View style={[styles.stateBadge, { backgroundColor: badgeColor + "20" }]}>
            <Text style={[styles.stateBadgeText, { color: badgeColor }]}>
              {STATE_LABELS[item.state] || item.state}
            </Text>
          </View>
        </View>
      </Pressable>
    );
  }

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (error && words.length === 0) {
    return (
      <View style={styles.centered}>
        <Ionicons name="warning-outline" size={48} color={colors.textSecondary} style={{ opacity: 0.5, marginBottom: 16 }} />
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
    "lapsed",
    "suspended",
    "reviewed",
  ];

  function filterLabel(f: FilterValue): string {
    const label = f.charAt(0).toUpperCase() + f.slice(1);
    if (f === "all") return `All (${words.length})`;
    if (f === "reviewed") return `Reviewed (${reviewedCount})`;
    return `${label} (${words.filter((w) => w.state === f).length})`;
  }

  return (
    <View style={styles.container}>
      <View style={styles.searchContainer}>
        <Ionicons name="search" size={18} color={colors.textSecondary} style={styles.searchIcon} />
        <TextInput
          style={styles.searchInput}
          placeholder="Search Arabic, English, or transliteration..."
          placeholderTextColor={colors.textSecondary}
          value={search}
          onChangeText={setSearch}
        />
        {search.length > 0 && (
          <Pressable onPress={() => setSearch("")} hitSlop={8}>
            <Ionicons name="close-circle" size={18} color={colors.textSecondary} />
          </Pressable>
        )}
      </View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filterRow}
      >
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
      </ScrollView>
      <FlatList
        data={filtered}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderWord}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Ionicons
              name="library-outline"
              size={48}
              color={colors.textSecondary}
              style={{ opacity: 0.4, marginBottom: 12 }}
            />
            <Text style={styles.emptyText}>No words found</Text>
            <Text style={styles.emptyHint}>
              {search ? "Try a different search term" : "Import words to get started"}
            </Text>
          </View>
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
  centered: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  searchContainer: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    marginHorizontal: 16,
    marginTop: 12,
    marginBottom: 10,
    paddingHorizontal: 14,
  },
  searchIcon: {
    marginRight: 10,
  },
  searchInput: {
    flex: 1,
    color: colors.text,
    fontSize: fonts.body,
    paddingVertical: 13,
  },
  filterRow: {
    paddingHorizontal: 16,
    gap: 8,
    marginBottom: 10,
    paddingRight: 24,
  },
  filterChip: {
    paddingVertical: 7,
    paddingHorizontal: 14,
    borderRadius: 20,
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
    fontWeight: "500",
  },
  filterChipTextActive: {
    color: "#fff",
    fontWeight: "600",
  },
  list: {
    paddingHorizontal: 16,
    paddingBottom: 24,
  },
  wordRow: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
    marginBottom: 8,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  wordLeft: {
    flex: 1,
    marginRight: 12,
  },
  wordArabic: {
    fontSize: fonts.arabicMedium,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    lineHeight: 36,
    marginBottom: 2,
  },
  wordEnglish: {
    fontSize: 15,
    color: colors.text,
    fontWeight: "500",
    marginBottom: 4,
  },
  wordMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  wordPos: {
    fontSize: fonts.caption,
    color: colors.accent,
    fontWeight: "600",
  },
  wordReviewStats: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
  wordRight: {
    alignItems: "flex-end",
    gap: 6,
  },
  cefrBadge: {
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  cefrText: {
    color: "#fff",
    fontSize: 11,
    fontWeight: "700",
  },
  scoreText: {
    fontSize: 22,
    fontWeight: "700",
    color: colors.accent,
  },
  stateBadge: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 10,
  },
  stateBadgeText: {
    fontSize: 11,
    fontWeight: "600",
  },
  emptyContainer: {
    alignItems: "center",
    marginTop: 60,
    paddingHorizontal: 40,
  },
  emptyText: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "600",
    marginBottom: 6,
  },
  emptyHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    textAlign: "center",
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
    marginBottom: 16,
  },
  retryButton: {
    backgroundColor: colors.accent,
    paddingVertical: 12,
    paddingHorizontal: 32,
    borderRadius: 12,
  },
  retryText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "600",
  },
});
