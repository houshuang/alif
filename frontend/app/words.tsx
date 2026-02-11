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
import { getWords, getFunctionWords, getProperNames, getNextWords, ProperName } from "../lib/api";
import { Word, LearnCandidate } from "../lib/types";

type CategoryTab = "vocab" | "function" | "names";
type SmartFilter = "all" | "leeches" | "struggling" | "recent" | "solid" | "next_up" | "learning" | "known" | "new" | "lapsed";

function isLeech(w: Word): boolean {
  return w.times_seen >= 6 && w.times_correct / w.times_seen < 0.5;
}

function isStruggling(w: Word): boolean {
  const ratings = w.last_ratings || [];
  if (ratings.length < 3) return false;
  const recent = ratings.slice(-4);
  return recent.filter((r) => r < 3).length >= 2;
}

function isRecent(w: Word): boolean {
  return w.state === "learning" && w.times_seen <= 4;
}

function isSolid(w: Word): boolean {
  return w.knowledge_score >= 70;
}

const SMART_FILTERS: { key: SmartFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "leeches", label: "Leeches" },
  { key: "struggling", label: "Struggling" },
  { key: "recent", label: "Recent" },
  { key: "solid", label: "Solid" },
  { key: "next_up", label: "Next Up" },
  { key: "learning", label: "Learning" },
  { key: "known", label: "Known" },
  { key: "new", label: "New" },
  { key: "lapsed", label: "Lapsed" },
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
  const [funcWords, setFuncWords] = useState<Word[]>([]);
  const [names, setNames] = useState<ProperName[]>([]);
  const [nextUp, setNextUp] = useState<LearnCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<CategoryTab>("vocab");
  const [stateFilter, setStateFilter] = useState<SmartFilter>("all");
  const router = useRouter();
  const { width } = useWindowDimensions();
  const numColumns = width > 500 ? 3 : 2;

  useFocusEffect(
    useCallback(() => {
      loadAll();
    }, [])
  );

  async function loadAll() {
    setLoading(true);
    try {
      const [w, fw, n, nu] = await Promise.all([
        getWords(),
        getFunctionWords().catch(() => []),
        getProperNames().catch(() => []),
        getNextWords(20).catch(() => []),
      ]);
      setWords(w);
      setFuncWords(fw);
      setNames(n);
      setNextUp(nu);
      setError(null);
    } catch (e) {
      console.error("Failed to load words:", e);
      setError("Failed to load words");
    } finally {
      setLoading(false);
    }
  }

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: words.length, next_up: nextUp.length };
    for (const w of words) {
      c[w.state] = (c[w.state] || 0) + 1;
      if (isLeech(w)) c.leeches = (c.leeches || 0) + 1;
      if (isStruggling(w)) c.struggling = (c.struggling || 0) + 1;
      if (isRecent(w)) c.recent = (c.recent || 0) + 1;
      if (isSolid(w)) c.solid = (c.solid || 0) + 1;
    }
    return c;
  }, [words, nextUp]);

  const filtered = useMemo(() => {
    const source = category === "function" ? funcWords : words;
    let result = source;
    if (category === "vocab" && stateFilter !== "all") {
      const smartFilters: Record<string, (w: Word) => boolean> = {
        leeches: isLeech,
        struggling: isStruggling,
        recent: isRecent,
        solid: isSolid,
      };
      const filterFn = smartFilters[stateFilter];
      if (filterFn) {
        result = result.filter(filterFn);
        if (stateFilter === "leeches") {
          result.sort((a, b) => (a.times_correct / (a.times_seen || 1)) - (b.times_correct / (b.times_seen || 1)));
        } else if (stateFilter === "solid") {
          result.sort((a, b) => b.knowledge_score - a.knowledge_score);
        }
      } else {
        result = result.filter((w) => w.state === stateFilter);
      }
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
  }, [words, funcWords, search, category, stateFilter]);

  const filteredNames = useMemo(() => {
    if (!search.trim()) return names;
    const q = search.trim().toLowerCase();
    return names.filter(
      (n) =>
        n.surface_form.includes(q) ||
        n.gloss_en.toLowerCase().includes(q) ||
        (n.story_title || "").toLowerCase().includes(q)
    );
  }, [names, search]);

  function renderWord({ item }: { item: Word }) {
    const sc = stateColor(item.state);
    const ratings = item.last_ratings || [];

    return (
      <Pressable
        style={styles.card}
        onPress={() => router.push(`/word/${item.id}`)}
      >
        <View style={styles.cardRow}>
          <View style={styles.cardLeft}>
            <Text style={styles.cardEnglish} numberOfLines={2}>
              {item.english}
            </Text>
            <View style={styles.cardMeta}>
              {item.pos ? <Text style={styles.metaText}>{item.pos}</Text> : null}
              {item.root ? <Text style={styles.metaRoot}>{item.root}</Text> : null}
              {item.frequency_rank ? <Text style={styles.metaText}>#{item.frequency_rank}</Text> : null}
            </View>
            {ratings.length > 0 && (
              <View style={styles.sparkline}>
                {ratings.map((r, i) => {
                  const size = 3 + (i / Math.max(ratings.length - 1, 1)) * 3;
                  const c = r >= 3 ? colors.stateKnown : r === 2 ? colors.confused : colors.missed;
                  return (
                    <View
                      key={i}
                      style={{
                        width: size,
                        height: size,
                        borderRadius: size / 2,
                        backgroundColor: c,
                      }}
                    />
                  );
                })}
                <View style={[styles.stateDot, { backgroundColor: sc, marginLeft: 2 }]} />
              </View>
            )}
            {ratings.length === 0 && (
              <View style={styles.sparkline}>
                <View style={[styles.stateDot, { backgroundColor: sc }]} />
              </View>
            )}
          </View>
          <Text style={styles.cardArabic} numberOfLines={1}>
            {item.arabic}
          </Text>
        </View>
      </Pressable>
    );
  }

  function renderCandidate({ item }: { item: LearnCandidate }) {
    return (
      <Pressable
        style={styles.card}
        onPress={() => router.push(`/word/${item.lemma_id}`)}
      >
        <View style={styles.cardRow}>
          <View style={styles.cardLeft}>
            <Text style={styles.cardEnglish} numberOfLines={2}>
              {item.gloss_en}
            </Text>
            <View style={styles.cardMeta}>
              {item.pos ? <Text style={styles.metaText}>{item.pos}</Text> : null}
              {item.root ? <Text style={styles.metaRoot}>{item.root}</Text> : null}
              {item.frequency_rank ? <Text style={styles.metaText}>#{item.frequency_rank}</Text> : null}
              {item.cefr_level ? <Text style={styles.metaText}>{item.cefr_level}</Text> : null}
            </View>
            <View style={styles.sparkline}>
              <View style={[styles.candidateScoreBadge]}>
                <Text style={styles.candidateScoreText}>
                  {Math.round(item.score * 100)}
                </Text>
              </View>
              {item.score_breakdown.known_siblings > 0 && (
                <Text style={styles.metaText}>
                  {item.score_breakdown.known_siblings}/{item.score_breakdown.total_siblings} root
                </Text>
              )}
            </View>
          </View>
          <Text style={styles.cardArabic} numberOfLines={1}>
            {item.lemma_ar}
          </Text>
        </View>
      </Pressable>
    );
  }

  function renderName({ item }: { item: ProperName }) {
    const typeColor = item.name_type === "personal" ? colors.accent : colors.listening;
    return (
      <Pressable
        style={styles.card}
        onPress={item.story_id ? () => router.push(`/story/${item.story_id}`) : undefined}
      >
        <View style={styles.cardRow}>
          <View style={styles.cardLeft}>
            <Text style={styles.cardEnglish} numberOfLines={1}>
              {item.gloss_en}
            </Text>
            <View style={styles.cardMeta}>
              <View style={[styles.nameTypeBadge, { backgroundColor: typeColor + "20" }]}>
                <Text style={[styles.nameTypeText, { color: typeColor }]}>
                  {item.name_type === "personal" ? "person" : "place"}
                </Text>
              </View>
              {item.story_title && (
                <Text style={styles.metaText} numberOfLines={1}>{item.story_title}</Text>
              )}
            </View>
          </View>
          <Text style={styles.cardArabic} numberOfLines={1}>
            {item.surface_form}
          </Text>
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
        <Pressable style={styles.retryButton} onPress={loadAll}>
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

      {/* Category tabs */}
      <View style={styles.categoryRow}>
        {([
          { key: "vocab" as CategoryTab, label: "Vocabulary", count: words.length },
          { key: "function" as CategoryTab, label: "Function", count: funcWords.length },
          { key: "names" as CategoryTab, label: "Names", count: names.length },
        ]).map((tab) => (
          <Pressable
            key={tab.key}
            style={[styles.categoryTab, category === tab.key && styles.categoryTabActive]}
            onPress={() => { setCategory(tab.key); setStateFilter("all"); }}
          >
            <Text style={[styles.categoryText, category === tab.key && styles.categoryTextActive]}>
              {tab.label}
            </Text>
            <Text style={[styles.categoryCount, category === tab.key && styles.categoryCountActive]}>
              {tab.count}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Smart filters (vocab tab only) */}
      {category === "vocab" && (
        <View style={styles.filterRow}>
          {SMART_FILTERS.map((f) => {
            const count = counts[f.key] || 0;
            if (f.key !== "all" && count === 0) return null;
            const active = stateFilter === f.key;
            return (
              <Pressable
                key={f.key}
                style={[styles.filterChip, active && styles.filterChipActive]}
                onPress={() => setStateFilter(f.key)}
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
      )}

      {/* Grid */}
      {category === "names" ? (
        <FlatList
          key={`names-${numColumns}`}
          data={filteredNames}
          keyExtractor={(item, i) => `${item.surface_form}-${i}`}
          renderItem={renderName}
          numColumns={numColumns}
          columnWrapperStyle={styles.gridRow}
          contentContainerStyle={styles.grid}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>No names found</Text>
              <Text style={styles.emptyHint}>Import a story to discover names</Text>
            </View>
          }
        />
      ) : stateFilter === "next_up" ? (
        <FlatList
          key={`candidates-${numColumns}`}
          data={nextUp}
          keyExtractor={(item) => String(item.lemma_id)}
          renderItem={renderCandidate}
          numColumns={numColumns}
          columnWrapperStyle={styles.gridRow}
          contentContainerStyle={styles.grid}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>No candidates</Text>
              <Text style={styles.emptyHint}>All words have been introduced</Text>
            </View>
          }
        />
      ) : (
        <FlatList
          key={`words-${numColumns}-${category}`}
          data={filtered}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderWord}
          numColumns={numColumns}
          columnWrapperStyle={styles.gridRow}
          contentContainerStyle={styles.grid}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>No words found</Text>
              <Text style={styles.emptyHint}>
                {search ? "Try a different search" : "Import words to get started"}
              </Text>
            </View>
          }
        />
      )}
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

  // Category tabs
  categoryRow: {
    flexDirection: "row",
    marginHorizontal: 12,
    marginBottom: 6,
    backgroundColor: colors.surface,
    borderRadius: 10,
    padding: 3,
  },
  categoryTab: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    paddingVertical: 7,
    borderRadius: 8,
  },
  categoryTabActive: {
    backgroundColor: colors.accent,
  },
  categoryText: {
    fontSize: 13,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  categoryTextActive: {
    color: "#fff",
  },
  categoryCount: {
    fontSize: 11,
    color: colors.textSecondary,
    opacity: 0.6,
  },
  categoryCountActive: {
    color: "rgba(255,255,255,0.7)",
  },

  // State filters
  filterRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    paddingHorizontal: 12,
    gap: 6,
    marginBottom: 6,
  },
  filterChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 4,
    paddingHorizontal: 9,
    borderRadius: 12,
    backgroundColor: colors.surface,
  },
  filterChipActive: {
    backgroundColor: colors.accent + "30",
  },
  filterText: {
    color: colors.textSecondary,
    fontSize: 11,
    fontWeight: "600",
  },
  filterTextActive: {
    color: colors.accent,
  },
  filterCount: {
    color: colors.textSecondary,
    fontSize: 10,
    opacity: 0.6,
  },
  filterCountActive: {
    color: colors.accent,
    opacity: 0.8,
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
    paddingVertical: 8,
  },
  cardRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  cardLeft: {
    flex: 1,
    marginRight: 8,
    gap: 2,
  },
  cardArabic: {
    fontSize: 26,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
    lineHeight: 36,
  },
  cardEnglish: {
    fontSize: 12,
    color: colors.text,
    lineHeight: 16,
  },
  cardMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
  },
  metaRoot: {
    fontSize: 11,
    color: colors.accent,
    fontFamily: fontFamily.arabic,
    opacity: 0.8,
  },
  metaText: {
    fontSize: 9,
    color: colors.textSecondary,
    opacity: 0.6,
  },
  sparkline: {
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    marginTop: 1,
  },
  stateDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
  },
  candidateScoreBadge: {
    backgroundColor: colors.accent + "25",
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 4,
  },
  candidateScoreText: {
    fontSize: 9,
    fontWeight: "600",
    color: colors.accent,
  },
  nameTypeBadge: {
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 4,
  },
  nameTypeText: {
    fontSize: 9,
    fontWeight: "600",
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
