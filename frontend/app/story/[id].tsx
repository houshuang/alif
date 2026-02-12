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
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, fontFamily } from "../../lib/theme";
import {
  getStoryDetail,
  lookupStoryWord,
  completeStory,
  skipStory,
  tooDifficultStory,
  suspendStory,
} from "../../lib/api";
import { StoryDetail, StoryWordMeta, StoryLookupResult } from "../../lib/types";
import ActionMenu, { ExtraAction } from "../../lib/review/ActionMenu";
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
  const lookupRequestRef = useRef(0);
  const router = useRouter();

  useEffect(() => {
    if (id) loadStory(Number(id));
  }, [id]);

  async function loadStory(storyId: number) {
    setLoading(true);
    lookupRequestRef.current += 1;
    setSubmitting(false);
    setSelectedWord(null);
    setSelectedPosition(null);
    setLookedUp(new Set());
    setLookedUpLemmaIds(new Set());
    storyStartTime.current = Date.now();
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

      if (lookedUp.has(word.position)) {
        lookupRequestRef.current += 1;
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

      const requestId = ++lookupRequestRef.current;
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
          if (lookupRequestRef.current !== requestId) return;
          setSelectedWord(result);
          persistLookups(nextPositions, nextLemmaIds);
        } catch {
          if (lookupRequestRef.current !== requestId) return;
          setSelectedWord({
            lemma_id: word.lemma_id,
            gloss_en: word.gloss_en || null,
            transliteration: null,
            root: null,
            pos: null,
          });
          persistLookups(nextPositions, nextLemmaIds);
        }
      } else {
        if (lookupRequestRef.current !== requestId) return;
        const posLabel = word.name_type === "personal"
          ? "personal name"
          : word.name_type === "place"
          ? "place name"
          : word.is_function_word
          ? "function word"
          : "not in vocabulary";
        setSelectedWord({
          lemma_id: null as any,
          gloss_en: word.gloss_en || (word.is_function_word ? posLabel : null),
          transliteration: null,
          root: null,
          pos: posLabel,
        });
        persistLookups(nextPositions, nextLemmaIds);
      }
    },
    [story, lookedUp, lookedUpLemmaIds, selectedPosition, id]
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
      console.error("Failed to mark story too difficult:", e);
      setSubmitting(false);
    }
  }

  async function handleSuspend() {
    if (!story || submitting) return;
    setSubmitting(true);
    try {
      await suspendStory(story.id);
      router.back();
    } catch (e) {
      console.error("Failed to suspend story:", e);
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

  const lookupCount = lookedUp.size;
  const totalWords = story.words.filter((w) => !w.is_function_word).length;

  const storyExtraActions: ExtraAction[] = [
    {
      icon: "pause-circle-outline",
      label: "Suspend Story",
      onPress: handleSuspend,
    },
  ];

  return (
    <View style={styles.container}>
      {/* Toggle + stats bar + action menu */}
      <View style={styles.headerBar}>
        <View style={styles.toggleContainer}>
          <Pressable
            style={[styles.toggleBtn, viewMode === "arabic" && styles.toggleBtnActive]}
            onPress={() => setViewMode("arabic")}
          >
            <Text style={[styles.toggleText, viewMode === "arabic" && styles.toggleTextActive]}>
              Arabic
            </Text>
          </Pressable>
          <Pressable
            style={[styles.toggleBtn, viewMode === "english" && styles.toggleBtnActive]}
            onPress={() => setViewMode("english")}
          >
            <Text style={[styles.toggleText, viewMode === "english" && styles.toggleTextActive]}>
              English
            </Text>
          </Pressable>
        </View>
        <View style={styles.headerRight}>
          {lookupCount > 0 && (
            <Text style={styles.lookupCountBadge}>
              {lookupCount} looked up
            </Text>
          )}
          <ActionMenu
            focusedLemmaId={selectedWord?.lemma_id ?? null}
            focusedLemmaAr={selectedPosition !== null ? (story?.words.find((w) => w.position === selectedPosition)?.surface_form ?? null) : null}
            sentenceId={null}
            askAIContextBuilder={() => {
              if (!story) return "";
              const parts = [`Story: ${story.title_en || story.title_ar || "Untitled"}`];
              const bodyPreview = story.body_ar.length > 500 ? story.body_ar.slice(0, 500) + "..." : story.body_ar;
              parts.push(`Arabic: ${bodyPreview}`);
              if (story.body_en) parts.push(`English: ${story.body_en}`);
              const looked = Array.from(lookedUpLemmaIds);
              if (looked.length > 0) parts.push(`Words looked up: ${looked.length}`);
              return parts.join("\n");
            }}
            askAIScreen="story"
            extraActions={storyExtraActions}
          />
        </View>
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

        {/* Actions at end of scroll */}
        <View style={styles.inlineActions}>
          <Pressable
            style={[styles.inlineBtn, styles.completeBtn]}
            onPress={handleComplete}
            disabled={submitting}
          >
            {submitting ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <>
                <Ionicons name="checkmark" size={18} color="#fff" />
                <Text style={styles.completeBtnText}>Complete</Text>
              </>
            )}
          </Pressable>
          <View style={styles.secondaryActions}>
            <Pressable
              style={[styles.secondaryBtn]}
              onPress={handleSkip}
              disabled={submitting}
            >
              <Text style={styles.secondaryBtnText}>Skip</Text>
            </Pressable>
            {story.source === "imported" && (
              <Pressable
                style={[styles.secondaryBtn]}
                onPress={handleTooDifficult}
                disabled={submitting}
              >
                <Text style={[styles.secondaryBtnText, { color: colors.stateLearning }]}>Too Hard</Text>
              </Pressable>
            )}
          </View>
        </View>
      </ScrollView>

      {/* Lookup panel */}
      <View style={[styles.lookupPanel, selectedWord && styles.lookupPanelActive]}>
        {selectedWord ? (
          <View style={styles.lookupContent}>
            <View style={styles.lookupMain}>
              <Text style={styles.lookupArabic}>
                {story.words.find((w) => w.position === selectedPosition)
                  ?.surface_form || ""}
              </Text>
              <View style={styles.lookupDivider} />
              <Text style={styles.lookupGloss}>
                {selectedWord.gloss_en || "Unknown"}
              </Text>
            </View>
            <View style={styles.lookupMeta}>
              {selectedWord.transliteration ? (
                <Text style={styles.lookupTranslit}>
                  {selectedWord.transliteration}
                </Text>
              ) : null}
              {selectedWord.root ? (
                <View style={styles.lookupRootBadge}>
                  <Text style={styles.lookupRootText}>{selectedWord.root}</Text>
                </View>
              ) : null}
              {selectedWord.pos ? (
                <Text style={styles.lookupPos}>{selectedWord.pos}</Text>
              ) : null}
            </View>
          </View>
        ) : (
          <View style={styles.lookupEmpty}>
            <Ionicons name="hand-left-outline" size={16} color={colors.textSecondary} style={{ opacity: 0.5, marginRight: 8 }} />
            <Text style={styles.lookupHint}>
              Tap any word to look it up
            </Text>
          </View>
        )}
      </View>
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

  // Header bar with toggle + stats
  headerBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  toggleContainer: {
    flexDirection: "row",
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    padding: 3,
  },
  toggleBtn: {
    paddingVertical: 7,
    paddingHorizontal: 20,
    borderRadius: 8,
  },
  toggleBtnActive: {
    backgroundColor: colors.accent,
  },
  toggleText: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  toggleTextActive: {
    color: "#fff",
  },
  headerRight: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  lookupCountBadge: {
    fontSize: fonts.caption,
    color: colors.missed,
    fontWeight: "600",
  },

  // Scroll area
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
    rowGap: 10,
  },
  lineBreak: {
    width: "100%",
    height: 16,
  },

  // Word chips
  wordChip: {
    paddingVertical: 4,
    paddingHorizontal: 6,
    borderRadius: 6,
  },
  storyWord: {
    fontSize: 30,
    lineHeight: 46,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
  },
  newWordDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
    backgroundColor: colors.accent,
    alignSelf: "center",
    opacity: 0.6,
    marginTop: 1,
  },
  lookedUpChip: {
    backgroundColor: colors.missed + "15",
    borderRadius: 6,
  },
  selectedChip: {
    backgroundColor: colors.missed + "28",
  },
  lookedUpWord: {
    color: colors.missed,
  },
  selectedWord: {
    color: colors.missed,
  },

  // English view
  englishText: {
    fontSize: 18,
    color: colors.text,
    lineHeight: 30,
  },

  // Lookup panel
  lookupPanel: {
    minHeight: 60,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    justifyContent: "center",
    paddingHorizontal: 20,
    paddingVertical: 12,
  },
  lookupPanelActive: {
    minHeight: 80,
  },
  lookupContent: {
    alignItems: "center",
    width: "100%",
  },
  lookupMain: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    marginBottom: 6,
  },
  lookupDivider: {
    width: 1,
    height: 24,
    backgroundColor: colors.border,
  },
  lookupArabic: {
    fontSize: 26,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    fontWeight: "600",
  },
  lookupGloss: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
  },
  lookupMeta: {
    flexDirection: "row",
    gap: 12,
    alignItems: "center",
    justifyContent: "center",
    flexWrap: "wrap",
  },
  lookupTranslit: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    fontStyle: "italic",
  },
  lookupRootBadge: {
    backgroundColor: colors.accent + "20",
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  lookupRootText: {
    fontSize: fonts.small,
    color: colors.accent,
    fontWeight: "600",
    fontFamily: fontFamily.arabic,
  },
  lookupPos: {
    fontSize: fonts.small,
    color: colors.textSecondary,
  },
  lookupEmpty: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
  },
  lookupHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
  },

  // Inline actions (at end of scroll)
  inlineActions: {
    marginTop: 40,
    marginBottom: 20,
    alignItems: "center",
    gap: 16,
  },
  inlineBtn: {
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 12,
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "center",
    gap: 6,
    alignSelf: "stretch",
  },
  completeBtn: {
    backgroundColor: colors.gotIt,
  },
  completeBtnText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "700",
  },
  secondaryActions: {
    flexDirection: "row",
    gap: 24,
    justifyContent: "center",
  },
  secondaryBtn: {
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  secondaryBtnText: {
    color: colors.textSecondary,
    fontSize: 15,
    fontWeight: "600",
  },
});
