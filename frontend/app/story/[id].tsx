import { useState, useEffect, useCallback, useRef } from "react";
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
import {
  getStoryDetail,
  lookupStoryWord,
  completeStory,
  skipStory,
  tooDifficultStory,
} from "../../lib/api";
import { StoryDetail, StoryWordMeta, StoryLookupResult } from "../../lib/types";
import AskAI from "../../lib/AskAI";
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
  const storyStartTime = useRef(Date.now());
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

      // Toggle off if already looked up
      if (lookedUp.has(word.position)) {
        const nextPositions = new Set(lookedUp);
        nextPositions.delete(word.position);
        setLookedUp(nextPositions);

        const nextLemmaIds = new Set(lookedUpLemmaIds);
        if (word.lemma_id != null) {
          const otherPositionsWithSameLemma = story.words.some(
            (w) => w.lemma_id === word.lemma_id && w.position !== word.position && nextPositions.has(w.position)
          );
          if (!otherPositionsWithSameLemma) {
            nextLemmaIds.delete(word.lemma_id);
            setLookedUpLemmaIds(nextLemmaIds);
          }
        }
        persistLookups(nextPositions, nextLemmaIds);

        if (selectedPosition === word.position) {
          setSelectedWord(null);
          setSelectedPosition(null);
        }
        return;
      }

      const nextPositions = new Set(lookedUp).add(word.position);
      const nextLemmaIds = new Set(lookedUpLemmaIds);
      if (word.lemma_id != null) {
        nextLemmaIds.add(word.lemma_id);
      }
      setSelectedPosition(word.position);
      setLookedUp(nextPositions);
      setLookedUpLemmaIds(nextLemmaIds);

      if (word.lemma_id != null) {
        try {
          const result = await lookupStoryWord(story.id, word.lemma_id, word.position);
          setSelectedWord(result);
          persistLookups(nextPositions, nextLemmaIds);
        } catch (e) {
          setSelectedWord({
            lemma_id: word.lemma_id,
            gloss_en: word.gloss_en || null,
            transliteration: null,
            root: null,
            pos: null,
          });
        }
      } else {
        setSelectedWord({
          lemma_id: null as any,
          gloss_en: word.gloss_en || (word.is_function_word ? "function word" : null),
          transliteration: null,
          root: null,
          pos: word.is_function_word ? "function word" : "not in vocabulary",
        });
        persistLookups(nextPositions, nextLemmaIds);
      }
    },
    [story, lookedUp, selectedPosition]
  );

  async function handleComplete() {
    if (!story || submitting) return;
    setSubmitting(true);
    try {
      const readingTimeMs = Date.now() - storyStartTime.current;
      await completeStory(story.id, Array.from(lookedUpLemmaIds), readingTimeMs);
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
      const readingTimeMs = Date.now() - storyStartTime.current;
      await skipStory(story.id, Array.from(lookedUpLemmaIds), readingTimeMs);
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
      const readingTimeMs = Date.now() - storyStartTime.current;
      await tooDifficultStory(story.id, Array.from(lookedUpLemmaIds), readingTimeMs);
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
            {buildFlatWordList(story.words).map((item) => {
              if (item.type === "break") {
                return (
                  <View key={item.key} style={styles.lineBreak} />
                );
              }
              const word = item.word!;
              const isLookedUp = lookedUp.has(word.position);
              const isSelected = selectedPosition === word.position;
              const isNewWord = !word.is_known && !word.is_function_word;

              return (
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
                  {isNewWord && !isLookedUp && (
                    <View style={styles.newWordDot} />
                  )}
                </Pressable>
              );
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
      <AskAI
        contextBuilder={() => {
          if (!story) return "";
          const parts = [`Story: ${story.title_en || story.title_ar || "Untitled"}`];
          const bodyPreview = story.body_ar.length > 500 ? story.body_ar.slice(0, 500) + "..." : story.body_ar;
          parts.push(`Arabic: ${bodyPreview}`);
          if (story.body_en) parts.push(`English: ${story.body_en}`);
          const looked = Array.from(lookedUpLemmaIds);
          if (looked.length > 0) parts.push(`Words looked up: ${looked.length}`);
          return parts.join("\n");
        }}
        screen="story"
      />
    </View>
  );
}

type FlatItem = { type: "word"; word: StoryWordMeta; key: string } | { type: "break"; key: string; word?: undefined };

function buildFlatWordList(words: StoryWordMeta[]): FlatItem[] {
  const items: FlatItem[] = [];
  let lastSentenceIndex = -1;
  for (const w of words) {
    if (lastSentenceIndex >= 0 && w.sentence_index !== lastSentenceIndex) {
      items.push({ type: "break", key: `break-${lastSentenceIndex}` });
    }
    items.push({ type: "word", word: w, key: `w-${w.position}` });
    lastSentenceIndex = w.sentence_index;
  }
  return items;
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
    flexDirection: "row-reverse",
    flexWrap: "wrap",
    alignItems: "flex-start",
    gap: 6,
    rowGap: 14,
  },
  lineBreak: {
    width: "100%",
    height: 12,
  },
  wordChip: {
    paddingVertical: 6,
    paddingHorizontal: 4,
    borderRadius: 4,
  },
  storyWord: {
    fontSize: 28,
    lineHeight: 36,
    color: colors.arabic,
  },
  newWordDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.textSecondary,
    alignSelf: "center",
    opacity: 0.4,
    marginTop: 2,
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
    fontFamily: fontFamily.arabic,
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
