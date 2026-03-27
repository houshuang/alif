import { useState, useEffect, useCallback, useRef } from "react";
import {
  View,
  Text,
  ScrollView,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Modal,
} from "react-native";
import { useLocalSearchParams, useRouter, useNavigation } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, fontFamily } from "../../lib/theme";
import {
  getStoryDetail,
  getPretestWords,
  lookupReviewWord,
  completeStory,
  suspendStory,
  markStoryHeard,
} from "../../lib/api";
import { Audio } from "expo-av";
import { StoryDetail, StoryWordMeta, WordLookupResult, PretestWord } from "../../lib/types";
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
  const [isPlaying, setIsPlaying] = useState(false);
  const [showPretest, setShowPretest] = useState(false);
  const [pretestWords, setPretestWords] = useState<PretestWord[]>([]);
  const [pretestIdx, setPretestIdx] = useState(0);
  const [pretestRevealed, setPretestRevealed] = useState(false);
  const pretestTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);
  const storyStartTime = useRef(Date.now());
  const lookupRequestRef = useRef(0);
  const router = useRouter();
  const navigation = useNavigation();

  useEffect(() => {
    navigation.setOptions({
      headerLeft: () => (
        <Pressable onPress={() => router.replace("/stories")} style={{ paddingLeft: 12 }}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </Pressable>
      ),
    });
  }, [navigation, router]);

  useEffect(() => {
    if (id) loadStory(Number(id));
  }, [id]);

  async function loadStory(storyId: number) {
    setLoading(true);
    setViewMode("arabic");
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

  async function openPretest() {
    if (!id) return;
    try {
      const words = await getPretestWords(Number(id));
      if (words.length === 0) return;
      setPretestWords(words);
      setPretestIdx(0);
      setPretestRevealed(false);
      setShowPretest(true);
    } catch (e) {
      console.error("Failed to fetch pretest words:", e);
    }
  }

  // Auto-reveal after 2s when a new pretest word is shown (the failed-attempt window)
  useEffect(() => {
    if (!showPretest || pretestRevealed || pretestIdx >= pretestWords.length) return;
    pretestTimerRef.current = setTimeout(() => setPretestRevealed(true), 2000);
    return () => {
      if (pretestTimerRef.current) clearTimeout(pretestTimerRef.current);
    };
  }, [showPretest, pretestIdx, pretestRevealed, pretestWords.length]);

  function advancePretest() {
    if (pretestTimerRef.current) clearTimeout(pretestTimerRef.current);
    setPretestIdx(pretestIdx + 1);
    setPretestRevealed(false);
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
        let label: string | null = null;
        if (word.name_type === "personal") label = "personal name";
        else if (word.name_type === "place") label = "place name";
        else if (word.is_function_word) label = "function word";
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
      router.replace("/stories");
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
      router.replace("/stories");
    } catch (e) {
      console.error("Failed to suspend story:", e);
      setSubmitting(false);
    }
  }

  async function handlePlayAudio() {
    if (!story?.audio_filename) return;
    try {
      if (isPlaying && soundRef.current) {
        await soundRef.current.stopAsync();
        await soundRef.current.unloadAsync();
        soundRef.current = null;
        setIsPlaying(false);
        return;
      }
      const { BASE_URL } = await import("../../lib/api");
      const { sound } = await Audio.Sound.createAsync(
        { uri: `${BASE_URL}/api/stories/${story.id}/audio` },
        { shouldPlay: true },
      );
      soundRef.current = sound;
      setIsPlaying(true);
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          setIsPlaying(false);
          sound.unloadAsync();
          soundRef.current = null;
        }
      });
    } catch (e) {
      console.error("Failed to play story audio:", e);
      setIsPlaying(false);
    }
  }

  async function handleMarkHeard() {
    if (!story) return;
    try {
      const result = await markStoryHeard(story.id);
      console.log("Marked heard:", result.words_heard, "words");
    } catch (e) {
      console.error("Failed to mark story heard:", e);
    }
  }

  // Cleanup audio on unmount
  useEffect(() => {
    return () => {
      if (soundRef.current) {
        soundRef.current.unloadAsync().catch(() => {});
      }
    };
  }, []);

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
    ...(story.audio_filename ? [{
      icon: isPlaying ? "stop-circle-outline" as const : "play-circle-outline" as const,
      label: isPlaying ? "Stop Audio" : "Play Audio",
      onPress: handlePlayAudio,
    }] : []),
    {
      icon: "ear-outline" as const,
      label: "Mark as Heard",
      onPress: handleMarkHeard,
    },
    {
      icon: "pause-circle-outline" as const,
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
          {story.audio_filename && (
            <Pressable onPress={handlePlayAudio} hitSlop={8} style={{ marginRight: 8 }}>
              <Ionicons
                name={isPlaying ? "stop-circle" : "play-circle"}
                size={28}
                color={isPlaying ? colors.missed : colors.accent}
              />
            </Pressable>
          )}
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

      {/* Reading Readiness banner — shown when cold unknowns exist and story not complete */}
      {story.status === "active" && (story.cold_unknown_count ?? 0) > 0 && (
        <View style={styles.readinessBanner}>
          <View style={styles.readinessInfo}>
            <Text style={styles.readinessPct}>
              {Math.round(story.reading_readiness_pct ?? story.readiness_pct)}% ready
            </Text>
            <Text style={styles.readinessBullet}> · </Text>
            <Text style={styles.coldBadge}>{story.cold_unknown_count} new</Text>
            {(story.warm_unknown_count ?? 0) > 0 && (
              <>
                <Text style={styles.readinessBullet}> · </Text>
                <Text style={styles.warmBadge}>{story.warm_unknown_count} familiar root</Text>
              </>
            )}
          </View>
          <Pressable style={styles.previewBtn} onPress={openPretest}>
            <Ionicons name="eye-outline" size={14} color={colors.accent} style={{ marginRight: 4 }} />
            <Text style={styles.previewBtnText}>Preview</Text>
          </Pressable>
        </View>
      )}

      <ScrollView
        style={styles.scrollArea}
        contentContainerStyle={styles.scrollContent}
      >
        {viewMode === "arabic" ? (
          <View style={styles.storyFlow}>
            {story.words.map((word) => {
              const isLookedUp = lookedUp.has(word.position);
              const isSelected = selectedPosition === word.position;
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

      {/* Pretest flash modal */}
      <Modal visible={showPretest} transparent animationType="fade">
        <View style={styles.pretestOverlay}>
          <View style={styles.pretestCard}>
            <Text style={styles.pretestLabel}>
              {pretestIdx >= pretestWords.length ? "Preview complete" : `Word ${pretestIdx + 1} of ${pretestWords.length}`}
            </Text>

            {pretestIdx >= pretestWords.length ? (
              <>
                <Text style={styles.pretestDoneText}>
                  You've previewed {pretestWords.length} new {pretestWords.length === 1 ? "word" : "words"}.{"\n"}
                  Watch for them as you read.
                </Text>
                <Pressable
                  style={styles.pretestNextBtn}
                  onPress={() => setShowPretest(false)}
                >
                  <Text style={styles.pretestNextText}>Start reading</Text>
                </Pressable>
              </>
            ) : (
              <>
                <Text style={styles.pretestArabic}>
                  {pretestWords[pretestIdx]?.arabic}
                </Text>

                {pretestRevealed ? (
                  <>
                    <Text style={styles.pretestGloss}>
                      {pretestWords[pretestIdx]?.gloss_en}
                    </Text>
                    {pretestWords[pretestIdx]?.root_ar && (
                      <Text style={styles.pretestRoot}>
                        root: {pretestWords[pretestIdx].root_ar}
                      </Text>
                    )}
                    <Pressable style={styles.pretestNextBtn} onPress={advancePretest}>
                      <Text style={styles.pretestNextText}>
                        {pretestIdx + 1 >= pretestWords.length ? "Done" : "Next"}
                      </Text>
                    </Pressable>
                  </>
                ) : (
                  <>
                    <Text style={styles.pretestHint}>
                      Do you recognise this word?
                    </Text>
                    <Pressable
                      style={[styles.pretestNextBtn, styles.pretestRevealBtn]}
                      onPress={() => {
                        if (pretestTimerRef.current) clearTimeout(pretestTimerRef.current);
                        setPretestRevealed(true);
                      }}
                    >
                      <Text style={styles.pretestRevealText}>Show meaning</Text>
                    </Pressable>
                  </>
                )}
              </>
            )}

            <Pressable
              style={styles.pretestSkip}
              onPress={() => setShowPretest(false)}
            >
              <Text style={styles.pretestSkipText}>Skip preview</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

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
              onNavigateToPattern={(wazn) => router.push(`/pattern/${encodeURIComponent(wazn)}`)}
              onNavigateToRoot={(rootId) => router.push(`/root/${rootId}`)}
              surfaceTranslit={null}
            />
          )}
        </View>
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

  // Reading Readiness banner
  readinessBanner: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 7,
    backgroundColor: colors.surfaceLight,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  readinessInfo: {
    flexDirection: "row",
    alignItems: "center",
    flexShrink: 1,
  },
  readinessPct: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  readinessBullet: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
  coldBadge: {
    fontSize: fonts.caption,
    color: colors.missed,
    fontWeight: "600",
  },
  warmBadge: {
    fontSize: fonts.caption,
    color: colors.stateLearning,
    fontWeight: "500",
  },
  previewBtn: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 4,
    paddingHorizontal: 10,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.accent,
    marginLeft: 8,
  },
  previewBtnText: {
    fontSize: fonts.caption,
    color: colors.accent,
    fontWeight: "600",
  },

  // Pretest flash modal
  pretestOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.75)",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  pretestCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 28,
    width: "100%",
    alignItems: "center",
  },
  pretestLabel: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 24,
  },
  pretestArabic: {
    fontSize: 42,
    color: colors.text,
    fontFamily: fontFamily.arabic,
    textAlign: "center",
    marginBottom: 20,
    lineHeight: 64,
  },
  pretestHint: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginBottom: 20,
    textAlign: "center",
  },
  pretestGloss: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "600",
    textAlign: "center",
    marginBottom: 8,
  },
  pretestRoot: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginBottom: 24,
  },
  pretestDoneText: {
    fontSize: fonts.body,
    color: colors.textSecondary,
    textAlign: "center",
    lineHeight: 24,
    marginBottom: 28,
  },
  pretestNextBtn: {
    backgroundColor: colors.accent,
    paddingVertical: 12,
    paddingHorizontal: 32,
    borderRadius: 10,
    marginTop: 8,
    marginBottom: 16,
  },
  pretestRevealBtn: {
    backgroundColor: colors.surfaceLight,
  },
  pretestNextText: {
    fontSize: fonts.body,
    color: "#fff",
    fontWeight: "700",
  },
  pretestRevealText: {
    fontSize: fonts.body,
    color: colors.text,
    fontWeight: "700",
  },
  pretestSkip: {
    paddingVertical: 8,
  },
  pretestSkipText: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
});
