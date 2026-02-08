import { useState, useEffect } from "react";
import {
  View,
  Text,
  Pressable,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { colors, fonts } from "../lib/theme";
import { getNextWords, introduceWord } from "../lib/api";
import { LearnCandidate, IntroduceResult, RootFamilyWord } from "../lib/types";

type Phase = "loading" | "pick" | "intro" | "quiz" | "done";

interface IntroducedWord {
  candidate: LearnCandidate;
  result: IntroduceResult;
}

export default function LearnScreen() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [candidates, setCandidates] = useState<LearnCandidate[]>([]);
  const [introduced, setIntroduced] = useState<IntroducedWord[]>([]);
  const [currentIntroIndex, setCurrentIntroIndex] = useState(0);
  const [quizIndex, setQuizIndex] = useState(0);
  const [quizRevealed, setQuizRevealed] = useState(false);
  const [quizResults, setQuizResults] = useState<boolean[]>([]);

  useEffect(() => {
    loadCandidates();
  }, []);

  async function loadCandidates() {
    setPhase("loading");
    try {
      const words = await getNextWords(5);
      setCandidates(words);
      setPhase("pick");
    } catch (e) {
      console.error("Failed to load candidates:", e);
      setPhase("pick");
    }
  }

  async function handleIntroduce(candidate: LearnCandidate) {
    try {
      const result = await introduceWord(candidate.lemma_id);
      const newIntroduced = [...introduced, { candidate, result }];
      setIntroduced(newIntroduced);
      setCurrentIntroIndex(newIntroduced.length - 1);
      setPhase("intro");
    } catch (e) {
      console.error("Failed to introduce word:", e);
    }
  }

  function handleIntroNext() {
    if (currentIntroIndex < introduced.length - 1) {
      setCurrentIntroIndex(currentIntroIndex + 1);
    } else {
      setPhase("pick");
    }
  }

  function startQuiz() {
    if (introduced.length === 0) return;
    setQuizIndex(0);
    setQuizRevealed(false);
    setQuizResults([]);
    setPhase("quiz");
  }

  function handleQuizReveal() {
    setQuizRevealed(true);
  }

  function handleQuizAnswer(correct: boolean) {
    const newResults = [...quizResults, correct];
    setQuizResults(newResults);

    if (quizIndex < introduced.length - 1) {
      setQuizIndex(quizIndex + 1);
      setQuizRevealed(false);
    } else {
      setPhase("done");
    }
  }

  function resetSession() {
    setIntroduced([]);
    setCurrentIntroIndex(0);
    setQuizIndex(0);
    setQuizRevealed(false);
    setQuizResults([]);
    loadCandidates();
  }

  if (phase === "loading") {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
        <Text style={styles.loadingText}>Finding words for you...</Text>
      </View>
    );
  }

  if (phase === "pick") {
    return (
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        <Text style={styles.title}>Learn New Words</Text>
        {introduced.length > 0 && (
          <View style={styles.progressRow}>
            <Text style={styles.progressText}>
              {introduced.length} word{introduced.length !== 1 ? "s" : ""} introduced
            </Text>
            <Pressable style={styles.quizButton} onPress={startQuiz}>
              <Text style={styles.quizButtonText}>Quick Quiz</Text>
            </Pressable>
          </View>
        )}
        <Text style={styles.subtitle}>Tap a word to learn it</Text>
        {candidates.map((c) => {
          const alreadyIntroduced = introduced.some(
            (i) => i.candidate.lemma_id === c.lemma_id
          );
          return (
            <Pressable
              key={c.lemma_id}
              style={[
                styles.candidateCard,
                alreadyIntroduced && styles.candidateCardDone,
              ]}
              onPress={() => !alreadyIntroduced && handleIntroduce(c)}
              disabled={alreadyIntroduced}
            >
              <View style={styles.candidateMain}>
                <Text style={styles.candidateArabic}>{c.lemma_ar}</Text>
                <Text style={styles.candidateEnglish}>{c.gloss_en}</Text>
              </View>
              <View style={styles.candidateMeta}>
                {c.transliteration && (
                  <Text style={styles.candidateTranslit}>{c.transliteration}</Text>
                )}
                <Text style={styles.candidatePos}>{c.pos}</Text>
                {c.root && (
                  <Text style={styles.candidateRoot}>
                    Root: {c.root}
                    {c.score_breakdown.known_siblings > 0 &&
                      ` (${c.score_breakdown.known_siblings}/${c.score_breakdown.total_siblings} known)`}
                  </Text>
                )}
              </View>
              {alreadyIntroduced && (
                <View style={styles.checkmark}>
                  <Text style={styles.checkmarkText}>Learned</Text>
                </View>
              )}
            </Pressable>
          );
        })}
        {candidates.length === 0 && (
          <Text style={styles.emptyText}>
            No new words available. Import more vocabulary or check back later.
          </Text>
        )}
      </ScrollView>
    );
  }

  if (phase === "intro") {
    const current = introduced[currentIntroIndex];
    const { candidate, result } = current;
    const hasFamily =
      result.root_family && result.root_family.length > 1;

    return (
      <ScrollView style={styles.container} contentContainerStyle={styles.introContent}>
        <Text style={styles.introStep}>
          Word {currentIntroIndex + 1} of {introduced.length}
        </Text>

        {/* Word Card */}
        <View style={styles.wordCard}>
          <Text style={styles.wordArabic}>{candidate.lemma_ar}</Text>
          <Text style={styles.wordEnglish}>{candidate.gloss_en}</Text>
          {candidate.transliteration && (
            <Text style={styles.wordTranslit}>{candidate.transliteration}</Text>
          )}
          <Text style={styles.wordPos}>{candidate.pos}</Text>
        </View>

        {/* Root Info */}
        {candidate.root && (
          <View style={styles.rootCard}>
            <Text style={styles.rootTitle}>Root: {candidate.root}</Text>
            {candidate.root_meaning && (
              <Text style={styles.rootMeaning}>
                Core meaning: {candidate.root_meaning}
              </Text>
            )}
            {hasFamily && (
              <View style={styles.familyList}>
                <Text style={styles.familyTitle}>Root family:</Text>
                {result.root_family!.map((fw: RootFamilyWord) => (
                  <View key={fw.lemma_id} style={styles.familyItem}>
                    <Text
                      style={[
                        styles.familyArabic,
                        fw.state === "known" && styles.familyKnown,
                        fw.state === "learning" && styles.familyLearning,
                        fw.lemma_id === candidate.lemma_id && styles.familyCurrent,
                      ]}
                    >
                      {fw.lemma_ar}
                    </Text>
                    <Text style={styles.familyGloss}>{fw.gloss_en}</Text>
                    {fw.state !== "unknown" && fw.lemma_id !== candidate.lemma_id && (
                      <Text
                        style={[
                          styles.familyState,
                          fw.state === "known" && { color: colors.good },
                          fw.state === "learning" && { color: colors.stateLearning },
                        ]}
                      >
                        {fw.state}
                      </Text>
                    )}
                    {fw.lemma_id === candidate.lemma_id && (
                      <Text style={[styles.familyState, { color: colors.accent }]}>
                        new
                      </Text>
                    )}
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        <Pressable style={styles.nextButton} onPress={handleIntroNext}>
          <Text style={styles.nextButtonText}>
            {currentIntroIndex < introduced.length - 1
              ? "Next Word"
              : "Done — Pick More or Quiz"}
          </Text>
        </Pressable>
      </ScrollView>
    );
  }

  if (phase === "quiz") {
    const current = introduced[quizIndex];
    const { candidate } = current;

    return (
      <View style={styles.container}>
        <View style={styles.quizHeader}>
          <Text style={styles.quizProgress}>
            {quizIndex + 1} / {introduced.length}
          </Text>
        </View>

        <View style={styles.quizContent}>
          {!quizRevealed ? (
            <>
              <Text style={styles.quizArabic}>{candidate.lemma_ar}</Text>
              <Text style={styles.quizHint}>What does this word mean?</Text>
              <Pressable style={styles.revealButton} onPress={handleQuizReveal}>
                <Text style={styles.revealButtonText}>Show Answer</Text>
              </Pressable>
            </>
          ) : (
            <>
              <Text style={styles.quizArabic}>{candidate.lemma_ar}</Text>
              <Text style={styles.quizAnswer}>{candidate.gloss_en}</Text>
              {candidate.transliteration && (
                <Text style={styles.quizTranslit}>{candidate.transliteration}</Text>
              )}
              <View style={styles.quizButtons}>
                <Pressable
                  style={[styles.quizBtn, styles.quizBtnGood]}
                  onPress={() => handleQuizAnswer(true)}
                >
                  <Text style={styles.quizBtnText}>Got it</Text>
                </Pressable>
                <Pressable
                  style={[styles.quizBtn, styles.quizBtnMissed]}
                  onPress={() => handleQuizAnswer(false)}
                >
                  <Text style={styles.quizBtnText}>Missed</Text>
                </Pressable>
              </View>
            </>
          )}
        </View>
      </View>
    );
  }

  // phase === "done"
  const correct = quizResults.filter(Boolean).length;
  return (
    <View style={styles.centered}>
      <Text style={styles.doneTitle}>Session Complete</Text>
      <Text style={styles.doneSubtitle}>
        {introduced.length} new word{introduced.length !== 1 ? "s" : ""} learned
      </Text>
      <View style={styles.doneStats}>
        <Text style={[styles.doneStat, { color: colors.good }]}>
          Got it: {correct}
        </Text>
        <Text style={[styles.doneStat, { color: colors.missed }]}>
          Missed: {quizResults.length - correct}
        </Text>
      </View>
      <Text style={styles.doneNote}>
        These words will now appear in your review sessions with simple, short
        sentences to help fix them in memory.
      </Text>
      <Pressable style={styles.nextButton} onPress={resetSession}>
        <Text style={styles.nextButtonText}>Learn More Words</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  content: { padding: 20, alignItems: "center" },
  introContent: { padding: 20, alignItems: "center" },
  loadingText: {
    color: colors.textSecondary,
    fontSize: 16,
    marginTop: 12,
  },
  title: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "700",
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 14,
    color: colors.textSecondary,
    marginBottom: 16,
  },
  progressRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    width: "100%",
    maxWidth: 500,
    marginBottom: 12,
  },
  progressText: {
    fontSize: 14,
    color: colors.accent,
    fontWeight: "600",
  },
  quizButton: {
    backgroundColor: colors.accent,
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 8,
  },
  quizButtonText: { color: "#fff", fontWeight: "600", fontSize: 14 },
  candidateCard: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: 18,
    width: "100%",
    maxWidth: 500,
    marginBottom: 10,
  },
  candidateCardDone: { opacity: 0.5 },
  candidateMain: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 6,
  },
  candidateArabic: {
    fontSize: fonts.arabicMedium,
    color: colors.arabic,
    fontWeight: "600",
  },
  candidateEnglish: {
    fontSize: 16,
    color: colors.text,
  },
  candidateMeta: { gap: 2 },
  candidateTranslit: {
    fontSize: 13,
    color: colors.textSecondary,
    fontStyle: "italic",
  },
  candidatePos: { fontSize: 12, color: colors.textSecondary },
  candidateRoot: { fontSize: 12, color: colors.accent },
  checkmark: {
    position: "absolute",
    top: 10,
    right: 10,
    backgroundColor: colors.good,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  checkmarkText: { color: "#fff", fontSize: 11, fontWeight: "600" },
  emptyText: {
    color: colors.textSecondary,
    fontSize: 16,
    textAlign: "center",
    marginTop: 40,
  },
  introStep: {
    fontSize: 14,
    color: colors.textSecondary,
    marginBottom: 16,
  },
  wordCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 32,
    width: "100%",
    maxWidth: 500,
    alignItems: "center",
    marginBottom: 16,
  },
  wordArabic: {
    fontSize: 40,
    color: colors.arabic,
    fontWeight: "700",
    marginBottom: 12,
  },
  wordEnglish: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 6,
  },
  wordTranslit: {
    fontSize: 16,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginBottom: 4,
  },
  wordPos: { fontSize: 13, color: colors.textSecondary },
  rootCard: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: 18,
    width: "100%",
    maxWidth: 500,
    marginBottom: 16,
  },
  rootTitle: {
    fontSize: 16,
    color: colors.accent,
    fontWeight: "700",
    marginBottom: 4,
  },
  rootMeaning: {
    fontSize: 14,
    color: colors.textSecondary,
    marginBottom: 10,
  },
  familyList: { marginTop: 4 },
  familyTitle: {
    fontSize: 13,
    color: colors.textSecondary,
    marginBottom: 6,
  },
  familyItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 4,
  },
  familyArabic: {
    fontSize: fonts.arabicList,
    color: colors.textSecondary,
  },
  familyKnown: { color: colors.good },
  familyLearning: { color: colors.stateLearning },
  familyCurrent: { color: colors.accent, fontWeight: "700" },
  familyGloss: { fontSize: 13, color: colors.textSecondary, flex: 1 },
  familyState: { fontSize: 11, color: colors.textSecondary },
  nextButton: {
    backgroundColor: colors.accent,
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 12,
    marginTop: 8,
  },
  nextButtonText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  quizHeader: { padding: 16, alignItems: "center" },
  quizProgress: { fontSize: 14, color: colors.textSecondary },
  quizContent: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  quizArabic: {
    fontSize: 44,
    color: colors.arabic,
    fontWeight: "700",
    marginBottom: 24,
  },
  quizHint: {
    fontSize: 16,
    color: colors.textSecondary,
    marginBottom: 32,
  },
  revealButton: {
    backgroundColor: colors.surfaceLight,
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 12,
  },
  revealButtonText: { color: colors.text, fontSize: 16, fontWeight: "600" },
  quizAnswer: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 8,
  },
  quizTranslit: {
    fontSize: 16,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginBottom: 32,
  },
  quizButtons: {
    flexDirection: "row",
    gap: 16,
  },
  quizBtn: {
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 12,
  },
  quizBtnGood: { backgroundColor: colors.good },
  quizBtnMissed: { backgroundColor: colors.missed },
  quizBtnText: { color: "#fff", fontSize: 16, fontWeight: "600" },
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
  doneStat: { fontSize: 18, fontWeight: "600" },
  doneNote: {
    fontSize: 14,
    color: colors.textSecondary,
    textAlign: "center",
    maxWidth: 300,
    marginBottom: 24,
    lineHeight: 20,
  },
});
