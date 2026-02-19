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
  lookupReviewWord,
  completeStory,
  suspendStory,
} from "../../lib/api";
import { StoryDetail, StoryWordMeta, WordLookupResult } from "../../lib/types";
import ActionMenu, { ExtraAction } from "../../lib/review/ActionMenu";
import WordInfoCard from "../../lib/review/WordInfoCard";
import { saveStoryLookups, getStoryLookups, clearStoryLookups } from "../../lib/offline-store";

type ViewMode = "arabic" | "english";

export default function StoryReadScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [story, setStory] = useState<StoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<ViewMode>("arabic");
  const [selectedWord, setSelectedWord] = useState<WordLookupResult | null>(null);
  const [selectedSurface, setSelectedSurface] = useState<string | null>(null);
  const [selectedPosition, setSelectedPosition] = useState<number | null>(null);
  const [cardLoading, setCardLoading] = useState(false);
  const [showCard, setShowCard] = useState(false);
  const [funcGloss, setFuncGloss] = useState<{ surface: string; gloss: string | null; label: string | null } | null>(null);
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
    setSelectedSurface(null);
    setSelectedPosition(null);
    setShowCard(false);
    setCardLoading(false);
    setFuncGloss(null);
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

      // Tapping an already-highlighted word: unselect (remove highlight + close card)
      if (lookedUp.has(word.position)) {
        const nextPositions = new Set(lookedUp);
        nextPositions.delete(word.position);
        setLookedUp(nextPositions);

        const nextLemmaIds = new Set(lookedUpLemmaIds);
        if (word.lemma_id != null) {
          const otherWithSameLemma = story.words.some(
            (w) => w.lemma_id === word.lemma_id && w.position !== word.position && nextPositions.has(w.position)
          );
          if (!otherWithSameLemma) nextLemmaIds.delete(word.lemma_id);
        }
        setLookedUpLemmaIds(nextLemmaIds);
        persistLookups(nextPositions, nextLemmaIds);

        if (selectedPosition === word.position) {
          setShowCard(false);
          setSelectedPosition(null);
          setSelectedWord(null);
          setFuncGloss(null);
        }
        return;
      }

      // New word tap: highlight + show card
      const nextPositions = new Set(lookedUp).add(word.position);
      const nextLemmaIds = new Set(lookedUpLemmaIds);
      if (word.lemma_id != null) {
        nextLemmaIds.add(word.lemma_id);
      }
      setSelectedPosition(word.position);
      setLookedUp(nextPositions);
      setLookedUpLemmaIds(nextLemmaIds);
      setSelectedSurface(word.surface_form);
      persistLookups(nextPositions, nextLemmaIds);

      if (word.lemma_id != null) {
        const requestId = ++lookupRequestRef.current;
        setCardLoading(true);
        setShowCard(true);
        setSelectedWord(null);
        setFuncGloss(null);
        try {
          const result = await lookupReviewWord(word.lemma_id);
          if (lookupRequestRef.current !== requestId) return;
          setSelectedWord(result);
        } catch {
          if (lookupRequestRef.current !== requestId) return;
          setSelectedWord(null);
          setShowCard(false);
        } finally {
          setCardLoading(false);
        }
      } else {
        // Function word, name, or unknown — show inline gloss
        setSelectedWord(null);
        setCardLoading(false);
        const label = word.name_type === "personal"
          ? "personal name"
          : word.name_type === "place"
          ? "place name"
          : word.is_function_word
          ? "function word"
          : null;
        setFuncGloss({
          surface: word.surface_form,
          gloss: word.gloss_en || null,
          label,
        });
        setShowCard(true);
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
            focusedLemmaId={showCard && selectedWord ? selectedWord.lemma_id : null}
            focusedLemmaAr={showCard ? selectedSurface : null}
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
            {(story.body_en || "No translation available.")
              .split(/(?<=\.)\s+/)
              .join("\n\n")}
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
          <Pressable
            style={[styles.secondaryBtn]}
            onPress={handleSuspend}
            disabled={submitting}
          >
            <Ionicons name="pause" size={14} color={colors.textSecondary} style={{ marginRight: 4 }} />
            <Text style={styles.secondaryBtnText}>Suspend</Text>
          </Pressable>
        </View>
      </ScrollView>

      {/* Word info card */}
      {showCard && (
        <View style={styles.cardContainer}>
          <Pressable style={styles.dismissBtn} onPress={() => { setShowCard(false); setSelectedPosition(null); setFuncGloss(null); }} hitSlop={12}>
            <Ionicons name="close" size={18} color={colors.textSecondary} />
          </Pressable>
          {funcGloss ? (
            <View style={styles.funcGlossCard}>
              <Text style={styles.funcGlossArabic}>{funcGloss.surface}</Text>
              {funcGloss.gloss && <Text style={styles.funcGlossEn}>{funcGloss.gloss}</Text>}
              {funcGloss.label && <Text style={styles.funcGlossLabel}>{funcGloss.label}</Text>}
            </View>
          ) : (
            <WordInfoCard
              loading={cardLoading}
              surfaceForm={selectedSurface}
              markState="missed"
              result={selectedWord}
              showMeaning={true}
              onShowMeaning={() => {}}
              reserveSpace={false}
              onNavigateToDetail={(lemmaId) => router.push(`/word/${lemmaId}`)}
            />
          )}
        </View>
      )}
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

  // Word info card
  cardContainer: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.bg,
    paddingHorizontal: 12,
    paddingTop: 4,
    paddingBottom: 8,
  },
  dismissBtn: {
    position: "absolute",
    top: 8,
    right: 8,
    zIndex: 10,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.surfaceLight,
    alignItems: "center",
    justifyContent: "center",
  },
  funcGlossCard: {
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 16,
    gap: 4,
  },
  funcGlossArabic: {
    fontSize: 24,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
  },
  funcGlossEn: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "600",
  },
  funcGlossLabel: {
    fontSize: 11,
    color: colors.textSecondary,
    fontStyle: "italic",
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
  secondaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  secondaryBtnText: {
    color: colors.textSecondary,
    fontSize: 15,
    fontWeight: "600",
  },
});
