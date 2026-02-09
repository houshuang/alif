import { useState, useEffect, useRef, useCallback } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Animated,
  Platform,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Audio } from "expo-av";
import { colors, fonts, fontFamily } from "../lib/theme";
import {
  getReviewSession,
  submitReview,
  getSentenceReviewSession,
  submitSentenceReview,
  getAnalytics,
  lookupReviewWord,
  deepPrefetchSessions,
  getGrammarLesson,
  introduceGrammarFeature,
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
  GrammarLesson,
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

interface WordOutcome {
  arabic: string;
  english: string | null;
  failed: boolean;
  prevState: string; // knowledge_state before this session
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
  const insets = useSafeAreaInsets();
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
  const [confusedIndices, setConfusedIndices] = useState<Set<number>>(new Set());
  const [lastMarkedIndex, setLastMarkedIndex] = useState<number | null>(null);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [autoIntroduced, setAutoIntroduced] = useState<IntroCandidate[]>([]);
  const [lookupResult, setLookupResult] = useState<WordLookupResult | null>(null);
  const [lookupSurfaceForm, setLookupSurfaceForm] = useState<string | null>(null);
  const [lookupLemmaId, setLookupLemmaId] = useState<number | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupShowMeaning, setLookupShowMeaning] = useState(false);
  const [audioPlayCount, setAudioPlayCount] = useState(0);
  const [lookupCount, setLookupCount] = useState(0);
  const [wordOutcomes, setWordOutcomes] = useState<Map<number, WordOutcome>>(new Map());
  const [grammarLessons, setGrammarLessons] = useState<GrammarLesson[]>([]);
  const [grammarLessonIndex, setGrammarLessonIndex] = useState(0);
  const [grammarLessonsLoading, setGrammarLessonsLoading] = useState(false);
  const showTime = useRef<number>(0);
  const soundRef = useRef<Audio.Sound | null>(null);
  const lookupRequestRef = useRef(0);

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
      if (totalCards === 0) {
        loadSession();
      }
    });
  }, [totalCards]);

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
    setAudioPlayCount(prev => prev + 1);
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
    setConfusedIndices(new Set());
    setLastMarkedIndex(null);
    setAudioPlaying(false);
    setAutoIntroduced([]);
    setWordOutcomes(new Map());
    setGrammarLessons([]);
    setGrammarLessonIndex(0);
    setGrammarLessonsLoading(false);
    lookupRequestRef.current += 1;
    setLookupResult(null);
    setLookupSurfaceForm(null);
    setLookupLemmaId(null);
    setLookupLoading(false);
    setLookupShowMeaning(false);
    setAudioPlayCount(0);
    setLookupCount(0);
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
        // Load grammar lessons (refreshers first, then intros)
        const featureKeys: string[] = [
          ...(ss.grammar_refresher_needed ?? []),
          ...(ss.grammar_intro_needed ?? []),
        ];
        if (featureKeys.length > 0) {
          setGrammarLessonsLoading(true);
          const lessons: GrammarLesson[] = [];
          for (const key of featureKeys) {
            try {
              const lesson = await getGrammarLesson(key);
              if (ss.grammar_refresher_needed?.includes(key)) {
                lesson.is_refresher = true;
              }
              lessons.push(lesson);
            } catch {}
          }
          setGrammarLessons(lessons);
          setGrammarLessonIndex(0);
          setGrammarLessonsLoading(false);
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
    // Triple-tap cycle: off → missed (red) → confused (yellow) → off
    const isConfused = confusedIndices.has(index);
    const isMissed = missedIndices.has(index);

    if (!isConfused && !isMissed) {
      // off → missed (red)
      setMissedIndices((prev) => {
        const next = new Set(prev);
        next.add(index);
        return next;
      });
      setLastMarkedIndex(index);
    } else if (isMissed && !isConfused) {
      // missed → confused (yellow)
      setMissedIndices((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
      setConfusedIndices((prev) => {
        const next = new Set(prev);
        next.add(index);
        return next;
      });
      setLastMarkedIndex(index);
    } else {
      // confused → off
      setConfusedIndices((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
      setLastMarkedIndex(null);
    }
  }, [confusedIndices, missedIndices]);

  const handleWordTap = useCallback(async (index: number, lemmaId: number) => {
    const requestId = ++lookupRequestRef.current;
    const tappedSurface = sentenceSession?.items[cardIndex]?.words[index]?.surface_form ?? null;
    // Cycle state: off → missed → confused → off (same as back phase)
    toggleMissed(index);
    // Trigger lookup for the tapped word
    setLookupCount(prev => prev + 1);
    setLookupSurfaceForm(tappedSurface);
    setLookupLemmaId(lemmaId);
    setLookupLoading(true);
    setLookupShowMeaning(false);
    setLookupResult(null);
    try {
      const result = await lookupReviewWord(lemmaId);
      if (lookupRequestRef.current !== requestId) return;
      setLookupResult(result);
      const knownSiblings = result.root_family.filter(
        (s) => (s.state === "known" || s.state === "learning") && s.lemma_id !== result.lemma_id
      );
      if (knownSiblings.length >= 2) {
        setLookupShowMeaning(false);
      } else {
        setLookupShowMeaning(true);
      }
    } catch {
      if (lookupRequestRef.current !== requestId) return;
      // Build lite result from session word metadata
      if (sentenceSession) {
        const item = sentenceSession.items[cardIndex];
        const word = item?.words.find((w) => w.lemma_id === lemmaId);
        if (word) {
          setLookupResult({
            lemma_id: lemmaId,
            lemma_ar: "",
            gloss_en: word.gloss_en,
            transliteration: null,
            root: word.root,
            root_meaning: word.root_meaning,
            root_id: word.root_id,
            pos: null,
            root_family: [],
          });
          setLookupShowMeaning(true);
        } else {
          setLookupResult(null);
          setLookupShowMeaning(true);
        }
      } else {
        setLookupResult(null);
        setLookupShowMeaning(true);
      }
    }
      if (lookupRequestRef.current === requestId) {
        setLookupLoading(false);
      }
  }, [toggleMissed, sentenceSession, cardIndex]);

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

    const confusedLemmaIds: number[] = [];
    for (const idx of confusedIndices) {
      const word = item.words[idx];
      if (word?.lemma_id != null) {
        confusedLemmaIds.push(word.lemma_id);
      }
    }

    // Word-only cards use explicit "Missed" action without per-word taps.
    // Treat partial with no marked words as a miss on the primary lemma.
    if (
      item.sentence_id === null &&
      signal === "partial" &&
      missedLemmaIds.length === 0 &&
      confusedLemmaIds.length === 0
    ) {
      missedLemmaIds.push(item.primary_lemma_id);
    }

    if (signal === "no_idea") {
      missedLemmaIds.push(item.primary_lemma_id);
    }

    // Track per-word outcomes
    const missedSet = new Set(missedLemmaIds);
    const confusedSet = new Set(confusedLemmaIds);
    setWordOutcomes(prev => {
      const next = new Map(prev);
      for (let i = 0; i < item.words.length; i++) {
        const w = item.words[i];
        if (w.is_function_word || w.lemma_id == null) continue;

        let failed = false;
        if (signal === "no_idea") {
          failed = true;
        } else if (signal === "partial") {
          if (missedSet.has(w.lemma_id) || confusedSet.has(w.lemma_id)) failed = true;
        }

        const existing = next.get(w.lemma_id);
        if (existing) {
          if (failed) existing.failed = true;
        } else {
          next.set(w.lemma_id, {
            arabic: w.surface_form,
            english: w.gloss_en,
            failed,
            prevState: w.knowledge_state ?? "new",
          });
        }
      }
      return next;
    });

    submitSentenceReview({
      sentence_id: item.sentence_id,
      primary_lemma_id: item.primary_lemma_id,
      comprehension_signal: signal,
      missed_lemma_ids: missedLemmaIds,
      confused_lemma_ids: confusedLemmaIds.length > 0 ? confusedLemmaIds : undefined,
      response_ms: responseMs,
      session_id: sentenceSession.session_id,
      review_mode: mode,
      audio_play_count: audioPlayCount > 0 ? audioPlayCount : undefined,
      lookup_count: lookupCount > 0 ? lookupCount : undefined,
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
    lookupRequestRef.current += 1;
    const prev = results ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 };
    const next = {
      total: prev.total + 1,
      gotIt: prev.gotIt + (signal === "understood" || signal === "grammar_confused" ? 1 : 0),
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
      setConfusedIndices(new Set());
      setLastMarkedIndex(null);
      setAudioPlaying(false);
      setAudioPlayCount(0);
      setLookupCount(0);
      setLookupResult(null);
      setLookupSurfaceForm(null);
      setLookupLemmaId(null);
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
      if (cardState === "front") {
        setCardState("back");
        setLookupShowMeaning(true);
      }
    }
  }

  function buildContext(): string {
    const parts: string[] = [
      "Screen: review",
      `Mode: ${mode}`,
      `Card: ${cardIndex + 1}/${totalCards}`,
      `Phase: ${cardState}`,
      `Online: ${online ? "yes" : "no"}`,
    ];
    if (usingSentences && sentenceSession) {
      const item = sentenceSession.items[cardIndex];
      if (item) {
        parts.push(`Sentence ID: ${item.sentence_id ?? "word_only"}`);
        parts.push(`Arabic: ${item.arabic_text || item.primary_lemma_ar}`);
        parts.push(`English: ${item.english_translation || item.primary_gloss_en}`);
        if (item.transliteration) {
          parts.push(`Transliteration: ${item.transliteration}`);
        }
        parts.push(
          `Primary lemma: lemma_id=${item.primary_lemma_id}, lemma_ar=${item.primary_lemma_ar}, gloss=${item.primary_gloss_en}`
        );
        if (item.grammar_features && item.grammar_features.length > 0) {
          parts.push(`Grammar features: ${item.grammar_features.join(", ")}`);
        }

        parts.push("Word knowledge:");
        item.words.forEach((word, i) => {
          const mark = missedIndices.has(i)
            ? "missed"
            : confusedIndices.has(i)
              ? "did_not_recognize"
              : "ok";
          const info = [
            `${i + 1}. ${word.surface_form}`,
            `lemma_id=${word.lemma_id ?? "none"}`,
            `gloss=${word.gloss_en ?? "unknown"}`,
            `state=${word.knowledge_state || "new"}`,
            `stability=${word.stability != null ? word.stability.toFixed(3) : "unknown"}`,
            `due=${word.is_due ? "yes" : "no"}`,
            `function_word=${word.is_function_word ? "yes" : "no"}`,
            `root=${word.root ?? "unknown"}`,
            `mark=${mark}`,
          ];
          parts.push(info.join(" | "));
        });

        const missed = Array.from(missedIndices)
          .map((i) => item.words[i]?.surface_form)
          .filter(Boolean);
        if (missed.length > 0) parts.push(`Missed: ${missed.join(", ")}`);
        const confused = Array.from(confusedIndices)
          .map((i) => item.words[i]?.surface_form)
          .filter(Boolean);
        if (confused.length > 0) parts.push(`Confused: ${confused.join(", ")}`);
      }
    } else if (legacySession) {
      const card = legacySession.cards[cardIndex];
      if (card) {
        parts.push(
          `Word: lemma_id=${card.lemma_id}, lemma_ar=${card.lemma_ar}, gloss=${card.gloss_en}`
        );
        if (card.sentence) {
          parts.push(`Sentence: ${card.sentence.arabic}`);
          parts.push(`Translation: ${card.sentence.english}`);
        }
      }
    }
    if (lookupSurfaceForm && lookupLemmaId !== null) {
      parts.push(
        `Lookup focus: surface=${lookupSurfaceForm}, lemma_id=${lookupLemmaId}`
      );
      if (lookupResult) {
        parts.push(
          `Lookup resolved lemma: lemma_ar=${lookupResult.lemma_ar || "unknown"}, gloss=${lookupResult.gloss_en || "unknown"}, root=${lookupResult.root || "unknown"}`
        );
      }
    }
    return parts.join("\n");
  }

  function buildExplainPrompt(): string | null {
    if (usingSentences && sentenceSession) {
      const item = sentenceSession.items[cardIndex];
      if (!item) return null;

      const marked = [
        ...Array.from(missedIndices).map((i) => ({ index: i, mark: "missed" })),
        ...Array.from(confusedIndices).map((i) => ({
          index: i,
          mark: "did_not_recognize",
        })),
      ].sort((a, b) => a.index - b.index);

      if (marked.length === 0) {
        const grammarFeats = item.grammar_features ?? [];
        if (grammarFeats.length > 0) {
          return [
            "I knew all the words but couldn't parse the grammar structure.",
            `Grammar features in this sentence: ${grammarFeats.join(", ")}.`,
            "Explain the grammatical structure step by step.",
            "For each grammar feature, show how it appears in this sentence and give a recognition tip.",
          ].join("\n");
        }
        return [
          "Explain this sentence briefly.",
          "Point out any word forms that can hide the base lemma (prefixes, suffixes, inflection, or attached particles).",
        ].join(" ");
      }

      const markedLines = marked
        .map(({ index, mark }) => {
          const word = item.words[index];
          if (!word) return null;
          return `- ${word.surface_form} (index=${index + 1}, mark=${mark}, lemma_id=${word.lemma_id ?? "none"}, gloss=${word.gloss_en ?? "unknown"}, state=${word.knowledge_state || "new"}, stability=${word.stability != null ? word.stability.toFixed(3) : "unknown"})`;
        })
        .filter(Boolean)
        .join("\n");

      return [
        "Explain why I missed or did not recognize these marked words.",
        "For each word:",
        "1) identify the base lemma in Arabic and transliteration if possible,",
        "2) explain how this surface form differs from the lemma (clitics/article/suffixes/inflection),",
        "3) give one short recognition heuristic.",
        "",
        "Marked words:",
        markedLines,
      ].join("\n");
    }

    return "Explain this card and why the seen form can differ from the underlying lemma.";
  }

  // --- Render ---

  const isSessionDone = !!(results && totalCards > 0 && results.total >= totalCards);

  // Session complete takes priority over loading — prevents flash-and-disappear
  // when background sync triggers a reload
  if (isSessionDone) {
    return (
      <SessionComplete
        results={results}
        mode={mode}
        autoIntroduced={autoIntroduced}
        wordOutcomes={wordOutcomes}
        onNewSession={() => loadSession()}
      />
    );
  }

  if (loading) {
    return (
      <View style={styles.centeredContainer}>
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

  const isListening = mode === "listening";

  // Grammar lesson card shown before review cards
  const showingGrammarLesson = grammarLessons.length > 0 && grammarLessonIndex < grammarLessons.length;
  if (showingGrammarLesson) {
    const lesson = grammarLessons[grammarLessonIndex];
    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <GrammarLessonCard
          lesson={lesson}
          current={grammarLessonIndex + 1}
          total={grammarLessons.length}
          onDismiss={async () => {
            try {
              await introduceGrammarFeature(lesson.feature_key);
            } catch {}
            if (grammarLessonIndex + 1 < grammarLessons.length) {
              setGrammarLessonIndex(grammarLessonIndex + 1);
            } else {
              setGrammarLessons([]);
              setGrammarLessonIndex(0);
            }
          }}
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
        style={[styles.container, isListening && styles.listeningContainer, { paddingTop: Math.max(insets.top, 12) }]}
      >
        <ProgressBar current={cardIndex + 1} total={totalCards} mode={mode} />

        <ScrollView
          contentContainerStyle={styles.sentenceArea}
          showsVerticalScrollIndicator={false}
        >
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
              confusedIndices={confusedIndices}
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
              confusedIndices={confusedIndices}
              onWordTap={handleWordTap}
            />
          )}
        </ScrollView>

        {!isListening && (
          <WordLookupPanel
            result={isWordOnly ? null : lookupResult}
            loading={!isWordOnly && lookupLoading}
            surfaceForm={lookupSurfaceForm}
            lemmaId={lookupLemmaId}
            showMeaning={lookupShowMeaning}
            onShowMeaning={() => setLookupShowMeaning(true)}
            placeholderText={
              isWordOnly
                ? "Word-only card"
                : "Tap a word to inspect root and meaning"
            }
          />
        )}

        <View style={styles.bottomActions}>
          {isListening ? (
            <ListeningActions
              cardState={cardState as ListeningCardState}
              missedCount={missedIndices.size}
              confusedCount={confusedIndices.size}
              lastMarkedGloss={lastMarkedIndex !== null ? item.words[lastMarkedIndex]?.gloss_en ?? null : null}
              onAdvance={advanceState}
              onSubmit={handleSubmit}
            />
          ) : (
            <ReadingActions
              cardState={cardState as ReadingCardState}
              hasSentence={!isWordOnly}
              missedCount={missedIndices.size}
              confusedCount={confusedIndices.size}
              onAdvance={advanceState}
              onSubmit={handleSubmit}
            />
          )}
        </View>
        <AskAI
          contextBuilder={buildContext}
          buildExplainPrompt={buildExplainPrompt}
          screen="review"
        />
      </View>
    );
  }

  // Legacy word-only fallback
  const card = legacySession!.cards[cardIndex];
  const hasSentence = card.sentence !== null;

  return (
    <View style={[styles.container, isListening && styles.listeningContainer]}>
      <ProgressBar current={cardIndex + 1} total={totalCards} mode={mode} />

      <ScrollView
        contentContainerStyle={styles.sentenceArea}
        showsVerticalScrollIndicator={false}
      >
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
      </ScrollView>

      <View style={styles.bottomActions}>
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
      <AskAI
        contextBuilder={buildContext}
        buildExplainPrompt={buildExplainPrompt}
        screen="review"
      />
    </View>
  );
}


// --- Sentence-First Cards ---

function SentenceReadingCard({
  item,
  cardState,
  missedIndices,
  confusedIndices,
  onWordTap,
}: {
  item: SentenceReviewItem;
  cardState: ReadingCardState;
  missedIndices: Set<number>;
  confusedIndices: Set<number>;
  onWordTap: (index: number, lemmaId: number) => void;
}) {
  return (
    <>
      <Text style={styles.sentenceArabic}>
        {item.words.map((word, i) => {
          const isMissed = missedIndices.has(i);
          const isConfused = confusedIndices.has(i);
          const wordStyle = isMissed
            ? styles.missedWord
            : isConfused
              ? styles.confusedWord
              : undefined;
          const canTap = word.lemma_id != null && !word.is_function_word;
          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text
                onPress={canTap ? () => onWordTap(i, word.lemma_id!) : undefined}
                style={wordStyle}
              >
                {word.surface_form}
              </Text>
            </Text>
          );
        })}
      </Text>

      {cardState === "back" && (
        <View style={styles.answerSection}>
          <View style={styles.tapHintSlot}>
            {missedIndices.size === 0 ? (
              <Text style={styles.tapHint}>Tap any word you didn't know</Text>
            ) : (
              <Text style={styles.tapHintPlaceholder}>.</Text>
            )}
          </View>
          <View style={styles.divider} />
          <Text style={styles.sentenceEnglish}>
            {item.english_translation}
          </Text>
          <View style={styles.translitSlot}>
            {item.transliteration ? (
              <Text style={styles.sentenceTranslit}>
                {item.transliteration}
              </Text>
            ) : (
              <Text style={styles.translitPlaceholder}>.</Text>
            )}
          </View>
          {item.grammar_features && item.grammar_features.length > 0 && (
            <GrammarChips features={item.grammar_features} />
          )}
        </View>
      )}
    </>
  );
}

function GrammarChips({ features }: { features: string[] }) {
  return (
    <View style={styles.grammarChipsRow}>
      {features.map((f) => (
        <View key={f} style={styles.grammarChip}>
          <Text style={styles.grammarChipText}>
            {f.replace(/_/g, " ")}
          </Text>
        </View>
      ))}
    </View>
  );
}

function WordLookupPanel({
  result,
  loading,
  surfaceForm,
  lemmaId,
  showMeaning,
  onShowMeaning,
  placeholderText,
}: {
  result: WordLookupResult | null;
  loading: boolean;
  surfaceForm: string | null;
  lemmaId: number | null;
  showMeaning: boolean;
  onShowMeaning: () => void;
  placeholderText?: string;
}) {
  const knownSiblings = result?.root_family.filter((s) => {
    if (s.lemma_id === result.lemma_id) return false;
    if (s.state !== "known" && s.state !== "learning") return false;
    const sGloss = (s.gloss_en ?? "").toLowerCase();
    const rGloss = (result.gloss_en ?? "").toLowerCase();
    if (sGloss && rGloss && (sGloss.includes(rGloss) || rGloss.includes(sGloss))) return false;
    return true;
  }) ?? [];
  const hasPredictionMode = result != null && knownSiblings.length >= 2 && !showMeaning;
  const lemmaArabic = result?.lemma_ar?.trim() || null;
  const lemmaDisplay = lemmaArabic ?? (lemmaId != null ? `lemma #${lemmaId}` : "unknown lemma");

  return (
    <View style={styles.lookupPanel}>
      {loading ? (
        <ActivityIndicator size="small" color={colors.accent} />
      ) : result ? (
        <>
          <View style={styles.lookupMapBlock}>
            <Text style={styles.lookupMapLine}>
              Clicked form:{" "}
              <Text style={styles.lookupMapArabic}>
                {surfaceForm ?? "\u2014"}
              </Text>
            </Text>
            <Text style={styles.lookupMapLine}>
              FSRS lemma:{" "}
              <Text style={styles.lookupMapArabic}>
                {lemmaDisplay}
              </Text>
            </Text>
          </View>
          {result.root && (
            <Text style={styles.lookupRoot}>
              Root: {result.root}
              {result.root_meaning ? ` \u2014 ${result.root_meaning}` : ""}
            </Text>
          )}
          {hasPredictionMode ? (
            <>
              <Text style={styles.lookupSiblingsText}>
                You know: {knownSiblings.slice(0, 3).map((s) => `${s.lemma_ar} (${s.gloss_en})`).join(", ")}
              </Text>
              <Pressable onPress={onShowMeaning}>
                <Text style={styles.lookupRevealText}>Tap to reveal</Text>
              </Pressable>
            </>
          ) : (
            <Text style={styles.lookupMeaning}>
              {`${lemmaDisplay} — ${result.gloss_en ?? ""}${result.transliteration ? ` (${result.transliteration})` : ""}${result.pos ? ` · ${result.pos}` : ""}`}
            </Text>
          )}
        </>
      ) : (
        <Text style={styles.lookupPlaceholderText}>
          {placeholderText ?? ""}
        </Text>
      )}
    </View>
  );
}

interface RootFamilyData {
  root: string;
  root_meaning: string | null;
  siblings: { lemma_ar: string; gloss_en: string; state: string }[];
}

function RootInfoBar({
  words,
  missedIndices,
}: {
  words: SentenceReviewItem["words"];
  missedIndices: Set<number>;
}) {
  const [familyData, setFamilyData] = useState<Record<number, RootFamilyData>>({});

  const missedWithRoots = Array.from(missedIndices)
    .map((i) => words[i])
    .filter((w) => w && w.root_id);

  // Deduplicate by root_id
  const uniqueRoots = new Map<number, typeof missedWithRoots[0]>();
  for (const w of missedWithRoots) {
    if (!uniqueRoots.has(w.root_id!)) uniqueRoots.set(w.root_id!, w);
  }

  const rootIds = Array.from(uniqueRoots.keys());
  const rootIdsKey = rootIds.sort().join(",");

  useEffect(() => {
    if (rootIds.length === 0) return;
    let cancelled = false;
    for (const rootId of rootIds) {
      if (familyData[rootId]) continue;
      fetch(`${BASE_URL}/api/learn/root-family/${rootId}`)
        .then((r) => r.json())
        .then((data) => {
          if (cancelled) return;
          const knownSiblings = (data.words || []).filter(
            (w: any) => w.state === "known" || w.state === "learning"
          );
          const rootWord = uniqueRoots.get(rootId);
          setFamilyData((prev) => ({
            ...prev,
            [rootId]: {
              root: data.root || rootWord?.root || "",
              root_meaning: data.root_meaning || rootWord?.root_meaning || null,
              siblings: knownSiblings.map((s: any) => ({
                lemma_ar: s.lemma_ar,
                gloss_en: s.gloss_en,
                state: s.state,
              })),
            },
          }));
        })
        .catch(() => {});
    }
    return () => { cancelled = true; };
  }, [rootIdsKey]);

  if (uniqueRoots.size === 0) return null;

  return (
    <View style={styles.rootInfoBar}>
      {Array.from(uniqueRoots.entries()).map(([rootId, w]) => {
        const family = familyData[rootId];
        return (
          <View key={rootId} style={styles.rootInfoEntry}>
            <Text style={styles.rootInfoText}>
              Root: {w.root}
              {(family?.root_meaning || w.root_meaning) ? ` \u2014 ${family?.root_meaning || w.root_meaning}` : ""}
            </Text>
            {family && family.siblings.length > 0 && (
              <View style={styles.rootSiblings}>
                {family.siblings.slice(0, 4).map((sib, i) => (
                  <View key={i} style={styles.rootSiblingPill}>
                    <Text style={styles.rootSiblingAr}>{sib.lemma_ar}</Text>
                    <Text style={styles.rootSiblingEn}>{sib.gloss_en}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        );
      })}
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
  confusedIndices,
  onToggleMissed,
  audioPlaying,
  onReplay,
  onReplaySlow,
}: {
  item: SentenceReviewItem;
  cardState: ListeningCardState;
  missedIndices: Set<number>;
  confusedIndices: Set<number>;
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
          const isConfused = confusedIndices.has(i);
          const wordStyle = isMissed
            ? styles.missedWord
            : isConfused
              ? styles.confusedWord
              : undefined;

          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text
                onPress={() => onToggleMissed(i)}
                style={wordStyle}
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
          <View style={styles.translitSlot}>
            {item.transliteration ? (
              <Text style={styles.sentenceTranslit}>
                {item.transliteration}
              </Text>
            ) : (
              <Text style={styles.translitPlaceholder}>.</Text>
            )}
          </View>
          {item.grammar_features && item.grammar_features.length > 0 && (
            <GrammarChips features={item.grammar_features} />
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
          <View style={styles.translitSlot}>
            {sentence.transliteration ? (
              <Text style={styles.sentenceTranslit}>
                {sentence.transliteration}
              </Text>
            ) : (
              <Text style={styles.translitPlaceholder}>.</Text>
            )}
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
          <View style={styles.translitSlot}>
            {sentence.transliteration ? (
              <Text style={styles.sentenceTranslit}>
                {sentence.transliteration}
              </Text>
            ) : (
              <Text style={styles.translitPlaceholder}>.</Text>
            )}
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
        </View>
      )}
    </>
  );
}

// --- Listening Actions ---

function ListeningActions({
  cardState,
  missedCount,
  confusedCount = 0,
  lastMarkedGloss,
  onAdvance,
  onSubmit,
}: {
  cardState: ListeningCardState;
  missedCount: number;
  confusedCount?: number;
  lastMarkedGloss?: string | null;
  onAdvance: () => void;
  onSubmit: (signal: ComprehensionSignal) => void;
}) {
  if (cardState === "audio") {
    return (
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, styles.noIdeaButton]}
          onPress={() => onSubmit("no_idea")}
        >
          <Text style={styles.noIdeaButtonText}>No idea</Text>
        </Pressable>
        <View style={styles.actionButtonSpacer} />
        <Pressable style={[styles.actionButton, styles.showButton]} onPress={onAdvance}>
          <Text style={styles.showButtonText}>Reveal Arabic</Text>
        </Pressable>
      </View>
    );
  }

  const totalMarked = missedCount + confusedCount;

  if (cardState === "arabic") {
    return (
      <View style={styles.actionColumn}>
        <View style={styles.actionHintSlot}>
          {totalMarked > 0 ? (
            <MarkedHint
              missedCount={missedCount}
              confusedCount={confusedCount}
              lastMarkedGloss={lastMarkedGloss}
            />
          ) : (
            <Text style={styles.hintTextListening}>
              Tap words you didn't catch, or show translation
            </Text>
          )}
        </View>
        <View style={styles.actionRow}>
          <Pressable
            style={[styles.actionButton, styles.noIdeaButton]}
            onPress={() => onSubmit("no_idea")}
          >
            <Text style={styles.noIdeaButtonText}>No idea</Text>
          </Pressable>
          <Pressable
            style={[styles.actionButton, styles.gotItButton]}
            onPress={() => onSubmit(totalMarked > 0 ? "partial" : "understood")}
          >
            <Text style={styles.actionButtonText}>
              {totalMarked > 0 ? "Continue" : "Know All"}
            </Text>
          </Pressable>
          <Pressable
            style={[styles.actionButton, styles.showButton]}
            onPress={onAdvance}
          >
            <Text style={styles.showButtonText}>Show Translation</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.actionColumn}>
      <View style={styles.actionHintSlot}>
        {totalMarked > 0 ? (
          <MarkedHint
            missedCount={missedCount}
            confusedCount={confusedCount}
            lastMarkedGloss={lastMarkedGloss}
          />
        ) : (
          <>
            <Text style={styles.hintText}>Tap any word you didn't catch</Text>
            <Pressable onPress={() => onSubmit("grammar_confused")}>
              <Text style={styles.grammarConfusedLink}>Knew words, not grammar</Text>
            </Pressable>
          </>
        )}
      </View>
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, styles.noIdeaButton]}
          onPress={() => onSubmit("no_idea")}
        >
          <Text style={styles.noIdeaButtonText}>No idea</Text>
        </Pressable>
        {totalMarked > 0 ? (
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
            <Text style={styles.actionButtonText}>Know All</Text>
          </Pressable>
        )}
        <View style={styles.actionButtonSpacer} />
      </View>
    </View>
  );
}

// --- Reading Actions ---

function ReadingActions({
  cardState,
  hasSentence,
  missedCount,
  confusedCount = 0,
  onAdvance,
  onSubmit,
}: {
  cardState: ReadingCardState;
  hasSentence: boolean;
  missedCount: number;
  confusedCount?: number;
  onAdvance: () => void;
  onSubmit: (signal: ComprehensionSignal) => void;
}) {
  const totalMarked = missedCount + confusedCount;

  if (cardState === "front") {
    return (
      <View style={styles.actionColumn}>
        <View style={styles.actionRow}>
          {hasSentence ? (
            <Pressable
              style={[styles.actionButton, styles.noIdeaButton]}
              onPress={() => onSubmit("no_idea")}
            >
              <Text style={styles.noIdeaButtonText}>No idea</Text>
            </Pressable>
          ) : (
            <View style={styles.actionButtonSpacer} />
          )}
          {hasSentence ? (
            <Pressable
              style={[styles.actionButton, styles.gotItButton]}
              onPress={() => onSubmit("understood")}
            >
              <Text style={styles.actionButtonText}>Know All</Text>
            </Pressable>
          ) : null}
          <Pressable style={[styles.actionButton, styles.showButton]} onPress={onAdvance}>
            <Text style={styles.showButtonText}>Show Translation</Text>
          </Pressable>
        </View>
        <View style={styles.actionHintSlot}>
          <Text style={styles.hintPlaceholder}>.</Text>
        </View>
      </View>
    );
  }

  let readingHint = "";
  if (hasSentence) {
    if (totalMarked === 0) {
      readingHint = "Tap any word you didn't know";
    } else if (totalMarked === 1) {
      readingHint = "1 word marked";
    } else {
      readingHint = `${totalMarked} words marked`;
    }
  }

  return (
    <View style={styles.actionColumn}>
      <View style={styles.actionRow}>
        {!hasSentence ? (
          <>
            <Pressable
              style={[styles.actionButton, styles.gotItButton]}
              onPress={() => onSubmit("understood")}
            >
              <Text style={styles.actionButtonText}>Know All</Text>
            </Pressable>
            <Pressable
              style={[styles.actionButton, styles.continueButton]}
              onPress={() => onSubmit("partial")}
            >
              <Text style={styles.actionButtonText}>Missed</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Pressable
              style={[styles.actionButton, styles.noIdeaButton]}
              onPress={() => onSubmit("no_idea")}
            >
              <Text style={styles.noIdeaButtonText}>No idea</Text>
            </Pressable>
            {totalMarked > 0 ? (
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
                <Text style={styles.actionButtonText}>Know All</Text>
              </Pressable>
            )}
            <View style={styles.actionButtonSpacer} />
          </>
        )}
      </View>
      <View style={styles.actionHintSlot}>
        {hasSentence ? (
          <>
            <Text style={[styles.hintText, totalMarked > 0 && styles.hintTextMarked]}>
              {readingHint}
            </Text>
            {totalMarked === 0 && (
              <Pressable onPress={() => onSubmit("grammar_confused")}>
                <Text style={styles.grammarConfusedLink}>Knew words, not grammar</Text>
              </Pressable>
            )}
          </>
        ) : (
          <Text style={styles.hintPlaceholder}>.</Text>
        )}
      </View>
    </View>
  );
}

// --- Marked Hint ---

function MarkedHint({
  missedCount,
  confusedCount,
  lastMarkedGloss,
}: {
  missedCount: number;
  confusedCount: number;
  lastMarkedGloss?: string | null;
}) {
  const parts: string[] = [];
  if (missedCount > 0) parts.push(`${missedCount} missed`);
  if (confusedCount > 0) parts.push(`${confusedCount} confused`);
  const label = parts.join(", ");

  return (
    <View style={styles.missedHintRow}>
      <Text style={styles.missedHint}>{label}</Text>
      {lastMarkedGloss && (
        <Text style={styles.lastMarkedGloss}>{lastMarkedGloss}</Text>
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
  wordOutcomes,
  onNewSession,
}: {
  results: SessionResults;
  mode: ReviewMode;
  autoIntroduced: IntroCandidate[];
  wordOutcomes: Map<number, WordOutcome>;
  onNewSession: () => void;
}) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const fadeAnim = useRef(new Animated.Value(0)).current;

  const accuracy = results.total > 0
    ? Math.round((results.gotIt / results.total) * 100)
    : 0;

  // Words newly learned: got right AND weren't already "known"
  const newlyLearned: { arabic: string; english: string | null }[] = [];
  const notKnown: { arabic: string; english: string | null }[] = [];
  for (const [, w] of wordOutcomes) {
    if (w.failed) {
      notKnown.push({ arabic: w.arabic, english: w.english });
    } else if (w.prevState !== "known") {
      newlyLearned.push({ arabic: w.arabic, english: w.english });
    }
  }

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
    deepPrefetchSessions(mode).catch(() => {});
  }, []);

  const accuracyColor =
    accuracy >= 80 ? colors.gotIt :
    accuracy >= 60 ? colors.accent :
    colors.noIdea;

  return (
    <ScrollView contentContainerStyle={styles.centeredContainer}>
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

      {/* Word-level outcomes */}
      {(newlyLearned.length > 0 || notKnown.length > 0) && (
        <View style={styles.wordOutcomeSection}>
          {newlyLearned.length > 0 && (
            <View style={styles.wordOutcomeBlock}>
              <Text style={[styles.wordOutcomeTitle, { color: colors.gotIt }]}>
                {newlyLearned.length} {newlyLearned.length === 1 ? "word" : "words"} learned
              </Text>
              <View style={styles.wordOutcomePills}>
                {newlyLearned.map((w, i) => (
                  <View key={i} style={[styles.wordOutcomePill, { borderColor: colors.gotIt + "60" }]}>
                    <Text style={styles.wordOutcomePillAr}>{w.arabic}</Text>
                    <Text style={styles.wordOutcomePillEn}>{w.english}</Text>
                  </View>
                ))}
              </View>
            </View>
          )}
          {notKnown.length > 0 && (
            <View style={styles.wordOutcomeBlock}>
              <Text style={[styles.wordOutcomeTitle, { color: colors.missed }]}>
                {notKnown.length} {notKnown.length === 1 ? "word" : "words"} to review
              </Text>
              <View style={styles.wordOutcomePills}>
                {notKnown.map((w, i) => (
                  <View key={i} style={[styles.wordOutcomePill, { borderColor: colors.missed + "60" }]}>
                    <Text style={styles.wordOutcomePillAr}>{w.arabic}</Text>
                    <Text style={styles.wordOutcomePillEn}>{w.english}</Text>
                  </View>
                ))}
              </View>
            </View>
          )}
        </View>
      )}

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
    <View style={styles.centeredContainer}>
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

// --- Grammar Lesson Card ---

function GrammarLessonCard({
  lesson,
  current,
  total,
  onDismiss,
}: {
  lesson: GrammarLesson;
  current: number;
  total: number;
  onDismiss: () => void;
}) {
  return (
    <ScrollView
      contentContainerStyle={styles.grammarLessonContainer}
      showsVerticalScrollIndicator={false}
    >
      {lesson.is_refresher && (
        <Text style={styles.grammarRefresherBadge}>Refresher</Text>
      )}

      {total > 1 && (
        <Text style={styles.grammarLessonProgress}>
          {current} / {total}
        </Text>
      )}

      <Text style={styles.grammarLessonTitle}>{lesson.label_en}</Text>
      {lesson.label_ar && (
        <Text style={styles.grammarLessonTitleAr}>{lesson.label_ar}</Text>
      )}

      <Text style={styles.grammarLessonCategory}>{lesson.category}</Text>

      <View style={styles.grammarLessonBody}>
        <Text style={styles.grammarLessonExplanation}>
          {lesson.explanation}
        </Text>

        {lesson.examples.length > 0 && (
          <View style={styles.grammarExamples}>
            {lesson.examples.map((ex, i) => (
              <View key={i} style={styles.grammarExampleRow}>
                <Text style={styles.grammarExampleAr}>{ex.ar}</Text>
                <Text style={styles.grammarExampleEn}>{ex.en}</Text>
              </View>
            ))}
          </View>
        )}

        {lesson.tip && (
          <View style={styles.grammarTipBox}>
            <Text style={styles.grammarTipText}>{lesson.tip}</Text>
          </View>
        )}

        {lesson.is_refresher && lesson.times_confused > 0 && (
          <Text style={styles.grammarConfusionNote}>
            Confused {lesson.times_confused} of {lesson.times_seen} times
          </Text>
        )}
      </View>

      <Pressable style={styles.grammarGotItButton} onPress={onDismiss}>
        <Text style={styles.grammarGotItText}>Got it</Text>
      </Pressable>
    </ScrollView>
  );
}

// --- Styles ---

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 16,
  },
  centeredContainer: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  listeningContainer: {
    backgroundColor: colors.listeningBg,
  },
  sentenceArea: {
    flexGrow: 1,
    justifyContent: "center",
    alignItems: "center",
    paddingVertical: 24,
    paddingHorizontal: 4,
    maxWidth: 500,
    alignSelf: "center",
    width: "100%",
  },
  bottomActions: {
    paddingTop: 12,
    paddingBottom: 4,
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
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
    fontSize: fonts.arabicSentence,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
    lineHeight: 64,
  },
  missedWord: {
    color: colors.missed,
    textDecorationLine: "underline",
    textDecorationColor: colors.missed,
  },
  confusedWord: {
    color: colors.confused,
    textDecorationLine: "underline",
    textDecorationColor: colors.confused,
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
    fontSize: 44,
    fontFamily: fontFamily.arabicBold,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
    lineHeight: 68,
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
  },
  showButtonText: {
    color: "#fff",
    fontSize: 17,
    fontWeight: "700",
    textAlign: "center",
  },

  noIdeaButton: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: colors.noIdea + "80",
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
  actionButtonSpacer: {
    flex: 1,
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
  actionHintSlot: {
    minHeight: 30,
    justifyContent: "center",
    alignItems: "center",
    width: "100%",
    marginTop: 10,
  },
  hintText: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    opacity: 0.75,
    textAlign: "center",
  },
  hintTextListening: {
    color: colors.listening,
    fontSize: fonts.small,
    opacity: 0.8,
    textAlign: "center",
  },
  hintTextMarked: {
    color: colors.missed,
    opacity: 0.9,
    fontWeight: "600",
  },
  hintPlaceholder: {
    color: "transparent",
    fontSize: fonts.small,
    lineHeight: 16,
  },
  tapHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    opacity: 0.7,
    textAlign: "center",
  },
  tapHintSlot: {
    minHeight: 20,
    justifyContent: "center",
  },
  tapHintPlaceholder: {
    color: "transparent",
    fontSize: fonts.small,
    lineHeight: 16,
  },
  tapHintListening: {
    color: colors.listening,
    fontSize: fonts.small,
    marginTop: 16,
    opacity: 0.8,
    textAlign: "center",
  },
  missedHintRow: {
    alignItems: "center",
    gap: 2,
  },
  missedHint: {
    color: colors.missed,
    fontSize: fonts.small,
    fontWeight: "600",
  },
  lastMarkedGloss: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    fontStyle: "italic",
  },

  progressContainer: {
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
    marginBottom: 4,
  },
  progressText: {
    color: colors.textSecondary,
    fontSize: 12,
    marginBottom: 4,
    textAlign: "center",
    opacity: 0.6,
  },
  progressTrack: {
    height: 3,
    backgroundColor: colors.surfaceLight,
    borderRadius: 1.5,
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    backgroundColor: colors.accent,
    borderRadius: 1.5,
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
    fontSize: 20,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
  },
  autoIntroPillEn: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  wordOutcomeSection: {
    marginTop: 20,
    width: "100%",
    maxWidth: 400,
    gap: 16,
  },
  wordOutcomeBlock: {
    alignItems: "center",
  },
  wordOutcomeTitle: {
    fontSize: 13,
    fontWeight: "700",
    textTransform: "uppercase" as const,
    letterSpacing: 1,
    marginBottom: 8,
  },
  wordOutcomePills: {
    flexDirection: "row" as const,
    flexWrap: "wrap" as const,
    justifyContent: "center" as const,
    gap: 6,
  },
  wordOutcomePill: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingVertical: 4,
    paddingHorizontal: 10,
    alignItems: "center" as const,
    borderWidth: 1,
  },
  wordOutcomePillAr: {
    fontSize: 18,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl" as const,
  },
  wordOutcomePillEn: {
    fontSize: 10,
    color: colors.textSecondary,
  },
  lookupPanel: {
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
    height: 110,
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
    marginVertical: 8,
    justifyContent: "center",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: colors.border,
  },
  translitSlot: {
    minHeight: 24,
    justifyContent: "flex-start",
    marginTop: 2,
  },
  translitPlaceholder: {
    color: "transparent",
    fontSize: 16,
    lineHeight: 20,
  },
  lookupRoot: {
    fontSize: 13,
    color: colors.accent,
    fontWeight: "600",
    textAlign: "center",
  },
  lookupMapBlock: {
    alignItems: "center",
    gap: 2,
  },
  lookupMapLine: {
    color: colors.textSecondary,
    fontSize: 12,
    textAlign: "center",
  },
  lookupMapArabic: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },
  lookupMeaning: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
    textAlign: "center",
  },
  lookupSiblingsText: {
    fontSize: 13,
    color: colors.textSecondary,
    textAlign: "center",
  },
  lookupRevealText: {
    color: colors.accent,
    fontSize: 14,
    fontWeight: "600",
  },
  lookupPlaceholderText: {
    color: colors.textSecondary,
    fontSize: 14,
    textAlign: "center",
    opacity: 0.7,
  },
  grammarConfusedLink: {
    color: colors.confused,
    fontSize: 12,
    marginTop: 4,
    opacity: 0.8,
  },
  grammarLessonContainer: {
    flexGrow: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 32,
    paddingHorizontal: 20,
    maxWidth: 500,
    alignSelf: "center",
    width: "100%",
  },
  grammarRefresherBadge: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.confused,
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 8,
  },
  grammarLessonProgress: {
    fontSize: 12,
    color: colors.textSecondary,
    opacity: 0.6,
    marginBottom: 12,
  },
  grammarLessonTitle: {
    fontSize: 24,
    fontWeight: "700",
    color: colors.text,
    textAlign: "center",
    marginBottom: 4,
  },
  grammarLessonTitleAr: {
    fontSize: 22,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
    marginBottom: 8,
  },
  grammarLessonCategory: {
    fontSize: 12,
    color: colors.textSecondary,
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 20,
    opacity: 0.7,
  },
  grammarLessonBody: {
    width: "100%",
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 20,
    gap: 16,
    marginBottom: 24,
  },
  grammarLessonExplanation: {
    fontSize: 16,
    color: colors.text,
    lineHeight: 24,
  },
  grammarExamples: {
    gap: 12,
  },
  grammarExampleRow: {
    alignItems: "center",
    gap: 4,
  },
  grammarExampleAr: {
    fontSize: 22,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "center",
  },
  grammarExampleEn: {
    fontSize: 14,
    color: colors.textSecondary,
    textAlign: "center",
  },
  grammarTipBox: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 8,
    padding: 12,
    borderLeftWidth: 3,
    borderLeftColor: colors.accent,
  },
  grammarTipText: {
    fontSize: 14,
    color: colors.textSecondary,
    lineHeight: 20,
    fontStyle: "italic",
  },
  grammarConfusionNote: {
    fontSize: 13,
    color: colors.confused,
    textAlign: "center",
    opacity: 0.8,
  },
  grammarGotItButton: {
    backgroundColor: colors.accent,
    paddingVertical: 16,
    paddingHorizontal: 48,
    borderRadius: 12,
    alignItems: "center",
  },
  grammarGotItText: {
    color: "#fff",
    fontSize: 17,
    fontWeight: "700",
  },
  grammarChipsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 6,
    marginTop: 12,
  },
  grammarChip: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    paddingVertical: 3,
    paddingHorizontal: 8,
  },
  grammarChipText: {
    fontSize: 11,
    color: colors.textSecondary,
    textTransform: "capitalize",
  },
  rootInfoBar: {
    marginTop: 12,
    gap: 12,
    alignItems: "center",
    width: "100%",
  },
  rootInfoEntry: {
    alignItems: "center",
    gap: 6,
  },
  rootInfoText: {
    fontSize: 13,
    color: colors.accent,
    fontWeight: "600",
  },
  rootSiblings: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    justifyContent: "center",
  },
  rootSiblingPill: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 8,
    paddingVertical: 3,
    paddingHorizontal: 8,
    alignItems: "center",
  },
  rootSiblingAr: {
    fontSize: 17,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
  },
  rootSiblingEn: {
    fontSize: 11,
    color: colors.textSecondary,
  },
});
