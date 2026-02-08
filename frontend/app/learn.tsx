import { useState, useEffect, useRef } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { colors, fonts } from "../lib/theme";
import {
  getNextWords,
  introduceWord,
  suspendWord,
  getLemmaSentence,
  submitSentenceReview,
  generateUuid,
} from "../lib/api";
import { LearnCandidate } from "../lib/types";

type Phase = "loading" | "pick" | "quiz" | "done";

interface IntroducedWord {
  candidate: LearnCandidate;
}

interface QuizSentence {
  sentence_id: number;
  arabic_text: string;
  english_translation: string;
  transliteration: string | null;
  audio_url: string | null;
  words: { lemma_id: number | null; surface_form: string; gloss_en: string | null }[];
}

export default function LearnScreen() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [candidates, setCandidates] = useState<LearnCandidate[]>([]);
  const [pickIndex, setPickIndex] = useState(0);
  const [introduced, setIntroduced] = useState<IntroducedWord[]>([]);
  const [quizIndex, setQuizIndex] = useState(0);
  const [quizRevealed, setQuizRevealed] = useState(false);
  const [quizResults, setQuizResults] = useState<boolean[]>([]);
  const [quizSentence, setQuizSentence] = useState<QuizSentence | null>(null);
  const [quizLoading, setQuizLoading] = useState(false);
  const sessionId = useRef(generateUuid());

  useEffect(() => {
    loadCandidates();
  }, []);

  async function loadCandidates() {
    setPhase("loading");
    try {
      const words = await getNextWords(5);
      setCandidates(words);
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
      await suspendWord(candidate.lemma_id);
    } catch (e) {
      console.error("Failed to suspend word:", e);
    }
    advancePick();
  }

  function advancePick() {
    const next = pickIndex + 1;
    if (next >= candidates.length) {
      startQuizOrDone();
    } else {
      setPickIndex(next);
    }
  }

  function startQuizOrDone() {
    if (introduced.length === 0) {
      setPhase("done");
    } else {
      setQuizIndex(0);
      setQuizRevealed(false);
      setQuizResults([]);
      setQuizSentence(null);
      setPhase("quiz");
      loadQuizSentence(0);
    }
  }

  function startQuizEarly() {
    if (introduced.length === 0) return;
    setQuizIndex(0);
    setQuizRevealed(false);
    setQuizResults([]);
    setQuizSentence(null);
    setPhase("quiz");
    loadQuizSentence(0);
  }

  async function loadQuizSentence(idx: number) {
    const words = introduced;
    if (idx >= words.length) return;

    setQuizLoading(true);
    setQuizSentence(null);
    const lemmaId = words[idx].candidate.lemma_id;

    // Poll for up to 20 seconds
    const maxAttempts = 10;
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const result = await getLemmaSentence(lemmaId);
        if (result.ready && result.sentence) {
          setQuizSentence(result.sentence);
          setQuizLoading(false);
          return;
        }
      } catch {}
      await new Promise((r) => setTimeout(r, 2000));
    }

    // Timeout — no sentence available, show word-only
    setQuizLoading(false);
  }

  async function handleQuizAnswer(gotIt: boolean) {
    const current = introduced[quizIndex];
    const newResults = [...quizResults, gotIt];
    setQuizResults(newResults);

    if (quizSentence) {
      submitSentenceReview({
        sentence_id: quizSentence.sentence_id,
        primary_lemma_id: current.candidate.lemma_id,
        comprehension_signal: gotIt ? "understood" : "no_idea",
        missed_lemma_ids: gotIt ? [] : [current.candidate.lemma_id],
        response_ms: 0,
        session_id: sessionId.current,
        review_mode: "quiz",
      });
    }

    if (quizIndex < introduced.length - 1) {
      const nextIdx = quizIndex + 1;
      setQuizIndex(nextIdx);
      setQuizRevealed(false);
      loadQuizSentence(nextIdx);
    } else {
      setPhase("done");
    }
  }

  function resetSession() {
    sessionId.current = generateUuid();
    setIntroduced([]);
    setPickIndex(0);
    setQuizIndex(0);
    setQuizRevealed(false);
    setQuizResults([]);
    setQuizSentence(null);
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

    const c = candidates[pickIndex];
    return (
      <View style={styles.centered}>
        <View style={styles.progressContainer}>
          <Text style={styles.progressText}>
            Word {pickIndex + 1} of {candidates.length}
          </Text>
          {introduced.length > 0 && (
            <Pressable style={styles.quizEarlyButton} onPress={startQuizEarly}>
              <Text style={styles.quizEarlyText}>
                Quiz ({introduced.length}) →
              </Text>
            </Pressable>
          )}
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
          <Text style={styles.wordArabic}>{c.lemma_ar}</Text>
          <Text style={styles.wordEnglish}>{c.gloss_en}</Text>
          {c.transliteration && (
            <Text style={styles.wordTranslit}>{c.transliteration}</Text>
          )}
          <Text style={styles.wordPos}>{c.pos}</Text>

          {c.root && (
            <View style={styles.rootInfo}>
              <Text style={styles.rootText}>
                Root: {c.root}
                {c.root_meaning ? ` — ${c.root_meaning}` : ""}
              </Text>
              {c.score_breakdown.total_siblings > 0 && (
                <Text style={styles.rootSiblings}>
                  {c.score_breakdown.known_siblings} of{" "}
                  {c.score_breakdown.total_siblings} root words known
                </Text>
              )}
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
            <Text style={styles.suspendButtonText}>Never show this word</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // --- Quiz Phase: sentence-based ---
  if (phase === "quiz") {
    const current = introduced[quizIndex];

    if (quizLoading) {
      return (
        <View style={styles.centered}>
          <View style={styles.progressContainer}>
            <Text style={styles.progressText}>
              Quiz {quizIndex + 1} of {introduced.length}
            </Text>
          </View>
          <ActivityIndicator size="large" color={colors.accent} />
          <Text style={styles.loadingText}>Preparing sentence...</Text>
        </View>
      );
    }

    // Word-only fallback (no sentence generated in time)
    if (!quizSentence) {
      return (
        <View style={styles.centered}>
          <View style={styles.progressContainer}>
            <Text style={styles.progressText}>
              Quiz {quizIndex + 1} of {introduced.length}
            </Text>
          </View>

          <View style={styles.card}>
            {!quizRevealed ? (
              <>
                <Text style={styles.quizArabic}>
                  {current.candidate.lemma_ar}
                </Text>
                <Text style={styles.quizHint}>What does this word mean?</Text>
              </>
            ) : (
              <>
                <Text style={styles.quizArabic}>
                  {current.candidate.lemma_ar}
                </Text>
                <View style={styles.divider} />
                <Text style={styles.quizAnswer}>
                  {current.candidate.gloss_en}
                </Text>
                {current.candidate.transliteration && (
                  <Text style={styles.wordTranslit}>
                    {current.candidate.transliteration}
                  </Text>
                )}
              </>
            )}
          </View>

          {!quizRevealed ? (
            <Pressable
              style={styles.primaryButton}
              onPress={() => setQuizRevealed(true)}
            >
              <Text style={styles.primaryButtonText}>Show Answer</Text>
            </Pressable>
          ) : (
            <View style={styles.ratingRow}>
              <Pressable
                style={[styles.ratingButton, styles.gotItButton]}
                onPress={() => handleQuizAnswer(true)}
              >
                <Text style={styles.ratingButtonText}>Got it</Text>
              </Pressable>
              <Pressable
                style={[styles.ratingButton, styles.missedButton]}
                onPress={() => handleQuizAnswer(false)}
              >
                <Text style={styles.ratingButtonText}>Missed</Text>
              </Pressable>
            </View>
          )}
        </View>
      );
    }

    // Sentence quiz
    return (
      <View style={styles.centered}>
        <View style={styles.progressContainer}>
          <Text style={styles.progressText}>
            Quiz {quizIndex + 1} of {introduced.length}
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.quizTargetHint}>
            {current.candidate.lemma_ar} — {current.candidate.gloss_en}
          </Text>
          <View style={styles.divider} />
          <Text style={styles.sentenceArabic}>
            {quizSentence.arabic_text}
          </Text>

          {quizRevealed && (
            <View style={styles.answerSection}>
              <View style={styles.divider} />
              <Text style={styles.sentenceEnglish}>
                {quizSentence.english_translation}
              </Text>
              {quizSentence.transliteration && (
                <Text style={styles.sentenceTranslit}>
                  {quizSentence.transliteration}
                </Text>
              )}
            </View>
          )}
        </View>

        {!quizRevealed ? (
          <Pressable
            style={styles.primaryButton}
            onPress={() => setQuizRevealed(true)}
          >
            <Text style={styles.primaryButtonText}>Show Translation</Text>
          </Pressable>
        ) : (
          <View style={styles.ratingRow}>
            <Pressable
              style={[styles.ratingButton, styles.gotItButton]}
              onPress={() => handleQuizAnswer(true)}
            >
              <Text style={styles.ratingButtonText}>Got it</Text>
            </Pressable>
            <Pressable
              style={[styles.ratingButton, styles.missedButton]}
              onPress={() => handleQuizAnswer(false)}
            >
              <Text style={styles.ratingButtonText}>Missed</Text>
            </Pressable>
          </View>
        )}
      </View>
    );
  }

  // --- Done Phase ---
  const correct = quizResults.filter(Boolean).length;
  return (
    <View style={styles.centered}>
      <Text style={styles.doneTitle}>Session Complete</Text>
      {introduced.length > 0 ? (
        <>
          <Text style={styles.doneSubtitle}>
            {introduced.length} new word{introduced.length !== 1 ? "s" : ""}{" "}
            learned
          </Text>
          {quizResults.length > 0 && (
            <View style={styles.doneStats}>
              <Text style={[styles.doneStat, { color: colors.good }]}>
                Got it: {correct}
              </Text>
              <Text style={[styles.doneStat, { color: colors.missed }]}>
                Missed: {quizResults.length - correct}
              </Text>
            </View>
          )}
          <Text style={styles.doneNote}>
            These words will now appear in your review sessions.
          </Text>
        </>
      ) : (
        <Text style={styles.doneSubtitle}>No words learned this session</Text>
      )}
      <Pressable style={styles.primaryButton} onPress={resetSession}>
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
  quizEarlyButton: {
    paddingVertical: 4,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: colors.surfaceLight,
  },
  quizEarlyText: {
    color: colors.accent,
    fontSize: fonts.small,
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
  wordArabic: {
    fontSize: 44,
    color: colors.arabic,
    fontWeight: "700",
    writingDirection: "rtl",
    marginBottom: 12,
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
    marginBottom: 12,
  },
  rootInfo: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 12,
    width: "100%",
    alignItems: "center",
  },
  rootText: {
    fontSize: 14,
    color: colors.accent,
    fontWeight: "600",
  },
  rootSiblings: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 4,
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

  // Quiz styles
  quizArabic: {
    fontSize: 44,
    color: colors.arabic,
    fontWeight: "700",
    writingDirection: "rtl",
    marginBottom: 16,
  },
  quizHint: {
    fontSize: 16,
    color: colors.textSecondary,
  },
  quizAnswer: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "600",
    textAlign: "center",
  },
  quizTargetHint: {
    fontSize: 14,
    color: colors.accent,
    fontWeight: "600",
    marginBottom: 8,
  },
  sentenceArabic: {
    fontSize: 28,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
    lineHeight: 46,
  },
  answerSection: {
    width: "100%",
    alignItems: "center",
  },
  sentenceEnglish: {
    fontSize: 20,
    color: colors.text,
    fontWeight: "600",
    textAlign: "center",
  },
  sentenceTranslit: {
    fontSize: 16,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginTop: 6,
    textAlign: "center",
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    width: "80%",
    marginVertical: 16,
  },
  ratingRow: {
    flexDirection: "row",
    gap: 12,
    width: "100%",
    maxWidth: 500,
  },
  ratingButton: {
    flex: 1,
    paddingVertical: 16,
    borderRadius: 12,
    alignItems: "center",
  },
  gotItButton: {
    backgroundColor: colors.gotIt,
  },
  missedButton: {
    backgroundColor: colors.missed,
  },
  ratingButtonText: {
    color: "#fff",
    fontSize: 17,
    fontWeight: "700",
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
  doneStats: {
    flexDirection: "row",
    gap: 24,
    marginBottom: 20,
  },
  doneStat: {
    fontSize: 18,
    fontWeight: "600",
  },
  doneNote: {
    fontSize: 14,
    color: colors.textSecondary,
    textAlign: "center",
    maxWidth: 300,
    marginBottom: 24,
    lineHeight: 20,
  },
});
