import { useState, useEffect, useRef, useCallback } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
} from "react-native";
import { colors, fonts } from "../lib/theme";
import { getReviewSession, submitReview } from "../lib/api";
import { ReviewCard, ReviewSession, ReviewMode, ComprehensionSignal } from "../lib/types";

/**
 * Listening mode flow:
 *   audio → arabic → answer
 *   1. "audio": blank screen, audio plays (simulated with timer for now)
 *   2. "arabic": Arabic text revealed, user can tap missed words
 *   3. "answer": English + transliteration revealed, rating buttons
 *
 * Reading mode flow:
 *   front → back
 *   1. "front": Arabic sentence shown
 *   2. "back": English + transliteration revealed, rating buttons
 */
type ReadingCardState = "front" | "back";
type ListeningCardState = "audio" | "arabic" | "answer";
type CardState = ReadingCardState | ListeningCardState;

interface SessionResults {
  total: number;
  gotIt: number;
  missed: number;
  noIdea: number;
}

function stripDiacritics(s: string): string {
  return s.replace(/[\u0610-\u065f\u0670\u06D6-\u06ED]/g, "");
}

function isTargetWordIndex(word: string, bareWord: string): boolean {
  const wordBare = stripDiacritics(word);
  const alPrefix = "\u0627\u0644";
  return (
    wordBare === bareWord ||
    wordBare === alPrefix + bareWord ||
    stripDiacritics(word.replace(/^وَ?ال/, alPrefix)) === alPrefix + bareWord
  );
}

export default function ReviewScreen() {
  const [mode, setMode] = useState<ReviewMode>("reading");
  const [session, setSession] = useState<ReviewSession | null>(null);
  const [cardIndex, setCardIndex] = useState(0);
  const [cardState, setCardState] = useState<CardState>("front");
  const [loading, setLoading] = useState(true);
  const [results, setResults] = useState<SessionResults | null>(null);
  const [missedIndices, setMissedIndices] = useState<Set<number>>(new Set());
  const [audioPlaying, setAudioPlaying] = useState(false);
  const showTime = useRef<number>(0);

  useEffect(() => {
    loadSession();
  }, []);

  useEffect(() => {
    if (cardState === "front" || cardState === "audio") {
      showTime.current = Date.now();
    }
  }, [cardIndex, cardState]);

  // Simulate audio playback for listening mode
  useEffect(() => {
    if (cardState === "audio" && !audioPlaying) {
      setAudioPlaying(true);
      // TODO: Replace with actual audio playback via TTS API
      const timer = setTimeout(() => {
        setAudioPlaying(false);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [cardState, cardIndex]);

  async function loadSession(newMode?: ReviewMode) {
    const m = newMode ?? mode;
    setLoading(true);
    setResults(null);
    setCardIndex(0);
    setCardState(m === "listening" ? "audio" : "front");
    setMissedIndices(new Set());
    setAudioPlaying(false);
    try {
      const s = await getReviewSession(m);
      setSession(s);
    } catch (e) {
      console.error("Failed to load review session:", e);
    } finally {
      setLoading(false);
    }
  }

  function switchMode(newMode: ReviewMode) {
    setMode(newMode);
    loadSession(newMode);
  }

  const toggleMissed = useCallback((index: number) => {
    setMissedIndices((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, []);

  async function handleSubmit(signal: ComprehensionSignal) {
    if (!session) return;
    const card = session.cards[cardIndex];
    const responseMs = Date.now() - showTime.current;

    const missedWords: string[] = [];
    let targetMissed = false;

    if (signal !== "understood" && card.sentence) {
      const words = card.sentence.arabic.split(/\s+/);
      for (const idx of missedIndices) {
        const w = words[idx];
        if (w) {
          missedWords.push(stripDiacritics(w));
          if (isTargetWordIndex(w, card.lemma_ar_bare)) {
            targetMissed = true;
          }
        }
      }
    }

    if (signal === "no_idea") {
      targetMissed = true;
    } else if (signal !== "understood" && !card.sentence) {
      targetMissed = true;
    }

    const rating: 1 | 3 = targetMissed ? 1 : 3;

    await submitReview({
      lemma_id: card.lemma_id,
      rating,
      response_ms: responseMs,
      session_id: session.session_id,
      missed_words: missedWords,
      review_mode: mode,
      comprehension_signal: signal,
    });

    const prev = results ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 };
    const next = {
      total: prev.total + 1,
      gotIt: prev.gotIt + (signal === "understood" ? 1 : 0),
      missed: prev.missed + (signal === "partial" ? 1 : 0),
      noIdea: prev.noIdea + (signal === "no_idea" ? 1 : 0),
    };

    if (cardIndex + 1 >= session.cards.length) {
      setResults(next);
      setCardState(mode === "listening" ? "audio" : "front");
    } else {
      setResults(next);
      setCardIndex(cardIndex + 1);
      setCardState(mode === "listening" ? "audio" : "front");
      setMissedIndices(new Set());
      setAudioPlaying(false);
    }
  }

  function advanceState() {
    if (mode === "listening") {
      if (cardState === "audio") setCardState("arabic");
      else if (cardState === "arabic") setCardState("answer");
    } else {
      if (cardState === "front") setCardState("back");
    }
  }

  // --- Render ---

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (!session || session.cards.length === 0) {
    return (
      <View style={styles.container}>
        <ModeToggle mode={mode} onSwitch={switchMode} />
        <Text style={styles.emptyText}>
          {mode === "listening"
            ? "No sentences ready for listening practice"
            : "No cards due for review"}
        </Text>
        <Pressable style={styles.startButton} onPress={() => loadSession()}>
          <Text style={styles.startButtonText}>Refresh</Text>
        </Pressable>
      </View>
    );
  }

  const isSessionDone = results && results.total >= session.cards.length;

  if (isSessionDone) {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.summaryTitle}>Session Complete</Text>
        <Text style={styles.summarySubtitle}>
          {mode === "listening" ? "Listening" : "Reading"} mode
        </Text>
        <Text style={styles.summaryCount}>{results.total} cards reviewed</Text>
        <View style={styles.summaryGrid}>
          <View style={styles.summaryItem}>
            <Text style={[styles.summaryLabel, { color: colors.gotIt }]}>Got it</Text>
            <Text style={styles.summaryValue}>{results.gotIt}</Text>
          </View>
          <View style={styles.summaryItem}>
            <Text style={[styles.summaryLabel, { color: colors.missed }]}>Missed</Text>
            <Text style={styles.summaryValue}>{results.missed}</Text>
          </View>
          {results.noIdea > 0 && (
            <View style={styles.summaryItem}>
              <Text style={[styles.summaryLabel, { color: colors.noIdea }]}>No idea</Text>
              <Text style={styles.summaryValue}>{results.noIdea}</Text>
            </View>
          )}
        </View>
        <Pressable style={styles.startButton} onPress={() => loadSession()}>
          <Text style={styles.startButtonText}>Start New Session</Text>
        </Pressable>
      </ScrollView>
    );
  }

  const card = session.cards[cardIndex];
  const hasSentence = card.sentence !== null;
  const isListening = mode === "listening";

  return (
    <View style={[styles.container, isListening && styles.listeningContainer]}>
      <ModeToggle mode={mode} onSwitch={switchMode} />
      <ProgressBar current={cardIndex + 1} total={session.cards.length} mode={mode} />

      <View style={[styles.card, isListening && styles.listeningCard]}>
        {isListening ? (
          <ListeningCard
            card={card}
            cardState={cardState as ListeningCardState}
            missedIndices={missedIndices}
            onToggleMissed={toggleMissed}
            audioPlaying={audioPlaying}
          />
        ) : hasSentence ? (
          <SentenceCard
            card={card}
            cardState={cardState as ReadingCardState}
            missedIndices={missedIndices}
            onToggleMissed={toggleMissed}
          />
        ) : (
          <WordOnlyCard card={card} cardState={cardState as ReadingCardState} />
        )}
      </View>

      {/* Action buttons */}
      {isListening ? (
        <ListeningActions
          cardState={cardState as ListeningCardState}
          missedCount={missedIndices.size}
          onAdvance={advanceState}
          onSubmit={handleSubmit}
        />
      ) : (
        <ReadingActions
          cardState={cardState as ReadingCardState}
          hasSentence={hasSentence}
          missedCount={missedIndices.size}
          onAdvance={advanceState}
          onSubmit={handleSubmit}
        />
      )}
    </View>
  );
}

// --- Mode Toggle ---

function ModeToggle({ mode, onSwitch }: { mode: ReviewMode; onSwitch: (m: ReviewMode) => void }) {
  return (
    <View style={styles.modeToggle}>
      <Pressable
        style={[styles.modeButton, mode === "reading" && styles.modeButtonActive]}
        onPress={() => onSwitch("reading")}
      >
        <Text style={[styles.modeButtonText, mode === "reading" && styles.modeButtonTextActive]}>
          Reading
        </Text>
      </Pressable>
      <Pressable
        style={[
          styles.modeButton,
          mode === "listening" && styles.modeButtonActiveListening,
        ]}
        onPress={() => onSwitch("listening")}
      >
        <Text
          style={[
            styles.modeButtonText,
            mode === "listening" && styles.modeButtonTextActive,
          ]}
        >
          Listening
        </Text>
      </Pressable>
    </View>
  );
}

// --- Listening Card ---

function ListeningCard({
  card,
  cardState,
  missedIndices,
  onToggleMissed,
  audioPlaying,
}: {
  card: ReviewCard;
  cardState: ListeningCardState;
  missedIndices: Set<number>;
  onToggleMissed: (index: number) => void;
  audioPlaying: boolean;
}) {
  const sentence = card.sentence!;

  if (cardState === "audio") {
    return (
      <View style={styles.listeningAudioState}>
        <Text style={styles.listeningIcon}>{audioPlaying ? "\u{1F50A}" : "\u{1F442}"}</Text>
        <Text style={styles.listeningHint}>
          {audioPlaying ? "Listening..." : "Audio finished — tap to reveal"}
        </Text>
      </View>
    );
  }

  const words = sentence.arabic.split(/\s+/);
  const showAnswer = cardState === "answer";

  return (
    <>
      <Text style={styles.sentenceArabic}>
        {words.map((word, i) => {
          const isTarget = isTargetWordIndex(word, card.lemma_ar_bare);
          const isMissed = missedIndices.has(i);

          const wordStyle = isMissed
            ? styles.missedWord
            : showAnswer && isTarget
            ? styles.targetWord
            : undefined;

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text onPress={() => onToggleMissed(i)} style={wordStyle}>
                {word}
              </Text>
            </Text>
          );
        })}
      </Text>

      {showAnswer && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />
          <Text style={styles.sentenceEnglish}>{sentence.english}</Text>
          <Text style={styles.sentenceTranslit}>{sentence.transliteration}</Text>

          <View style={styles.targetWordBox}>
            <Text style={styles.targetWordArabic}>{card.lemma_ar}</Text>
            <Text style={styles.targetWordGloss}>{card.gloss_en}</Text>
            <View style={styles.targetWordMeta}>
              {card.root && <Text style={styles.metaText}>{card.root}</Text>}
              <Text style={styles.metaText}>{card.pos}</Text>
            </View>
          </View>
        </View>
      )}

      {cardState === "arabic" && (
        <Text style={styles.tapHintListening}>
          Tap words you didn't catch, then tap below to reveal answer
        </Text>
      )}
    </>
  );
}

// --- Listening Actions ---

function ListeningActions({
  cardState,
  missedCount,
  onAdvance,
  onSubmit,
}: {
  cardState: ListeningCardState;
  missedCount: number;
  onAdvance: () => void;
  onSubmit: (signal: ComprehensionSignal) => void;
}) {
  if (cardState === "audio") {
    return (
      <View style={styles.actionColumn}>
        <Pressable style={styles.showButton} onPress={onAdvance}>
          <Text style={styles.showButtonText}>Reveal Arabic</Text>
        </Pressable>
        <Pressable
          style={[styles.noIdeaButton, { marginTop: 12 }]}
          onPress={() => onSubmit("no_idea")}
        >
          <Text style={styles.noIdeaButtonText}>I didn't catch any of that</Text>
        </Pressable>
      </View>
    );
  }

  if (cardState === "arabic") {
    return (
      <View style={styles.actionColumn}>
        <Pressable style={styles.showButton} onPress={onAdvance}>
          <Text style={styles.showButtonText}>Show Translation</Text>
        </Pressable>
      </View>
    );
  }

  // cardState === "answer"
  return (
    <View style={styles.actionColumn}>
      {missedCount > 0 && (
        <Text style={styles.missedHint}>
          {missedCount} word{missedCount > 1 ? "s" : ""} marked
        </Text>
      )}
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, styles.gotItButton]}
          onPress={() => onSubmit("understood")}
        >
          <Text style={styles.actionButtonText}>Got it</Text>
        </Pressable>
        {missedCount > 0 && (
          <Pressable
            style={[styles.actionButton, styles.continueButton]}
            onPress={() => onSubmit("partial")}
          >
            <Text style={styles.actionButtonText}>Continue</Text>
          </Pressable>
        )}
      </View>
      {missedCount === 0 && (
        <Text style={styles.tapHint}>Tap any word you didn't catch</Text>
      )}
    </View>
  );
}

// --- Reading Card (sentence mode) ---

function SentenceCard({
  card,
  cardState,
  missedIndices,
  onToggleMissed,
}: {
  card: ReviewCard;
  cardState: ReadingCardState;
  missedIndices: Set<number>;
  onToggleMissed: (index: number) => void;
}) {
  const sentence = card.sentence!;
  const words = sentence.arabic.split(/\s+/);

  return (
    <>
      <Text style={styles.sentenceArabic}>
        {words.map((word, i) => {
          const isTarget = isTargetWordIndex(word, card.lemma_ar_bare);
          const isMissed = cardState === "back" && missedIndices.has(i);

          const wordStyle = isMissed
            ? styles.missedWord
            : isTarget
            ? styles.targetWord
            : undefined;

          const wordEl = (
            <Text key={`w-${i}`} style={wordStyle}>
              {word}
            </Text>
          );

          if (cardState === "back") {
            return (
              <Text key={`t-${i}`}>
                {i > 0 && " "}
                <Text onPress={() => onToggleMissed(i)} style={wordStyle}>
                  {word}
                </Text>
              </Text>
            );
          }

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              {wordEl}
            </Text>
          );
        })}
      </Text>

      {cardState === "back" && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />

          <Text style={styles.sentenceEnglish}>{sentence.english}</Text>
          <Text style={styles.sentenceTranslit}>{sentence.transliteration}</Text>

          <View style={styles.targetWordBox}>
            <Text style={styles.targetWordArabic}>{card.lemma_ar}</Text>
            <Text style={styles.targetWordGloss}>{card.gloss_en}</Text>
            <View style={styles.targetWordMeta}>
              {card.root && <Text style={styles.metaText}>{card.root}</Text>}
              <Text style={styles.metaText}>{card.pos}</Text>
            </View>
          </View>
        </View>
      )}
    </>
  );
}

function WordOnlyCard({ card, cardState }: { card: ReviewCard; cardState: ReadingCardState }) {
  return (
    <>
      <Text style={styles.wordOnlyIndicator}>word only</Text>
      <Text style={styles.wordOnlyArabic}>{card.lemma_ar}</Text>

      {cardState === "back" && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />
          <Text style={styles.wordOnlyGloss}>{card.gloss_en}</Text>
          <View style={styles.targetWordMeta}>
            {card.root && <Text style={styles.metaText}>{card.root}</Text>}
            <Text style={styles.metaText}>{card.pos}</Text>
          </View>
        </View>
      )}
    </>
  );
}

// --- Reading Actions ---

function ReadingActions({
  cardState,
  hasSentence,
  missedCount,
  onAdvance,
  onSubmit,
}: {
  cardState: ReadingCardState;
  hasSentence: boolean;
  missedCount: number;
  onAdvance: () => void;
  onSubmit: (signal: ComprehensionSignal) => void;
}) {
  if (cardState === "front") {
    return (
      <View style={styles.actionColumn}>
        <Pressable style={styles.showButton} onPress={onAdvance}>
          <Text style={styles.showButtonText}>Show Answer</Text>
        </Pressable>
        {hasSentence && (
          <Pressable
            style={[styles.noIdeaButton, { marginTop: 12 }]}
            onPress={() => onSubmit("no_idea")}
          >
            <Text style={styles.noIdeaButtonText}>I have no idea</Text>
          </Pressable>
        )}
      </View>
    );
  }

  return (
    <View style={styles.actionColumn}>
      {hasSentence && missedCount > 0 && (
        <Text style={styles.missedHint}>
          {missedCount} word{missedCount > 1 ? "s" : ""} marked
        </Text>
      )}
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, styles.gotItButton]}
          onPress={() => onSubmit("understood")}
        >
          <Text style={styles.actionButtonText}>Got it</Text>
        </Pressable>
        {(missedCount > 0 || !hasSentence) && (
          <Pressable
            style={[styles.actionButton, styles.continueButton]}
            onPress={() => onSubmit(hasSentence ? "partial" : "partial")}
          >
            <Text style={styles.actionButtonText}>
              {hasSentence ? "Continue" : "Missed"}
            </Text>
          </Pressable>
        )}
      </View>
      {hasSentence && missedCount === 0 && (
        <Text style={styles.tapHint}>Tap any word you didn't know</Text>
      )}
    </View>
  );
}

// --- Progress Bar ---

function ProgressBar({ current, total, mode }: { current: number; total: number; mode: ReviewMode }) {
  const pct = (current / total) * 100;
  const barColor = mode === "listening" ? colors.listening : colors.accent;
  return (
    <View style={styles.progressContainer}>
      <Text style={styles.progressText}>
        Card {current} of {total}
      </Text>
      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${pct}%`, backgroundColor: barColor }]} />
      </View>
    </View>
  );
}

// --- Styles ---

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  listeningContainer: {
    backgroundColor: colors.listeningBg,
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
  listeningCard: {
    borderWidth: 1,
    borderColor: colors.listening + "40",
  },

  // Mode toggle
  modeToggle: {
    flexDirection: "row",
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    padding: 3,
    marginBottom: 16,
  },
  modeButton: {
    paddingVertical: 8,
    paddingHorizontal: 20,
    borderRadius: 8,
  },
  modeButtonActive: {
    backgroundColor: colors.accent,
  },
  modeButtonActiveListening: {
    backgroundColor: colors.listening,
  },
  modeButtonText: {
    color: colors.textSecondary,
    fontSize: 14,
    fontWeight: "600",
  },
  modeButtonTextActive: {
    color: "#fff",
  },

  // Listening audio state
  listeningAudioState: {
    alignItems: "center",
    paddingVertical: 40,
  },
  listeningIcon: {
    fontSize: 64,
    marginBottom: 16,
  },
  listeningHint: {
    color: colors.textSecondary,
    fontSize: 16,
    fontStyle: "italic",
  },

  // Sentence mode
  sentenceArabic: {
    fontSize: 30,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
    lineHeight: 50,
  },
  targetWord: {
    color: colors.targetWord,
    textDecorationLine: "underline",
    textDecorationColor: colors.targetWord,
  },
  missedWord: {
    color: colors.missed,
    textDecorationLine: "underline",
    textDecorationColor: colors.missed,
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
  targetWordBox: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    padding: 16,
    marginTop: 20,
    width: "100%",
    alignItems: "center",
  },
  targetWordArabic: {
    fontSize: fonts.arabicMedium,
    color: colors.targetWord,
    writingDirection: "rtl",
    fontWeight: "600",
  },
  targetWordGloss: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
    marginTop: 4,
  },
  targetWordMeta: {
    flexDirection: "row",
    gap: 12,
    marginTop: 8,
  },
  metaText: {
    fontSize: 14,
    color: colors.textSecondary,
  },

  // Word-only fallback
  wordOnlyIndicator: {
    fontSize: 11,
    color: colors.textSecondary,
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 12,
    opacity: 0.6,
  },
  wordOnlyArabic: {
    fontSize: fonts.arabicLarge,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
    lineHeight: 52,
    fontWeight: "600",
  },
  wordOnlyGloss: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "600",
    textAlign: "center",
  },

  // Shared
  answerSection: {
    width: "100%",
    alignItems: "center",
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    width: "80%",
    marginVertical: 20,
  },
  showButton: {
    backgroundColor: colors.accent,
    paddingVertical: 16,
    paddingHorizontal: 48,
    borderRadius: 12,
    width: "100%",
    maxWidth: 500,
  },
  showButtonText: {
    color: "#fff",
    fontSize: 18,
    fontWeight: "600",
    textAlign: "center",
  },

  // "No idea" / "Didn't catch" button
  noIdeaButton: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: colors.noIdea + "80",
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 12,
    width: "100%",
    maxWidth: 500,
  },
  noIdeaButtonText: {
    color: colors.noIdea,
    fontSize: 15,
    fontWeight: "500",
    textAlign: "center",
  },

  // Action buttons (post-reveal)
  actionColumn: {
    width: "100%",
    maxWidth: 500,
    alignItems: "center",
  },
  actionRow: {
    flexDirection: "row",
    gap: 12,
    width: "100%",
  },
  actionButton: {
    flex: 1,
    paddingVertical: 16,
    borderRadius: 12,
    alignItems: "center",
  },
  gotItButton: {
    backgroundColor: colors.gotIt,
  },
  continueButton: {
    backgroundColor: colors.accent,
  },
  actionButtonText: {
    color: "#fff",
    fontSize: 17,
    fontWeight: "700",
  },
  tapHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginTop: 12,
    opacity: 0.7,
  },
  tapHintListening: {
    color: colors.listening,
    fontSize: fonts.small,
    marginTop: 16,
    opacity: 0.8,
    textAlign: "center",
  },
  missedHint: {
    color: colors.missed,
    fontSize: fonts.small,
    marginBottom: 10,
    fontWeight: "600",
  },

  // Progress
  progressContainer: {
    width: "100%",
    maxWidth: 500,
    marginBottom: 20,
  },
  progressText: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginBottom: 6,
    textAlign: "center",
  },
  progressTrack: {
    height: 4,
    backgroundColor: colors.surfaceLight,
    borderRadius: 2,
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    backgroundColor: colors.accent,
    borderRadius: 2,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: 18,
    marginBottom: 20,
    textAlign: "center",
  },
  startButton: {
    backgroundColor: colors.accent,
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 12,
    marginTop: 20,
  },
  startButtonText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "600",
  },
  summaryTitle: {
    fontSize: 28,
    color: colors.text,
    fontWeight: "700",
    marginBottom: 4,
  },
  summarySubtitle: {
    fontSize: 16,
    color: colors.textSecondary,
    marginBottom: 12,
  },
  summaryCount: {
    fontSize: 18,
    color: colors.textSecondary,
    marginBottom: 24,
  },
  summaryGrid: {
    flexDirection: "row",
    gap: 24,
    marginBottom: 16,
  },
  summaryItem: {
    alignItems: "center",
    minWidth: 60,
  },
  summaryLabel: {
    fontSize: 14,
    fontWeight: "600",
    marginBottom: 4,
  },
  summaryValue: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "700",
  },
});
