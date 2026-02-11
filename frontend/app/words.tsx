import { useState, useCallback, useMemo } from "react";
import {
  View,
  Text,
  FlatList,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  useWindowDimensions,
} from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, fontFamily } from "../lib/theme";
import { getWords } from "../lib/api";
import { Word } from "../lib/types";

type FilterValue =
  | "all"
  | "new"
  | "learning"
  | "known"
  | "lapsed"
  | "suspended";

const FILTERS: { key: FilterValue; label: string }[] = [
  { key: "all", label: "All" },
  { key: "learning", label: "Learning" },
  { key: "known", label: "Known" },
  { key: "new", label: "New" },
  { key: "lapsed", label: "Lapsed" },
  { key: "suspended", label: "Suspended" },
];

function stateColor(state: Word["state"]): string {
  return {
    new: colors.textSecondary,
    learning: colors.stateLearning,
    known: colors.stateKnown,
    lapsed: colors.missed,
    suspended: colors.textSecondary,
  }[state];
}

export default function WordsScreen() {
  const [words, setWords] = useState<Word[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FilterValue>("all");
  const router = useRouter();
  const { width } = useWindowDimensions();
  const numColumns = width > 500 ? 3 : 2;

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

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: words.length };
    for (const w of words) c[w.state] = (c[w.state] || 0) + 1;
    return c;
  }, [words]);

  const filtered = useMemo(() => {
    let result = words;
    if (filter !== "all") {
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
    return result;
  }, [words, search, filter]);

  function renderWord({ item }: { item: Word }) {
    const sc = stateColor(item.state);

    return (
      <Pressable
        style={styles.card}
        onPress={() => router.push(`/word/${item.id}`)}
      >
        <View style={styles.cardTop}>
          <View style={[styles.stateDot, { backgroundColor: sc }]} />
          <Text style={styles.cardArabic} numberOfLines={1}>
            {item.arabic}
          </Text>
        </View>
        <Text style={styles.cardEnglish} numberOfLines={1}>
          {item.english}
        </Text>
        <View style={styles.cardMeta}>
          {item.root ? (
            <Text style={styles.metaRoot}>{item.root}</Text>
          ) : null}
          {item.pos ? (
            <Text style={styles.metaText}>{item.pos}</Text>
          ) : null}
          {item.knowledge_score > 0 ? (
            <Text style={styles.metaScore}>{item.knowledge_score}</Text>
          ) : null}
          {item.frequency_rank ? (
            <Text style={styles.metaText}>#{item.frequency_rank}</Text>
          ) : null}
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
        <Text style={styles.errorText}>{error}</Text>
        <Pressable style={styles.retryButton} onPress={loadWords}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Search */}
      <View style={styles.searchContainer}>
        <Ionicons name="search" size={16} color={colors.textSecondary} style={{ marginRight: 8 }} />
        <TextInput
          style={styles.searchInput}
          placeholder="Search..."
          placeholderTextColor={colors.textSecondary}
          value={search}
          onChangeText={setSearch}
        />
        {search.length > 0 && (
          <Pressable onPress={() => setSearch("")} hitSlop={8}>
            <Ionicons name="close-circle" size={16} color={colors.textSecondary} />
          </Pressable>
        )}
      </View>

      {/* Filter row */}
      <View style={styles.filterRow}>
        {FILTERS.map((f) => {
          const count = counts[f.key] || 0;
          if (f.key !== "all" && count === 0) return null;
          const active = filter === f.key;
          return (
            <Pressable
              key={f.key}
              style={[styles.filterChip, active && styles.filterChipActive]}
              onPress={() => setFilter(f.key)}
            >
              <Text style={[styles.filterText, active && styles.filterTextActive]}>
                {f.label}
              </Text>
              <Text style={[styles.filterCount, active && styles.filterCountActive]}>
                {count}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {/* Grid */}
      <FlatList
        key={numColumns}
        data={filtered}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderWord}
        numColumns={numColumns}
        columnWrapperStyle={styles.gridRow}
        contentContainerStyle={styles.grid}
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
              {search ? "Try a different search" : "Import words to get started"}
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

  // Search
  searchContainer: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 10,
    marginHorizontal: 12,
    marginTop: 10,
    marginBottom: 8,
    paddingHorizontal: 12,
    height: 40,
  },
  searchInput: {
    flex: 1,
    color: colors.text,
    fontSize: fonts.small,
    paddingVertical: 0,
  },

  // Filters
  filterRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    paddingHorizontal: 12,
    gap: 6,
    marginBottom: 8,
  },
  filterChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 5,
    paddingHorizontal: 10,
    borderRadius: 14,
    backgroundColor: colors.surface,
  },
  filterChipActive: {
    backgroundColor: colors.accent,
  },
  filterText: {
    color: colors.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  filterTextActive: {
    color: "#fff",
  },
  filterCount: {
    color: colors.textSecondary,
    fontSize: 11,
    opacity: 0.7,
  },
  filterCountActive: {
    color: "rgba(255,255,255,0.7)",
  },

  // Grid
  grid: {
    paddingHorizontal: 12,
    paddingBottom: 24,
  },
  gridRow: {
    gap: 6,
    marginBottom: 6,
  },

  // Card
  card: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingTop: 6,
    paddingBottom: 5,
  },
  cardTop: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  stateDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
  },
  cardArabic: {
    flex: 1,
    fontSize: 20,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
    textAlign: "right",
    lineHeight: 28,
  },
  cardEnglish: {
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 3,
    numberOfLines: 1,
  },
  cardMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    flexWrap: "wrap",
  },
  metaRoot: {
    fontSize: 10,
    color: colors.accent,
    fontFamily: fontFamily.arabic,
    opacity: 0.8,
  },
  metaText: {
    fontSize: 9,
    color: colors.textSecondary,
    opacity: 0.7,
  },
  metaScore: {
    fontSize: 9,
    color: colors.accent,
    fontWeight: "700",
  },

  // Empty / error
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
