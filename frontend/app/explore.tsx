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
import {
  getWords,
  getFunctionWords,
  getProperNames,
  getNextWords,
  getRoots,
  getPatterns,
  ProperName,
} from "../lib/api";
import { Word, LearnCandidate, RootListItem, PatternListItem } from "../lib/types";

type TopTab = "words" | "roots" | "patterns";
type CategoryTab = "vocab" | "function" | "names";
type SmartFilter =
  | "all"
  | "leeches"
  | "struggling"
  | "recent"
  | "solid"
  | "next_up"
  | "learning"
  | "known"
  | "new"
  | "lapsed"
  | "acquiring"
  | "encountered"
  | "most_seen";

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
  { key: "acquiring", label: "Acquiring" },
  { key: "leeches", label: "Leeches" },
  { key: "struggling", label: "Struggling" },
  { key: "recent", label: "Recent" },
  { key: "solid", label: "Solid" },
  { key: "next_up", label: "Next Up" },
  { key: "learning", label: "Learning" },
  { key: "known", label: "Known" },
  { key: "new", label: "New" },
  { key: "lapsed", label: "Lapsed" },
  { key: "encountered", label: "Encountered" },
  { key: "most_seen", label: "Most seen" },
];

function stateColor(state: Word["state"]): string {
  return (
    ({
      new: colors.textSecondary,
      learning: colors.stateLearning,
      known: colors.stateKnown,
      lapsed: colors.missed,
      suspended: colors.textSecondary,
      acquiring: colors.stateAcquiring,
      encountered: colors.stateEncountered,
    } as Record<string, string>)[state] ?? colors.textSecondary
  );
}

function knowledgeColor(state: string | null): string {
  return stateColor((state || "new") as Word["state"]);
}

function cleanCoreMeaning(s: string | null): string {
  if (!s) return "—";
  return s.replace(/^related to\s+/i, "").replace(/^the concept of\s+/i, "");
}

export default function ExploreScreen() {
  const [topTab, setTopTab] = useState<TopTab>("words");
  const [words, setWords] = useState<Word[]>([]);
  const [funcWords, setFuncWords] = useState<Word[]>([]);
  const [names, setNames] = useState<ProperName[]>([]);
  const [nextUp, setNextUp] = useState<LearnCandidate[]>([]);
  const [roots, setRoots] = useState<RootListItem[]>([]);
  const [patterns, setPatterns] = useState<PatternListItem[]>([]);
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
      const [w, fw, n, nu, r, p] = await Promise.all([
        getWords(),
        getFunctionWords().catch(() => []),
        getProperNames().catch(() => []),
        getNextWords(20)
          .then((d) => d.words)
          .catch(() => []),
        getRoots().catch(() => []),
        getPatterns().catch(() => []),
      ]);
      setWords(w);
      setFuncWords(fw);
      setNames(n);
      setNextUp(nu);
      setRoots(r);
      setPatterns(p);
      setError(null);
    } catch (e) {
      console.error("Failed to load explore data:", e);
      setError("Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  const counts = useMemo(() => {
    const c: Record<string, number> = {
      all: words.length,
      next_up: nextUp.length,
      most_seen: words.length,
    };
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
          result.sort(
            (a, b) =>
              a.times_correct / (a.times_seen || 1) -
              b.times_correct / (b.times_seen || 1)
          );
        } else if (stateFilter === "solid") {
          result.sort((a, b) => b.knowledge_score - a.knowledge_score);
        }
      } else if (stateFilter === "most_seen") {
        result = [...result].sort((a, b) => b.times_seen - a.times_seen);
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

  const filteredRoots = useMemo(() => {
    if (!search.trim()) return roots;
    const q = search.trim().toLowerCase();
    return roots.filter(
      (r) =>
        r.root.includes(q) ||
        (r.core_meaning_en || "").toLowerCase().includes(q)
    );
  }, [roots, search]);

  const filteredPatterns = useMemo(() => {
    if (!search.trim()) return patterns;
    const q = search.trim().toLowerCase();
    return patterns.filter(
      (p) =>
        p.wazn.toLowerCase().includes(q) ||
        (p.wazn_meaning || "").toLowerCase().includes(q)
    );
  }, [patterns, search]);

  function renderWord({ item }: { item: Word }) {
    const sc = stateColor(item.state);
    const ratings = item.last_ratings || [];
    const gaps = item.last_review_gaps || [];

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
              {item.pos ? (
                <Text style={styles.metaText}>{item.pos}</Text>
              ) : null}
              {item.root ? (
                <Text style={styles.metaRoot}>{item.root}</Text>
              ) : null}
              {item.frequency_rank ? (
                <Text style={styles.metaText}>#{item.frequency_rank}</Text>
              ) : null}
              {stateFilter === "most_seen" && item.times_seen > 0 ? (
                <Text style={styles.metaText}>{item.times_seen}x</Text>
              ) : null}
            </View>
            {ratings.length > 0 && (
              <View style={styles.sparkline}>
                {ratings.map((r, i) => {
                  const size = 3 + (i / Math.max(ratings.length - 1, 1)) * 3;
                  const c =
                    r >= 3
                      ? colors.stateKnown
                      : r === 2
                      ? colors.confused
                      : colors.missed;
                  const gapHours = gaps[i];
                  const ml =
                    i === 0
                      ? 0
                      : gapHours == null
                      ? 2
                      : gapHours < 1
                      ? 1
                      : gapHours < 24
                      ? 2
                      : gapHours < 72
                      ? 4
                      : gapHours < 168
                      ? 6
                      : 9;
                  return (
                    <View
                      key={i}
                      style={{
                        width: size,
                        height: size,
                        borderRadius: size / 2,
                        backgroundColor: c,
                        marginLeft: ml,
                      }}
                    />
                  );
                })}
                <View
                  style={[
                    styles.stateDot,
                    { backgroundColor: sc, marginLeft: 2 },
                  ]}
                />
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
              {item.pos ? (
                <Text style={styles.metaText}>{item.pos}</Text>
              ) : null}
              {item.root ? (
                <Text style={styles.metaRoot}>{item.root}</Text>
              ) : null}
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
    const typeColor =
      item.name_type === "personal" ? colors.accent : colors.listening;
    return (
      <Pressable
        style={styles.card}
        onPress={
          item.story_id
            ? () => router.push(`/story/${item.story_id}`)
            : undefined
        }
      >
        <View style={styles.cardRow}>
          <View style={styles.cardLeft}>
            <Text style={styles.cardEnglish} numberOfLines={1}>
              {item.gloss_en}
            </Text>
            <View style={styles.cardMeta}>
              <View
                style={[
                  styles.nameTypeBadge,
                  { backgroundColor: typeColor + "20" },
                ]}
              >
                <Text style={[styles.nameTypeText, { color: typeColor }]}>
                  {item.name_type === "personal" ? "person" : "place"}
                </Text>
              </View>
              {item.story_title && (
                <Text style={styles.metaText} numberOfLines={1}>
                  {item.story_title}
                </Text>
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

  function renderRoot({ item }: { item: RootListItem }) {
    const pct = item.coverage_pct;
    return (
      <Pressable
        style={styles.card}
        onPress={() => router.push(`/root/${item.root_id}`)}
      >
        <View style={styles.cardRow}>
          <View style={styles.cardLeft}>
            <Text style={styles.cardEnglish} numberOfLines={2}>
              {cleanCoreMeaning(item.core_meaning_en)}
            </Text>
            <View style={styles.cardMeta}>
              <Text style={styles.metaText}>
                {item.known_words}/{item.total_words} words
              </Text>
            </View>
            <View style={styles.coverageBarBg}>
              <View
                style={[styles.coverageBarFill, { width: `${pct}%` }]}
              />
            </View>
          </View>
          <Text style={styles.rootArabic}>{item.root}</Text>
        </View>
      </Pressable>
    );
  }

  function renderPattern({ item }: { item: PatternListItem }) {
    const pct = item.coverage_pct;
    return (
      <Pressable
        style={styles.card}
        onPress={() => router.push(`/pattern/${encodeURIComponent(item.wazn)}`)}
      >
        <View style={styles.cardRow}>
          <View style={styles.cardLeft}>
            <Text style={styles.patternName}>{item.wazn}</Text>
            <Text style={styles.cardEnglish} numberOfLines={2}>
              {item.wazn_meaning || "—"}
            </Text>
            <View style={styles.cardMeta}>
              <Text style={styles.metaText}>
                {item.known_words}/{item.total_words} words
              </Text>
            </View>
            <View style={styles.coverageBarBg}>
              <View
                style={[styles.coverageBarFill, { width: `${pct}%` }]}
              />
            </View>
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
        <Ionicons
          name="search"
          size={16}
          color={colors.textSecondary}
          style={{ marginRight: 8 }}
        />
        <TextInput
          style={styles.searchInput}
          placeholder="Search..."
          placeholderTextColor={colors.textSecondary}
          value={search}
          onChangeText={setSearch}
        />
        {search.length > 0 && (
          <Pressable onPress={() => setSearch("")} hitSlop={8}>
            <Ionicons
              name="close-circle"
              size={16}
              color={colors.textSecondary}
            />
          </Pressable>
        )}
      </View>

      {/* Top-level tabs: Words / Roots / Patterns */}
      <View style={styles.categoryRow}>
        {(
          [
            { key: "words" as TopTab, label: "Words", count: words.length },
            { key: "roots" as TopTab, label: "Roots", count: roots.length },
            {
              key: "patterns" as TopTab,
              label: "Patterns",
              count: patterns.length,
            },
          ] as const
        ).map((tab) => (
          <Pressable
            key={tab.key}
            style={[
              styles.categoryTab,
              topTab === tab.key && styles.categoryTabActive,
            ]}
            onPress={() => {
              setTopTab(tab.key);
              setCategory("vocab");
              setStateFilter("all");
            }}
          >
            <Text
              style={[
                styles.categoryText,
                topTab === tab.key && styles.categoryTextActive,
              ]}
            >
              {tab.label}
            </Text>
            <Text
              style={[
                styles.categoryCount,
                topTab === tab.key && styles.categoryCountActive,
              ]}
            >
              {tab.count}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Words sub-tabs + smart filters */}
      {topTab === "words" && (
        <>
          <View style={styles.subCategoryRow}>
            {(
              [
                {
                  key: "vocab" as CategoryTab,
                  label: "Vocabulary",
                  count: words.length,
                },
                {
                  key: "function" as CategoryTab,
                  label: "Function",
                  count: funcWords.length,
                },
                {
                  key: "names" as CategoryTab,
                  label: "Names",
                  count: names.length,
                },
              ] as const
            ).map((tab) => (
              <Pressable
                key={tab.key}
                style={[
                  styles.subTab,
                  category === tab.key && styles.subTabActive,
                ]}
                onPress={() => {
                  setCategory(tab.key);
                  setStateFilter("all");
                }}
              >
                <Text
                  style={[
                    styles.subTabText,
                    category === tab.key && styles.subTabTextActive,
                  ]}
                >
                  {tab.label}
                </Text>
                <Text
                  style={[
                    styles.subTabCount,
                    category === tab.key && styles.subTabCountActive,
                  ]}
                >
                  {tab.count}
                </Text>
              </Pressable>
            ))}
          </View>

          {category === "vocab" && (
            <View style={styles.filterRow}>
              {SMART_FILTERS.map((f) => {
                const count = counts[f.key] || 0;
                if (f.key !== "all" && count === 0) return null;
                const active = stateFilter === f.key;
                return (
                  <Pressable
                    key={f.key}
                    style={[
                      styles.filterChip,
                      active && styles.filterChipActive,
                    ]}
                    onPress={() => setStateFilter(f.key)}
                  >
                    <Text
                      style={[
                        styles.filterText,
                        active && styles.filterTextActive,
                      ]}
                    >
                      {f.label}
                    </Text>
                    <Text
                      style={[
                        styles.filterCount,
                        active && styles.filterCountActive,
                      ]}
                    >
                      {count}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          )}
        </>
      )}

      {/* Content */}
      {topTab === "words" && category === "names" ? (
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
            </View>
          }
        />
      ) : topTab === "words" && stateFilter === "next_up" ? (
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
            </View>
          }
        />
      ) : topTab === "words" ? (
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
                {search
                  ? "Try a different search"
                  : "Import words to get started"}
              </Text>
            </View>
          }
        />
      ) : topTab === "roots" ? (
        <FlatList
          key={`roots-${numColumns}`}
          data={filteredRoots}
          keyExtractor={(item) => String(item.root_id)}
          renderItem={renderRoot}
          numColumns={numColumns}
          columnWrapperStyle={styles.gridRow}
          contentContainerStyle={styles.grid}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>No roots found</Text>
            </View>
          }
        />
      ) : (
        <FlatList
          key={`patterns-${numColumns}`}
          data={filteredPatterns}
          keyExtractor={(item) => item.wazn}
          renderItem={renderPattern}
          numColumns={numColumns}
          columnWrapperStyle={styles.gridRow}
          contentContainerStyle={styles.grid}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>No patterns found</Text>
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

  subCategoryRow: {
    flexDirection: "row",
    marginHorizontal: 12,
    marginBottom: 6,
    gap: 6,
  },
  subTab: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 5,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: colors.surface,
  },
  subTabActive: {
    backgroundColor: colors.accent + "30",
  },
  subTabText: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  subTabTextActive: {
    color: colors.accent,
  },
  subTabCount: {
    fontSize: 10,
    color: colors.textSecondary,
    opacity: 0.6,
  },
  subTabCountActive: {
    color: colors.accent,
    opacity: 0.8,
  },

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

  grid: {
    paddingHorizontal: 12,
    paddingBottom: 24,
  },
  gridRow: {
    gap: 6,
    marginBottom: 6,
  },

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
  rootArabic: {
    fontSize: 22,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
    lineHeight: 32,
  },
  cardEnglish: {
    fontSize: 12,
    color: colors.text,
    lineHeight: 16,
  },
  patternName: {
    fontSize: 14,
    color: colors.accent,
    fontWeight: "700",
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
    marginTop: 1,
  },
  stateDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
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

  coverageBarBg: {
    height: 3,
    backgroundColor: colors.border,
    borderRadius: 2,
    marginTop: 4,
    overflow: "hidden",
  },
  coverageBarFill: {
    height: 3,
    backgroundColor: colors.stateKnown,
    borderRadius: 2,
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
