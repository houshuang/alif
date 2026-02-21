import { useState, useEffect, useRef } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Animated,
} from "react-native";
import { colors, fonts, fontFamily } from "../lib/theme";
import {
  BASE_URL,
  getNextWords,
  introduceWord,
  neverShowWord,
  getAnalytics,
  generateUuid,
} from "../lib/api";
import { LearnCandidate, WordForms, Analytics } from "../lib/types";
import { posLabel, FormsRow, GrammarRow, PlayButton } from "../lib/WordCardComponents";
import { getFrequencyBand, getCefrColor } from "../lib/frequency";
import ActionMenu from "../lib/review/ActionMenu";
import { TOPIC_LABELS } from "../lib/topic-labels";

type Phase = "loading" | "pick" | "done";

interface IntroducedWord {
  candidate: LearnCandidate;
}

export default function LearnScreen() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [candidates, setCandidates] = useState<LearnCandidate[]>([]);
  const [activeTopic, setActiveTopic] = useState<string | null>(null);
  const [pickIndex, setPickIndex] = useState(0);
  const [introduced, setIntroduced] = useState<IntroducedWord[]>([]);
  const sessionId = useRef(generateUuid());

  useEffect(() => {
    loadCandidates();
  }, []);

  async function loadCandidates() {
    setPhase("loading");
    try {
      const data = await getNextWords(5);
      setCandidates(data.words);
      setActiveTopic(data.active_topic);
      setPickIndex(0);
      setIntroduced([]);
      setPhase("pick");
    } catch (e) {
      console.error("Failed to load candidates:", e);
      setCandidates([]);
      setPhase("pick");
    }
  }

  async function handleLearn() {
    const candidate = candidates[pickIndex];
    try {
      await introduceWord(candidate.lemma_id);
      setIntroduced((prev) => [...prev, { candidate }]);
    } catch (e) {
      console.error("Failed to introduce word:", e);
    }
    advancePick();
  }

  function handleSkip() {
    advancePick();
  }

  async function handleSuspend() {
    const candidate = candidates[pickIndex];
    try {
      await neverShowWord(candidate.lemma_id);
    } catch (e) {
      console.error("Failed to suspend word:", e);
    }
    advancePick();
  }

  function advancePick() {
    const next = pickIndex + 1;
    if (next >= candidates.length) {
      setPhase("done");
    } else {
      setPickIndex(next);
    }
  }

  function resetSession() {
    sessionId.current = generateUuid();
    setIntroduced([]);
    setPickIndex(0);
    loadCandidates();
  }

  // --- Loading ---
  if (phase === "loading") {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
        <Text style={styles.loadingText}>Finding words for you...</Text>
      </View>
    );
  }

  // --- Pick Phase: one word at a time ---
  if (phase === "pick") {
    if (candidates.length === 0) {
      return (
        <View style={styles.centered}>
          <Text style={styles.emptyText}>
            No new words available right now.
          </Text>
          <Pressable style={styles.primaryButton} onPress={resetSession}>
            <Text style={styles.primaryButtonText}>Refresh</Text>
          </Pressable>
        </View>
      );
    }

    const topicLabel = activeTopic ? (TOPIC_LABELS[activeTopic] || activeTopic) : null;
    const c = candidates[pickIndex];
    const buildLearnContext = () => {
      const parts = [`Word: ${c.lemma_ar} (${c.gloss_en})`];
      if (c.pos) parts.push(`POS: ${c.pos}`);
      if (c.root) parts.push(`Root: ${c.root}${c.root_meaning ? ` (${c.root_meaning})` : ""}`);
      if (c.transliteration) parts.push(`Transliteration: ${c.transliteration}`);
      if (c.forms_json) parts.push(`Forms: ${JSON.stringify(c.forms_json)}`);
      if (c.grammar_details && c.grammar_details.length > 0) {
        parts.push(`Grammar: ${c.grammar_details.map((g) => g.label_en).join(", ")}`);
      }
      if (c.wazn) parts.push(`Pattern: ${c.wazn}${c.wazn_meaning ? ` (${c.wazn_meaning})` : ""}`);
      return parts.join("\n");
    };
    return (
      <View style={styles.centered}>
        {topicLabel && (
          <View style={styles.topicBadge}>
            <Text style={styles.topicBadgeText}>{topicLabel}</Text>
          </View>
        )}
        <View style={styles.progressContainer}>
          <Text style={styles.progressText}>
            Word {pickIndex + 1} of {candidates.length}
          </Text>
        </View>
        <View style={styles.progressTrack}>
          <View
            style={[
              styles.progressFill,
              { width: `${((pickIndex + 1) / candidates.length) * 100}%` },
            ]}
          />
        </View>

        <View style={styles.card}>
          {c.story_title && (
            <View style={styles.storyBadge}>
              <Text style={styles.storyBadgeText}>From: {c.story_title}</Text>
            </View>
          )}
          <View style={styles.wordHeader}>
            <Text style={styles.wordArabic}>{c.lemma_ar}</Text>
            <PlayButton audioUrl={c.audio_url} word={c.lemma_ar} />
          </View>
          <Text style={styles.wordEnglish}>{c.gloss_en}</Text>
          {c.transliteration && (
            <Text style={styles.wordTranslit}>{c.transliteration}</Text>
          )}
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 2 }}>
            <Text style={styles.wordPos}>
              {posLabel(c.pos, c.forms_json)}
            </Text>
            {c.cefr_level && (
              <View style={{ backgroundColor: getCefrColor(c.cefr_level), borderRadius: 4, paddingHorizontal: 6, paddingVertical: 1 }}>
                <Text style={{ color: "#fff", fontSize: 11, fontWeight: "700" }}>{c.cefr_level}</Text>
              </View>
            )}
            {c.frequency_rank != null && (
              <Text style={{ color: getFrequencyBand(c.frequency_rank).color, fontSize: 12 }}>
                #{c.frequency_rank.toLocaleString()}
              </Text>
            )}
            {c.word_category && (
              <View style={{ backgroundColor: "rgba(243, 156, 18, 0.2)", borderRadius: 4, paddingHorizontal: 6, paddingVertical: 1 }}>
                <Text style={{ color: "#f39c12", fontSize: 11, fontWeight: "600" }}>
                  {c.word_category === "proper_name" ? "Name" : "Sound"}
                </Text>
              </View>
            )}
          </View>
          <FormsRow pos={c.pos} forms={c.forms_json} />
          <GrammarRow details={c.grammar_details} />

          {c.wazn ? (
            <View style={styles.patternSection}>
              <Text style={styles.patternLabel}>
                {c.wazn}
                {c.wazn_meaning ? <Text style={styles.patternMeaning}> — {c.wazn_meaning}</Text> : null}
              </Text>
              {c.root && (
                <Text style={styles.patternDecomposition}>
                  {c.wazn} + {c.root}
                  {c.root_meaning ? ` (${c.root_meaning})` : ""}
                </Text>
              )}
              {c.score_breakdown.known_siblings > 0 && (
                <Text style={styles.knownSiblingsNote}>
                  {c.score_breakdown.known_siblings} known word{c.score_breakdown.known_siblings !== 1 ? "s" : ""} from root {c.root}
                </Text>
              )}
            </View>
          ) : c.root ? (
            <View style={styles.rootInfo}>
              <View style={styles.rootRow}>
                <Text style={styles.rootLetters}>{c.root}</Text>
                {c.root_meaning && <Text style={styles.rootMeaning}>{c.root_meaning}</Text>}
              </View>
              {c.score_breakdown.total_siblings > 0 && (
                <Text style={styles.rootSiblings}>
                  {c.score_breakdown.known_siblings}/{c.score_breakdown.total_siblings}
                </Text>
              )}
            </View>
          ) : null}

          {c.etymology_json?.derivation && (
            <View style={styles.infoHighlight}>
              <Text style={styles.infoHighlightText}>
                {c.etymology_json.pattern ? `${c.etymology_json.pattern}: ` : ""}
                {c.etymology_json.derivation}
              </Text>
            </View>
          )}
          {c.memory_hooks_json?.mnemonic && (
            <View style={styles.infoHighlight}>
              <Text style={styles.infoHighlightText}>
                {c.memory_hooks_json.mnemonic}
              </Text>
            </View>
          )}
        </View>

        <View style={styles.actionColumn}>
          <Pressable style={styles.primaryButton} onPress={handleLearn}>
            <Text style={styles.primaryButtonText}>Learn</Text>
          </Pressable>
          <Pressable style={styles.skipButton} onPress={handleSkip}>
            <Text style={styles.skipButtonText}>Skip</Text>
          </Pressable>
          <Pressable style={styles.suspendButton} onPress={handleSuspend}>
            <Text style={styles.suspendButtonText}>Suspend</Text>
          </Pressable>
        </View>
        <ActionMenu
          focusedLemmaId={c.lemma_id}
          focusedLemmaAr={c.lemma_ar}
          sentenceId={null}
          askAIContextBuilder={buildLearnContext}
          askAIScreen="learn"
        />
      </View>
    );
  }

  // --- Done Phase ---
  return (
    <LearnDoneScreen
      introduced={introduced}
      onReset={resetSession}
    />
  );
}

function LearnSparkle({ count = 6 }: { count?: number }) {
  const anims = useRef(
    Array.from({ length: count }, () => ({
      opacity: new Animated.Value(0),
      translateY: new Animated.Value(0),
      scale: new Animated.Value(0.5),
    }))
  ).current;

  useEffect(() => {
    const animations = anims.map((a, i) =>
      Animated.sequence([
        Animated.delay(i * 80),
        Animated.parallel([
          Animated.timing(a.opacity, { toValue: 1, duration: 300, useNativeDriver: true }),
          Animated.timing(a.scale, { toValue: 1.3, duration: 300, useNativeDriver: true }),
          Animated.timing(a.translateY, { toValue: -(15 + Math.random() * 25), duration: 600, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.timing(a.opacity, { toValue: 0, duration: 400, useNativeDriver: true }),
          Animated.timing(a.scale, { toValue: 0.6, duration: 400, useNativeDriver: true }),
        ]),
      ])
    );
    Animated.parallel(animations).start();
  }, []);

  const chars = ["\u2728", "\u2B50", "\u2728", "\u2B50"];

  return (
    <View style={styles.sparkleContainer}>
      {anims.map((a, i) => {
        const angle = (i / count) * 2 * Math.PI;
        const radius = 30 + (i % 3) * 8;
        const left = 40 + Math.cos(angle) * radius;
        const top = 40 + Math.sin(angle) * radius;
        return (
          <Animated.Text
            key={i}
            style={{
              position: "absolute",
              left,
              top,
              fontSize: 11 + (i % 3) * 3,
              opacity: a.opacity,
              transform: [{ translateY: a.translateY }, { scale: a.scale }],
            }}
          >
            {chars[i % chars.length]}
          </Animated.Text>
        );
      })}
    </View>
  );
}

function LearnDoneScreen({
  introduced,
  onReset,
}: {
  introduced: IntroducedWord[];
  onReset: () => void;
}) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const fadeAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    getAnalytics()
      .then((data) => {
        setAnalytics(data);
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 400,
          useNativeDriver: true,
        }).start();
      })
      .catch(() => {});
  }, []);

  return (
    <View style={styles.centered}>
      {introduced.length > 0 && (
        <View style={styles.doneCelebration}>
          <LearnSparkle />
          <Text style={styles.doneCelebrationIcon}>{"\u2728"}</Text>
        </View>
      )}

      <Text style={styles.doneTitle}>
        {introduced.length > 0 ? "Words Learned!" : "Session Complete"}
      </Text>

      {introduced.length > 0 ? (
        <Text style={styles.doneSubtitle}>
          {introduced.length} new word{introduced.length !== 1 ? "s" : ""}{" "}
          learned
        </Text>
      ) : (
        <Text style={styles.doneSubtitle}>No words learned this session</Text>
      )}

      {analytics && (
        <Animated.View style={[styles.doneProgress, { opacity: fadeAnim }]}>
          <Text style={styles.doneProgressTotal}>
            {analytics.cefr.known_words} words known
          </Text>
          <Text style={styles.doneProgressLevel}>
            {analytics.cefr.sublevel} reading level
          </Text>
          {analytics.cefr.words_to_next !== null && analytics.cefr.words_to_next <= 30 && (
            <Text style={styles.doneProgressNext}>
              {analytics.cefr.words_to_next} words to {analytics.cefr.next_level}
            </Text>
          )}
        </Animated.View>
      )}

      <Text style={styles.doneNote}>
        These words will now appear in your review sessions.
      </Text>

      <Pressable style={styles.primaryButton} onPress={onReset}>
        <Text style={styles.primaryButtonText}>Learn More Words</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  centered: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  loadingText: {
    color: colors.textSecondary,
    fontSize: 16,
    marginTop: 12,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: 18,
    textAlign: "center",
    marginBottom: 20,
  },

  topicBadge: {
    backgroundColor: colors.surfaceLight,
    paddingHorizontal: 14,
    paddingVertical: 5,
    borderRadius: 12,
    marginBottom: 12,
  },
  topicBadgeText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "600",
  },
  progressContainer: {
    width: "100%",
    maxWidth: 500,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 6,
  },
  progressText: {
    color: colors.textSecondary,
    fontSize: fonts.small,
  },
  progressTrack: {
    width: "100%",
    maxWidth: 500,
    height: 4,
    backgroundColor: colors.surfaceLight,
    borderRadius: 2,
    overflow: "hidden",
    marginBottom: 20,
  },
  progressFill: {
    height: "100%",
    backgroundColor: colors.accent,
    borderRadius: 2,
  },

  storyBadge: {
    backgroundColor: colors.accent + "20",
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    marginBottom: 12,
  },
  storyBadgeText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "600",
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 32,
    width: "100%",
    maxWidth: 500,
    alignItems: "center",
    marginBottom: 24,
  },
  wordHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginBottom: 12,
  },
  wordArabic: {
    fontSize: 44,
    color: colors.arabic,
    fontWeight: "700",
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
  },
  wordEnglish: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 6,
    textAlign: "center",
  },
  wordTranslit: {
    fontSize: 16,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginBottom: 4,
  },
  wordPos: {
    fontSize: 13,
    color: colors.textSecondary,
    marginBottom: 8,
  },

  patternSection: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 12,
    width: "100%",
    alignItems: "center",
    gap: 4,
  },
  patternLabel: {
    fontSize: 15,
    color: colors.accent,
    fontWeight: "700",
  },
  patternMeaning: {
    fontWeight: "400",
    color: colors.textSecondary,
  },
  patternDecomposition: {
    fontSize: 14,
    color: colors.text,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },
  knownSiblingsNote: {
    fontSize: 12,
    color: colors.textSecondary,
    marginTop: 2,
  },

  rootInfo: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 12,
    width: "100%",
    alignItems: "center",
  },
  rootRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  rootLetters: {
    fontSize: 16,
    color: colors.accent,
    fontWeight: "600",
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },
  rootMeaning: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  rootSiblings: {
    fontSize: 12,
    color: colors.textSecondary,
    marginTop: 2,
  },
  infoHighlight: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    width: "100%",
    marginTop: 8,
  },
  infoHighlightText: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
    textAlign: "center",
  },

  actionColumn: {
    width: "100%",
    maxWidth: 500,
    alignItems: "center",
    gap: 10,
  },
  primaryButton: {
    backgroundColor: colors.accent,
    paddingVertical: 16,
    paddingHorizontal: 48,
    borderRadius: 12,
    width: "100%",
    maxWidth: 500,
  },
  primaryButtonText: {
    color: "#fff",
    fontSize: 18,
    fontWeight: "600",
    textAlign: "center",
  },
  skipButton: {
    backgroundColor: colors.surfaceLight,
    paddingVertical: 14,
    paddingHorizontal: 48,
    borderRadius: 12,
    width: "100%",
    maxWidth: 500,
  },
  skipButtonText: {
    color: colors.textSecondary,
    fontSize: 16,
    fontWeight: "600",
    textAlign: "center",
  },
  suspendButton: {
    paddingVertical: 8,
  },
  suspendButtonText: {
    color: colors.textSecondary,
    fontSize: 13,
    opacity: 0.6,
  },

  // Done styles
  doneTitle: {
    fontSize: 28,
    color: colors.text,
    fontWeight: "700",
    marginBottom: 8,
  },
  doneSubtitle: {
    fontSize: 16,
    color: colors.textSecondary,
    marginBottom: 20,
  },
  doneNote: {
    fontSize: 14,
    color: colors.textSecondary,
    textAlign: "center",
    maxWidth: 300,
    marginBottom: 24,
    lineHeight: 20,
  },
  sparkleContainer: {
    width: 80,
    height: 80,
    position: "absolute",
  },
  doneCelebration: {
    width: 80,
    height: 80,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 8,
  },
  doneCelebrationIcon: {
    fontSize: 40,
  },
  doneProgress: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
    alignItems: "center",
    marginBottom: 16,
    width: "100%",
    maxWidth: 300,
  },
  doneProgressTotal: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "700",
  },
  doneProgressLevel: {
    fontSize: 14,
    color: colors.accent,
    fontWeight: "600",
    marginTop: 2,
  },
  doneProgressNext: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 4,
  },
});
