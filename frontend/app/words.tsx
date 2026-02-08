import { useState, useEffect, useMemo } from "react";
import {
  View,
  Text,
  FlatList,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useRouter } from "expo-router";
import { colors, fonts } from "../lib/theme";
import { getWords } from "../lib/api";
import { Word } from "../lib/types";

export default function WordsScreen() {
  const [words, setWords] = useState<Word[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "new" | "learning" | "known">("all");
  const router = useRouter();

  useEffect(() => {
    loadWords();
  }, []);

  async function loadWords() {
    setLoading(true);
    try {
      const data = await getWords();
      setWords(data);
    } catch (e) {
      console.error("Failed to load words:", e);
    } finally {
      setLoading(false);
    }
  }

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

  function stateBadgeColor(state: Word["state"]): string {
    return {
      new: colors.stateNew,
      learning: colors.stateLearning,
      known: colors.stateKnown,
    }[state];
  }

  function renderWord({ item }: { item: Word }) {
    return (
      <Pressable
        style={styles.wordRow}
        onPress={() => router.push(`/word/${item.id}`)}
      >
        <View style={styles.wordLeft}>
          <Text style={styles.wordArabic}>{item.arabic}</Text>
          <Text style={styles.wordEnglish}>{item.english}</Text>
        </View>
        <View style={styles.wordRight}>
          <View
            style={[
              styles.stateBadge,
              { backgroundColor: stateBadgeColor(item.state) },
            ]}
          >
            <Text style={styles.stateBadgeText}>{item.state}</Text>
          </View>
          <Text style={styles.wordPos}>{item.pos}</Text>
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

  const filters: Array<"all" | "new" | "learning" | "known"> = [
    "all",
    "new",
    "learning",
    "known",
  ];

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
              {f.charAt(0).toUpperCase() + f.slice(1)}
              {f !== "all" && ` (${words.filter((w) => f === "all" || w.state === f).length})`}
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
    marginBottom: 2,
  },
  wordEnglish: {
    fontSize: fonts.small,
    color: colors.textSecondary,
  },
  wordRight: {
    alignItems: "flex-end",
    marginLeft: 12,
  },
  stateBadge: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 10,
    marginBottom: 4,
  },
  stateBadgeText: {
    color: "#fff",
    fontSize: fonts.caption,
    fontWeight: "600",
  },
  wordPos: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    textAlign: "center",
    marginTop: 40,
  },
});
