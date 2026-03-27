import { useState, useEffect, useRef } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Animated,
  ScrollView,
} from "react-native";
import { useRouter } from "expo-router";
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
import { posLabel, FormsStrip } from "../lib/WordCardComponents";
import { getFrequencyBand, getCefrColor } from "../lib/frequency";
import ActionMenu from "../lib/review/ActionMenu";
import { TOPIC_LABELS } from "../lib/topic-labels";

const VERB_FORM_RELATION: Record<string, string> = {
  form_2: "causative/intensive of",
  form_3: "reciprocal of",
  form_4: "causative of",
  form_5: "reflexive of (Form II)",
  form_6: "reciprocal reflexive of (Form III)",
  form_7: "passive/reflexive of",
  form_8: "middle voice of",
  form_9: "color/physical state of",
  form_10: "seeks/considers",
};

type Phase = "loading" | "pick" | "done";

interface IntroducedWord {
  candidate: LearnCandidate;
}

export default function LearnScreen() {
  const router = useRouter();
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

    const hasMnemonic = !!c.memory_hooks_json?.mnemonic;
    const hasEtymology = !!c.etymology_json?.derivation;
    const hasCognates = c.memory_hooks_json?.cognates && c.memory_hooks_json.cognates.length > 0;
    const hasFunFact = !!c.memory_hooks_json?.fun_fact;
    const hasCulturalNote = !!c.etymology_json?.cultural_note;
    const hasPatternExamples = c.pattern_examples && c.pattern_examples.length > 0;
    const hasRootFamily = c.score_breakdown.total_siblings > 0;
    const hasUsageContext = !!c.memory_hooks_json?.usage_context;
    const hasInfoSections = hasMnemonic || hasEtymology || hasCognates || hasFunFact
      || hasCulturalNote || hasPatternExamples || hasRootFamily || hasUsageContext;

    return (
      <View style={styles.pickContainer}>
        <ScrollView
          style={styles.scrollView}
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          {topicLabel && (
            <View style={styles.topicBadge}>
              <Text style={styles.topicBadgeText}>{topicLabel}</Text>
            </View>
          )}
          <View style={styles.progressContainer}>
            <Text style={styles.progressText}>
              New word {pickIndex + 1} of {candidates.length}
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

          {/* Hero card */}
          <View style={[styles.card, !hasInfoSections && { borderRadius: 16 }]}>
            {c.story_title && (
              <View style={styles.storyBadge}>
                <Text style={styles.storyBadgeText}>From: {c.story_title}</Text>
              </View>
            )}
            <Text style={styles.wordArabic}>{c.lemma_ar}</Text>
            <Text style={styles.wordEnglish}>{c.gloss_en}</Text>
            {c.transliteration && (
              <Text style={styles.wordTranslit}>{c.transliteration}</Text>
            )}

            {/* Flow chips */}
            <View style={styles.chipsArea}>
              <View style={styles.chipPos}>
                <Text style={styles.chipPosText}>{posLabel(c.pos, c.forms_json)}</Text>
              </View>
              {c.cefr_level && (
                <View style={[styles.chipOutline, { borderColor: getCefrColor(c.cefr_level) + "60", backgroundColor: getCefrColor(c.cefr_level) + "20" }]}>
                  <Text style={[styles.chipOutlineText, { color: getCefrColor(c.cefr_level) }]}>{c.cefr_level}</Text>
                </View>
              )}
              {c.frequency_rank != null && (
                <View style={[styles.chipOutline, { borderColor: "#4a9eff30", backgroundColor: "#4a9eff15" }]}>
                  <Text style={[styles.chipOutlineText, { color: colors.accent }]}>#{c.frequency_rank}</Text>
                </View>
              )}
              {c.word_category && (
                <View style={[styles.chipOutline, { borderColor: "#f39c1240", backgroundColor: "#f39c1220" }]}>
                  <Text style={[styles.chipOutlineText, { color: "#f39c12" }]}>
                    {c.word_category === "proper_name" ? "Name" : "Sound"}
                  </Text>
                </View>
              )}
              {c.root && c.root_id && (
                <Pressable
                  style={[styles.chipOutline, { borderColor: "#9b59b630", backgroundColor: "#9b59b620" }]}
                  onPress={() => router.push(`/root/${c.root_id}`)}
                >
                  <Text style={[styles.chipArabic, { color: "#9b59b6" }]}>{c.root}</Text>
                  {c.root_meaning && (
                    <Text style={[styles.chipOutlineText, { color: "#9b59b6" }]} numberOfLines={1}>{c.root_meaning}</Text>
                  )}
                  <Text style={{ color: "#9b59b660", fontSize: 10 }}>{" \u203A"}</Text>
                </Pressable>
              )}
              {c.wazn && (
                <Pressable
                  style={[styles.chipOutline, { borderColor: "#f39c1230", backgroundColor: "#f39c1220" }]}
                  onPress={() => router.push(`/pattern/${encodeURIComponent(c.wazn!)}`)}
                >
                  <Text style={[styles.chipOutlineText, { color: "#f39c12" }]}>{c.wazn}</Text>
                  {c.wazn_meaning && (
                    <Text style={[styles.chipOutlineText, { color: "#f39c12" }]} numberOfLines={1}>{c.wazn_meaning}</Text>
                  )}
                  <Text style={{ color: "#f39c1260", fontSize: 10 }}>{" \u203A"}</Text>
                </Pressable>
              )}
            </View>

            {/* Forms strip */}
            <FormsStrip pos={c.pos} forms={c.forms_json} formsTranslit={c.forms_translit} />
          </View>

          {/* Info sections below hero */}
          {hasInfoSections && (
            <View style={styles.infoSections}>
              {/* Memory Hook - highest priority */}
              {hasMnemonic && (
                <View style={styles.infoSection}>
                  <Text style={[styles.sectionLabel, { color: "#9b59b6" }]}>Memory Hook</Text>
                  <View style={styles.mnemonicCard}>
                    <Text style={styles.mnemonicText}>{c.memory_hooks_json!.mnemonic}</Text>
                  </View>
                </View>
              )}

              {/* Etymology */}
              {hasEtymology && (
                <View style={styles.infoSection}>
                  <Text style={[styles.sectionLabel, { color: colors.accent }]}>Etymology</Text>
                  <Text style={styles.infoText}>
                    {c.etymology_json!.pattern ? `${c.etymology_json!.pattern}: ` : ""}
                    {c.etymology_json!.derivation}
                  </Text>
                </View>
              )}

              {/* Cognates */}
              {hasCognates && (
                <View style={styles.infoSection}>
                  <Text style={[styles.sectionLabel, { color: "#f39c12" }]}>Cross-Language</Text>
                  {c.memory_hooks_json!.cognates!.map((cog, i) => (
                    <View key={i} style={styles.cognateRow}>
                      <Text style={styles.cognateLang}>{cog.lang}</Text>
                      <Text style={styles.cognateWord}>{cog.word}</Text>
                      {cog.note ? <Text style={styles.cognateNote}>{cog.note}</Text> : null}
                    </View>
                  ))}
                </View>
              )}

              {/* Root Family */}
              {hasRootFamily && c.root && (
                <View style={styles.infoSection}>
                  <Pressable
                    style={styles.sectionLabelRow}
                    onPress={c.root_id ? () => router.push(`/root/${c.root_id}`) : undefined}
                  >
                    <Text style={[styles.sectionLabel, { color: "#2ecc71", marginBottom: 0 }]}>
                      Root Family ({c.score_breakdown.known_siblings}/{c.score_breakdown.total_siblings})
                    </Text>
                    {c.root_id && <Text style={styles.sectionLink}>View all ›</Text>}
                  </Pressable>
                  <View style={styles.rootRow}>
                    <Text style={styles.rootLetters}>{c.root}</Text>
                    {c.root_meaning && <Text style={styles.rootMeaning}>{c.root_meaning}</Text>}
                  </View>
                  {c.wazn && VERB_FORM_RELATION[c.wazn] && (() => {
                    const base = c.root_family?.find((s) => s.wazn === "form_1" && s.pos === "verb");
                    if (!base) return null;
                    return (
                      <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                        <Text style={{ fontSize: 11, color: "#f39c12", fontWeight: "600" }}>
                          {VERB_FORM_RELATION[c.wazn]}
                        </Text>
                        <Text style={{ fontSize: 14, color: colors.arabic, fontFamily: fontFamily.arabic }}>{base.lemma_ar}</Text>
                        {base.gloss_en && <Text style={{ fontSize: 11, color: colors.textSecondary }}>{base.gloss_en}</Text>}
                      </View>
                    );
                  })()}
                </View>
              )}

              {/* Pattern Examples */}
              {hasPatternExamples && (
                <View style={styles.infoSection}>
                  <Pressable
                    style={styles.sectionLabelRow}
                    onPress={c.wazn ? () => router.push(`/pattern/${encodeURIComponent(c.wazn!)}`) : undefined}
                  >
                    <Text style={[styles.sectionLabel, { color: "#f39c12", marginBottom: 0 }]}>
                      Pattern: {c.wazn}
                    </Text>
                    {c.wazn && <Text style={styles.sectionLink}>View all ›</Text>}
                  </Pressable>
                  <View style={styles.patternSiblings}>
                    {c.pattern_examples!.slice(0, 4).map((ex) => (
                      <View key={ex.lemma_id} style={styles.sibItem}>
                        <View style={[styles.sibDot, {
                          backgroundColor: ex.knowledge_state === "known" ? "#2ecc71"
                            : (ex.knowledge_state === "learning" || ex.knowledge_state === "acquiring") ? "#e67e22"
                            : "#8888a0"
                        }]} />
                        <Text style={styles.sibArabic}>{ex.lemma_ar}</Text>
                        {ex.gloss_en && <Text style={styles.sibGloss}>{ex.gloss_en}</Text>}
                      </View>
                    ))}
                  </View>
                </View>
              )}

              {/* Usage Context */}
              {hasUsageContext && (
                <View style={styles.infoSection}>
                  <Text style={[styles.sectionLabel, { color: colors.textSecondary }]}>Usage</Text>
                  <Text style={styles.infoText}>{c.memory_hooks_json!.usage_context}</Text>
                </View>
              )}

              {/* Fun Fact or Cultural Note */}
              {(hasFunFact || hasCulturalNote) && (
                <View style={styles.infoSection}>
                  <Text style={[styles.sectionLabel, { color: "#2ecc71" }]}>Did You Know?</Text>
                  <View style={styles.funFactCard}>
                    <Text style={styles.funFactText}>
                      {c.memory_hooks_json?.fun_fact || c.etymology_json?.cultural_note}
                    </Text>
                  </View>
                </View>
              )}
            </View>
          )}
        </ScrollView>

        {/* Fixed actions at bottom */}
        <View style={styles.actionColumn}>
          <Pressable style={styles.primaryButton} onPress={handleLearn}>
            <Text style={styles.primaryButtonText}>Learn</Text>
          </Pressable>
          <View style={styles.actionRow}>
            <Pressable style={styles.skipButton} onPress={handleSkip}>
              <Text style={styles.skipButtonText}>Skip</Text>
            </Pressable>
            <Pressable style={styles.suspendButton} onPress={handleSuspend}>
              <Text style={styles.suspendButtonText}>Suspend</Text>
            </Pressable>
          </View>
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
  pickContainer: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  scrollView: {
    flex: 1,
  },
  scrollContent: {
    padding: 16,
    paddingBottom: 8,
    alignItems: "center",
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
    height: 3,
    backgroundColor: colors.surfaceLight,
    borderRadius: 2,
    overflow: "hidden",
    marginBottom: 16,
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

  // Hero card
  card: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderBottomLeftRadius: 0,
    borderBottomRightRadius: 0,
    padding: 28,
    paddingBottom: 20,
    width: "100%",
    maxWidth: 500,
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
    borderBottomWidth: 0,
  },
  wordArabic: {
    fontSize: 52,
    color: colors.arabic,
    fontWeight: "700",
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    marginBottom: 6,
    lineHeight: 72,
  },
  wordEnglish: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "700",
    marginBottom: 4,
    textAlign: "center",
  },
  wordTranslit: {
    fontSize: 15,
    color: colors.textSecondary,
    fontFamily: fontFamily.translit,
    marginBottom: 10,
  },

  // Flow chips
  chipsArea: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    justifyContent: "center",
    marginBottom: 10,
  },
  chipPos: {
    backgroundColor: colors.surfaceLight,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
  },
  chipPosText: {
    color: colors.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  chipOutline: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
    borderWidth: 1,
  },
  chipOutlineText: {
    fontSize: 12,
    fontWeight: "600",
  },
  chipArabic: {
    fontSize: 14,
    fontWeight: "600",
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },

  // Info sections below hero
  infoSections: {
    backgroundColor: colors.surface,
    borderBottomLeftRadius: 16,
    borderBottomRightRadius: 16,
    borderWidth: 1,
    borderColor: colors.border,
    borderTopWidth: 0,
    width: "100%",
    maxWidth: 500,
    overflow: "hidden",
  },
  infoSection: {
    padding: 14,
    paddingHorizontal: 20,
    borderTopWidth: 1,
    borderTopColor: colors.surfaceLight,
  },
  sectionLabel: {
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 1,
    fontWeight: "700",
    marginBottom: 8,
  },
  sectionLabelRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  sectionLink: {
    fontSize: 12,
    color: colors.accent,
    fontWeight: "600",
  },
  infoText: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
  },

  // Mnemonic
  mnemonicCard: {
    backgroundColor: "#2a1f4e",
    borderRadius: 10,
    padding: 12,
    borderLeftWidth: 3,
    borderLeftColor: "#9b59b6",
  },
  mnemonicText: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
  },

  // Cognates
  cognateRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  cognateLang: {
    fontSize: 10,
    color: colors.textSecondary,
    textTransform: "uppercase",
    letterSpacing: 0.8,
    width: 36,
  },
  cognateWord: {
    fontSize: 14,
    color: colors.text,
    fontWeight: "600",
  },
  cognateNote: {
    fontSize: 12,
    color: colors.textSecondary,
  },

  // Root family
  rootRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  rootLetters: {
    fontSize: 20,
    color: colors.accent,
    fontWeight: "700",
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },
  rootMeaning: {
    fontSize: 13,
    color: colors.textSecondary,
    flex: 1,
  },

  // Pattern / root siblings
  patternSiblings: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 4,
  },
  sibItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  sibDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
  },
  sibArabic: {
    fontSize: 15,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },
  sibGloss: {
    fontSize: 11,
    color: colors.textSecondary,
  },

  // Fun fact
  funFactCard: {
    backgroundColor: "#1e2a1e",
    borderRadius: 10,
    padding: 12,
    borderLeftWidth: 3,
    borderLeftColor: "#2ecc71",
  },
  funFactText: {
    fontSize: 13,
    color: "#c8e8c8",
    lineHeight: 19,
  },

  // Actions (fixed at bottom)
  actionColumn: {
    width: "100%",
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 16,
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.bg,
  },
  actionRow: {
    flexDirection: "row",
    gap: 8,
  },
  primaryButton: {
    backgroundColor: colors.accent,
    paddingVertical: 16,
    borderRadius: 14,
    width: "100%",
    maxWidth: 500,
  },
  primaryButtonText: {
    color: "#fff",
    fontSize: 17,
    fontWeight: "600",
    textAlign: "center",
  },
  skipButton: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
    paddingVertical: 12,
    borderRadius: 12,
  },
  skipButtonText: {
    color: colors.textSecondary,
    fontSize: 14,
    fontWeight: "600",
    textAlign: "center",
  },
  suspendButton: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
    paddingVertical: 12,
    borderRadius: 12,
  },
  suspendButtonText: {
    color: colors.textSecondary,
    fontSize: 13,
    textAlign: "center",
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
