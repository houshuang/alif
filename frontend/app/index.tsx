import { useState, useEffect, useRef, useCallback } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Animated,
} from "react-native";
import { Audio } from "expo-av";
import { colors, fonts } from "../lib/theme";
import {
  getReviewSession,
  submitReview,
  getSentenceReviewSession,
  submitSentenceReview,
  getAnalytics,
  lookupReviewWord,
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
  Analytics,
  WordLookupResult,
} from "../lib/types";
import { syncEvents } from "../lib/sync-events";
import { useNetStatus } from "../lib/net-status";
import AskAI from "../lib/AskAI";

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

export default function ReadingScreen() {
  return <ReviewScreen fixedMode="reading" />;
}

export function ReviewScreen({ fixedMode }: { fixedMode: ReviewMode }) {
  const mode = fixedMode;
  const online = useNetStatus();
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
  const [autoIntroduced, setAutoIntroduced] = useState<IntroCandidate[]>([]);
  const [lookupResult, setLookupResult] = useState<WordLookupResult | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupShowMeaning, setLookupShowMeaning] = useState(false);
  const showTime = useRef<number>(0);
  const soundRef = useRef<Audio.Sound | null>(null);

  const usingSentences = sentenceSession !== null && sentenceSession.items.length > 0;
  const totalCards = usingSentences
    ? sentenceSession!.items.length
    : legacySession?.cards.length ?? 0;

  useEffect(() => {
    Audio.setAudioModeAsync({
      playsInSilentModeIOS: true,
      staysActiveInBackground: false,
    });
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

  // TTS audio playback for listening mode — wait until session data is loaded
  useEffect(() => {
    if (cardState === "audio" && !loading && totalCards > 0) {
      playTtsAudio();
    }
    return () => {
      cleanupSound();
    };
  }, [cardState, cardIndex, loading, totalCards]);

  // Reload session when sync completes and user is between sessions
  useEffect(() => {
    return syncEvents.on("synced", () => {
      const isSessionDone = results && results.total >= totalCards;
      if (isSessionDone || totalCards === 0) {
        loadSession();
      }
    });
  }, [results, totalCards]);

  async function cleanupSound() {
    if (soundRef.current) {
      try {
        await soundRef.current.unloadAsync();
      } catch {}
      soundRef.current = null;
    }
  }

  async function playTtsAudio(slow = false) {
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
      : `${BASE_URL}/api/tts/speak/${encodeURIComponent(arabicText)}`;

    try {
      const { sound } = await Audio.Sound.createAsync({
        uri: audioUri,
      });
      soundRef.current = sound;
      if (slow) {
        await sound.setRateAsync(0.6, true);
      }
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          setAudioPlaying(false);
        }
      });
      await sound.playAsync();
    } catch {
      setAudioPlaying(false);
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
    setAutoIntroduced([]);
    setSentenceSession(null);
    setLegacySession(null);
    await cleanupSound();

    try {
      const ss = await getSentenceReviewSession(m);
      if (ss.items.length > 0) {
        setSentenceSession(ss);
        if (ss.intro_candidates && ss.intro_candidates.length > 0) {
          setAutoIntroduced(ss.intro_candidates);
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

  const handleWordLookup = useCallback(async (index: number, lemmaId: number) => {
    // Auto-mark as missed
    setMissedIndices((prev) => {
      const next = new Set(prev);
      next.add(index);
      return next;
    });
    setLookupLoading(true);
    setLookupShowMeaning(false);
    setLookupResult(null);
    try {
      const result = await lookupReviewWord(lemmaId);
      setLookupResult(result);
      // If root has 2+ known siblings, don't show meaning yet (prediction mode)
      const knownSiblings = result.root_family.filter(
        (s) => s.state === "known" || s.state === "learning"
      );
      if (knownSiblings.length >= 2) {
        setLookupShowMeaning(false);
      } else {
        setLookupShowMeaning(true);
      }
    } catch {
      setLookupResult(null);
      setLookupShowMeaning(true);
    }
    setLookupLoading(false);
  }, []);

  const dismissLookup = useCallback(() => {
    setLookupResult(null);
    setLookupShowMeaning(false);
  }, []);

  function handleSentenceSubmit(signal: ComprehensionSignal) {
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

    submitSentenceReview({
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

  function handleLegacySubmit(signal: ComprehensionSignal) {
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

    submitReview({
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
      setCardIndex(nextCardIndex);
      setCardState(mode === "listening" ? "audio" : "front");
      setMissedIndices(new Set());
      setAudioPlaying(false);
      setLookupResult(null);
      setLookupShowMeaning(false);
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

  function buildContext(): string {
    const parts: string[] = [`Mode: ${mode}`];
    if (usingSentences && sentenceSession) {
      const item = sentenceSession.items[cardIndex];
      if (item) {
        parts.push(`Arabic: ${item.arabic_text || item.primary_lemma_ar}`);
        parts.push(`English: ${item.english_translation || item.primary_gloss_en}`);
        const glosses = item.words
          .filter((w) => w.gloss_en)
          .map((w) => `${w.surface_form} (${w.gloss_en})`)
          .join(", ");
        if (glosses) parts.push(`Words: ${glosses}`);
        const missed = Array.from(missedIndices)
          .map((i) => item.words[i]?.surface_form)
          .filter(Boolean);
        if (missed.length > 0) parts.push(`Missed: ${missed.join(", ")}`);
      }
    } else if (legacySession) {
      const card = legacySession.cards[cardIndex];
      if (card) {
        parts.push(`Word: ${card.lemma_ar} (${card.gloss_en})`);
        if (card.sentence) {
          parts.push(`Sentence: ${card.sentence.arabic}`);
          parts.push(`Translation: ${card.sentence.english}`);
        }
      }
    }
    return parts.join("\n");
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
      <EmptyState
        online={online}
        mode={mode}
        onRefresh={() => loadSession()}
      />
    );
  }

  const isSessionDone = results && results.total >= totalCards;

  if (isSessionDone) {
    return (
      <SessionComplete
        results={results}
        mode={mode}
        autoIntroduced={autoIntroduced}
        onNewSession={() => loadSession()}
      />
    );
  }

  const isListening = mode === "listening";

  // Sentence-first flow
  if (usingSentences) {
    const item = sentenceSession!.items[cardIndex];
    const isWordOnly = item.sentence_id === null;

    return (
      <View
        style={[styles.container, isListening && styles.listeningContainer]}
      >

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
              onReplay={() => playTtsAudio()}
              onReplaySlow={() => playTtsAudio(true)}
            />
          ) : isListening ? (
            <SentenceListeningCard
              item={item}
              cardState={cardState as ListeningCardState}
              missedIndices={missedIndices}
              onToggleMissed={toggleMissed}
              audioPlaying={audioPlaying}
              onReplay={() => playTtsAudio()}
              onReplaySlow={() => playTtsAudio(true)}
            />
          ) : (
            <SentenceReadingCard
              item={item}
              cardState={cardState as ReadingCardState}
              missedIndices={missedIndices}
              onToggleMissed={toggleMissed}
              onWordLookup={handleWordLookup}
              lookupResult={lookupResult}
              lookupLoading={lookupLoading}
              lookupShowMeaning={lookupShowMeaning}
              onShowMeaning={() => setLookupShowMeaning(true)}
              onDismissLookup={dismissLookup}
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
        <AskAI contextBuilder={buildContext} screen="review" />
      </View>
    );
  }

  // Legacy word-only fallback
  const card = legacySession!.cards[cardIndex];
  const hasSentence = card.sentence !== null;

  return (
    <View style={[styles.container, isListening && styles.listeningContainer]}>


      <ProgressBar current={cardIndex + 1} total={totalCards} mode={mode} />

      <View style={[styles.card, isListening && styles.listeningCard]}>
        {isListening ? (
          <LegacyListeningCard
            card={card}
            cardState={cardState as ListeningCardState}
            missedIndices={missedIndices}
            onToggleMissed={toggleMissed}
            audioPlaying={audioPlaying}
            onReplay={() => playTtsAudio()}
            onReplaySlow={() => playTtsAudio(true)}
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
      <AskAI contextBuilder={buildContext} screen="review" />
    </View>
  );
}


// --- Sentence-First Cards ---

function SentenceReadingCard({
  item,
  cardState,
  missedIndices,
  onToggleMissed,
  onWordLookup,
  lookupResult,
  lookupLoading,
  lookupShowMeaning,
  onShowMeaning,
  onDismissLookup,
}: {
  item: SentenceReviewItem;
  cardState: ReadingCardState;
  missedIndices: Set<number>;
  onToggleMissed: (index: number) => void;
  onWordLookup: (index: number, lemmaId: number) => void;
  lookupResult: WordLookupResult | null;
  lookupLoading: boolean;
  lookupShowMeaning: boolean;
  onShowMeaning: () => void;
  onDismissLookup: () => void;
}) {
  return (
    <>
      <Text style={styles.sentenceArabic}>
        {item.words.map((word, i) => {
          const isMissed = missedIndices.has(i);

          if (cardState === "back") {
            return (
              <Text key={`t-${i}`}>
                {i > 0 && " "}
                <Text
                  onPress={() => onToggleMissed(i)}
                  style={isMissed ? styles.missedWord : undefined}
                >
                  {word.surface_form}
                </Text>
              </Text>
            );
          }

          // Front phase: tap to look up (non-function words only)
          const canTap = word.lemma_id != null && !word.is_function_word;
          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text
                onPress={canTap ? () => onWordLookup(i, word.lemma_id!) : undefined}
                style={isMissed ? styles.missedWord : undefined}
              >
                {word.surface_form}
              </Text>
            </Text>
          );
        })}
      </Text>

      {/* Lookup panel (front phase) */}
      {cardState === "front" && (lookupResult || lookupLoading) && (
        <WordLookupPanel
          result={lookupResult}
          loading={lookupLoading}
          showMeaning={lookupShowMeaning}
          onShowMeaning={onShowMeaning}
          onDismiss={onDismissLookup}
        />
      )}

      {cardState === "front" && !lookupResult && !lookupLoading && (
        <Text style={styles.tapHintFront}>Tap a word to look it up</Text>
      )}

      {cardState === "back" && (
        <View style={styles.answerSection}>
          {missedIndices.size === 0 && (
            <Text style={styles.tapHint}>Tap any word you didn't know</Text>
          )}
          <View style={styles.divider} />
          <Text style={styles.sentenceEnglish}>
            {item.english_translation}
          </Text>
          {item.transliteration && (
            <Text style={styles.sentenceTranslit}>
              {item.transliteration}
            </Text>
          )}
          <RootInfoBar words={item.words} missedIndices={missedIndices} />
        </View>
      )}
    </>
  );
}

function WordLookupPanel({
  result,
  loading,
  showMeaning,
  onShowMeaning,
  onDismiss,
}: {
  result: WordLookupResult | null;
  loading: boolean;
  showMeaning: boolean;
  onShowMeaning: () => void;
  onDismiss: () => void;
}) {
  if (loading) {
    return (
      <View style={styles.lookupPanel}>
        <ActivityIndicator size="small" color={colors.accent} />
      </View>
    );
  }

  if (!result) return null;

  const knownSiblings = result.root_family.filter(
    (s) => s.state === "known" || s.state === "learning"
  );
  const hasPredictionMode = knownSiblings.length >= 2 && !showMeaning;

  return (
    <View style={styles.lookupPanel}>
      {result.root && (
        <Text style={styles.lookupRoot}>
          Root: {result.root}
          {result.root_meaning ? ` \u2014 ${result.root_meaning}` : ""}
        </Text>
      )}
      {hasPredictionMode ? (
        <>
          <Text style={styles.lookupPredictionHint}>
            You know words from this root:
          </Text>
          <View style={styles.lookupSiblings}>
            {knownSiblings.slice(0, 4).map((sib) => (
              <View key={sib.lemma_id} style={styles.lookupSiblingPill}>
                <Text style={styles.lookupSiblingAr}>{sib.lemma_ar}</Text>
                <Text style={styles.lookupSiblingEn}>{sib.gloss_en}</Text>
              </View>
            ))}
          </View>
          <Text style={styles.lookupPredictionPrompt}>
            Can you guess the meaning?
          </Text>
          <Pressable style={styles.lookupRevealButton} onPress={onShowMeaning}>
            <Text style={styles.lookupRevealText}>Show meaning</Text>
          </Pressable>
        </>
      ) : (
        <>
          <Text style={styles.lookupMeaning}>
            {result.lemma_ar} \u2014 {result.gloss_en}
          </Text>
          {result.transliteration && (
            <Text style={styles.lookupTranslit}>{result.transliteration}</Text>
          )}
          {result.pos && (
            <Text style={styles.lookupPos}>{result.pos}</Text>
          )}
          {knownSiblings.length > 0 && (
            <View style={styles.lookupSiblings}>
              {knownSiblings.slice(0, 3).map((sib) => (
                <View key={sib.lemma_id} style={styles.lookupSiblingPill}>
                  <Text style={styles.lookupSiblingAr}>{sib.lemma_ar}</Text>
                  <Text style={styles.lookupSiblingEn}>{sib.gloss_en}</Text>
                </View>
              ))}
            </View>
          )}
        </>
      )}
      <Pressable onPress={onDismiss} style={styles.lookupDismiss}>
        <Text style={styles.lookupDismissText}>Done</Text>
      </Pressable>
    </View>
  );
}

function RootInfoBar({
  words,
  missedIndices,
}: {
  words: SentenceReviewItem["words"];
  missedIndices: Set<number>;
}) {
  // Show root info for missed words that have roots
  const missedWithRoots = Array.from(missedIndices)
    .map((i) => words[i])
    .filter((w) => w && w.root);

  if (missedWithRoots.length === 0) return null;

  // Deduplicate by root
  const seen = new Set<string>();
  const unique = missedWithRoots.filter((w) => {
    if (seen.has(w.root!)) return false;
    seen.add(w.root!);
    return true;
  });

  return (
    <View style={styles.rootInfoBar}>
      {unique.map((w) => (
        <Text key={w.root} style={styles.rootInfoText}>
          {w.surface_form}: root {w.root}
          {w.root_meaning ? ` (${w.root_meaning})` : ""}
        </Text>
      ))}
    </View>
  );
}

function AudioControls({
  audioPlaying,
  onReplay,
  onReplaySlow,
}: {
  audioPlaying: boolean;
  onReplay: () => void;
  onReplaySlow: () => void;
}) {
  return (
    <View style={styles.audioControls}>
      <Pressable
        style={[styles.audioControlButton, audioPlaying && styles.audioControlDisabled]}
        onPress={onReplay}
        disabled={audioPlaying}
      >
        <Text style={styles.audioControlIcon}>{"\u{1F501}"}</Text>
        <Text style={styles.audioControlLabel}>Replay</Text>
      </Pressable>
      <Pressable
        style={[styles.audioControlButton, audioPlaying && styles.audioControlDisabled]}
        onPress={onReplaySlow}
        disabled={audioPlaying}
      >
        <Text style={styles.audioControlIcon}>{"\u{1F422}"}</Text>
        <Text style={styles.audioControlLabel}>Slow</Text>
      </Pressable>
    </View>
  );
}

function SentenceListeningCard({
  item,
  cardState,
  missedIndices,
  onToggleMissed,
  audioPlaying,
  onReplay,
  onReplaySlow,
}: {
  item: SentenceReviewItem;
  cardState: ListeningCardState;
  missedIndices: Set<number>;
  onToggleMissed: (index: number) => void;
  audioPlaying: boolean;
  onReplay: () => void;
  onReplaySlow: () => void;
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
        <AudioControls audioPlaying={audioPlaying} onReplay={onReplay} onReplaySlow={onReplaySlow} />
      </View>
    );
  }

  const showAnswer = cardState === "answer";

  return (
    <>
      <Text style={styles.sentenceArabic}>
        {item.words.map((word, i) => {
          const isMissed = missedIndices.has(i);

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text
                onPress={() => onToggleMissed(i)}
                style={isMissed ? styles.missedWord : undefined}
              >
                {word.surface_form}
              </Text>
            </Text>
          );
        })}
      </Text>

      <AudioControls audioPlaying={audioPlaying} onReplay={onReplay} onReplaySlow={onReplaySlow} />

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
  onReplay,
  onReplaySlow,
}: {
  item: SentenceReviewItem;
  cardState: CardState;
  isListening: boolean;
  audioPlaying: boolean;
  onReplay: () => void;
  onReplaySlow: () => void;
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
        <AudioControls audioPlaying={audioPlaying} onReplay={onReplay} onReplaySlow={onReplaySlow} />
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
  onReplay,
  onReplaySlow,
}: {
  card: ReviewCard;
  cardState: ListeningCardState;
  missedIndices: Set<number>;
  onToggleMissed: (index: number) => void;
  audioPlaying: boolean;
  onReplay: () => void;
  onReplaySlow: () => void;
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
        <AudioControls audioPlaying={audioPlaying} onReplay={onReplay} onReplaySlow={onReplaySlow} />
      </View>
    );
  }

  const words = sentence.arabic.split(/\s+/);
  const showAnswer = cardState === "answer";

  return (
    <>
      <Text style={styles.sentenceArabic}>
        {words.map((word, i) => {
          const isMissed = missedIndices.has(i);

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text
                onPress={() => onToggleMissed(i)}
                style={isMissed ? styles.missedWord : undefined}
              >
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
          const isMissed = cardState === "back" && missedIndices.has(i);

          if (cardState === "back") {
            return (
              <Text key={`t-${i}`}>
                {i > 0 && " "}
                <Text
                  onPress={() => onToggleMissed(i)}
                  style={isMissed ? styles.missedWord : undefined}
                >
                  {word}
                </Text>
              </Text>
            );
          }

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text>{word}</Text>
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
        {missedCount > 0 && (
          <Text style={styles.missedHint}>
            {missedCount} word{missedCount > 1 ? "s" : ""} marked
          </Text>
        )}
        <View style={styles.actionRow}>
          <Pressable
            style={[styles.actionButton, styles.gotItButton]}
            onPress={() => onSubmit(missedCount > 0 ? "partial" : "understood")}
          >
            <Text style={styles.actionButtonText}>
              {missedCount > 0 ? "Continue" : "Understood"}
            </Text>
          </Pressable>
          <Pressable
            style={[styles.actionButton, { backgroundColor: colors.surfaceLight }]}
            onPress={onAdvance}
          >
            <Text style={[styles.actionButtonText, { color: colors.textSecondary }]}>
              Translation
            </Text>
          </Pressable>
        </View>
        {missedCount === 0 && (
          <Text style={styles.tapHintListening}>
            Tap words you didn't catch, or show translation
          </Text>
        )}
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
        {missedCount > 0 ? (
          <Pressable
            style={[styles.actionButton, styles.continueButton]}
            onPress={() => onSubmit("partial")}
          >
            <Text style={styles.actionButtonText}>Continue</Text>
          </Pressable>
        ) : (
          <Pressable
            style={[styles.actionButton, styles.gotItButton]}
            onPress={() => onSubmit("understood")}
          >
            <Text style={styles.actionButtonText}>Got it</Text>
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
        {missedCount > 0 ? (
          <Pressable
            style={[styles.actionButton, styles.continueButton]}
            onPress={() => onSubmit("partial")}
          >
            <Text style={styles.actionButtonText}>Continue</Text>
          </Pressable>
        ) : !hasSentence ? (
          <>
            <Pressable
              style={[styles.actionButton, styles.gotItButton]}
              onPress={() => onSubmit("understood")}
            >
              <Text style={styles.actionButtonText}>Got it</Text>
            </Pressable>
            <Pressable
              style={[styles.actionButton, styles.continueButton]}
              onPress={() => onSubmit("partial")}
            >
              <Text style={styles.actionButtonText}>Missed</Text>
            </Pressable>
          </>
        ) : (
          <Pressable
            style={[styles.actionButton, styles.gotItButton]}
            onPress={() => onSubmit("understood")}
          >
            <Text style={styles.actionButtonText}>Got it</Text>
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


// --- Sparkle Effect ---

function SparkleEffect({ count = 8 }: { count?: number }) {
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
          Animated.timing(a.translateY, { toValue: -(20 + Math.random() * 30), duration: 600, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.timing(a.opacity, { toValue: 0, duration: 400, useNativeDriver: true }),
          Animated.timing(a.scale, { toValue: 0.6, duration: 400, useNativeDriver: true }),
        ]),
      ])
    );
    Animated.parallel(animations).start();
  }, []);

  const sparkleChars = ["\u2728", "\u2B50", "\u2728", "\u2B50"];

  return (
    <View style={styles.sparkleContainer}>
      {anims.map((a, i) => {
        const angle = (i / count) * 2 * Math.PI;
        const radius = 35 + (i % 3) * 10;
        const left = 50 + Math.cos(angle) * radius;
        const top = 50 + Math.sin(angle) * radius;
        return (
          <Animated.Text
            key={i}
            style={{
              position: "absolute",
              left,
              top,
              fontSize: 12 + (i % 3) * 4,
              opacity: a.opacity,
              transform: [{ translateY: a.translateY }, { scale: a.scale }],
            }}
          >
            {sparkleChars[i % sparkleChars.length]}
          </Animated.Text>
        );
      })}
    </View>
  );
}

// --- Motivational Message ---

function getMotivationalMessage(
  accuracy: number,
  streak: number,
  wordsToNext: number | null,
): string {
  let msg: string;
  if (accuracy === 100) {
    msg = "Flawless! Every word understood.";
  } else if (accuracy >= 90) {
    msg = "Sharp recall today.";
  } else if (accuracy >= 70) {
    msg = "Solid session. Consistency builds fluency.";
  } else if (accuracy >= 50) {
    msg = "Good effort. Missed words will come back for practice.";
  } else {
    msg = "Tough session. These words will get easier with repetition.";
  }

  if (streak >= 7) {
    msg += ` ${streak} days in a row!`;
  }

  if (wordsToNext !== null && wordsToNext <= 15) {
    msg += ` Only ${wordsToNext} words to the next level.`;
  }

  return msg;
}

// --- Session Complete ---

function SessionComplete({
  results,
  mode,
  autoIntroduced,
  onNewSession,
}: {
  results: SessionResults;
  mode: ReviewMode;
  autoIntroduced: IntroCandidate[];
  onNewSession: () => void;
}) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const fadeAnim = useRef(new Animated.Value(0)).current;

  const accuracy = results.total > 0
    ? Math.round((results.gotIt / results.total) * 100)
    : 0;

  const showSparkles = accuracy >= 70;
  const sparkleCount = accuracy === 100 ? 12 : 8;

  const title =
    accuracy === 100 ? "Perfect!" :
    accuracy >= 80 ? "Great Session!" :
    accuracy >= 60 ? "Session Complete" :
    "Keep Practicing!";

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

  const accuracyColor =
    accuracy >= 80 ? colors.gotIt :
    accuracy >= 60 ? colors.accent :
    colors.noIdea;

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {/* Celebration header */}
      <View style={styles.celebrationHeader}>
        {showSparkles && <SparkleEffect count={sparkleCount} />}
        <Text style={styles.celebrationIcon}>
          {accuracy === 100 ? "\u{1F31F}" : accuracy >= 70 ? "\u2728" : "\u{1F4DA}"}
        </Text>
      </View>

      <Text style={styles.summaryTitle}>{title}</Text>
      <Text style={styles.summarySubtitle}>
        {mode === "listening" ? "Listening" : "Reading"} mode
      </Text>

      {/* Accuracy */}
      <Text style={[styles.accuracyText, { color: accuracyColor }]}>
        {accuracy}%
      </Text>

      {/* Session breakdown */}
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

      {/* Progress nuggets (fades in when analytics loads) */}
      {analytics && (
        <Animated.View style={[styles.progressNuggets, { opacity: fadeAnim }]}>
          <View style={styles.nuggetPill}>
            <Text style={styles.nuggetText}>
              {analytics.stats.reviews_today} reviews today
            </Text>
          </View>
          {analytics.pace.current_streak >= 2 && (
            <View style={styles.nuggetPill}>
              <Text style={styles.nuggetText}>
                {analytics.pace.current_streak} day streak
              </Text>
            </View>
          )}
          <View style={styles.nuggetPill}>
            <Text style={styles.nuggetText}>
              {analytics.cefr.known_words} words known
            </Text>
          </View>
          {analytics.cefr.words_to_next !== null && analytics.cefr.words_to_next <= 20 && (
            <View style={[styles.nuggetPill, { backgroundColor: colors.accent + "30" }]}>
              <Text style={[styles.nuggetText, { color: colors.accent }]}>
                {analytics.cefr.words_to_next} to {analytics.cefr.next_level}
              </Text>
            </View>
          )}
        </Animated.View>
      )}

      {/* Auto-introduced words */}
      {autoIntroduced.length > 0 && (
        <View style={styles.autoIntroSection}>
          <Text style={styles.autoIntroTitle}>
            {autoIntroduced.length === 1 ? "New word added" : `${autoIntroduced.length} new words added`}
          </Text>
          <View style={styles.autoIntroPills}>
            {autoIntroduced.map((w) => (
              <View key={w.lemma_id} style={styles.autoIntroPill}>
                <Text style={styles.autoIntroPillAr}>{w.lemma_ar}</Text>
                <Text style={styles.autoIntroPillEn}>{w.gloss_en}</Text>
              </View>
            ))}
          </View>
        </View>
      )}

      {/* Motivational message */}
      {analytics && (
        <Animated.Text style={[styles.motivationalText, { opacity: fadeAnim }]}>
          {getMotivationalMessage(
            accuracy,
            analytics.pace.current_streak,
            analytics.cefr.words_to_next,
          )}
        </Animated.Text>
      )}

      <Pressable style={styles.startButton} onPress={onNewSession}>
        <Text style={styles.startButtonText}>Start New Session</Text>
      </Pressable>
    </ScrollView>
  );
}

// --- Empty State with Progress ---

function EmptyState({
  online,
  mode,
  onRefresh,
}: {
  online: boolean;
  mode: ReviewMode;
  onRefresh: () => void;
}) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);

  useEffect(() => {
    if (online) {
      getAnalytics().then(setAnalytics).catch(() => {});
    }
  }, [online]);

  return (
    <View style={styles.container}>
      <Text style={styles.emptyText}>
        {!online
          ? "No sessions available offline"
          : mode === "listening"
            ? "No sentences ready for listening practice"
            : "No cards due for review"}
      </Text>

      {analytics && analytics.cefr.known_words > 0 && (
        <View style={styles.emptyProgressCard}>
          <Text style={styles.emptyProgressLevel}>
            {analytics.cefr.sublevel}
          </Text>
          <Text style={styles.emptyProgressDetail}>
            {analytics.cefr.known_words} words known
          </Text>
          {analytics.pace.current_streak >= 2 && (
            <Text style={styles.emptyProgressDetail}>
              {analytics.pace.current_streak} day streak
            </Text>
          )}
        </View>
      )}

      <Pressable style={styles.startButton} onPress={onRefresh}>
        <Text style={styles.startButtonText}>Refresh</Text>
      </Pressable>
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
  audioControls: {
    flexDirection: "row",
    gap: 16,
    marginTop: 20,
  },
  audioControlButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 20,
    backgroundColor: colors.surfaceLight,
  },
  audioControlDisabled: {
    opacity: 0.4,
  },
  audioControlIcon: {
    fontSize: 18,
  },
  audioControlLabel: {
    fontSize: 14,
    color: colors.textSecondary,
    fontWeight: "600",
  },

  sentenceArabic: {
    fontSize: 30,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
    lineHeight: 50,
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
  sparkleContainer: {
    width: 100,
    height: 100,
    position: "absolute",
  },
  celebrationHeader: {
    width: 100,
    height: 100,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 8,
  },
  celebrationIcon: {
    fontSize: 48,
  },
  accuracyText: {
    fontSize: 36,
    fontWeight: "800",
    marginBottom: 16,
  },
  progressNuggets: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 8,
    marginTop: 20,
    marginBottom: 8,
  },
  nuggetPill: {
    backgroundColor: colors.surfaceLight,
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 16,
  },
  nuggetText: {
    color: colors.textSecondary,
    fontSize: 13,
    fontWeight: "600",
  },
  motivationalText: {
    color: colors.textSecondary,
    fontSize: 14,
    fontStyle: "italic",
    textAlign: "center",
    marginTop: 12,
    marginBottom: 8,
    paddingHorizontal: 20,
  },
  emptyProgressCard: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 20,
    alignItems: "center",
    marginBottom: 16,
    width: "100%",
    maxWidth: 300,
  },
  emptyProgressLevel: {
    fontSize: 28,
    color: colors.accent,
    fontWeight: "800",
    marginBottom: 4,
  },
  emptyProgressDetail: {
    fontSize: 14,
    color: colors.textSecondary,
    marginTop: 2,
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
  autoIntroSection: {
    marginTop: 16,
    alignItems: "center",
    width: "100%",
    maxWidth: 400,
  },
  autoIntroTitle: {
    fontSize: 13,
    color: colors.accent,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 8,
  },
  autoIntroPills: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 8,
  },
  autoIntroPill: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingVertical: 6,
    paddingHorizontal: 12,
    alignItems: "center",
  },
  autoIntroPillAr: {
    fontSize: 18,
    color: colors.arabic,
    writingDirection: "rtl",
  },
  autoIntroPillEn: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  tapHintFront: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginTop: 16,
    opacity: 0.5,
    fontStyle: "italic",
  },
  lookupPanel: {
    width: "100%",
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    padding: 16,
    marginTop: 16,
    alignItems: "center",
    gap: 8,
  },
  lookupRoot: {
    fontSize: 14,
    color: colors.accent,
    fontWeight: "600",
  },
  lookupMeaning: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
    textAlign: "center",
  },
  lookupTranslit: {
    fontSize: 14,
    color: colors.textSecondary,
    fontStyle: "italic",
  },
  lookupPos: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  lookupPredictionHint: {
    fontSize: 14,
    color: colors.textSecondary,
    fontStyle: "italic",
  },
  lookupPredictionPrompt: {
    fontSize: 15,
    color: colors.text,
    fontWeight: "500",
    marginTop: 4,
  },
  lookupSiblings: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    justifyContent: "center",
    marginTop: 4,
  },
  lookupSiblingPill: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    paddingVertical: 4,
    paddingHorizontal: 10,
    alignItems: "center",
  },
  lookupSiblingAr: {
    fontSize: 16,
    color: colors.arabic,
    writingDirection: "rtl",
  },
  lookupSiblingEn: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  lookupRevealButton: {
    backgroundColor: colors.accent,
    paddingVertical: 8,
    paddingHorizontal: 20,
    borderRadius: 8,
    marginTop: 4,
  },
  lookupRevealText: {
    color: "#fff",
    fontSize: 14,
    fontWeight: "600",
  },
  lookupDismiss: {
    paddingVertical: 6,
    paddingHorizontal: 16,
  },
  lookupDismissText: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  rootInfoBar: {
    marginTop: 12,
    gap: 4,
    alignItems: "center",
  },
  rootInfoText: {
    fontSize: 13,
    color: colors.accent,
    fontStyle: "italic",
  },
});
