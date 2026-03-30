import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Animated,
  Platform,
  AppState,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Audio } from "expo-av";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, fontFamily, arabicFonts, arabicFontForSentence, ltr } from "../lib/theme";
import {
  getSentenceReviewSession,
  fetchFreshSession,
  submitSentenceReview,
  undoSentenceReview,
  submitReintroResult,
  acknowledgeExperimentIntro,
  submitVerseReview,
  getAnalytics,
  getSessionEnd,
  lookupReviewWord,
  prefetchSessions,
  warmSentences,
  getGrammarLesson,
  introduceGrammarFeature,
  introduceWord,
  getWrapUpCards,
  getConfusionHelp,
  generateUuid,
  BASE_URL,
} from "../lib/api";
import {
  ReviewMode,
  ComprehensionSignal,
  SentenceReviewItem,
  SentenceReviewSession,
  IntroCandidate,
  ReintroCard,
  VerseCard,
  Analytics,
  SessionEndData,
  WordLookupResult,
  ConfusionAnalysis,
  GrammarLesson,
  WrapUpCard,
} from "../lib/types";
import { posLabel, FormsRow, FormsStrip, GrammarRow, PlayButton } from "../lib/WordCardComponents";
import { syncEvents } from "../lib/sync-events";
import { flushQueue } from "../lib/sync-queue";
import { useNetStatus } from "../lib/net-status";
import ActionMenu from "../lib/review/ActionMenu";
import SentenceInfoModal from "../lib/review/SentenceInfoModal";
import WordInfoCard, { FocusWordMark } from "../lib/review/WordInfoCard";
import { IntroducedWordsTable } from "../lib/IntroducedWordsTable";
import { GraduatedWordsTable } from "../lib/GraduatedWordsTable";

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

type SessionSlot =
  | { type: "sentence"; itemIndex: number }
  | { type: "intro"; candidateIndex: number }
  | { type: "experiment_intro"; introIndex: number }
  | { type: "verse"; verseIndex: number };

/**
 * Build an interleaved session: experiment intro cards are distributed among
 * review sentences rather than front-loaded. Sentences sharing words with a
 * recently-shown intro card are spaced apart for better retention.
 */
function buildInterleavedSession(
  items: SentenceReviewItem[],
  introCards: ReintroCard[],
  introCandidates: IntroCandidate[],
  readingMode: boolean,
  verseCards: VerseCard[] = [],
): SessionSlot[] {
  // If no intro cards, just build sentence slots + deprecated intro candidates
  if (introCards.length === 0) {
    const slots: SessionSlot[] = items.map((_, i) => ({
      type: "sentence" as const,
      itemIndex: i,
    }));
    if (introCandidates.length > 0 && readingMode) {
      for (let ci = introCandidates.length - 1; ci >= 0; ci--) {
        const insertPos = Math.min(introCandidates[ci].insert_at, slots.length);
        slots.splice(insertPos, 0, { type: "intro" as const, candidateIndex: ci });
      }
    }
    return slots;
  }

  // 1. Build intro-word → sentence-index map
  const introLemmaIds = new Set(introCards.map((c) => c.lemma_id));
  const sentenceIntroWords = new Map<number, Set<number>>();
  items.forEach((item, idx) => {
    const overlap = new Set<number>();
    for (const w of item.words) {
      if (w.lemma_id && introLemmaIds.has(w.lemma_id)) {
        overlap.add(w.lemma_id);
      }
    }
    if (overlap.size > 0) {
      sentenceIntroWords.set(idx, overlap);
    }
  });

  // 2. Build position template: first 2 intros, then repeat (3 sentences, 1 intro)
  const template: ("intro" | "sentence")[] = [];
  let introsLeft = introCards.length;
  let sentsLeft = items.length;

  const firstBatch = Math.min(2, introsLeft);
  for (let i = 0; i < firstBatch; i++) {
    template.push("intro");
    introsLeft--;
  }

  while (sentsLeft > 0 || introsLeft > 0) {
    const batch = Math.min(3, sentsLeft);
    for (let i = 0; i < batch; i++) {
      template.push("sentence");
      sentsLeft--;
    }
    if (introsLeft > 0) {
      template.push("intro");
      introsLeft--;
    }
  }

  // 3. Assign intro cards and sentences to positions with spacing
  const lastSeen = new Map<number, number>();
  const available = items.map((_, i) => i); // sentence indices
  let introIdx = 0;
  const result: SessionSlot[] = [];

  for (let pos = 0; pos < template.length; pos++) {
    if (template[pos] === "intro") {
      const card = introCards[introIdx];
      result.push({ type: "experiment_intro" as const, introIndex: introIdx });
      lastSeen.set(card.lemma_id, pos);
      introIdx++;
    } else {
      // Pick the sentence whose intro-word overlap was shown longest ago
      let bestArrayIdx = 0;
      let bestMinGap = -1;

      for (let ai = 0; ai < available.length; ai++) {
        const sentIdx = available[ai];
        const overlap = sentenceIntroWords.get(sentIdx);
        let minGap: number;
        if (!overlap || overlap.size === 0) {
          minGap = Infinity; // no intro words — always safe as spacer
        } else {
          minGap = Infinity;
          for (const lemmaId of overlap) {
            const seen = lastSeen.get(lemmaId);
            const gap = seen !== undefined ? pos - seen : Infinity;
            if (gap < minGap) minGap = gap;
          }
        }

        if (minGap > bestMinGap || (minGap === bestMinGap && ai < bestArrayIdx)) {
          bestMinGap = minGap;
          bestArrayIdx = ai;
        }
      }

      const chosenIdx = available[bestArrayIdx];
      result.push({ type: "sentence" as const, itemIndex: chosenIdx });

      // Update lastSeen for intro words in this sentence
      const overlap = sentenceIntroWords.get(chosenIdx);
      if (overlap) {
        for (const lemmaId of overlap) {
          lastSeen.set(lemmaId, pos);
        }
      }

      available.splice(bestArrayIdx, 1);
    }
  }

  // 4. Splice in deprecated intro_candidates at their insert_at positions
  if (introCandidates.length > 0 && readingMode) {
    for (let ci = introCandidates.length - 1; ci >= 0; ci--) {
      const insertPos = Math.min(introCandidates[ci].insert_at, result.length);
      result.splice(insertPos, 0, { type: "intro" as const, candidateIndex: ci });
    }
  }

  // 5. Splice in verse cards at evenly-spaced positions
  if (verseCards.length > 0) {
    const spacing = Math.floor(result.length / (verseCards.length + 1));
    for (let vi = verseCards.length - 1; vi >= 0; vi--) {
      const insertPos = Math.min(spacing * (vi + 1), result.length);
      result.splice(insertPos, 0, { type: "verse" as const, verseIndex: vi });
    }
  }

  return result;
}

interface CardSnapshot {
  missedIndices: Set<number>;
  confusedIndices: Set<number>;
  signal: ComprehensionSignal;
  sentenceId: number | null;
  primaryLemmaId: number;
  wordOutcomesBefore: Map<number, WordOutcome>;
}

function stripDiacritics(s: string): string {
  return s.replace(/[\u0610-\u061a\u064b-\u065f\u0670\u06D6-\u06ED]/g, "");
}

// Maps higher verb forms to a short label describing their relationship to Form I
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

function getFormIBase(
  wazn: string | null | undefined,
  rootFamily: { lemma_id: number; lemma_ar: string; gloss_en: string | null; pos: string | null; wazn?: string | null }[]
): { lemma_id: number; lemma_ar: string; gloss_en: string | null } | null {
  if (!wazn || !VERB_FORM_RELATION[wazn]) return null;
  return rootFamily.find((s) => s.wazn === "form_1" && s.pos === "verb") ?? null;
}

// Track grammar features already introduced this app session to avoid
// showing them again from stale prefetched sessions
const introducedGrammarKeys = new Set<string>();

export default function ReadingScreen() {
  return <ReviewScreen fixedMode="reading" />;
}

export function ReviewScreen({ fixedMode }: { fixedMode: ReviewMode }) {
  const mode = fixedMode;
  const online = useNetStatus();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [sentenceSession, setSentenceSession] =
    useState<SentenceReviewSession | null>(null);
  const [cardIndex, setCardIndex] = useState(0);
  const [cardState, setCardState] = useState<CardState>("front");
  const [loading, setLoading] = useState(true);
  const [results, setResults] = useState<SessionResults | null>(null);
  const [missedIndices, setMissedIndices] = useState<Set<number>>(new Set());
  const [confusedIndices, setConfusedIndices] = useState<Set<number>>(new Set());
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [autoIntroduced, setAutoIntroduced] = useState<IntroCandidate[]>([]);
  const [lookupResult, setLookupResult] = useState<WordLookupResult | null>(null);
  const [lookupSurfaceForm, setLookupSurfaceForm] = useState<string | null>(null);
  const [lookupLemmaId, setLookupLemmaId] = useState<number | null>(null);
  const [focusedWordMark, setFocusedWordMark] = useState<FocusWordMark | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupShowMeaning, setLookupShowMeaning] = useState(false);
  const [lookupSurfaceTranslit, setLookupSurfaceTranslit] = useState<string | null>(null);
  const [confusionData, setConfusionData] = useState<ConfusionAnalysis | null>(null);
  const [tappedOrder, setTappedOrder] = useState<number[]>([]);
  const [tappedCursor, setTappedCursor] = useState(-1);
  const tappedCacheRef = useRef<Map<number, TappedEntry>>(new Map());
  const [audioPlayCount, setAudioPlayCount] = useState(0);
  const [lookupCount, setLookupCount] = useState(0);
  const [submittingReview, setSubmittingReview] = useState(false);
  const [wordOutcomes, setWordOutcomes] = useState<Map<number, WordOutcome>>(new Map());
  const [reintroCards, setReintroCards] = useState<ReintroCard[]>([]);
  const [reintroIndex, setReintroIndex] = useState(0);
  const [experimentIntroCards, setExperimentIntroCards] = useState<ReintroCard[]>([]);
  const [verseCards, setVerseCards] = useState<VerseCard[]>([]);
  const [verseFlipped, setVerseFlipped] = useState(false);
  const [verseTappedIdx, setVerseTappedIdx] = useState<number | null>(null);
  const [verseLookedUp, setVerseLookedUp] = useState<Set<number>>(new Set());
  const verseShowTimeRef = useRef<number>(0);
  const [grammarLessons, setGrammarLessons] = useState<GrammarLesson[]>([]);
  const [grammarLessonIndex, setGrammarLessonIndex] = useState(0);
  const [grammarLessonsLoading, setGrammarLessonsLoading] = useState(false);
  const [sessionSlots, setSessionSlots] = useState<SessionSlot[]>([]);
  const [introducedLemmaIds, setIntroducedLemmaIds] = useState<Set<number>>(new Set());
  const [cardReviewIds, setCardReviewIds] = useState<(string | null)[]>([]);
  const [cardSnapshots, setCardSnapshots] = useState<CardSnapshot[]>([]);
  const [undoing, setUndoing] = useState(false);
  const [wrapUpCards, setWrapUpCards] = useState<WrapUpCard[]>([]);
  const [wrapUpIndex, setWrapUpIndex] = useState(0);
  const [wrapUpRevealed, setWrapUpRevealed] = useState(false);
  const [inWrapUp, setInWrapUp] = useState(false);
  const [seenLemmaIds, setSeenLemmaIds] = useState<Set<number>>(new Set());
  const [sentenceInfoVisible, setSentenceInfoVisible] = useState(false);
  const showTime = useRef<number>(0);
  const soundRef = useRef<Audio.Sound | null>(null);
  const lookupRequestRef = useRef(0);
  const prefetchTriggered = useRef(false);
  const lastReviewedAt = useRef<number>(0);
  const pendingRefreshRef = useRef<SentenceReviewSession | null>(null);
  const refreshingRef = useRef(false);
  const hasActiveSessionRef = useRef(false);
  const introScrollRef = useRef<ScrollView>(null);

  const totalCards = sentenceSession
    ? (sessionSlots.length > 0 ? sessionSlots.length : sentenceSession.items.length)
    : 0;
  const currentSlot: SessionSlot | null = sentenceSession && sessionSlots.length > 0
    ? sessionSlots[cardIndex] ?? null
    : null;
  const isIntroSlot = currentSlot?.type === "intro";
  const isExperimentIntroSlot = currentSlot?.type === "experiment_intro";
  const isVerseSlot = currentSlot?.type === "verse";
  const sentenceItemIndex = currentSlot?.type === "sentence" ? currentSlot.itemIndex : cardIndex;

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

  // Warm sentence cache when nearing end of session
  useEffect(() => {
    if (totalCards > 0 && cardIndex >= totalCards - 3 && !prefetchTriggered.current) {
      prefetchTriggered.current = true;
      warmSentences().catch(() => {});
    }
  }, [cardIndex, totalCards, mode]);

  // Reload session when sync completes and user is between sessions
  useEffect(() => {
    return syncEvents.on("synced", () => {
      if (totalCards === 0) {
        loadSession();
      }
    });
  }, [totalCards]);

  // Track whether we have an active (in-progress) session
  useEffect(() => {
    hasActiveSessionRef.current = sentenceSession !== null && results === null;
  }, [sentenceSession, results]);

  // Background refresh: when app resumes after 15+ min gap, fetch fresh session
  const STALE_IN_SESSION_MS = 15 * 60 * 1000;
  useEffect(() => {
    const sub = AppState.addEventListener("change", async (nextState) => {
      if (
        nextState === "active" &&
        lastReviewedAt.current > 0 &&
        hasActiveSessionRef.current &&
        !refreshingRef.current &&
        !pendingRefreshRef.current
      ) {
        const gap = Date.now() - lastReviewedAt.current;
        if (gap > STALE_IN_SESSION_MS) {
          refreshingRef.current = true;
          try {
            const fresh = await fetchFreshSession(mode);
            if (fresh.items.length > 0) {
              pendingRefreshRef.current = fresh;
            }
          } catch {}
          refreshingRef.current = false;
        }
      }
    });
    return () => sub.remove();
  }, [mode]);

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

    const currentItem = !isIntroSlot
      ? sentenceSession?.items[sentenceItemIndex] ?? null
      : null;
    const arabicText = currentItem?.arabic_text;

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

  async function loadSession(newMode?: ReviewMode, skipCache?: boolean) {
    const m = newMode ?? mode;
    pendingRefreshRef.current = null;
    lastReviewedAt.current = 0;
    setLoading(true);
    setResults(null);
    setCardIndex(0);
    setCardState(m === "listening" ? "audio" : "front");
    setMissedIndices(new Set());
    setConfusedIndices(new Set());
    setTappedOrder([]);
    setTappedCursor(-1);
    tappedCacheRef.current = new Map();
    setAudioPlaying(false);
    setAutoIntroduced([]);
    setWordOutcomes(new Map());
    setSessionSlots([]);
    setIntroducedLemmaIds(new Set());
    setCardReviewIds([]);
    setCardSnapshots([]);
    setUndoing(false);
    setWrapUpCards([]);
    setWrapUpIndex(0);
    setWrapUpRevealed(false);
    setInWrapUp(false);
    setSeenLemmaIds(new Set());
    setReintroCards([]);
    setReintroIndex(0);
    setExperimentIntroCards([]);
    setGrammarLessons([]);
    setGrammarLessonIndex(0);
    setGrammarLessonsLoading(false);
    lookupRequestRef.current += 1;
    prefetchTriggered.current = false;
    setLookupResult(null);
    setLookupSurfaceForm(null);
    setLookupLemmaId(null);
    setFocusedWordMark(null);
    setLookupLoading(false);
    setLookupShowMeaning(false);
    setConfusionData(null);
    setAudioPlayCount(0);
    setLookupCount(0);
    setSubmittingReview(false);
    setSentenceSession(null);
    await cleanupSound();

    try {
      const ss = skipCache ? await fetchFreshSession(m) : await getSentenceReviewSession(m);
      if (ss.items.length > 0) {
        setSentenceSession(ss);
        // Build interleaved session slots: intro cards distributed among sentences
        if (ss.intro_candidates && ss.intro_candidates.length > 0 && m === "reading") {
          setAutoIntroduced(ss.intro_candidates);
        }
        const slots = buildInterleavedSession(
          ss.items,
          ss.experiment_intro_cards ?? [],
          ss.intro_candidates ?? [],
          m === "reading",
          ss.verse_cards ?? [],
        );
        setSessionSlots(slots);
        if (ss.verse_cards && ss.verse_cards.length > 0) {
          setVerseCards(ss.verse_cards);
        }
        if (ss.experiment_intro_cards && ss.experiment_intro_cards.length > 0) {
          setExperimentIntroCards(ss.experiment_intro_cards);
        }
        if (ss.reintro_cards && ss.reintro_cards.length > 0) {
          setReintroCards(ss.reintro_cards);
          setReintroIndex(0);
        }
        // Load grammar lessons (refreshers first, then intros)
        // Filter out features already introduced this app session (stale prefetch)
        const featureKeys: string[] = [
          ...(ss.grammar_refresher_needed ?? []),
          ...(ss.grammar_intro_needed ?? []),
        ].filter(k => !introducedGrammarKeys.has(k));
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
    } catch (e) {
      console.error("Failed to load review session:", e);
    }
    setLoading(false);
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
    } else {
      // confused → off
      setConfusedIndices((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
    }
  }, [confusedIndices, missedIndices]);

  type TappedEntry = { surfaceForm: string; lemmaId: number | null; result: WordLookupResult | null; markState: FocusWordMark | null; showMeaning: boolean; surfaceTranslit?: string | null };

  const applyTappedEntry = useCallback((entry: TappedEntry) => {
    setLookupSurfaceForm(entry.surfaceForm);
    setLookupLemmaId(entry.lemmaId);
    setFocusedWordMark(entry.markState);
    setLookupResult(entry.result);
    setLookupShowMeaning(entry.showMeaning);
    setLookupSurfaceTranslit(entry.surfaceTranslit ?? null);
    setLookupLoading(false);
  }, []);

  const handleLookupPrev = useCallback(() => {
    setTappedCursor(prev => {
      const newIdx = prev - 1;
      if (newIdx < 0) return prev;
      const wordIdx = tappedOrder[newIdx];
      const entry = tappedCacheRef.current.get(wordIdx);
      if (entry) applyTappedEntry(entry);
      return newIdx;
    });
  }, [tappedOrder, applyTappedEntry]);

  const handleLookupNext = useCallback(() => {
    setTappedCursor(prev => {
      const newIdx = prev + 1;
      if (newIdx >= tappedOrder.length) return prev;
      const wordIdx = tappedOrder[newIdx];
      const entry = tappedCacheRef.current.get(wordIdx);
      if (entry) applyTappedEntry(entry);
      return newIdx;
    });
  }, [tappedOrder, applyTappedEntry]);

  const addToTappedHistory = useCallback((wordIndex: number, entry: TappedEntry) => {
    tappedCacheRef.current.set(wordIndex, entry);
    setTappedOrder(prev => {
      if (prev.includes(wordIndex)) {
        setTappedCursor(prev.indexOf(wordIndex));
        return prev;
      }
      const next = [...prev, wordIndex];
      setTappedCursor(next.length - 1);
      return next;
    });
  }, []);

  const removeFromTappedHistory = useCallback((wordIndex: number) => {
    tappedCacheRef.current.delete(wordIndex);
    setTappedOrder(prev => {
      const next = prev.filter(i => i !== wordIndex);
      if (next.length === 0) {
        setTappedCursor(-1);
        setLookupResult(null);
        setLookupSurfaceForm(null);
        setLookupLemmaId(null);
        setFocusedWordMark(null);
        setLookupShowMeaning(false);
      } else {
        const oldPos = prev.indexOf(wordIndex);
        const newCursor = Math.min(oldPos, next.length - 1);
        setTappedCursor(newCursor);
        const fallbackEntry = tappedCacheRef.current.get(next[newCursor]);
        if (fallbackEntry) applyTappedEntry(fallbackEntry);
      }
      return next;
    });
  }, [applyTappedEntry]);

  const handleWordTap = useCallback(async (index: number, lemmaId: number | null) => {
    const word = sentenceSession?.items[sentenceItemIndex]?.words[index];
    const isFunctionWord = word?.is_function_word ?? false;
    const wordTranslit = word?.transliteration ?? null;

    // Function words / words without lemma: show gloss only, no marking or API call
    if (!lemmaId || isFunctionWord) {
      // Toggle off if re-tapping same word
      if (lookupSurfaceForm === (word?.surface_form ?? null) && focusedWordMark !== null) {
        lookupRequestRef.current += 1;
        setLookupResult(null);
        setLookupSurfaceForm(null);
        setLookupLemmaId(null);
        setFocusedWordMark(null);
        setLookupLoading(false);
        setLookupShowMeaning(false);
        removeFromTappedHistory(index);
        return;
      }
      lookupRequestRef.current += 1;
      const fnSurface = word?.surface_form ?? null;
      setLookupSurfaceForm(fnSurface);
      setLookupLemmaId(null);
      setFocusedWordMark("missed");
      setLookupLoading(false);
      setLookupShowMeaning(true);
      const fnResult: WordLookupResult = {
        lemma_id: lemmaId ?? 0,
        lemma_ar: "",
        gloss_en: word?.gloss_en ?? null,
        transliteration: null,
        root: null,
        root_meaning: null,
        root_id: null,
        pos: null,
        forms_json: null,
        example_ar: null,
        frequency_rank: null,
        cefr_level: null,
        example_en: null,
        grammar_details: [],
        root_family: [],
        is_function_word: !lemmaId || isFunctionWord,
      };
      setLookupResult(fnResult);
      setLookupSurfaceTranslit(wordTranslit);
      addToTappedHistory(index, { surfaceForm: fnSurface ?? "", lemmaId: null, result: fnResult, markState: "missed", showMeaning: true, surfaceTranslit: wordTranslit });
      return;
    }

    const isConfused = confusedIndices.has(index);
    const isMissed = missedIndices.has(index);
    const nextMark: FocusWordMark | null = !isConfused && !isMissed
      ? "missed"
      : isMissed && !isConfused
        ? "did_not_recognize"
        : null;

    // Cycle state: off → missed → confused → off
    toggleMissed(index);

    // If tap clears this word, hide the info card or show previous.
    if (nextMark === null) {
      lookupRequestRef.current += 1;
      setLookupLoading(false);
      removeFromTappedHistory(index);
      return;
    }

    const requestId = ++lookupRequestRef.current;
    const tappedSurface = word?.surface_form ?? null;

    setLookupCount((prev) => prev + 1);
    setLookupSurfaceForm(tappedSurface);
    setLookupLemmaId(lemmaId);
    setFocusedWordMark(nextMark);
    setLookupLoading(true);
    setLookupShowMeaning(false);
    setLookupResult(null);
    setConfusionData(null);

    // Update cursor to this word (add to order if new)
    setTappedOrder(prev => {
      if (prev.includes(index)) {
        setTappedCursor(prev.indexOf(index));
        return prev;
      }
      const next = [...prev, index];
      setTappedCursor(next.length - 1);
      return next;
    });

    try {
      const result = await lookupReviewWord(lemmaId);
      if (lookupRequestRef.current !== requestId) return;
      setLookupResult(result);
      const knownSiblings = result.root_family.filter(
        (s) => (s.state === "known" || s.state === "learning") && s.lemma_id !== result.lemma_id
      );
      const showMeaning = knownSiblings.length < 1;
      setLookupShowMeaning(showMeaning);
      setLookupSurfaceTranslit(wordTranslit);
      addToTappedHistory(index, { surfaceForm: tappedSurface ?? "", lemmaId, result, markState: nextMark, showMeaning, surfaceTranslit: wordTranslit });

      // Fetch confusion analysis when marking as "did not recognize" (yellow)
      if (nextMark === "did_not_recognize" && tappedSurface) {
        getConfusionHelp(lemmaId, tappedSurface).then((data) => {
          if (lookupRequestRef.current !== requestId) return;
          if (data?.confusion_type) setConfusionData(data);
        });
      }
    } catch {
      if (lookupRequestRef.current !== requestId) return;
      let fallbackResult: WordLookupResult | null = null;
      if (word) {
        fallbackResult = {
          lemma_id: lemmaId,
          lemma_ar: "",
          gloss_en: word.gloss_en,
          transliteration: null,
          root: word.root,
          root_meaning: word.root_meaning,
          root_id: word.root_id,
          pos: null,
          forms_json: null,
          example_ar: null,
          frequency_rank: word.frequency_rank ?? null,
          cefr_level: word.cefr_level ?? null,
          example_en: null,
          grammar_details: [],
          root_family: [],
        };
        setLookupResult(fallbackResult);
      } else {
        setLookupResult(null);
      }
      setLookupShowMeaning(true);
      setLookupSurfaceTranslit(wordTranslit);
      addToTappedHistory(index, { surfaceForm: tappedSurface ?? "", lemmaId, result: fallbackResult, markState: nextMark, showMeaning: true, surfaceTranslit: wordTranslit });
    }

    if (lookupRequestRef.current === requestId) {
      setLookupLoading(false);
    }
  }, [toggleMissed, sentenceSession, cardIndex, confusedIndices, missedIndices, lookupSurfaceForm, focusedWordMark, addToTappedHistory, removeFromTappedHistory]);

  async function handleSentenceSubmit(signal: ComprehensionSignal) {
    if (!sentenceSession) return;
    const item = sentenceSession.items[sentenceItemIndex];
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

    // Save snapshot for undo before updating wordOutcomes
    const snapshot: CardSnapshot = {
      missedIndices: new Set(missedIndices),
      confusedIndices: new Set(confusedIndices),
      signal,
      sentenceId: item.sentence_id,
      primaryLemmaId: item.primary_lemma_id,
      wordOutcomesBefore: new Map(wordOutcomes),
    };

    // Track per-word outcomes
    const missedSet = new Set(missedLemmaIds);
    const confusedSet = new Set(confusedLemmaIds);
    setWordOutcomes(prev => {
      const next = new Map(prev);
      for (let i = 0; i < item.words.length; i++) {
        const w = item.words[i];
        if (w.lemma_id == null) continue;

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

    const clientReviewId = generateUuid();

    try {
      await submitSentenceReview({
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
      }, clientReviewId);
    } catch (e) {
      console.warn("sentence submit failed:", e);
    }

    // Track seen lemma IDs for wrap-up
    setSeenLemmaIds(prev => {
      const next = new Set(prev);
      for (const w of item.words) {
        if (w.lemma_id != null) next.add(w.lemma_id);
      }
      return next;
    });


    // Save review ID and snapshot for undo
    setCardReviewIds(prev => {
      const next = [...prev];
      next[cardIndex] = clientReviewId;
      return next;
    });
    setCardSnapshots(prev => {
      const next = [...prev];
      next[cardIndex] = snapshot;
      return next;
    });

    advanceAfterSubmit(signal);
  }

  async function handleGoBack() {
    if (!sentenceSession || undoing) return;

    // Find the previous sentence slot (skip intro slots)
    let targetIndex = cardIndex - 1;
    while (targetIndex >= 0) {
      const slot = sessionSlots.length > 0 ? sessionSlots[targetIndex] : null;
      if (!slot || slot.type === "sentence") break;
      targetIndex--;
    }
    if (targetIndex < 0) return;

    const reviewId = cardReviewIds[targetIndex];
    const snapshot = cardSnapshots[targetIndex];
    if (!reviewId || !snapshot) return;

    setUndoing(true);
    try {
      await undoSentenceReview(
        reviewId,
        sentenceSession.session_id,
        snapshot.sentenceId,
        snapshot.primaryLemmaId,
        mode
      );
    } catch (e) {
      console.warn("undo failed:", e);
    }
    setUndoing(false);

    // Restore card state
    setCardIndex(targetIndex);
    setMissedIndices(snapshot.missedIndices);
    setConfusedIndices(snapshot.confusedIndices);
    setTappedOrder([]);
    setTappedCursor(-1);
    tappedCacheRef.current = new Map();
    setLookupResult(null);
    setLookupSurfaceForm(null);
    setLookupLemmaId(null);
    setFocusedWordMark(null);
    setLookupShowMeaning(false);
    setCardState(mode === "listening" ? "answer" : "back");
    setWordOutcomes(snapshot.wordOutcomesBefore);

    // Decrement results counter
    setResults(prev => {
      if (!prev) return prev;
      return {
        total: prev.total - 1,
        gotIt: prev.gotIt - (snapshot.signal === "understood" ? 1 : 0),
        missed: prev.missed - (snapshot.signal === "partial" ? 1 : 0),
        noIdea: prev.noIdea - (snapshot.signal === "no_idea" ? 1 : 0),
      };
    });

    // Clear the saved review for this card
    setCardReviewIds(prev => {
      const next = [...prev];
      next[targetIndex] = null;
      return next;
    });
    setCardSnapshots(prev => {
      const next = [...prev];
      delete next[targetIndex];
      return next;
    });

    // Reset lookup state
    lookupRequestRef.current += 1;
    setLookupResult(null);
    setLookupSurfaceForm(null);
    setLookupLemmaId(null);
    setFocusedWordMark(null);
    setLookupShowMeaning(false);
    setAudioPlayCount(0);
    setLookupCount(0);
  }

  // Can go back if there's a previous sentence slot with a saved review
  const canGoBack = useMemo(() => {
    if (!sentenceSession || cardIndex === 0) return false;
    for (let i = cardIndex - 1; i >= 0; i--) {
      const slot = sessionSlots.length > 0 ? sessionSlots[i] : null;
      if (!slot || slot.type === "sentence") {
        return !!cardReviewIds[i];
      }
    }
    return false;
  }, [sentenceSession, cardIndex, sessionSlots, cardReviewIds]);

  function applyFreshSession(fresh: SentenceReviewSession) {
    setSentenceSession(fresh);
    if (fresh.intro_candidates && fresh.intro_candidates.length > 0 && mode === "reading") {
      setAutoIntroduced(fresh.intro_candidates);
    } else {
      setAutoIntroduced([]);
    }
    const slots = buildInterleavedSession(
      fresh.items,
      fresh.experiment_intro_cards ?? [],
      fresh.intro_candidates ?? [],
      mode === "reading",
      fresh.verse_cards ?? [],
    );
    setVerseCards(fresh.verse_cards ?? []);
    setSessionSlots(slots);
    setCardIndex(0);
    setCardState(mode === "listening" ? "audio" : "front");
    setResults(null);
    setMissedIndices(new Set());
    setConfusedIndices(new Set());
    setTappedOrder([]);
    setTappedCursor(-1);
    tappedCacheRef.current = new Map();
    setAudioPlaying(false);
    setAudioPlayCount(0);
    setLookupCount(0);
    setLookupResult(null);
    setLookupSurfaceForm(null);
    setLookupLemmaId(null);
    setFocusedWordMark(null);
    setLookupShowMeaning(false);
    setWordOutcomes(new Map());
    setSeenLemmaIds(new Set());
    setCardReviewIds([]);
    setCardSnapshots([]);
    setUndoing(false);
    prefetchTriggered.current = false;
    if (fresh.reintro_cards && fresh.reintro_cards.length > 0) {
      setReintroCards(fresh.reintro_cards);
      setReintroIndex(0);
    } else {
      setReintroCards([]);
    }
    setExperimentIntroCards(fresh.experiment_intro_cards ?? []);
    const featureKeys: string[] = [
      ...(fresh.grammar_refresher_needed ?? []),
      ...(fresh.grammar_intro_needed ?? []),
    ];
    if (featureKeys.length > 0) {
      (async () => {
        const lessons: GrammarLesson[] = [];
        for (const key of featureKeys) {
          try {
            const lesson = await getGrammarLesson(key);
            if (fresh.grammar_refresher_needed?.includes(key)) {
              lesson.is_refresher = true;
            }
            lessons.push(lesson);
          } catch {}
        }
        setGrammarLessons(lessons);
        setGrammarLessonIndex(0);
      })();
    } else {
      setGrammarLessons([]);
      setGrammarLessonIndex(0);
    }
  }

  function advanceAfterSubmit(signal: ComprehensionSignal) {
    lastReviewedAt.current = Date.now();
    lookupRequestRef.current += 1;
    const prev = results ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 };
    const next = {
      total: prev.total + 1,
      gotIt: prev.gotIt + (signal === "understood" ? 1 : 0),
      missed: prev.missed + (signal === "partial" ? 1 : 0),
      noIdea: prev.noIdea + (signal === "no_idea" ? 1 : 0),
    };

    const nextCardIndex = cardIndex + 1;

    // Swap in background-refreshed session if available
    const pendingRefresh = pendingRefreshRef.current;
    if (pendingRefresh) {
      pendingRefreshRef.current = null;
      applyFreshSession(pendingRefresh);
      return;
    }

    if (nextCardIndex >= totalCards) {
      setResults(next);
      setCardState(mode === "listening" ? "audio" : "front");
    } else {
      setResults(next);
      setCardIndex(nextCardIndex);
      setCardState(mode === "listening" ? "audio" : "front");
      setMissedIndices(new Set());
      setConfusedIndices(new Set());
      setTappedOrder([]);
      setTappedCursor(-1);
      tappedCacheRef.current = new Map();
      setAudioPlaying(false);
      setAudioPlayCount(0);
      setLookupCount(0);
      setLookupResult(null);
      setLookupSurfaceForm(null);
      setLookupLemmaId(null);
      setFocusedWordMark(null);
      setLookupShowMeaning(false);
    }
  }

  async function handleSubmit(signal: ComprehensionSignal) {
    if (submittingReview) return;
    setSubmittingReview(true);
    try {
      await handleSentenceSubmit(signal);
    } finally {
      setSubmittingReview(false);
    }
  }

  async function handleWrapUp() {
    if (seenLemmaIds.size === 0) return;
    // Collect lemma IDs of words the user missed (failed=true in wordOutcomes)
    const missedIds: number[] = [];
    for (const [lemmaId, outcome] of wordOutcomes) {
      if (outcome.failed) missedIds.push(lemmaId);
    }
    try {
      const cards = await getWrapUpCards(
        Array.from(seenLemmaIds),
        missedIds,
        sentenceSession?.session_id
      );
      if (cards.length > 0) {
        setWrapUpCards(cards);
        setWrapUpIndex(0);
        setWrapUpRevealed(false);
        setInWrapUp(true);
      } else {
        // No words to quiz — just end session
        setResults(prev => prev ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 });
        const r = results ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 };
        setResults({ ...r, total: totalCards });
      }
    } catch {
      const r = results ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 };
      setResults({ ...r, total: totalCards });
    }
  }

  async function handleWrapUpAnswer(gotIt: boolean) {
    const card = wrapUpCards[wrapUpIndex];
    // Submit as acquisition review
    try {
      await submitSentenceReview({
        sentence_id: null,
        primary_lemma_id: card.lemma_id,
        comprehension_signal: gotIt ? "understood" : "no_idea",
        missed_lemma_ids: gotIt ? [] : [card.lemma_id],
        response_ms: Date.now() - showTime.current,
        session_id: sentenceSession?.session_id ?? generateUuid(),
        review_mode: "quiz",
      });
    } catch {}

    if (wrapUpIndex < wrapUpCards.length - 1) {
      setWrapUpIndex(wrapUpIndex + 1);
      setWrapUpRevealed(false);
      showTime.current = Date.now();
    } else {
      // Done — show session results
      setInWrapUp(false);
      const r = results ?? { total: 0, gotIt: 0, missed: 0, noIdea: 0 };
      setResults({ ...r, total: totalCards });
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
    if (sentenceSession) {
      const item = isIntroSlot ? null : sentenceSession.items[sentenceItemIndex];
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
    }
    if (lookupSurfaceForm && lookupLemmaId !== null) {
      parts.push(
        `Lookup focus: surface=${lookupSurfaceForm}, lemma_id=${lookupLemmaId}, mark=${focusedWordMark ?? "none"}`
      );
      if (lookupResult) {
        parts.push(
          `Lookup resolved lemma: lemma_ar=${lookupResult.lemma_ar || "unknown"}, gloss=${lookupResult.gloss_en || "unknown"}, root=${lookupResult.root || "unknown"}`
        );
      }
    }
    return parts.join("\n");
  }

  function buildAutoExplainPrompt(): string | null {
    if (!sentenceSession) return null;
    const item = isIntroSlot ? null : sentenceSession.items[sentenceItemIndex];
    if (!item) return null;

    const parts: string[] = [
      "Explain how this Arabic sentence works — how the words combine to produce the meaning.",
      "",
      "Focus on:",
      "- What is this sentence really saying? What's the overall meaning and feel?",
      "- Why it's translated this way — nuances, idioms, cultural context?",
      "- Alternative translations and what shades of meaning they'd carry",
      "- Structural patterns worth noticing (word order, verb forms, idafa) — only when they affect meaning",
      "",
      "Don't list every particle or explain obvious words. I can see the glosses already.",
    ];

    const marked = [
      ...Array.from(missedIndices).map((i) => ({ index: i, mark: "MISSED" })),
      ...Array.from(confusedIndices).map((i) => ({ index: i, mark: "CONFUSED" })),
    ].sort((a, b) => a.index - b.index);

    if (marked.length > 0) {
      parts.push(
        "",
        "I marked some words below. For each marked word:",
        "1) Identify the base lemma (Arabic + transliteration)",
        "2) Explain how the surface form differs from the lemma (clitics, article, suffixes, inflection)",
        "3) Give one short recognition tip",
      );
    }

    parts.push("", "Words:");
    item.words.forEach((w, i) => {
      const markLabel = marked.find((m) => m.index === i);
      const prefix = markLabel ? `[${markLabel.mark}] ` : "";
      const known = w.knowledge_state === "known" || w.knowledge_state === "learning";
      const status = w.is_function_word ? "function" : (known ? "known" : "learning/new");
      const lineParts = [`${prefix}${i + 1}. ${w.surface_form} — ${status}`];
      if (w.gloss_en) lineParts.push(`gloss: "${w.gloss_en}"`);
      if (w.root) lineParts.push(`root: ${w.root}`);
      if (w.grammar_tags?.length) lineParts.push(`grammar: ${w.grammar_tags.join(", ")}`);
      parts.push(lineParts.join(", "));
    });

    parts.push(
      "",
      "Also check: do any words have a wrong or confusing lemma assignment? If a word's gloss doesn't match what it means in this specific sentence context, flag it.",
      "",
      "Finally: is the English translation accurate? If it's wrong or misleading, say so.",
    );

    return parts.join("\n");
  }

  // --- Render ---

  const isSessionDone = !!(results && !inWrapUp && totalCards > 0 && results.total >= totalCards);

  // Wrap-up quiz flow
  if (inWrapUp && wrapUpCards.length > 0) {
    const wc = wrapUpCards[wrapUpIndex];
    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <View style={styles.progressContainer}>
          <Text style={styles.progressText}>
            Wrap-up {wrapUpIndex + 1}/{wrapUpCards.length}
          </Text>
        </View>
        <ScrollView
          contentContainerStyle={styles.reintroScrollContent}
          showsVerticalScrollIndicator={false}
        >
          <View style={[styles.eiHero, { borderRadius: 16 }]}>
            <Text style={styles.eiArabic}>{wc.lemma_ar}</Text>
            {wc.transliteration && (
              <Text style={styles.eiTranslit}>{wc.transliteration}</Text>
            )}
            {wrapUpRevealed && (
              <>
                <View style={styles.divider} />
                <Text style={styles.eiEnglish}>{wc.gloss_en || ""}</Text>
                <View style={styles.eiChipsArea}>
                  {wc.pos && (
                    <View style={styles.eiChipPos}>
                      <Text style={styles.eiChipPosText}>{posLabel(wc.pos, wc.forms_json)}</Text>
                    </View>
                  )}
                  {wc.root && wc.root_id && (
                    <Pressable
                      style={[styles.eiChipOutline, { borderColor: "#9b59b630", backgroundColor: "#9b59b620" }]}
                      onPress={() => router.push(`/root/${wc.root_id}`)}
                    >
                      <Text style={{ fontSize: 14, fontWeight: "600", fontFamily: fontFamily.arabic, writingDirection: "rtl", color: "#9b59b6" }}>{wc.root}</Text>
                      {wc.root_meaning && (
                        <Text style={{ fontSize: 12, fontWeight: "600", color: "#9b59b6" }} numberOfLines={1}>{wc.root_meaning}</Text>
                      )}
                      <Text style={{ color: "#9b59b660", fontSize: 10 }}>{" \u203A"}</Text>
                    </Pressable>
                  )}
                  {wc.wazn && (
                    <Pressable
                      style={[styles.eiChipOutline, { borderColor: "#f39c1230", backgroundColor: "#f39c1220" }]}
                      onPress={() => router.push(`/pattern/${encodeURIComponent(wc.wazn!)}`)}
                    >
                      <Text style={{ fontSize: 12, fontWeight: "600", color: "#f39c12" }}>{wc.wazn}</Text>
                      {wc.wazn_meaning && (
                        <Text style={{ fontSize: 12, fontWeight: "600", color: "#f39c12" }} numberOfLines={1}>{wc.wazn_meaning}</Text>
                      )}
                      <Text style={{ color: "#f39c1260", fontSize: 10 }}>{" \u203A"}</Text>
                    </Pressable>
                  )}
                </View>
                <FormsStrip pos={wc.pos} forms={wc.forms_json} formsTranslit={wc.forms_translit} />
                {wc.etymology_json?.derivation && (
                  <Text style={[styles.eiInfoText, { fontStyle: "italic", marginTop: 4, fontSize: 12, color: colors.textSecondary }]}>
                    {ltr(wc.etymology_json.derivation)}
                  </Text>
                )}
                {wc.memory_hooks_json?.mnemonic && (
                  <View style={[styles.eiMnemonicCard, { marginTop: 4 }]}>
                    <Text style={styles.eiInfoText}>{ltr(wc.memory_hooks_json.mnemonic)}</Text>
                  </View>
                )}
              </>
            )}
          </View>
        </ScrollView>
        <View style={styles.bottomActions}>
          {!wrapUpRevealed ? (
            <View style={styles.actionRow}>
              <Pressable
                style={[styles.actionButton, styles.showButton]}
                onPress={() => { setWrapUpRevealed(true); showTime.current = Date.now(); }}
              >
                <Text style={styles.showButtonText}>Show Answer</Text>
              </Pressable>
            </View>
          ) : (
            <View style={styles.actionRow}>
              <Pressable
                style={[styles.actionButton, styles.gotItButton]}
                onPress={() => handleWrapUpAnswer(true)}
              >
                <Text style={styles.actionButtonText}>Got it</Text>
              </Pressable>
              <Pressable
                style={[styles.actionButton, styles.continueButton]}
                onPress={() => handleWrapUpAnswer(false)}
              >
                <Text style={styles.actionButtonText}>Missed</Text>
              </Pressable>
            </View>
          )}
        </View>
      </View>
    );
  }

  // Session complete takes priority over loading — prevents flash-and-disappear
  // when background sync triggers a reload
  if (isSessionDone) {
    return (
      <SessionComplete
        results={results}
        mode={mode}
        autoIntroduced={autoIntroduced}
        introducedLemmaIds={introducedLemmaIds}
        wordOutcomes={wordOutcomes}
        sessionId={sentenceSession?.session_id ?? ""}
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
        onRefresh={() => loadSession(undefined, true)}
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
            introducedGrammarKeys.add(lesson.feature_key);
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

  // Re-introduction cards shown before sentence review — pure re-exposure, no self-assessment
  const showingReintro = reintroCards.length > 0 && reintroIndex < reintroCards.length;
  if (showingReintro) {
    const card = reintroCards[reintroIndex];
    const knownSiblings = card.root_family.filter(
      (s) => (s.state === "known" || s.state === "learning") && s.lemma_id !== card.lemma_id
    );
    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <View style={styles.progressContainer}>
          <Text style={styles.progressText}>
            Review {reintroIndex + 1} of {reintroCards.length}
          </Text>
          <View style={styles.actionMenuRow}>
            <ActionMenu
              focusedLemmaId={card.lemma_id}
              focusedLemmaAr={card.lemma_ar}
              sentenceId={null}
              askAIContextBuilder={buildContext}
              askAIScreen="review"
            />
          </View>
        </View>
        <ScrollView
          ref={introScrollRef}
          contentContainerStyle={styles.reintroScrollContent}
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.reintroCard}>
            <View style={styles.reintroWordHeader}>
              <Text style={styles.reintroArabic}>{card.lemma_ar}</Text>
              <PlayButton audioUrl={card.audio_url} word={card.lemma_ar} />
            </View>
            <Text style={styles.introMeaningLabel}>Meaning</Text>
            <Text style={styles.reintroEnglish}>{card.gloss_en}</Text>
            {card.transliteration && (
              <Text style={styles.reintroTranslit}>{card.transliteration}</Text>
            )}
            {card.pos && (
              <Text style={styles.reintroPos}>
                {posLabel(card.pos, card.forms_json)}
              </Text>
            )}
            <FormsRow pos={card.pos} forms={card.forms_json} />

            {card.wazn && (
              <View style={styles.expIntroHighlight}>
                <Text style={styles.expIntroHighlightLabel}>Pattern</Text>
                <Text style={styles.expIntroHighlightText}>
                  {card.wazn}{card.wazn_meaning ? ` \u2014 ${card.wazn_meaning}` : ""}
                </Text>
                {(() => {
                  const base = getFormIBase(card.wazn, card.root_family);
                  if (!base) return null;
                  return (
                    <Text style={{ color: colors.textSecondary, fontSize: 12, marginTop: 4 }}>
                      {VERB_FORM_RELATION[card.wazn]}{" "}
                      <Text style={{ fontFamily: fontFamily.arabic, color: colors.arabic }}>{base.lemma_ar}</Text>
                      {base.gloss_en ? ` \u2014 ${base.gloss_en}` : ""}
                    </Text>
                  );
                })()}
              </View>
            )}

            {card.root && (
              <View style={styles.expIntroHighlight}>
                <Text style={styles.expIntroHighlightLabel}>Root</Text>
                <Text style={styles.expIntroHighlightText}>
                  {card.root}
                  {card.root_meaning ? ` \u2014 ${card.root_meaning}` : ""}
                </Text>
                {knownSiblings.length > 0 && (
                  <View style={{ marginTop: 6, gap: 3 }}>
                    <Text style={{ color: colors.accent, fontSize: 12, fontWeight: "600" }}>
                      You know {knownSiblings.length} word{knownSiblings.length !== 1 ? "s" : ""} from this root:
                    </Text>
                    {knownSiblings.slice(0, 3).map((sib) => (
                      <Text key={sib.lemma_id} style={{ color: colors.text, fontSize: 13, fontFamily: fontFamily.arabic }}>
                        {sib.lemma_ar} <Text style={{ color: colors.textSecondary, fontFamily: undefined }}>{sib.gloss_en}</Text>
                      </Text>
                    ))}
                  </View>
                )}
              </View>
            )}

            {card.etymology?.derivation && (
              <View style={styles.expIntroHighlight}>
                <Text style={styles.expIntroHighlightLabel}>Origin</Text>
                <Text style={styles.expIntroHighlightText}>
                  {ltr(`${card.etymology.pattern ? `${card.etymology.pattern}: ` : ""}${card.etymology.derivation}`)}
                </Text>
              </View>
            )}

            {card.memory_hooks?.mnemonic && (
              <View style={[styles.expIntroHighlight, { backgroundColor: "rgba(74, 158, 255, 0.12)" }]}>
                <Text style={styles.expIntroHighlightLabel}>Remember</Text>
                <Text style={styles.expIntroHighlightText}>
                  {ltr(card.memory_hooks.mnemonic)}
                </Text>
              </View>
            )}

            {card.example_ar && (
              <View style={styles.reintroExample}>
                <Text style={styles.reintroExampleAr}>{card.example_ar}</Text>
                {card.example_en && (
                  <Text style={styles.reintroExampleEn}>{card.example_en}</Text>
                )}
              </View>
            )}
          </View>
        </ScrollView>

        <View style={styles.reintroActions}>
          <Pressable
            style={styles.reintroRememberBtn}
            onPress={() => {
              submitReintroResult(
                card.lemma_id,
                "remember",
                sentenceSession?.session_id,
              ).catch(() => {});
              introScrollRef.current?.scrollTo({ y: 0, animated: false });
              if (reintroIndex + 1 < reintroCards.length) {
                setReintroIndex(reintroIndex + 1);
              } else {
                setReintroCards([]);
                setReintroIndex(0);
              }
            }}
          >
            <Text style={styles.reintroRememberText}>Continue</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // Experiment intro card (interleaved in session via sessionSlots)
  if (sentenceSession && isExperimentIntroSlot) {
    const eiSlotIndex = (currentSlot as { type: "experiment_intro"; introIndex: number }).introIndex;
    const card = experimentIntroCards[eiSlotIndex];
    const isRescueCard = (card.times_seen ?? 0) > 0;
    const knownSiblings = card.root_family.filter(
      (s) => (s.state === "known" || s.state === "learning") && s.lemma_id !== card.lemma_id
    );
    const eiHasMnemonic = !!card.memory_hooks?.mnemonic;
    const eiHasEtymology = !!card.etymology?.derivation;
    const eiHasFunFact = !!card.memory_hooks?.fun_fact;
    const eiHasCulturalNote = !!card.etymology?.cultural_note;
    const eiHasUsageContext = !!card.memory_hooks?.usage_context;

    // Count intro slots for progress display
    const introSlotCount = sessionSlots.filter((s) => s.type === "experiment_intro").length;
    const introSlotsSoFar = sessionSlots.slice(0, cardIndex + 1).filter((s) => s.type === "experiment_intro").length;

    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <ProgressBar
          current={cardIndex + 1}
          total={totalCards}
          mode={mode}
          actionMenu={
            <ActionMenu
              focusedLemmaId={card.lemma_id}
              focusedLemmaAr={card.lemma_ar}
              sentenceId={null}
              askAIContextBuilder={buildContext}
              askAIScreen="review"
            />
          }
        />
        <ScrollView
          ref={introScrollRef}
          contentContainerStyle={styles.reintroScrollContent}
          showsVerticalScrollIndicator={false}
        >
          {/* Hero card */}
          <View style={styles.eiHero}>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, marginBottom: 4 }}>
              <Text style={[styles.reintroLabel, { color: colors.accent }]}>
                {isRescueCard ? "Let\u2019s revisit" : "New word"} {introSlotsSoFar} of {introSlotCount}
              </Text>
              {card.source && (
                <Text style={{ fontSize: 10, color: colors.textSecondary, fontWeight: "500", opacity: 0.7 }}>
                  {card.source.replace(/_/g, " ")}
                </Text>
              )}
            </View>
            <Text style={styles.eiArabic}>{card.lemma_ar}</Text>
            <Text style={styles.introMeaningLabel}>Meaning</Text>
            <Text style={styles.eiEnglish}>{card.gloss_en}</Text>
            {card.transliteration && (
              <Text style={styles.eiTranslit}>{card.transliteration}</Text>
            )}

            {/* Flow chips */}
            <View style={styles.eiChipsArea}>
              {card.pos && (
                <View style={styles.eiChipPos}>
                  <Text style={styles.eiChipPosText}>{posLabel(card.pos, card.forms_json)}</Text>
                </View>
              )}
              {card.root && card.root_id && (
                <Pressable
                  style={[styles.eiChipOutline, { borderColor: "#9b59b630", backgroundColor: "#9b59b620", maxWidth: "90%", flexShrink: 1 }]}
                  onPress={() => router.push(`/root/${card.root_id}`)}
                >
                  <Text style={{ fontSize: 14, fontWeight: "600", fontFamily: fontFamily.arabic, writingDirection: "rtl", color: "#9b59b6" }}>{card.root}</Text>
                  {card.root_meaning && (
                    <Text style={{ fontSize: 12, fontWeight: "600", color: "#9b59b6", flexShrink: 1 }} numberOfLines={1}>{card.root_meaning}</Text>
                  )}
                  <Text style={{ color: "#9b59b660", fontSize: 10 }}>{" \u203A"}</Text>
                </Pressable>
              )}
              {card.wazn && (
                <Pressable
                  style={[styles.eiChipOutline, { borderColor: "#f39c1230", backgroundColor: "#f39c1220", maxWidth: "90%", flexShrink: 1 }]}
                  onPress={() => router.push(`/pattern/${encodeURIComponent(card.wazn!)}`)}
                >
                  <Text style={{ fontSize: 12, fontWeight: "600", color: "#f39c12" }}>{card.wazn}</Text>
                  {card.wazn_meaning && (
                    <Text style={{ fontSize: 12, fontWeight: "600", color: "#f39c12", flexShrink: 1 }} numberOfLines={1}>{card.wazn_meaning}</Text>
                  )}
                  <Text style={{ color: "#f39c1260", fontSize: 10 }}>{" \u203A"}</Text>
                </Pressable>
              )}
            </View>

            <FormsStrip pos={card.pos} forms={card.forms_json} formsTranslit={card.forms_translit} />
          </View>

          {/* Info sections below hero */}
          <View style={styles.eiInfoSections}>
            {eiHasEtymology && (
              <View style={styles.eiInfoSection}>
                <Text style={[styles.eiSectionLabel, { color: colors.accent }]}>Etymology</Text>
                <Text style={styles.eiInfoText}>
                  {ltr(`${card.etymology!.pattern ? `${card.etymology!.pattern}: ` : ""}${card.etymology!.derivation}`)}
                </Text>
              </View>
            )}

            {eiHasMnemonic && (
              <View style={styles.eiInfoSection}>
                <Text style={[styles.eiSectionLabel, { color: "#9b59b6" }]}>Memory Hook</Text>
                <View style={styles.eiMnemonicCard}>
                  <Text style={styles.eiInfoText}>{ltr(card.memory_hooks!.mnemonic!)}</Text>
                </View>
              </View>
            )}

            {card.root && (
              <View style={styles.eiInfoSection}>
                <Pressable
                  style={styles.eiSectionLabelRow}
                  onPress={card.root_id ? () => router.push(`/root/${card.root_id}`) : undefined}
                >
                  <Text style={[styles.eiSectionLabel, { color: "#2ecc71", marginBottom: 0 }]}>
                    Root Family ({knownSiblings.length}/{card.root_family.length})
                  </Text>
                  {card.root_id && <Text style={styles.eiSectionLink}>View all ›</Text>}
                </Pressable>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <Text style={{ fontSize: 20, color: colors.accent, fontWeight: "700", fontFamily: fontFamily.arabic, writingDirection: "rtl" }}>{card.root}</Text>
                  {card.root_meaning && <Text style={{ fontSize: 13, color: colors.textSecondary, flex: 1 }}>{card.root_meaning}</Text>}
                </View>
                {(() => {
                  const base = getFormIBase(card.wazn, card.root_family);
                  if (!base) return null;
                  return (
                    <View style={{ marginTop: 6, flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                      <Text style={{ fontSize: 11, color: "#f39c12", fontWeight: "600" }}>
                        {VERB_FORM_RELATION[card.wazn!]}
                      </Text>
                      <Text style={{ fontSize: 14, color: colors.arabic, fontFamily: fontFamily.arabic }}>{base.lemma_ar}</Text>
                      {base.gloss_en && <Text style={{ fontSize: 11, color: colors.textSecondary }}>{base.gloss_en}</Text>}
                    </View>
                  );
                })()}
                {knownSiblings.length > 0 && (
                  <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
                    {knownSiblings.slice(0, 4).map((sib) => (
                      <View key={sib.lemma_id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                        <View style={{ width: 5, height: 5, borderRadius: 3, backgroundColor: sib.state === "known" ? "#2ecc71" : "#e67e22" }} />
                        <Text style={{ fontSize: 15, color: colors.arabic, fontFamily: fontFamily.arabic, writingDirection: "rtl" }}>{sib.lemma_ar}</Text>
                        {sib.wazn && <Text style={{ fontSize: 10, color: "#f39c1299", fontWeight: "600" }}>{sib.wazn.replace("form_", "F")}</Text>}
                        {sib.gloss_en && <Text style={{ fontSize: 11, color: colors.textSecondary }}>{sib.gloss_en}</Text>}
                      </View>
                    ))}
                  </View>
                )}
              </View>
            )}

            {card.example_ar && (
              <View style={styles.eiInfoSection}>
                <Text style={[styles.eiSectionLabel, { color: colors.textSecondary }]}>Example</Text>
                <Text style={{ fontSize: 18, color: colors.arabic, fontFamily: fontFamily.arabic, writingDirection: "rtl", lineHeight: 30, textAlign: "right" }}>{card.example_ar}</Text>
                {card.example_en && (
                  <Text style={{ fontSize: 14, color: colors.textSecondary, marginTop: 4 }}>{card.example_en}</Text>
                )}
              </View>
            )}

            {eiHasUsageContext && (
              <View style={styles.eiInfoSection}>
                <Text style={[styles.eiSectionLabel, { color: colors.textSecondary }]}>Usage</Text>
                <Text style={styles.eiInfoText}>{ltr(card.memory_hooks!.usage_context!)}</Text>
              </View>
            )}

            {(eiHasFunFact || eiHasCulturalNote) && (
              <View style={styles.eiInfoSection}>
                <Text style={[styles.eiSectionLabel, { color: "#2ecc71" }]}>Did You Know?</Text>
                <View style={styles.eiFunFactCard}>
                  <Text style={styles.eiFunFactText}>
                    {ltr((card.memory_hooks?.fun_fact || card.etymology?.cultural_note)!)}
                  </Text>
                </View>
              </View>
            )}
          </View>
        </ScrollView>

        <View style={styles.reintroActions}>
          <Pressable
            style={styles.reintroRememberBtn}
            onPress={() => {
              acknowledgeExperimentIntro(
                card.lemma_id,
                sentenceSession?.session_id,
              ).catch(() => {});
              introScrollRef.current?.scrollTo({ y: 0, animated: false });
              advanceAfterSubmit("understood");
            }}
          >
            <Text style={styles.reintroRememberText}>Continue</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // Quranic verse card (interleaved in session)
  if (sentenceSession && isVerseSlot) {
    const verseIdx = (currentSlot as { type: "verse"; verseIndex: number }).verseIndex;
    const verse = verseCards[verseIdx];
    if (!verseFlipped && verseShowTimeRef.current === 0) {
      verseShowTimeRef.current = Date.now();
    }

    if (verse) {
      const hasWords = verse.words && verse.words.length > 0;
      const activeVerseWordIdx = tappedOrder.length > 0 && tappedCursor >= 0 ? tappedOrder[tappedCursor] : null;
      const lookedUpWords = verse.words?.filter((_, i) => tappedOrder.includes(i) && !verse.words[i].is_function_word) ?? [];

      return (
        <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
          <ProgressBar
            current={cardIndex + 1}
            total={totalCards}
            mode={mode}
            actionMenu={
              <ActionMenu
                focusedLemmaId={lookupLemmaId}
                focusedLemmaAr={lookupResult?.lemma_ar ?? null}
                sentenceId={null}
                askAIContextBuilder={() => `Quranic verse: ${verse.surah_name_en} ${verse.surah}:${verse.ayah}\nArabic: ${verse.arabic_text}\nTranslation: ${verse.english_translation}`}
                askAIScreen="review"
                extraActions={[
                  {
                    icon: "refresh-outline" as const,
                    label: "Refresh session",
                    onPress: () => loadSession(undefined, true),
                  },
                ]}
              />
            }
          />
          <ScrollView
            contentContainerStyle={{ padding: 20, paddingBottom: 8 }}
            showsVerticalScrollIndicator={false}
          >
            {/* Attribution — pinned at top */}
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, marginBottom: 20 }}>
              <Text style={{ fontSize: 13, color: "#d4a056", fontWeight: "600" }}>
                {verse.surah_name_en || `Surah ${verse.surah}`} : {verse.ayah}
              </Text>
              {verse.surah_name_ar && (
                <Text style={{ fontSize: 15, color: "#d4a056", fontFamily: fontFamily.arabic, writingDirection: "rtl" }}>
                  {verse.surah_name_ar}
                </Text>
              )}
              {verse.is_new && (
                <View style={{ backgroundColor: "#d4a05620", borderRadius: 8, paddingHorizontal: 8, paddingVertical: 2 }}>
                  <Text style={{ fontSize: 11, color: "#d4a056", fontWeight: "600" }}>NEW</Text>
                </View>
              )}
            </View>

            {/* Arabic text — tappable words if word data available */}
            {hasWords ? (
              <View style={{ flexDirection: "row-reverse", flexWrap: "wrap", justifyContent: "center", gap: 6, marginBottom: 20 }}>
                {verse.words.map((w, i) => {
                  const isActive = tappedOrder.length > 0 && tappedCursor >= 0 && tappedOrder[tappedCursor] === i;
                  const isLookedUp = tappedOrder.includes(i);
                  return (
                  <Pressable
                    key={i}
                    onPress={async () => {
                      // Re-tap active word → remove from history
                      if (isActive) {
                        removeFromTappedHistory(i);
                        return;
                      }

                      // Function words / no lemma → show gloss-only card
                      if (w.is_function_word || !w.lemma_id) {
                        const fnResult: WordLookupResult = {
                          lemma_id: w.lemma_id ?? 0,
                          lemma_ar: w.lemma_ar ?? "",
                          gloss_en: w.gloss_en ?? null,
                          transliteration: null,
                          root: w.root ?? null,
                          root_meaning: w.root_meaning ?? null,
                          root_id: null,
                          pos: w.pos ?? null,
                          forms_json: null,
                          example_ar: null,
                          frequency_rank: null,
                          cefr_level: null,
                          example_en: null,
                          grammar_details: [],
                          root_family: [],
                          is_function_word: true,
                        };
                        setLookupSurfaceForm(w.surface_form);
                        setLookupLemmaId(null);
                        setLookupResult(fnResult);
                        setLookupLoading(false);
                        setLookupShowMeaning(true);
                        addToTappedHistory(i, { surfaceForm: w.surface_form, lemmaId: null, result: fnResult, markState: null, showMeaning: true });
                        return;
                      }

                      // Content word → fetch full WordInfoCard
                      const reqId = ++lookupRequestRef.current;
                      setLookupSurfaceForm(w.surface_form);
                      setLookupLoading(true);
                      setLookupShowMeaning(true);
                      setLookupResult(null);
                      setLookupLemmaId(w.lemma_id);
                      addToTappedHistory(i, { surfaceForm: w.surface_form, lemmaId: w.lemma_id, result: null, markState: null, showMeaning: true });
                      try {
                        const result = await lookupReviewWord(w.lemma_id!);
                        if (lookupRequestRef.current !== reqId) return;
                        setLookupResult(result);
                        tappedCacheRef.current.set(i, { surfaceForm: w.surface_form, lemmaId: w.lemma_id, result, markState: null, showMeaning: true });
                      } catch {
                        if (lookupRequestRef.current !== reqId) return;
                        const fallbackResult: WordLookupResult = {
                          lemma_id: w.lemma_id ?? 0,
                          lemma_ar: w.lemma_ar ?? "",
                          gloss_en: w.gloss_en ?? null,
                          transliteration: null,
                          root: w.root ?? null,
                          root_meaning: w.root_meaning ?? null,
                          root_id: null,
                          pos: w.pos ?? null,
                          forms_json: null,
                          example_ar: null,
                          frequency_rank: null,
                          cefr_level: null,
                          example_en: null,
                          grammar_details: [],
                          root_family: [],
                        };
                        setLookupResult(fallbackResult);
                        tappedCacheRef.current.set(i, { surfaceForm: w.surface_form, lemmaId: w.lemma_id, result: fallbackResult, markState: null, showMeaning: true });
                      } finally {
                        if (lookupRequestRef.current === reqId) setLookupLoading(false);
                      }
                    }}
                    style={{
                      paddingHorizontal: 4,
                      paddingVertical: 2,
                      borderRadius: 4,
                      backgroundColor: isActive ? "#d4a05625" : "transparent",
                      borderBottomWidth: isLookedUp && !isActive ? 2 : isActive ? 2 : 0,
                      borderBottomColor: isActive ? "#d4a056" : "#d4a05650",
                    }}
                  >
                    <Text style={{
                      fontSize: verseFlipped ? 30 : 36,
                      lineHeight: verseFlipped ? 58 : 68,
                      color: colors.arabic,
                      fontFamily: fontFamily.arabic,
                      writingDirection: "rtl",
                    }}>
                      {w.surface_form}
                    </Text>
                  </Pressable>
                  );
                })}
              </View>
            ) : (
              <Text style={{
                fontSize: verseFlipped ? 30 : 36,
                lineHeight: verseFlipped ? 58 : 68,
                color: colors.arabic,
                fontFamily: fontFamily.arabic,
                writingDirection: "rtl",
                textAlign: "center",
                marginBottom: 20,
              }}>
                {verse.arabic_text}
              </Text>
            )}

            {/* Translation (shown after flip) */}
            {verseFlipped && (
              <View style={{ borderTopWidth: 1, borderTopColor: "#d4a05630", paddingTop: 16, marginBottom: 12 }}>
                <Text style={{
                  fontSize: 16,
                  lineHeight: 26,
                  color: colors.textSecondary,
                  textAlign: "center",
                }}>
                  {verse.english_translation}
                </Text>
                {verse.transliteration && (
                  <Text style={{
                    fontSize: 15,
                    color: "#8888a0",
                    fontFamily: fontFamily.translit,
                    marginTop: 8,
                    textAlign: "center",
                    lineHeight: 24,
                  }}>
                    {verse.transliteration}
                  </Text>
                )}

                {/* Looked-up words summary pills */}
                {lookedUpWords.length > 0 && (
                  <View style={{ flexDirection: "row", flexWrap: "wrap", justifyContent: "center", gap: 6, marginTop: 14 }}>
                    {lookedUpWords.map((w, i) => (
                      <View key={i} style={{
                        backgroundColor: "#d4a05610",
                        borderWidth: 1,
                        borderColor: "#d4a05620",
                        borderRadius: 8,
                        paddingHorizontal: 8,
                        paddingVertical: 3,
                        flexDirection: "row",
                        alignItems: "center",
                        gap: 4,
                      }}>
                        <Text style={{ fontSize: 13, color: colors.arabic, fontFamily: fontFamily.arabic, writingDirection: "rtl" }}>
                          {w.lemma_ar || w.surface_form}
                        </Text>
                        {w.gloss_en && (
                          <Text style={{ fontSize: 10, color: "#888" }}>{w.gloss_en}</Text>
                        )}
                      </View>
                    ))}
                  </View>
                )}
              </View>
            )}
          </ScrollView>

          {tappedOrder.length > 0 && (
            <WordInfoCard
              result={lookupResult}
              loading={lookupLoading}
              surfaceForm={lookupSurfaceForm}
              markState={null}
              showMeaning={true}
              reserveSpace={false}
              onNavigateToDetail={(id) => router.push(`/word/${id}`)}
              onNavigateToPattern={(wazn) => router.push(`/pattern/${encodeURIComponent(wazn)}`)}
              onNavigateToRoot={(rootId) => router.push(`/root/${rootId}`)}
              onPrev={handleLookupPrev}
              onNext={handleLookupNext}
              hasPrev={tappedCursor > 0}
              hasNext={tappedCursor < tappedOrder.length - 1}
            />
          )}

          <View style={{ padding: 16, gap: 10 }}>
            {!verseFlipped ? (
              <Pressable
                style={{ backgroundColor: "#d4a056", borderRadius: 12, paddingVertical: 14, alignItems: "center" }}
                onPress={() => setVerseFlipped(true)}
              >
                <Text style={{ color: "#fff", fontSize: 16, fontWeight: "600" }}>Show Translation</Text>
              </Pressable>
            ) : (
              <View style={{ flexDirection: "row", gap: 10 }}>
                <Pressable
                  style={{ flex: 1, backgroundColor: "#e74c3c20", borderWidth: 1, borderColor: "#e74c3c30", borderRadius: 12, paddingVertical: 14, alignItems: "center" }}
                  onPress={() => {
                    const ms = Date.now() - verseShowTimeRef.current;
                    submitVerseReview(verse.verse_id, "not_yet", sentenceSession?.session_id, ms).catch(() => {});
                    setVerseFlipped(false);
                    setTappedOrder([]); setTappedCursor(-1); tappedCacheRef.current = new Map();
                    setLookupResult(null); setLookupSurfaceForm(null); setLookupLemmaId(null);
                    verseShowTimeRef.current = 0;
                    advanceAfterSubmit("no_idea");
                  }}
                >
                  <Text style={{ color: "#e74c3c", fontSize: 14, fontWeight: "600" }}>Not yet</Text>
                </Pressable>
                <Pressable
                  style={{ flex: 1, backgroundColor: "#f39c1220", borderWidth: 1, borderColor: "#f39c1230", borderRadius: 12, paddingVertical: 14, alignItems: "center" }}
                  onPress={() => {
                    const ms = Date.now() - verseShowTimeRef.current;
                    submitVerseReview(verse.verse_id, "partially", sentenceSession?.session_id, ms).catch(() => {});
                    setVerseFlipped(false);
                    setTappedOrder([]); setTappedCursor(-1); tappedCacheRef.current = new Map();
                    setLookupResult(null); setLookupSurfaceForm(null); setLookupLemmaId(null);
                    verseShowTimeRef.current = 0;
                    advanceAfterSubmit("partial");
                  }}
                >
                  <Text style={{ color: "#f39c12", fontSize: 14, fontWeight: "600" }}>Partially</Text>
                </Pressable>
                <Pressable
                  style={{ flex: 1, backgroundColor: "#2ecc7120", borderWidth: 1, borderColor: "#2ecc7130", borderRadius: 12, paddingVertical: 14, alignItems: "center" }}
                  onPress={() => {
                    const ms = Date.now() - verseShowTimeRef.current;
                    submitVerseReview(verse.verse_id, "got_it", sentenceSession?.session_id, ms).catch(() => {});
                    setVerseFlipped(false);
                    setTappedOrder([]); setTappedCursor(-1); tappedCacheRef.current = new Map();
                    setLookupResult(null); setLookupSurfaceForm(null); setLookupLemmaId(null);
                    verseShowTimeRef.current = 0;
                    advanceAfterSubmit("understood");
                  }}
                >
                  <Text style={{ color: "#2ecc71", fontSize: 14, fontWeight: "600" }}>Got it</Text>
                </Pressable>
              </View>
            )}
          </View>
        </View>
      );
    }
  }

  // Intro card mid-session (deprecated intro_candidates)
  if (sentenceSession && isIntroSlot) {
    const candidate = autoIntroduced[currentSlot!.type === "intro" ? currentSlot!.candidateIndex : 0];
    const knownSiblings = candidate.root_family?.filter(
      (s) => (s.state === "known" || s.state === "learning") && s.lemma_id !== candidate.lemma_id
    ) ?? [];

    function handleIntroLearn() {
      introduceWord(candidate.lemma_id).catch(() => {});
      setIntroducedLemmaIds(prev => new Set([...prev, candidate.lemma_id]));
      advanceAfterSubmit("understood");
    }

    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <ProgressBar
          current={cardIndex + 1}
          total={totalCards}
          mode={mode}
          actionMenu={
            <ActionMenu
              focusedLemmaId={candidate.lemma_id}
              focusedLemmaAr={candidate.lemma_ar}
              sentenceId={null}
              askAIContextBuilder={buildContext}
              askAIScreen="review"
            />
          }
        />
        <ScrollView
          ref={introScrollRef}
          contentContainerStyle={styles.reintroScrollContent}
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.reintroCard}>
            <Text style={[styles.reintroLabel, { color: colors.accent }]}>New word</Text>
            {candidate.story_title && (
              <View style={styles.storySourceBadge}>
                <Text style={styles.storySourceText}>From: {candidate.story_title}</Text>
              </View>
            )}
            <View style={styles.reintroWordHeader}>
              <Text style={styles.reintroArabic}>{candidate.lemma_ar}</Text>
              <PlayButton audioUrl={candidate.audio_url} word={candidate.lemma_ar} />
            </View>
            <Text style={styles.introMeaningLabel}>Meaning</Text>
            <Text style={styles.reintroEnglish}>{candidate.gloss_en}</Text>
            {candidate.transliteration && (
              <Text style={styles.reintroTranslit}>{candidate.transliteration}</Text>
            )}
            {candidate.pos && (
              <Text style={styles.reintroPos}>
                {posLabel(candidate.pos, candidate.forms_json)}
              </Text>
            )}
            <FormsRow pos={candidate.pos} forms={candidate.forms_json} />
            <GrammarRow details={candidate.grammar_details} />

            {candidate.example_ar && (
              <View style={styles.reintroExample}>
                <Text style={styles.reintroExampleAr}>{candidate.example_ar}</Text>
                {candidate.example_en && (
                  <Text style={styles.reintroExampleEn}>{candidate.example_en}</Text>
                )}
              </View>
            )}

            {candidate.root && (
              <View style={styles.reintroRoot}>
                <Text style={styles.reintroRootText}>
                  {candidate.root}
                  {candidate.root_meaning ? ` \u2014 ${candidate.root_meaning}` : ""}
                </Text>
                {candidate.root_family.length > 0 && (
                  <Text style={styles.reintroRootSiblings}>
                    {knownSiblings.length}/{candidate.root_family.length}
                  </Text>
                )}
                {knownSiblings.length > 0 && (
                  <View style={styles.reintroSiblingList}>
                    {knownSiblings.slice(0, 4).map((sib) => (
                      <Text key={sib.lemma_id} style={styles.reintroSiblingItem}>
                        {sib.lemma_ar} {sib.gloss_en ? `\u2014 ${sib.gloss_en}` : ""}
                      </Text>
                    ))}
                  </View>
                )}
              </View>
            )}
          </View>
        </ScrollView>

        <View style={styles.reintroActions}>
          <Pressable
            style={styles.reintroShowAgainBtn}
            onPress={() => advanceAfterSubmit("understood")}
          >
            <Text style={styles.reintroShowAgainText}>Skip</Text>
          </Pressable>
          <Pressable
            style={styles.reintroRememberBtn}
            onPress={handleIntroLearn}
          >
            <Text style={styles.reintroRememberText}>Learn</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (!sentenceSession) {
    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <Text style={styles.emptyTitle}>No sentences available</Text>
        <Text style={styles.emptySubtitle}>Try introducing more words in Learn mode</Text>
      </View>
    );
  }

  const item = sentenceSession.items[sentenceItemIndex];

  return (
    <View
      style={[styles.container, isListening && styles.listeningContainer, { paddingTop: Math.max(insets.top, 12) }]}
    >
      <ProgressBar
        current={cardIndex + 1}
        total={totalCards}
        mode={mode}
        onBack={canGoBack ? handleGoBack : null}
        actionMenu={
          <ActionMenu
            focusedLemmaId={lookupLemmaId}
            focusedLemmaAr={lookupResult?.lemma_ar ?? null}
            sentenceId={item.sentence_id}
            askAIContextBuilder={buildContext}
            askAIScreen="review"
            askAIAutoExplainPrompt={buildAutoExplainPrompt}
            extraActions={[
              ...(item.sentence_id ? [{
                icon: "information-circle-outline" as const,
                label: "Sentence info",
                onPress: () => setSentenceInfoVisible(true),
              }] : []),
              {
                icon: "refresh-outline" as const,
                label: "Refresh session",
                onPress: () => loadSession(undefined, true),
              },
            ]}
          />
        }
        onWrapUp={(results?.total ?? 0) >= 2 ? handleWrapUp : null}
      />

      <ScrollView
        contentContainerStyle={styles.sentenceArea}
        showsVerticalScrollIndicator={false}
      >
        {isListening ? (
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
        <WordInfoCard
          result={lookupResult}
          loading={lookupLoading}
          surfaceForm={lookupSurfaceForm}
          markState={focusedWordMark}
          showMeaning={lookupShowMeaning}
          onShowMeaning={() => {
            setLookupShowMeaning(true);
            if (tappedCursor >= 0 && tappedCursor < tappedOrder.length) {
              const idx = tappedOrder[tappedCursor];
              const cached = tappedCacheRef.current.get(idx);
              if (cached) tappedCacheRef.current.set(idx, { ...cached, showMeaning: true });
            }
          }}
          reserveSpace={false}
          onNavigateToDetail={(id) => router.push(`/word/${id}`)}
          onNavigateToPattern={(wazn) => router.push(`/pattern/${encodeURIComponent(wazn)}`)}
          onNavigateToRoot={(rootId) => router.push(`/root/${rootId}`)}
          onPrev={handleLookupPrev}
          onNext={handleLookupNext}
          hasPrev={tappedCursor > 0}
          hasNext={tappedCursor < tappedOrder.length - 1}
          surfaceTranslit={lookupSurfaceTranslit}
          confusionData={confusionData}
        />
      )}

      <View style={styles.bottomActions}>
        {isListening ? (
          <ListeningActions
            cardState={cardState as ListeningCardState}
            hasMarked={missedIndices.size + confusedIndices.size > 0}
            onAdvance={advanceState}
            onSubmit={handleSubmit}
            submitting={submittingReview}
          />
        ) : (
          <ReadingActions
            cardState={cardState as ReadingCardState}
            hasSentence={true}
            hasMarked={missedIndices.size + confusedIndices.size > 0}
            onAdvance={advanceState}
            onSubmit={handleSubmit}
            submitting={submittingReview}
          />
        )}
      </View>

      {item.sentence_id != null && (
        <SentenceInfoModal
          sentenceId={item.sentence_id}
          visible={sentenceInfoVisible}
          onClose={() => setSentenceInfoVisible(false)}
          selectionInfo={item.selection_info}
        />
      )}
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
  onWordTap: (index: number, lemmaId: number | null) => void;
}) {
  const showAnswer = cardState === "back";

  const [fontIdx, setFontIdx] = useState<number | null>(null);
  // 0 = default (fade per backend), 1 = all vowels, 2 = no vowels
  const [tashkeelMode, setTashkeelMode] = useState(0);
  const defaultFont = arabicFontForSentence(item.sentence_id);
  const currentFont = fontIdx != null ? arabicFonts[fontIdx] : defaultFont;

  return (
    <>
      <Text style={[styles.sentenceArabic, { fontFamily: currentFont.font }]}>
        {item.words.map((word, i) => {
          const isMissed = missedIndices.has(i);
          const isConfused = confusedIndices.has(i);
          const wordStyle = isMissed
            ? styles.missedWord
            : isConfused
              ? styles.confusedWord
              : undefined;
          const showDiacritics = showAnswer || tashkeelMode === 1 || (tashkeelMode === 0 && word.show_tashkeel !== false);
          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text
                onPress={() => onWordTap(i, word.lemma_id ?? null)}
                style={wordStyle}
              >
                {showDiacritics ? word.surface_form : stripDiacritics(word.surface_form)}
              </Text>
            </Text>
          );
        })}
      </Text>

      <View style={styles.cardToggles}>
        <Pressable
          onPress={() => {
            const cur = fontIdx ?? arabicFonts.findIndex(f => f.font === defaultFont.font);
            setFontIdx((cur + 1) % arabicFonts.length);
          }}
          style={styles.toggleDot}
          hitSlop={12}
        >
          <View style={[styles.toggleDotInner, fontIdx != null && styles.toggleDotActive]} />
        </Pressable>
        <Pressable
          onPress={() => setTashkeelMode((tashkeelMode + 1) % 3)}
          style={styles.toggleDot}
          hitSlop={12}
        >
          <View style={[styles.toggleDotInner, { opacity: [0.2, 0.5, 1.0][tashkeelMode] }]} />
        </Pressable>
      </View>

      <View
        style={[
          styles.answerSection,
          styles.answerSectionStable,
          !showAnswer && styles.answerSectionHidden,
        ]}
      >
          <View style={styles.divider} />
          <Text style={styles.sentenceEnglish}>
            {showAnswer ? item.english_translation : " "}
          </Text>
          <View style={styles.translitSlot}>
            <Text style={[styles.sentenceTranslit, !showAnswer && styles.hiddenText]}>
              {item.transliteration ?? " "}
            </Text>
          </View>
        </View>
    </>
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

  const [fontIdx, setFontIdx] = useState<number | null>(null);
  // 0 = default (fade per backend), 1 = all vowels, 2 = no vowels
  const [tashkeelMode, setTashkeelMode] = useState(0);
  const defaultFont = arabicFontForSentence(item.sentence_id);
  const currentFont = fontIdx != null ? arabicFonts[fontIdx] : defaultFont;

  return (
    <>
      <Text style={[styles.sentenceArabic, { fontFamily: currentFont.font }]}>
        {item.words.map((word, i) => {
          const isMissed = missedIndices.has(i);
          const isConfused = confusedIndices.has(i);
          const wordStyle = isMissed
            ? styles.missedWord
            : isConfused
              ? styles.confusedWord
              : undefined;
          const showDiacritics = showAnswer || tashkeelMode === 1 || (tashkeelMode === 0 && word.show_tashkeel !== false);
          return (
            <Text key={`t-${i}`}>
              {i > 0 && " "}
              <Text
                onPress={() => onToggleMissed(i)}
                style={wordStyle}
              >
                {showDiacritics ? word.surface_form : stripDiacritics(word.surface_form)}
              </Text>
            </Text>
          );
        })}
      </Text>

      <View style={styles.cardToggles}>
        <Pressable
          onPress={() => {
            const cur = fontIdx ?? arabicFonts.findIndex(f => f.font === defaultFont.font);
            setFontIdx((cur + 1) % arabicFonts.length);
          }}
          style={styles.toggleDot}
          hitSlop={12}
        >
          <View style={[styles.toggleDotInner, fontIdx != null && styles.toggleDotActive]} />
        </Pressable>
        <Pressable
          onPress={() => setTashkeelMode((tashkeelMode + 1) % 3)}
          style={styles.toggleDot}
          hitSlop={12}
        >
          <View style={[styles.toggleDotInner, { opacity: [0.2, 0.5, 1.0][tashkeelMode] }]} />
        </Pressable>
      </View>

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
  hasMarked,
  onAdvance,
  onSubmit,
  submitting,
}: {
  cardState: ListeningCardState;
  hasMarked: boolean;
  onAdvance: () => void;
  onSubmit: (signal: ComprehensionSignal) => Promise<void>;
  submitting: boolean;
}) {
  if (cardState === "audio") {
    return (
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, styles.noIdeaButton, submitting && styles.actionButtonDisabled]}
          onPress={() => void onSubmit("no_idea")}
          disabled={submitting}
        >
          <Text style={styles.noIdeaButtonText}>No idea</Text>
        </Pressable>
        <View style={styles.actionButtonSpacer} />
        <Pressable
          style={[styles.actionButton, styles.showButton, submitting && styles.actionButtonDisabled]}
          onPress={onAdvance}
          disabled={submitting}
        >
          <Text style={styles.showButtonText}>Reveal Arabic</Text>
        </Pressable>
      </View>
    );
  }

  if (cardState === "arabic") {
    return (
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, styles.noIdeaButton, submitting && styles.actionButtonDisabled]}
          onPress={() => void onSubmit("no_idea")}
          disabled={submitting}
        >
          <Text style={styles.noIdeaButtonText}>No idea</Text>
        </Pressable>
        <Pressable
          style={[
            styles.actionButton,
            hasMarked ? styles.continueButton : styles.gotItButton,
            submitting && styles.actionButtonDisabled,
          ]}
          onPress={() => void onSubmit(hasMarked ? "partial" : "understood")}
          disabled={submitting}
        >
          <Text style={styles.actionButtonText}>
            {hasMarked ? "Continue" : "Know All"}
          </Text>
        </Pressable>
        <Pressable
          style={[styles.actionButton, styles.showButton, submitting && styles.actionButtonDisabled]}
          onPress={onAdvance}
          disabled={submitting}
        >
          <Text style={styles.showButtonText}>Show Translation</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.actionRow}>
      <Pressable
        style={[styles.actionButton, styles.noIdeaButton, submitting && styles.actionButtonDisabled]}
        onPress={() => void onSubmit("no_idea")}
        disabled={submitting}
      >
        <Text style={styles.noIdeaButtonText}>No idea</Text>
      </Pressable>
      <Pressable
        style={[
          styles.actionButton,
          hasMarked ? styles.continueButton : styles.gotItButton,
          submitting && styles.actionButtonDisabled,
        ]}
        onPress={() => void onSubmit(hasMarked ? "partial" : "understood")}
        disabled={submitting}
      >
        <Text style={styles.actionButtonText}>
          {hasMarked ? "Continue" : "Know All"}
        </Text>
      </Pressable>
      <View style={styles.actionButtonSpacer} />
    </View>
  );
}

// --- Reading Actions ---

function ReadingActions({
  cardState,
  hasMarked,
  onAdvance,
  onSubmit,
  submitting,
}: {
  cardState: ReadingCardState;
  hasSentence?: boolean;
  hasMarked: boolean;
  onAdvance: () => void;
  onSubmit: (signal: ComprehensionSignal) => Promise<void>;
  submitting: boolean;
}) {
  if (cardState === "front") {
    return (
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, styles.noIdeaButton, submitting && styles.actionButtonDisabled]}
          onPress={() => void onSubmit("no_idea")}
          disabled={submitting}
        >
          <Text style={styles.noIdeaButtonText}>No idea</Text>
        </Pressable>
        <Pressable
          style={[
            styles.actionButton,
            hasMarked ? styles.continueButton : styles.gotItButton,
            submitting && styles.actionButtonDisabled,
          ]}
          onPress={() => void onSubmit(hasMarked ? "partial" : "understood")}
          disabled={submitting}
        >
          <Text style={styles.actionButtonText}>
            {hasMarked ? "Continue" : "Know All"}
          </Text>
        </Pressable>
        <Pressable
          style={[styles.actionButton, styles.showButton, submitting && styles.actionButtonDisabled]}
          onPress={onAdvance}
          disabled={submitting}
        >
          <Text style={styles.showButtonText}>Show Translation</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.actionRow}>
      <Pressable
        style={[styles.actionButton, styles.noIdeaButton, submitting && styles.actionButtonDisabled]}
        onPress={() => void onSubmit("no_idea")}
        disabled={submitting}
      >
        <Text style={styles.noIdeaButtonText}>No idea</Text>
      </Pressable>
      <Pressable
        style={[
          styles.actionButton,
          hasMarked ? styles.continueButton : styles.gotItButton,
          submitting && styles.actionButtonDisabled,
        ]}
        onPress={() => void onSubmit(hasMarked ? "partial" : "understood")}
        disabled={submitting}
      >
        <Text style={styles.actionButtonText}>
          {hasMarked ? "Continue" : "Know All"}
        </Text>
      </Pressable>
      <View style={styles.actionButtonSpacer} />
    </View>
  );
}

// --- Progress Bar ---

function ProgressBar({
  current,
  total,
  mode,
  actionMenu,
  onWrapUp,
  onBack,
}: {
  current: number;
  total: number;
  mode: ReviewMode;
  actionMenu?: React.ReactNode;
  onWrapUp?: (() => void) | null;
  onBack?: (() => void) | null;
}) {
  const pct = (current / total) * 100;
  const barColor = mode === "listening" ? colors.listening : colors.accent;
  return (
    <View style={styles.progressContainer}>
      <View style={styles.progressHeader}>
        {onBack ? (
          <Pressable onPress={onBack} hitSlop={12} style={styles.backButton}>
            <Ionicons name="chevron-back" size={20} color={colors.textSecondary} />
          </Pressable>
        ) : (
          <View style={styles.backButtonPlaceholder} />
        )}
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <Text style={styles.progressText}>
            Card {current} of {total}
          </Text>
        </View>
        {onWrapUp ? (
          <Pressable onPress={onWrapUp} hitSlop={8} style={styles.wrapUpButton}>
            <Text style={styles.wrapUpButtonText}>Wrap Up</Text>
          </Pressable>
        ) : (
          <View style={styles.backButtonPlaceholder} />
        )}
      </View>
      <View style={styles.progressTrack}>
        <View
          style={[
            styles.progressFill,
            { width: `${pct}%`, backgroundColor: barColor },
          ]}
        />
      </View>
      {actionMenu && (
        <View style={styles.actionMenuRow}>
          {actionMenu}
        </View>
      )}
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
    msg = "Flawless recall. Every word clicked.";
  } else if (accuracy >= 90) {
    msg = "Sharp recall today.";
  } else if (accuracy >= 70) {
    msg = "Strong session. A few misses are part of learning.";
  } else if (accuracy >= 50) {
    msg = "Good work. Seeing unfamiliar words is completely normal.";
  } else {
    msg = "Normal stretch zone. Repetition will make these words easier.";
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
  introducedLemmaIds,
  wordOutcomes,
  sessionId,
  onNewSession,
}: {
  results: SessionResults;
  mode: ReviewMode;
  autoIntroduced: IntroCandidate[];
  introducedLemmaIds: Set<number>;
  wordOutcomes: Map<number, WordOutcome>;
  sessionId: string;
  onNewSession: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [data, setData] = useState<SessionEndData | null>(null);
  const [dataReady, setDataReady] = useState(false);
  const fadeAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const load = async () => {
      await flushQueue().catch(() => {});
      const d = sessionId ? await getSessionEnd(sessionId).catch(() => null) : null;
      if (d) setData(d);
      setDataReady(true);
      Animated.timing(fadeAnim, {
        toValue: 1,
        duration: 400,
        useNativeDriver: true,
      }).start();
    };
    load();
    prefetchSessions(mode).catch(() => {});
  }, []);

  // Derive journey categories
  const graduated = data?.word_journeys.filter(w => w.graduated) ?? [];
  const boxAdvanced = data?.word_journeys.filter(w =>
    !w.graduated && w.old_box != null && w.new_box != null && w.new_box > w.old_box
  ) ?? [];
  const boxSlipped = data?.word_journeys.filter(w =>
    w.old_box != null && w.new_box != null && w.new_box < w.old_box
  ) ?? [];

  // Box advancement breakdown
  const boxBreakdown: Record<string, number> = {};
  for (const w of boxAdvanced) {
    const key = `${w.old_box}\u2192${w.new_box}`;
    boxBreakdown[key] = (boxBreakdown[key] || 0) + 1;
  }
  const boxBreakdownStr = Object.entries(boxBreakdown)
    .map(([k, v]) => `Box ${k}: ${v}`)
    .join(" \u00B7 ");

  // Speed comparison
  const thisSessionMs = data?.avg_response_ms;
  const overallAvgMs = data?.historical_avg_response_ms;
  const speedPctDiff = (thisSessionMs && overallAvgMs && overallAvgMs > 0)
    ? Math.round(((overallAvgMs - thisSessionMs) / overallAvgMs) * 100)
    : null;
  const formatTime = (ms: number) => {
    const s = Math.round(ms / 1000);
    return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  };

  // Root coverage
  const topRoots = data?.top_partial_roots ?? [];

  // Title based on what happened
  const sentenceCount = data?.sentence_count ?? results.total;
  const title = graduated.length > 0
    ? `${graduated.length} ${graduated.length === 1 ? "word" : "words"} graduated!`
    : boxAdvanced.length > 0
      ? "Words moving forward"
      : "Session Complete";

  const MAX_PILLS = 8;

  return (
    <ScrollView
      style={styles.sessionCompleteScroll}
      contentContainerStyle={[styles.sessionCompleteContent, { paddingTop: Math.max(insets.top, 12) + 24 }]}
      showsVerticalScrollIndicator={false}
    >
      {/* Title area */}
      <Text style={styles.summaryTitle}>{title}</Text>
      <Text style={styles.summarySubtitle}>
        {sentenceCount} sentences in {mode === "listening" ? "listening" : "reading"} mode
      </Text>

      {/* Hero button */}
      <Pressable style={styles.nextSessionHeroButton} onPress={onNewSession}>
        <Text style={styles.nextSessionHeroTitle}>Next Session</Text>
      </Pressable>

      {/* Journey timeline */}
      {dataReady && data && (graduated.length > 0 || boxAdvanced.length > 0 || boxSlipped.length > 0) && (
        <Animated.View style={[styles.journeyTimeline, { opacity: fadeAnim }]}>
          {graduated.length > 0 && (
            <View style={styles.journeyNode}>
              <View style={[styles.journeyDot, { backgroundColor: colors.gotIt }]} />
              <View style={styles.journeyContent}>
                <Text style={[styles.journeyNodeTitle, { color: colors.gotIt }]}>
                  {graduated.length} {graduated.length === 1 ? "word" : "words"} graduated
                </Text>
                <Text style={styles.journeyNodeDetail}>Moved to long-term memory</Text>
                <View style={styles.wordOutcomePills}>
                  {graduated.slice(0, MAX_PILLS).map((w) => (
                    <View key={w.lemma_id} style={[styles.wordOutcomePill, { borderColor: colors.gotIt + "40" }]}>
                      <Text style={styles.wordOutcomePillAr}>{w.lemma_ar}</Text>
                      <Text style={styles.wordOutcomePillEn}>{w.gloss_en}</Text>
                    </View>
                  ))}
                  {graduated.length > MAX_PILLS && (
                    <View style={[styles.wordOutcomePill, { borderColor: colors.border }]}>
                      <Text style={[styles.wordOutcomePillEn, { color: colors.textSecondary }]}>+{graduated.length - MAX_PILLS}</Text>
                    </View>
                  )}
                </View>
              </View>
            </View>
          )}
          {boxAdvanced.length > 0 && (
            <View style={styles.journeyNode}>
              <View style={[styles.journeyDot, { backgroundColor: colors.noIdea }]} />
              <View style={styles.journeyContent}>
                <Text style={[styles.journeyNodeTitle, { color: colors.noIdea }]}>
                  {boxAdvanced.length} {boxAdvanced.length === 1 ? "word" : "words"} advanced
                </Text>
                {boxBreakdownStr ? <Text style={styles.journeyNodeDetail}>{boxBreakdownStr}</Text> : null}
                <View style={styles.wordOutcomePills}>
                  {boxAdvanced.slice(0, MAX_PILLS).map((w) => (
                    <View key={w.lemma_id} style={[styles.wordOutcomePill, { borderColor: colors.noIdea + "40" }]}>
                      <Text style={styles.wordOutcomePillAr}>{w.lemma_ar}</Text>
                      <Text style={styles.wordOutcomePillEn}>{w.gloss_en}</Text>
                    </View>
                  ))}
                  {boxAdvanced.length > MAX_PILLS && (
                    <View style={[styles.wordOutcomePill, { borderColor: colors.border }]}>
                      <Text style={[styles.wordOutcomePillEn, { color: colors.textSecondary }]}>+{boxAdvanced.length - MAX_PILLS}</Text>
                    </View>
                  )}
                </View>
              </View>
            </View>
          )}
          {boxSlipped.length > 0 && (
            <View style={styles.journeyNode}>
              <View style={[styles.journeyDot, { backgroundColor: colors.missed }]} />
              <View style={styles.journeyContent}>
                <Text style={[styles.journeyNodeTitle, { color: colors.missed }]}>
                  {boxSlipped.length} {boxSlipped.length === 1 ? "word needs" : "words need"} more practice
                </Text>
                <View style={styles.wordOutcomePills}>
                  {boxSlipped.slice(0, MAX_PILLS).map((w) => (
                    <View key={w.lemma_id} style={[styles.wordOutcomePill, { borderColor: colors.missed + "40" }]}>
                      <Text style={styles.wordOutcomePillAr}>{w.lemma_ar}</Text>
                      <Text style={styles.wordOutcomePillEn}>{w.gloss_en}</Text>
                    </View>
                  ))}
                </View>
              </View>
            </View>
          )}
          {/* Comprehension node */}
          <View style={styles.journeyNode}>
            <View style={[styles.journeyDot, { backgroundColor: colors.accent }]} />
            <View style={styles.journeyContent}>
              <Text style={[styles.journeyNodeTitle, { color: colors.accent }]}>
                {data.sentences_understood} of {data.sentence_count} sentences fully understood
              </Text>
            </View>
          </View>
        </Animated.View>
      )}

      {/* Pipeline bar with deltas */}
      {dataReady && data && (data.pipeline_box_1 + data.pipeline_box_2 + data.pipeline_box_3 > 0) && (
        <Animated.View style={[styles.sectionCard, { opacity: fadeAnim }]}>
          <Text style={styles.sectionTitle}>WORD PIPELINE</Text>
          <View style={styles.pipelineBar}>
            <View style={{ flex: data.pipeline_box_1 || 1, backgroundColor: colors.missed, borderRadius: 4 }} />
            <View style={{ flex: data.pipeline_box_2 || 1, backgroundColor: colors.noIdea, borderRadius: 4 }} />
            <View style={{ flex: data.pipeline_box_3 || 1, backgroundColor: colors.gotIt, borderRadius: 4 }} />
            <View style={{ flex: data.known_count || 1, backgroundColor: "#2dd4bf", borderRadius: 4 }} />
          </View>
          <View style={styles.pipelineLegend}>
            <View style={styles.pipelineLegendItem}>
              <View style={[styles.pipelineDot, { backgroundColor: colors.missed }]} />
              <Text style={styles.pipelineLegendNum}>{data.pipeline_box_1}</Text>
              <Text style={styles.pipelineLegendLabel}> new</Text>
            </View>
            <View style={styles.pipelineLegendItem}>
              <View style={[styles.pipelineDot, { backgroundColor: colors.noIdea }]} />
              <Text style={styles.pipelineLegendNum}>{data.pipeline_box_2}</Text>
            </View>
            <View style={styles.pipelineLegendItem}>
              <View style={[styles.pipelineDot, { backgroundColor: colors.gotIt }]} />
              <Text style={styles.pipelineLegendNum}>{data.pipeline_box_3}</Text>
            </View>
            <View style={styles.pipelineLegendItem}>
              <View style={[styles.pipelineDot, { backgroundColor: "#2dd4bf" }]} />
              <Text style={styles.pipelineLegendNum}>{data.known_count}</Text>
              <Text style={styles.pipelineLegendLabel}> known</Text>
            </View>
          </View>
          {(graduated.length > 0 || boxAdvanced.length > 0 || boxSlipped.length > 0) && (
            <Text style={styles.pipelineDelta}>
              Session:{" "}
              {graduated.length > 0 && <Text style={{ color: colors.gotIt, fontWeight: "600" }}>+{graduated.length} known</Text>}
              {graduated.length > 0 && boxAdvanced.length > 0 && " \u00B7 "}
              {boxAdvanced.length > 0 && <Text style={{ color: colors.noIdea, fontWeight: "600" }}>+{boxAdvanced.length} advanced</Text>}
              {(graduated.length > 0 || boxAdvanced.length > 0) && boxSlipped.length > 0 && " \u00B7 "}
              {boxSlipped.length > 0 && <Text style={{ color: colors.missed, fontWeight: "600" }}>{"\u2212"}{boxSlipped.length} slipped</Text>}
            </Text>
          )}
        </Animated.View>
      )}

      {/* Speed comparison */}
      {dataReady && thisSessionMs != null && thisSessionMs < 300_000 && (
        <Animated.View style={[styles.sectionCard, { opacity: fadeAnim }]}>
          <Text style={styles.sectionTitle}>RESPONSE SPEED</Text>
          <View style={styles.speedRow}>
            <Text style={styles.speedLabel}>This session</Text>
            <Text style={styles.speedValue}>{formatTime(thisSessionMs)} avg</Text>
          </View>
          {overallAvgMs != null && (
            <View style={styles.speedRow}>
              <Text style={styles.speedLabel}>Your average</Text>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Text style={styles.speedValue}>{formatTime(overallAvgMs)} avg</Text>
                {speedPctDiff != null && speedPctDiff !== 0 && (
                  <View style={[styles.speedBadge, {
                    backgroundColor: speedPctDiff > 0 ? colors.gotIt + "20" : colors.missed + "20",
                  }]}>
                    <Text style={{ fontSize: 11, fontWeight: "600", color: speedPctDiff > 0 ? colors.gotIt : colors.missed }}>
                      {speedPctDiff > 0 ? `${speedPctDiff}% faster` : `${Math.abs(speedPctDiff)}% slower`}
                    </Text>
                  </View>
                )}
              </View>
            </View>
          )}
        </Animated.View>
      )}

      {/* Today stats */}
      {dataReady && data && (
        <Animated.View style={[styles.todayRow, { opacity: fadeAnim }]}>
          <View style={{ width: "100%", paddingHorizontal: 16, marginBottom: 8 }}>
            {(data.fsrs_reviewed_today || 0) > 0 && (
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                <Text style={[styles.todayProgressText, { color: colors.gotIt }]}>
                  {data.fsrs_reviewed_today} reviewed today
                </Text>
                {data.retention_7d_pct != null && (
                  <Text style={[styles.todayProgressText, { color: data.retention_7d_pct >= 90 ? colors.gotIt : data.retention_7d_pct >= 85 ? colors.accent : colors.missed }]}>
                    {data.retention_7d_pct}% retention
                  </Text>
                )}
              </View>
            )}
          </View>
          <View style={{ flexDirection: "row", justifyContent: "space-around", width: "100%" }}>
            <View style={styles.todayStat}>
              <Text style={[styles.todayNum, { color: "#2dd4bf" }]}>{data.sentence_count ?? results.total}</Text>
              <Text style={styles.todayLabel}>SENTENCES</Text>
            </View>
            {data.graduated_today_count > 0 && (
              <View style={styles.todayStat}>
                <Text style={[styles.todayNum, { color: colors.gotIt }]}>{data.graduated_today_count}</Text>
                <Text style={styles.todayLabel}>GRADUATED</Text>
              </View>
            )}
          </View>
        </Animated.View>
      )}

      {/* Root coverage */}
      {dataReady && topRoots.length > 0 && (
        <Animated.View style={[styles.sectionCard, { opacity: fadeAnim }]}>
          <Text style={styles.sectionTitle}>ROOT FAMILIES — CLOSEST TO COMPLETE</Text>
          {topRoots.slice(0, 3).map((root) => (
            <View key={root.root} style={styles.rootNugget}>
              <Text style={styles.rootAr}>{root.root}</Text>
              <View style={styles.rootInfo}>
                <Text style={styles.rootMeaning} numberOfLines={1}>{root.root_meaning}</Text>
                <View style={styles.rootBar}>
                  <View style={[styles.rootBarFill, { width: `${(root.known / root.total) * 100}%` }]} />
                </View>
              </View>
              <Text style={styles.rootFrac}>{root.known}/{root.total}</Text>
            </View>
          ))}
        </Animated.View>
      )}

      {/* Today's graduated words */}
      {dataReady && data?.graduated_today && data.graduated_today.length > 0 && (
        <Animated.View style={[styles.autoIntroSection, { opacity: fadeAnim }]}>
          <GraduatedWordsTable words={data.graduated_today} />
        </Animated.View>
      )}

      {/* Today's introduced words */}
      {dataReady && data?.introduced_words_today && data.introduced_words_today.length > 0 && (
        <Animated.View style={[styles.autoIntroSection, { opacity: fadeAnim }]}>
          <IntroducedWordsTable words={data.introduced_words_today} />
        </Animated.View>
      )}

      <Pressable style={styles.sessionCompleteFooterButton} onPress={onNewSession}>
        <Text style={styles.sessionCompleteFooterButtonText}>Next Session</Text>
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
            : "No cards ready for review"}
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
              {analytics.pace.current_streak}d streak
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
  emptyTitle: {
    fontSize: 20,
    color: colors.text,
    textAlign: "center" as const,
    marginTop: 40,
  },
  emptySubtitle: {
    fontSize: 14,
    color: colors.textSecondary,
    textAlign: "center" as const,
    marginTop: 8,
  },
  sessionCompleteScroll: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  sessionCompleteContent: {
    flexGrow: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    paddingHorizontal: 20,
    paddingBottom: 32,
  },
  listeningContainer: {
    backgroundColor: colors.listeningBg,
  },
  sentenceArea: {
    flexGrow: 1,
    justifyContent: "flex-start",
    alignItems: "center",
    paddingTop: 40,
    paddingBottom: 18,
    paddingHorizontal: 4,
    maxWidth: 500,
    alignSelf: "center",
    width: "100%",
  },
  bottomActions: {
    paddingTop: 8,
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
  cardToggles: {
    flexDirection: "row" as const,
    justifyContent: "space-between" as const,
    width: "100%",
    marginTop: 10,
    marginBottom: 2,
  },
  toggleDot: {
    padding: 8,
  },
  toggleDotInner: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.textSecondary,
    opacity: 0.2,
  },
  toggleDotActive: {
    opacity: 0.5,
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
    fontFamily: fontFamily.translit,
    marginTop: 6,
    textAlign: "center",
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
  answerSectionStable: {
    minHeight: 150,
    justifyContent: "flex-start",
  },
  missedWordSummary: {
    width: "100%",
    marginTop: 12,
    gap: 6,
  },
  missedWordRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(231, 76, 60, 0.1)",
    borderLeftWidth: 3,
    borderLeftColor: colors.missed,
    borderRadius: 6,
    paddingVertical: 6,
    paddingHorizontal: 10,
    gap: 8,
  },
  confusedWordRow: {
    backgroundColor: "rgba(243, 156, 18, 0.1)",
    borderLeftColor: colors.confused,
  },
  missedWordAr: {
    fontSize: 18,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },
  missedWordGloss: {
    fontSize: 14,
    color: colors.text,
    flex: 1,
  },
  missedWordTags: {
    fontSize: 12,
    color: colors.textSecondary,
    fontStyle: "italic",
  },
  answerSectionHidden: {
    opacity: 0,
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    width: "80%",
    marginVertical: 14,
  },
  hiddenText: {
    color: "transparent",
  },
  showButton: {
    backgroundColor: colors.accent,
  },
  showButtonText: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "600",
    textAlign: "center",
  },

  noIdeaButton: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.noIdea + "55",
  },
  noIdeaButtonText: {
    color: colors.noIdea,
    fontSize: 15,
    fontWeight: "600",
    textAlign: "center",
  },

  actionRow: {
    flexDirection: "row",
    gap: 10,
    width: "100%",
  },
  actionButton: {
    flex: 1,
    minHeight: 52,
    paddingVertical: 10,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  actionButtonDisabled: {
    opacity: 0.5,
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
    fontSize: 15,
    fontWeight: "600",
  },
  tapHintListening: {
    color: colors.listening,
    fontSize: fonts.small,
    marginTop: 16,
    opacity: 0.8,
    textAlign: "center",
  },

  progressContainer: {
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
    marginBottom: 4,
  },
  progressHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 4,
  },
  backButtonPlaceholder: {
    width: 36,
  },
  backButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  progressText: {
    color: colors.textSecondary,
    fontSize: 12,
    textAlign: "center",
    opacity: 0.6,
  },
  wrapUpButton: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
    backgroundColor: colors.surfaceLight,
  },
  wrapUpButtonText: {
    color: colors.textSecondary,
    fontSize: 11,
    fontWeight: "600",
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
  actionMenuRow: {
    flexDirection: "row",
    justifyContent: "flex-end",
    marginTop: 8,
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
  nextSessionHeroButton: {
    backgroundColor: colors.accent,
    width: "100%",
    maxWidth: 380,
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 14,
    alignItems: "center",
    marginBottom: 20,
  },
  nextSessionHeroTitle: {
    color: "#fff",
    fontSize: 20,
    fontWeight: "800",
  },
  nextSessionHeroSubtitle: {
    color: "#fff",
    fontSize: 13,
    marginTop: 4,
    opacity: 0.9,
    textAlign: "center",
  },
  accuracyText: {
    fontSize: 36,
    fontWeight: "800",
    marginBottom: 12,
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
  wordOutcomeSupportText: {
    marginTop: 10,
    fontSize: 12,
    color: colors.textSecondary,
    textAlign: "center",
    opacity: 0.85,
  },
  sessionCompleteFooterButton: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 24,
    marginTop: 20,
    borderWidth: 1,
    borderColor: colors.accent + "45",
  },
  sessionCompleteFooterButtonText: {
    color: colors.accent,
    fontSize: 15,
    fontWeight: "700",
  },
  // Journey timeline
  journeyTimeline: {
    width: "100%",
    maxWidth: 400,
    paddingLeft: 8,
    marginTop: 8,
    marginBottom: 8,
    borderLeftWidth: 2,
    borderLeftColor: colors.border,
  },
  journeyNode: {
    flexDirection: "row" as const,
    alignItems: "flex-start" as const,
    marginBottom: 16,
    marginLeft: -9,
  },
  journeyDot: {
    width: 16,
    height: 16,
    borderRadius: 8,
    marginTop: 2,
    borderWidth: 2,
    borderColor: colors.bg,
  },
  journeyContent: {
    flex: 1,
    marginLeft: 10,
  },
  journeyNodeTitle: {
    fontSize: 15,
    fontWeight: "700" as const,
    marginBottom: 2,
  },
  journeyNodeDetail: {
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 6,
  },
  // Section cards
  sectionCard: {
    width: "100%",
    maxWidth: 400,
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
    marginTop: 12,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: "700" as const,
    color: colors.textSecondary,
    textTransform: "uppercase" as const,
    letterSpacing: 1.2,
    marginBottom: 10,
  },
  // Pipeline bar
  pipelineBar: {
    flexDirection: "row" as const,
    height: 10,
    borderRadius: 5,
    overflow: "hidden" as const,
    gap: 2,
    marginBottom: 8,
  },
  pipelineLegend: {
    flexDirection: "row" as const,
    flexWrap: "wrap" as const,
    gap: 12,
    marginTop: 4,
  },
  pipelineLegendItem: {
    flexDirection: "row" as const,
    alignItems: "center" as const,
  },
  pipelineDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 4,
  },
  pipelineLegendNum: {
    fontSize: 13,
    fontWeight: "700" as const,
    color: colors.text,
  },
  pipelineLegendLabel: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  pipelineDelta: {
    fontSize: 12,
    color: colors.textSecondary,
    marginTop: 8,
  },
  // Speed section
  speedRow: {
    flexDirection: "row" as const,
    justifyContent: "space-between" as const,
    alignItems: "center" as const,
    paddingVertical: 4,
  },
  speedLabel: {
    fontSize: 14,
    color: colors.textSecondary,
  },
  speedValue: {
    fontSize: 14,
    fontWeight: "600" as const,
    color: colors.text,
  },
  speedBadge: {
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  // Today stats
  todayRow: {
    flexDirection: "column" as const,
    alignItems: "center" as const,
    width: "100%",
    maxWidth: 400,
    marginTop: 16,
    paddingVertical: 12,
    backgroundColor: colors.surface,
    borderRadius: 12,
  },
  todayStat: {
    alignItems: "center" as const,
  },
  todayNum: {
    fontSize: 22,
    fontWeight: "700" as const,
  },
  todayLabel: {
    fontSize: 10,
    fontWeight: "600" as const,
    color: colors.textSecondary,
    letterSpacing: 0.5,
    marginTop: 2,
  },
  todayProgressBar: {
    flexDirection: "row" as const,
    height: 6,
    borderRadius: 3,
    overflow: "hidden" as const,
    backgroundColor: colors.surfaceLight,
    marginBottom: 6,
  },
  todayProgressFill: {
    height: "100%",
    backgroundColor: colors.gotIt,
    borderRadius: 3,
  },
  todayProgressLabels: {
    flexDirection: "row" as const,
    justifyContent: "space-between" as const,
  },
  todayProgressText: {
    fontSize: 12,
    fontWeight: "600" as const,
  },
  // Root coverage
  rootNugget: {
    flexDirection: "row" as const,
    alignItems: "center" as const,
    marginBottom: 10,
    gap: 10,
  },
  rootAr: {
    fontSize: 20,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl" as const,
    minWidth: 70,
    textAlign: "center" as const,
  },
  rootInfo: {
    flex: 1,
    gap: 3,
  },
  rootMeaning: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  rootBar: {
    height: 6,
    backgroundColor: colors.surfaceLight,
    borderRadius: 3,
    overflow: "hidden" as const,
  },
  rootBarFill: {
    height: 6,
    backgroundColor: colors.gotIt,
    borderRadius: 3,
  },
  rootFrac: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: "600" as const,
    minWidth: 30,
    textAlign: "right" as const,
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
  // Re-introduction card styles
  reintroScrollContent: {
    flexGrow: 1,
    justifyContent: "center",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 4,
    maxWidth: 500,
    alignSelf: "center",
    width: "100%",
  },
  reintroCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 28,
    width: "100%",
    alignItems: "center",
  },
  reintroLabel: {
    fontSize: 12,
    color: colors.missed,
    fontWeight: "600",
    marginBottom: 12,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  storySourceBadge: {
    backgroundColor: colors.accent + "20",
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 8,
    marginBottom: 8,
  },
  storySourceText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "600",
  },
  reintroWordHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginBottom: 10,
  },
  reintroArabic: {
    fontSize: 44,
    color: colors.arabic,
    fontWeight: "700",
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
  },
  introMeaningLabel: {
    fontSize: 11,
    color: colors.textSecondary,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 1,
    marginTop: 4,
    marginBottom: 2,
  },
  reintroEnglish: {
    fontSize: 22,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 6,
    textAlign: "center",
  },
  reintroTranslit: {
    fontSize: 16,
    color: colors.textSecondary,
    fontFamily: fontFamily.translit,
    marginBottom: 4,
  },
  reintroPos: {
    fontSize: 13,
    color: colors.textSecondary,
    marginBottom: 8,
  },
  reintroExample: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    padding: 12,
    width: "100%",
    alignItems: "center",
    marginBottom: 12,
  },
  reintroExampleAr: {
    fontSize: 20,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlign: "center",
    lineHeight: 32,
  },
  reintroExampleEn: {
    fontSize: 14,
    color: colors.textSecondary,
    marginTop: 4,
    textAlign: "center",
  },
  reintroRoot: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 12,
    width: "100%",
    alignItems: "center",
  },
  reintroRootText: {
    fontSize: 14,
    color: colors.accent,
    fontWeight: "600",
  },
  reintroRootSiblings: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 4,
  },
  reintroSiblingList: {
    marginTop: 8,
    gap: 2,
    alignItems: "center",
  },
  reintroSiblingItem: {
    fontSize: 14,
    color: colors.textSecondary,
    textAlign: "center",
  },
  reintroActions: {
    paddingTop: 16,
    paddingBottom: 8,
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
    gap: 10,
  },
  reintroRememberBtn: {
    backgroundColor: colors.good,
    paddingVertical: 16,
    borderRadius: 12,
    width: "100%",
  },
  reintroRememberText: {
    color: "#fff",
    fontSize: 18,
    fontWeight: "600",
    textAlign: "center",
  },
  reintroShowAgainBtn: {
    backgroundColor: colors.surfaceLight,
    paddingVertical: 16,
    borderRadius: 12,
    width: "100%",
  },
  reintroShowAgainText: {
    color: colors.textSecondary,
    fontSize: 18,
    fontWeight: "600",
    textAlign: "center",
  },
  expIntroHighlight: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    width: "100%",
    marginTop: 10,
  },
  expIntroHighlightLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.accent,
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  expIntroHighlightText: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
    writingDirection: "ltr" as const,
  },

  // Experiment intro card — info-dense design
  eiHero: {
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
  eiArabic: {
    fontSize: 52,
    color: colors.arabic,
    fontWeight: "700",
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    marginBottom: 6,
    marginTop: 4,
    lineHeight: 84,
  },
  eiEnglish: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "700",
    marginBottom: 4,
    textAlign: "center",
  },
  eiTranslit: {
    fontSize: 15,
    color: colors.textSecondary,
    fontFamily: fontFamily.translit,
    marginBottom: 10,
  },
  eiChipsArea: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    justifyContent: "center",
    marginBottom: 10,
  },
  eiChipPos: {
    backgroundColor: colors.surfaceLight,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
  },
  eiChipPosText: {
    color: colors.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  eiChipOutline: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
    borderWidth: 1,
  },
  eiInfoSections: {
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
  eiInfoSection: {
    padding: 14,
    paddingHorizontal: 20,
    borderTopWidth: 1,
    borderTopColor: colors.surfaceLight,
  },
  eiSectionLabel: {
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 1,
    fontWeight: "700",
    marginBottom: 8,
  },
  eiSectionLabelRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  eiSectionLink: {
    fontSize: 12,
    color: colors.accent,
    fontWeight: "600",
  },
  eiInfoText: {
    fontSize: 14,
    color: colors.text,
    lineHeight: 20,
    writingDirection: "ltr" as const,
  },
  eiMnemonicCard: {
    backgroundColor: "#2a1f4e",
    borderRadius: 10,
    padding: 12,
    borderLeftWidth: 3,
    borderLeftColor: "#9b59b6",
  },
  eiFunFactCard: {
    backgroundColor: "#1e2a1e",
    borderRadius: 10,
    padding: 12,
    borderLeftWidth: 3,
    borderLeftColor: "#2ecc71",
  },
  eiFunFactText: {
    fontSize: 13,
    color: "#c8e8c8",
    lineHeight: 19,
    writingDirection: "ltr" as const,
  },
});
