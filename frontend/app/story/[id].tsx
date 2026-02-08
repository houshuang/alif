import { useState, useEffect, useCallback } from "react";
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
import {
  getStoryDetail,
  lookupStoryWord,
  completeStory,
  skipStory,
  tooDifficultStory,
} from "../../lib/api";
import { StoryDetail, StoryWordMeta, StoryLookupResult } from "../../lib/types";
import { saveStoryLookups, getStoryLookups, clearStoryLookups } from "../../lib/offline-store";

type ViewMode = "arabic" | "english";

export default function StoryReadScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [story, setStory] = useState<StoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<ViewMode>("arabic");
  const [selectedWord, setSelectedWord] = useState<StoryLookupResult | null>(null);
  const [selectedPosition, setSelectedPosition] = useState<number | null>(null);
  const [lookedUp, setLookedUp] = useState<Set<number>>(new Set());
  const [lookedUpLemmaIds, setLookedUpLemmaIds] = useState<Set<number>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const router = useRouter();

  useEffect(() => {
    if (id) loadStory(Number(id));
  }, [id]);

  async function loadStory(storyId: number) {
    setLoading(true);
    try {
      const data = await getStoryDetail(storyId);
      setStory(data);
      const saved = await getStoryLookups(storyId);
      if (saved) {
        setLookedUp(saved.positions);
        setLookedUpLemmaIds(saved.lemmaIds);
      }
    } catch (e) {
      console.error("Failed to load story:", e);
    } finally {
      setLoading(false);
    }
  }

  function persistLookups(positions: Set<number>, lemmaIds: Set<number>) {
    if (id) saveStoryLookups(Number(id), positions, lemmaIds).catch(() => {});
  }

  const handleWordTap = useCallback(
    async (word: StoryWordMeta) => {
      if (!story) return;
      if (word.is_function_word || word.lemma_id == null) return;

      // Toggle off if already looked up
      if (lookedUp.has(word.position)) {
        const nextPositions = new Set(lookedUp);
        nextPositions.delete(word.position);
        setLookedUp(nextPositions);

        const nextLemmaIds = new Set(lookedUpLemmaIds);
        const otherPositionsWithSameLemma = story.words.some(
          (w) => w.lemma_id === word.lemma_id && w.position !== word.position && nextPositions.has(w.position)
        );
        if (!otherPositionsWithSameLemma) {
          nextLemmaIds.delete(word.lemma_id!);
          setLookedUpLemmaIds(nextLemmaIds);
        }
        persistLookups(nextPositions, nextLemmaIds);

        if (selectedPosition === word.position) {
          setSelectedWord(null);
          setSelectedPosition(null);
        }
        return;
      }

      const nextPositions = new Set(lookedUp).add(word.position);
      const nextLemmaIds = new Set(lookedUpLemmaIds).add(word.lemma_id!);
      setSelectedPosition(word.position);
      setLookedUp(nextPositions);
      setLookedUpLemmaIds(nextLemmaIds);
      persistLookups(nextPositions, nextLemmaIds);

      try {
        const result = await lookupStoryWord(story.id, word.lemma_id, word.position);
        setSelectedWord(result);
      } catch (e) {
        setSelectedWord({
          lemma_id: word.lemma_id,
          gloss_en: word.gloss_en,
          transliteration: null,
          root: null,
          pos: null,
        });
      }
    },
    [story, lookedUp, selectedPosition]
  );

  async function handleComplete() {
    if (!story || submitting) return;
    setSubmitting(true);
    try {
      await completeStory(story.id, Array.from(lookedUpLemmaIds));
      clearStoryLookups(story.id).catch(() => {});
      router.back();
    } catch (e) {
      console.error("Failed to complete story:", e);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSkip() {
    if (!story || submitting) return;
    setSubmitting(true);
    try {
      await skipStory(story.id, Array.from(lookedUpLemmaIds));
      clearStoryLookups(story.id).catch(() => {});
      router.back();
    } catch (e) {
      console.error("Failed to skip story:", e);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTooDifficult() {
    if (!story || submitting) return;
    setSubmitting(true);
    try {
      await tooDifficultStory(story.id, Array.from(lookedUpLemmaIds));
      clearStoryLookups(story.id).catch(() => {});
      router.back();
    } catch (e) {
      console.error("Failed to mark story:", e);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (!story) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>Story not found</Text>
      </View>
    );
  }

  const sentences = groupBySentence(story.words);

  return (
    <View style={styles.container}>
      <View style={styles.tabBar}>
        <Pressable
          style={[styles.tab, viewMode === "arabic" && styles.tabActive]}
          onPress={() => setViewMode("arabic")}
        >
          <Text
            style={[
              styles.tabText,
              viewMode === "arabic" && styles.tabTextActive,
            ]}
          >
            Arabic
          </Text>
        </Pressable>
        <Pressable
          style={[styles.tab, viewMode === "english" && styles.tabActive]}
          onPress={() => setViewMode("english")}
        >
          <Text
            style={[
              styles.tabText,
              viewMode === "english" && styles.tabTextActive,
            ]}
          >
            English
          </Text>
        </Pressable>
      </View>

      <ScrollView
        style={styles.scrollArea}
        contentContainerStyle={styles.scrollContent}
      >
        {viewMode === "arabic" ? (
          <View style={styles.storyFlow}>
            {sentences.map((sentenceWords, si) => {
              const elements: React.ReactNode[] = [];
              sentenceWords.forEach((word) => {
                const tappable =
                  !word.is_function_word && word.lemma_id != null;
                const isLookedUp = lookedUp.has(word.position);
                const isSelected = selectedPosition === word.position;

                elements.push(
                  tappable ? (
                    <Pressable
                      key={word.position}
                      onPress={() => handleWordTap(word)}
                      style={[
                        styles.wordChip,
                        isLookedUp && styles.lookedUpChip,
                        isSelected && styles.selectedChip,
                      ]}
                    >
                      <Text
                        style={[
                          styles.storyWord,
                          isLookedUp && styles.lookedUpWord,
                          isSelected && styles.selectedWord,
                        ]}
                      >
                        {word.surface_form}
                      </Text>
                    </Pressable>
                  ) : (
                    <View key={word.position} style={styles.wordChip}>
                      <Text style={styles.storyWord}>
                        {word.surface_form}
                      </Text>
                    </View>
                  )
                );
              });
              // Add period after each sentence
              elements.push(
                <View key={`period-${si}`} style={styles.wordChip}>
                  <Text style={styles.storyWord}>.</Text>
                </View>
              );
              return elements;
            })}
          </View>
        ) : (
          <Text style={styles.englishText}>
            {story.body_en || "No translation available."}
          </Text>
        )}
      </ScrollView>

      <View style={styles.lookupPanel}>
        {selectedWord ? (
          <View style={styles.lookupContent}>
            <Text style={styles.lookupArabic}>
              {story.words.find((w) => w.position === selectedPosition)
                ?.surface_form || ""}
            </Text>
            <Text style={styles.lookupGloss}>
              {selectedWord.gloss_en || "Unknown"}
            </Text>
            <View style={styles.lookupMeta}>
              {selectedWord.transliteration && (
                <Text style={styles.lookupTranslit}>
                  {selectedWord.transliteration}
                </Text>
              )}
              {selectedWord.root && (
                <Text style={styles.lookupRoot}>
                  Root: {selectedWord.root}
                </Text>
              )}
              {selectedWord.pos && (
                <Text style={styles.lookupPos}>{selectedWord.pos}</Text>
              )}
            </View>
          </View>
        ) : (
          <Text style={styles.lookupHint}>
            Tap any word to see its translation
          </Text>
        )}
      </View>

      <View style={styles.bottomActions}>
        <Pressable
          style={[styles.bottomBtn, styles.skipBtn]}
          onPress={handleSkip}
          disabled={submitting}
        >
          <Text style={styles.skipBtnText}>Skip</Text>
        </Pressable>
        {story.source === "imported" && (
          <Pressable
            style={[styles.bottomBtn, styles.difficultBtn]}
            onPress={handleTooDifficult}
            disabled={submitting}
          >
            <Text style={styles.difficultBtnText}>Too Difficult</Text>
          </Pressable>
        )}
        <Pressable
          style={[styles.bottomBtn, styles.completeBtn]}
          onPress={handleComplete}
          disabled={submitting}
        >
          {submitting ? (
            <ActivityIndicator size="small" color="#fff" />
          ) : (
            <Text style={styles.completeBtnText}>Complete</Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}

function groupBySentence(words: StoryWordMeta[]): StoryWordMeta[][] {
  const groups: Map<number, StoryWordMeta[]> = new Map();
  for (const w of words) {
    const list = groups.get(w.sentence_index) || [];
    list.push(w);
    groups.set(w.sentence_index, list);
  }
  return Array.from(groups.entries())
    .sort(([a], [b]) => a - b)
    .map(([, ws]) => ws.sort((a, b) => a.position - b.position));
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
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
  },
  tabBar: {
    flexDirection: "row",
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  tab: {
    flex: 1,
    paddingVertical: 12,
    alignItems: "center",
  },
  tabActive: {
    borderBottomWidth: 2,
    borderBottomColor: colors.accent,
  },
  tabText: {
    fontSize: fonts.body,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  tabTextActive: {
    color: colors.accent,
  },
  scrollArea: {
    flex: 1,
  },
  scrollContent: {
    padding: 20,
    paddingBottom: 40,
  },
  storyFlow: {
    flexDirection: "row",
    flexWrap: "wrap",
    direction: "rtl",
    gap: 6,
    rowGap: 12,
  },
  wordChip: {
    paddingVertical: 4,
    paddingHorizontal: 3,
    borderRadius: 4,
  },
  storyWord: {
    fontSize: 28,
    lineHeight: 36,
    color: colors.arabic,
  },
  lookedUpChip: {
    backgroundColor: colors.missed + "20",
  },
  selectedChip: {
    backgroundColor: colors.missed + "35",
  },
  lookedUpWord: {
    color: colors.missed,
  },
  selectedWord: {
    color: colors.missed,
  },
  englishText: {
    fontSize: 20,
    color: colors.text,
    lineHeight: 32,
  },
  lookupPanel: {
    height: 120,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    justifyContent: "center",
    paddingHorizontal: 20,
  },
  lookupContent: {
    alignItems: "center",
  },
  lookupArabic: {
    fontSize: fonts.arabicMedium,
    color: colors.arabic,
    writingDirection: "rtl",
    fontWeight: "600",
  },
  lookupGloss: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
    marginTop: 4,
  },
  lookupMeta: {
    flexDirection: "row",
    gap: 12,
    marginTop: 6,
  },
  lookupTranslit: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    fontStyle: "italic",
  },
  lookupRoot: {
    fontSize: fonts.small,
    color: colors.accent,
  },
  lookupPos: {
    fontSize: fonts.small,
    color: colors.textSecondary,
  },
  lookupHint: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    textAlign: "center",
    fontStyle: "italic",
  },
  bottomActions: {
    flexDirection: "row",
    gap: 10,
    padding: 12,
    paddingBottom: 24,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  bottomBtn: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: "center",
  },
  skipBtn: {
    backgroundColor: colors.surfaceLight,
  },
  skipBtnText: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    fontWeight: "600",
  },
  difficultBtn: {
    backgroundColor: colors.stateLearning + "30",
  },
  difficultBtnText: {
    color: colors.stateLearning,
    fontSize: fonts.body,
    fontWeight: "600",
  },
  completeBtn: {
    backgroundColor: colors.gotIt,
  },
  completeBtnText: {
    color: "#fff",
    fontSize: fonts.body,
    fontWeight: "700",
  },
});
