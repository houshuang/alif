import { useState, useEffect, useRef, useCallback } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
} from "react-native";
import { Audio } from "expo-av";
import { colors, fonts } from "../lib/theme";
import {
  getReviewSession,
  submitReview,
  getSentenceReviewSession,
  submitSentenceReview,
  introduceWord,
  BASE_URL,
} from "../lib/api";
import {
  ReviewCard,
  ReviewSession,
  ReviewMode,
  ComprehensionSignal,
  SentenceReviewItem,
  SentenceReviewSession,
  IntroCandidate,
} from "../lib/types";

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
  // Sentence-first session (preferred)
  const [sentenceSession, setSentenceSession] =
    useState<SentenceReviewSession | null>(null);
  // Legacy word-only fallback
  const [legacySession, setLegacySession] = useState<ReviewSession | null>(
    null
  );
  const [cardIndex, setCardIndex] = useState(0);
  const [cardState, setCardState] = useState<CardState>("front");
  const [loading, setLoading] = useState(true);
  const [results, setResults] = useState<SessionResults | null>(null);
  const [missedIndices, setMissedIndices] = useState<Set<number>>(new Set());
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [introQueue, setIntroQueue] = useState<IntroCandidate[]>([]);
  const [showingIntro, setShowingIntro] = useState<IntroCandidate | null>(null);
  const showTime = useRef<number>(0);
  const soundRef = useRef<Audio.Sound | null>(null);

  const usingSentences = sentenceSession !== null && sentenceSession.items.length > 0;
  const totalCards = usingSentences
    ? sentenceSession!.items.length
    : legacySession?.cards.length ?? 0;

  useEffect(() => {
    loadSession();
    return () => {
      cleanupSound();
    };
  }, []);

  useEffect(() => {
    if (cardState === "front" || cardState === "audio") {
      showTime.current = Date.now();
    }
  }, [cardIndex, cardState]);

  // TTS audio playback for listening mode
  useEffect(() => {
    if (cardState === "audio") {
      playTtsAudio();
    }
    return () => {
      cleanupSound();
    };
  }, [cardState, cardIndex]);

  async function cleanupSound() {
    if (soundRef.current) {
      try {
        await soundRef.current.unloadAsync();
      } catch {}
      soundRef.current = null;
    }
  }

  async function playTtsAudio() {
    setAudioPlaying(true);
    await cleanupSound();

    const currentItem = usingSentences
      ? sentenceSession!.items[cardIndex]
      : null;
    const arabicText = currentItem?.arabic_text
      ?? legacySession?.cards[cardIndex]?.sentence?.arabic
      ?? legacySession?.cards[cardIndex]?.lemma_ar;

    if (!arabicText) {
      setAudioPlaying(false);
      return;
    }

    const audioUri = currentItem?.audio_url
      ? `${BASE_URL}${currentItem.audio_url}`
      : `${BASE_URL}/api/tts/audio/${encodeURIComponent(arabicText)}`;

    try {
      const { sound } = await Audio.Sound.createAsync({
        uri: audioUri,
      });
      soundRef.current = sound;
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          setAudioPlaying(false);
        }
      });
      await sound.playAsync();
    } catch {
      // Fallback: simulate with timer if TTS fails
      const timer = setTimeout(() => {
        setAudioPlaying(false);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }

  async function loadSession(newMode?: ReviewMode) {
    const m = newMode ?? mode;
    setLoading(true);
    setResults(null);
    setCardIndex(0);
    setCardState(m === "listening" ? "audio" : "front");
    setMissedIndices(new Set());
    setAudioPlaying(false);
    setIntroQueue([]);
    setShowingIntro(null);
    setSentenceSession(null);
    setLegacySession(null);
    await cleanupSound();

    try {
      const ss = await getSentenceReviewSession(m);
      if (ss.items.length > 0) {
        setSentenceSession(ss);
        if (ss.intro_candidates && ss.intro_candidates.length > 0) {
          setIntroQueue(ss.intro_candidates);
        }
        setLoading(false);
        return;
      }
    } catch {}

    // Fallback to legacy word-only session
    try {
      const s = await getReviewSession(m);
      setLegacySession(s);
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

  async function handleSentenceSubmit(signal: ComprehensionSignal) {
    if (!sentenceSession) return;
    const item = sentenceSession.items[cardIndex];
    const responseMs = Date.now() - showTime.current;

    const missedLemmaIds: number[] = [];
    for (const idx of missedIndices) {
      const word = item.words[idx];
      if (word?.lemma_id != null) {
        missedLemmaIds.push(word.lemma_id);
      }
    }

    if (signal === "no_idea") {
      missedLemmaIds.push(item.primary_lemma_id);
    }

    await submitSentenceReview({
      sentence_id: item.sentence_id,
      primary_lemma_id: item.primary_lemma_id,
      comprehension_signal: signal,
      missed_lemma_ids: missedLemmaIds,
      response_ms: responseMs,
      session_id: sentenceSession.session_id,
      review_mode: mode,
    });

    advanceAfterSubmit(signal);
  }

  async function handleLegacySubmit(signal: ComprehensionSignal) {
    if (!legacySession) return;
    const card = legacySession.cards[cardIndex];
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
      session_id: legacySession.session_id,
      missed_words: missedWords,
      review_mode: mode,
      comprehension_signal: signal,
    });

    advanceAfterSubmit(signal);
  }

  function advanceAfterSubmit(signal: ComprehensionSignal) {
    const prev = results ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 };
    const next = {
      total: prev.total + 1,
      gotIt: prev.gotIt + (signal === "understood" ? 1 : 0),
      missed: prev.missed + (signal === "partial" ? 1 : 0),
      noIdea: prev.noIdea + (signal === "no_idea" ? 1 : 0),
    };

    const nextCardIndex = cardIndex + 1;

    if (nextCardIndex >= totalCards) {
      setResults(next);
      setCardState(mode === "listening" ? "audio" : "front");
    } else {
      setResults(next);

      // Check if there's an intro candidate to show at this position
      const pendingIntro = introQueue.find((c) => c.insert_at === nextCardIndex);
      if (pendingIntro) {
        setShowingIntro(pendingIntro);
        setIntroQueue((q) => q.filter((c) => c.lemma_id !== pendingIntro.lemma_id));
      }

      setCardIndex(nextCardIndex);
      setCardState(mode === "listening" ? "audio" : "front");
      setMissedIndices(new Set());
      setAudioPlaying(false);
    }
  }

  function handleSubmit(signal: ComprehensionSignal) {
    if (usingSentences) {
      handleSentenceSubmit(signal);
    } else {
      handleLegacySubmit(signal);
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

  if (totalCards === 0) {
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

  const isSessionDone = results && results.total >= totalCards;

  if (isSessionDone) {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.summaryTitle}>Session Complete</Text>
        <Text style={styles.summarySubtitle}>
          {mode === "listening" ? "Listening" : "Reading"} mode
        </Text>
        <Text style={styles.summaryCount}>
          {results.total} cards reviewed
        </Text>
        <View style={styles.summaryGrid}>
          <View style={styles.summaryItem}>
            <Text style={[styles.summaryLabel, { color: colors.gotIt }]}>
              Got it
            </Text>
            <Text style={styles.summaryValue}>{results.gotIt}</Text>
          </View>
          <View style={styles.summaryItem}>
            <Text style={[styles.summaryLabel, { color: colors.missed }]}>
              Missed
            </Text>
            <Text style={styles.summaryValue}>{results.missed}</Text>
          </View>
          {results.noIdea > 0 && (
            <View style={styles.summaryItem}>
              <Text style={[styles.summaryLabel, { color: colors.noIdea }]}>
                No idea
              </Text>
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

  const isListening = mode === "listening";

  // Inline intro card (shown between review cards)
  if (showingIntro) {
    return (
      <View style={styles.container}>
        <ModeToggle mode={mode} onSwitch={switchMode} />
        <ProgressBar current={cardIndex + 1} total={totalCards} mode={mode} />
        <InlineIntroCard
          candidate={showingIntro}
          onLearn={async () => {
            try {
              await introduceWord(showingIntro.lemma_id);
            } catch (e) {
              console.error("Failed to introduce word:", e);
            }
            setShowingIntro(null);
          }}
          onSkip={() => setShowingIntro(null)}
        />
      </View>
    );
  }

  // Sentence-first flow
  if (usingSentences) {
    const item = sentenceSession!.items[cardIndex];
    const isWordOnly = item.sentence_id === null;

    return (
      <View
        style={[styles.container, isListening && styles.listeningContainer]}
      >
        <ModeToggle mode={mode} onSwitch={switchMode} />
        <ProgressBar current={cardIndex + 1} total={totalCards} mode={mode} />

        <View style={[styles.card, isListening && styles.listeningCard]}>
          {isWordOnly ? (
            <WordOnlySentenceCard
              item={item}
              cardState={
                isListening
                  ? (cardState as ListeningCardState)
                  : (cardState as ReadingCardState)
              }
              isListening={isListening}
              audioPlaying={audioPlaying}
            />
          ) : isListening ? (
            <SentenceListeningCard
              item={item}
              cardState={cardState as ListeningCardState}
              missedIndices={missedIndices}
              onToggleMissed={toggleMissed}
              audioPlaying={audioPlaying}
            />
          ) : (
            <SentenceReadingCard
              item={item}
              cardState={cardState as ReadingCardState}
              missedIndices={missedIndices}
              onToggleMissed={toggleMissed}
            />
          )}
        </View>

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
            hasSentence={!isWordOnly}
            missedCount={missedIndices.size}
            onAdvance={advanceState}
            onSubmit={handleSubmit}
          />
        )}
      </View>
    );
  }

  // Legacy word-only fallback
  const card = legacySession!.cards[cardIndex];
  const hasSentence = card.sentence !== null;

  return (
    <View style={[styles.container, isListening && styles.listeningContainer]}>
      <ModeToggle mode={mode} onSwitch={switchMode} />
      <ProgressBar current={cardIndex + 1} total={totalCards} mode={mode} />

      <View style={[styles.card, isListening && styles.listeningCard]}>
        {isListening ? (
          <LegacyListeningCard
            card={card}
            cardState={cardState as ListeningCardState}
            missedIndices={missedIndices}
            onToggleMissed={toggleMissed}
            audioPlaying={audioPlaying}
          />
        ) : hasSentence ? (
          <LegacySentenceCard
            card={card}
            cardState={cardState as ReadingCardState}
            missedIndices={missedIndices}
            onToggleMissed={toggleMissed}
          />
        ) : (
          <LegacyWordOnlyCard
            card={card}
            cardState={cardState as ReadingCardState}
          />
        )}
      </View>

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

function ModeToggle({
  mode,
  onSwitch,
}: {
  mode: ReviewMode;
  onSwitch: (m: ReviewMode) => void;
}) {
  return (
    <View style={styles.modeToggle}>
      <Pressable
        style={[
          styles.modeButton,
          mode === "reading" && styles.modeButtonActive,
        ]}
        onPress={() => onSwitch("reading")}
      >
        <Text
          style={[
            styles.modeButtonText,
            mode === "reading" && styles.modeButtonTextActive,
          ]}
        >
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

// --- Sentence-First Cards ---

function SentenceReadingCard({
  item,
  cardState,
  missedIndices,
  onToggleMissed,
}: {
  item: SentenceReviewItem;
  cardState: ReadingCardState;
  missedIndices: Set<number>;
  onToggleMissed: (index: number) => void;
}) {
  const arabicText = item.arabic_diacritized ?? item.arabic_text;

  return (
    <>
      <Text style={styles.sentenceArabic}>
        {item.words.map((word, i) => {
          const isMissed = cardState === "back" && missedIndices.has(i);
          const isPrimary = word.lemma_id === item.primary_lemma_id;

          const wordStyle = isMissed
            ? styles.missedWord
            : isPrimary
            ? styles.targetWord
            : undefined;

          if (cardState === "back") {
            return (
              <Text key={`t-${i}`}>
                {i > 0 && " "}
                <Text onPress={() => onToggleMissed(i)} style={wordStyle}>
                  {word.surface_form}
                </Text>
              </Text>
            );
          }

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text style={wordStyle}>{word.surface_form}</Text>
            </Text>
          );
        })}
      </Text>

      {cardState === "back" && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />
          <Text style={styles.sentenceEnglish}>
            {item.english_translation}
          </Text>
          {item.transliteration && (
            <Text style={styles.sentenceTranslit}>
              {item.transliteration}
            </Text>
          )}
          <View style={styles.targetWordBox}>
            <Text style={styles.targetWordArabic}>
              {item.primary_lemma_ar}
            </Text>
            <Text style={styles.targetWordGloss}>
              {item.primary_gloss_en}
            </Text>
          </View>
        </View>
      )}
    </>
  );
}

function SentenceListeningCard({
  item,
  cardState,
  missedIndices,
  onToggleMissed,
  audioPlaying,
}: {
  item: SentenceReviewItem;
  cardState: ListeningCardState;
  missedIndices: Set<number>;
  onToggleMissed: (index: number) => void;
  audioPlaying: boolean;
}) {
  if (cardState === "audio") {
    return (
      <View style={styles.listeningAudioState}>
        <Text style={styles.listeningIcon}>
          {audioPlaying ? "\u{1F50A}" : "\u{1F442}"}
        </Text>
        <Text style={styles.listeningHint}>
          {audioPlaying ? "Listening..." : "Audio finished \u2014 tap to reveal"}
        </Text>
      </View>
    );
  }

  const showAnswer = cardState === "answer";

  return (
    <>
      <Text style={styles.sentenceArabic}>
        {item.words.map((word, i) => {
          const isMissed = missedIndices.has(i);
          const isPrimary = word.lemma_id === item.primary_lemma_id;

          const wordStyle = isMissed
            ? styles.missedWord
            : showAnswer && isPrimary
            ? styles.targetWord
            : undefined;

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text onPress={() => onToggleMissed(i)} style={wordStyle}>
                {word.surface_form}
              </Text>
            </Text>
          );
        })}
      </Text>

      {showAnswer && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />
          <Text style={styles.sentenceEnglish}>
            {item.english_translation}
          </Text>
          {item.transliteration && (
            <Text style={styles.sentenceTranslit}>
              {item.transliteration}
            </Text>
          )}
          <View style={styles.targetWordBox}>
            <Text style={styles.targetWordArabic}>
              {item.primary_lemma_ar}
            </Text>
            <Text style={styles.targetWordGloss}>
              {item.primary_gloss_en}
            </Text>
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

function WordOnlySentenceCard({
  item,
  cardState,
  isListening,
  audioPlaying,
}: {
  item: SentenceReviewItem;
  cardState: CardState;
  isListening: boolean;
  audioPlaying: boolean;
}) {
  if (isListening && cardState === "audio") {
    return (
      <View style={styles.listeningAudioState}>
        <Text style={styles.listeningIcon}>
          {audioPlaying ? "\u{1F50A}" : "\u{1F442}"}
        </Text>
        <Text style={styles.listeningHint}>
          {audioPlaying ? "Listening..." : "Audio finished \u2014 tap to reveal"}
        </Text>
      </View>
    );
  }

  const showBack =
    cardState === "back" || cardState === "answer";
  const showArabic = !isListening || cardState !== "audio";

  return (
    <>
      <Text style={styles.wordOnlyIndicator}>word only</Text>
      {showArabic && (
        <Text style={styles.wordOnlyArabic}>{item.primary_lemma_ar}</Text>
      )}
      {showBack && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />
          <Text style={styles.wordOnlyGloss}>{item.primary_gloss_en}</Text>
        </View>
      )}
    </>
  );
}

// --- Legacy Cards (old word-centric flow) ---

function LegacyListeningCard({
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
        <Text style={styles.listeningIcon}>
          {audioPlaying ? "\u{1F50A}" : "\u{1F442}"}
        </Text>
        <Text style={styles.listeningHint}>
          {audioPlaying ? "Listening..." : "Audio finished \u2014 tap to reveal"}
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
          <Text style={styles.sentenceTranslit}>
            {sentence.transliteration}
          </Text>

          <View style={styles.targetWordBox}>
            <Text style={styles.targetWordArabic}>{card.lemma_ar}</Text>
            <Text style={styles.targetWordGloss}>{card.gloss_en}</Text>
            <View style={styles.targetWordMeta}>
              {card.root && (
                <Text style={styles.metaText}>{card.root}</Text>
              )}
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

function LegacySentenceCard({
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
              <Text style={wordStyle}>{word}</Text>
            </Text>
          );
        })}
      </Text>

      {cardState === "back" && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />
          <Text style={styles.sentenceEnglish}>{sentence.english}</Text>
          <Text style={styles.sentenceTranslit}>
            {sentence.transliteration}
          </Text>

          <View style={styles.targetWordBox}>
            <Text style={styles.targetWordArabic}>{card.lemma_ar}</Text>
            <Text style={styles.targetWordGloss}>{card.gloss_en}</Text>
            <View style={styles.targetWordMeta}>
              {card.root && (
                <Text style={styles.metaText}>{card.root}</Text>
              )}
              <Text style={styles.metaText}>{card.pos}</Text>
            </View>
          </View>
        </View>
      )}
    </>
  );
}

function LegacyWordOnlyCard({
  card,
  cardState,
}: {
  card: ReviewCard;
  cardState: ReadingCardState;
}) {
  return (
    <>
      <Text style={styles.wordOnlyIndicator}>word only</Text>
      <Text style={styles.wordOnlyArabic}>{card.lemma_ar}</Text>

      {cardState === "back" && (
        <View style={styles.answerSection}>
          <View style={styles.divider} />
          <Text style={styles.wordOnlyGloss}>{card.gloss_en}</Text>
          <View style={styles.targetWordMeta}>
            {card.root && (
              <Text style={styles.metaText}>{card.root}</Text>
            )}
            <Text style={styles.metaText}>{card.pos}</Text>
          </View>
        </View>
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
          <Text style={styles.noIdeaButtonText}>
            I didn't catch any of that
          </Text>
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

function ProgressBar({
  current,
  total,
  mode,
}: {
  current: number;
  total: number;
  mode: ReviewMode;
}) {
  const pct = (current / total) * 100;
  const barColor = mode === "listening" ? colors.listening : colors.accent;
  return (
    <View style={styles.progressContainer}>
      <Text style={styles.progressText}>
        Card {current} of {total}
      </Text>
      <View style={styles.progressTrack}>
        <View
          style={[
            styles.progressFill,
            { width: `${pct}%`, backgroundColor: barColor },
          ]}
        />
      </View>
    </View>
  );
}

// --- Inline Intro Card ---

function InlineIntroCard({
  candidate,
  onLearn,
  onSkip,
}: {
  candidate: IntroCandidate;
  onLearn: () => void;
  onSkip: () => void;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.introLabel}>New Word</Text>
      <Text style={styles.introArabic}>{candidate.lemma_ar}</Text>
      <Text style={styles.introEnglish}>{candidate.gloss_en}</Text>
      {candidate.transliteration && (
        <Text style={styles.introTranslit}>{candidate.transliteration}</Text>
      )}
      <View style={styles.introMeta}>
        {candidate.pos && (
          <Text style={styles.introPos}>{candidate.pos}</Text>
        )}
        {candidate.root && (
          <Text style={styles.introRoot}>Root: {candidate.root}</Text>
        )}
        {candidate.root_meaning && (
          <Text style={styles.introRootMeaning}>{candidate.root_meaning}</Text>
        )}
      </View>
      <View style={styles.introButtons}>
        <Pressable style={[styles.actionButton, styles.gotItButton]} onPress={onLearn}>
          <Text style={styles.actionButtonText}>Learn</Text>
        </Pressable>
        <Pressable style={[styles.actionButton, { backgroundColor: colors.surfaceLight }]} onPress={onSkip}>
          <Text style={[styles.actionButtonText, { color: colors.textSecondary }]}>Skip</Text>
        </Pressable>
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
  introLabel: {
    fontSize: 11,
    color: colors.accent,
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 12,
    fontWeight: "700",
  },
  introArabic: {
    fontSize: 40,
    color: colors.arabic,
    fontWeight: "700",
    writingDirection: "rtl",
    marginBottom: 12,
  },
  introEnglish: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 6,
  },
  introTranslit: {
    fontSize: 16,
    color: colors.textSecondary,
    fontStyle: "italic",
    marginBottom: 12,
  },
  introMeta: {
    gap: 4,
    alignItems: "center",
    marginBottom: 20,
  },
  introPos: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  introRoot: {
    fontSize: 13,
    color: colors.accent,
  },
  introRootMeaning: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  introButtons: {
    flexDirection: "row",
    gap: 12,
    width: "100%",
  },
});
