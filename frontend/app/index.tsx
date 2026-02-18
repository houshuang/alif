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
import { colors, fonts, fontFamily } from "../lib/theme";
import {
  getSentenceReviewSession,
  fetchFreshSession,
  submitSentenceReview,
  undoSentenceReview,
  submitReintroResult,
  getAnalytics,
  lookupReviewWord,
  prefetchSessions,
  warmSentences,
  getGrammarLesson,
  introduceGrammarFeature,
  introduceWord,
  getWrapUpCards,
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
  Analytics,
  WordLookupResult,
  GrammarLesson,
  WrapUpCard,
} from "../lib/types";
import { posLabel, FormsRow, GrammarRow, PlayButton } from "../lib/WordCardComponents";
import { syncEvents } from "../lib/sync-events";
import { useNetStatus } from "../lib/net-status";
import ActionMenu from "../lib/review/ActionMenu";
import SentenceInfoModal from "../lib/review/SentenceInfoModal";
import WordInfoCard, { FocusWordMark } from "../lib/review/WordInfoCard";

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
  | { type: "intro"; candidateIndex: number };

interface CardSnapshot {
  missedIndices: Set<number>;
  confusedIndices: Set<number>;
  signal: ComprehensionSignal;
  sentenceId: number | null;
  primaryLemmaId: number;
  wordOutcomesBefore: Map<number, WordOutcome>;
}

function stripDiacritics(s: string): string {
  return s.replace(/[\u0610-\u065f\u0670\u06D6-\u06ED]/g, "");
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
  const [tappedOrder, setTappedOrder] = useState<number[]>([]);
  const [tappedCursor, setTappedCursor] = useState(-1);
  const tappedCacheRef = useRef<Map<number, { surfaceForm: string; lemmaId: number | null; result: WordLookupResult | null; markState: FocusWordMark; showMeaning: boolean }>>(new Map());
  const [audioPlayCount, setAudioPlayCount] = useState(0);
  const [lookupCount, setLookupCount] = useState(0);
  const [submittingReview, setSubmittingReview] = useState(false);
  const [wordOutcomes, setWordOutcomes] = useState<Map<number, WordOutcome>>(new Map());
  const [reintroCards, setReintroCards] = useState<ReintroCard[]>([]);
  const [reintroIndex, setReintroIndex] = useState(0);
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

  const totalCards = sentenceSession
    ? (sessionSlots.length > 0 ? sessionSlots.length : sentenceSession.items.length)
    : 0;
  const currentSlot: SessionSlot | null = sentenceSession && sessionSlots.length > 0
    ? sessionSlots[cardIndex] ?? null
    : null;
  const isIntroSlot = currentSlot?.type === "intro";
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
    setAudioPlayCount(0);
    setLookupCount(0);
    setSubmittingReview(false);
    setSentenceSession(null);
    await cleanupSound();

    try {
      const ss = skipCache ? await fetchFreshSession(m) : await getSentenceReviewSession(m);
      if (ss.items.length > 0) {
        setSentenceSession(ss);
        // Build session slots: interleave sentence items and intro candidates
        const slots: SessionSlot[] = ss.items.map((_, i) => ({
          type: "sentence" as const,
          itemIndex: i,
        }));
        if (ss.intro_candidates && ss.intro_candidates.length > 0 && m === "reading") {
          setAutoIntroduced(ss.intro_candidates);
          for (let ci = ss.intro_candidates.length - 1; ci >= 0; ci--) {
            const insertPos = Math.min(ss.intro_candidates[ci].insert_at, slots.length);
            slots.splice(insertPos, 0, { type: "intro" as const, candidateIndex: ci });
          }
        }
        setSessionSlots(slots);
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

  type TappedEntry = { surfaceForm: string; lemmaId: number | null; result: WordLookupResult | null; markState: FocusWordMark; showMeaning: boolean };

  const applyTappedEntry = useCallback((entry: TappedEntry) => {
    setLookupSurfaceForm(entry.surfaceForm);
    setLookupLemmaId(entry.lemmaId);
    setFocusedWordMark(entry.markState);
    setLookupResult(entry.result);
    setLookupShowMeaning(entry.showMeaning);
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
      addToTappedHistory(index, { surfaceForm: fnSurface ?? "", lemmaId: null, result: fnResult, markState: "missed", showMeaning: true });
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
      addToTappedHistory(index, { surfaceForm: tappedSurface ?? "", lemmaId, result, markState: nextMark, showMeaning });
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
      addToTappedHistory(index, { surfaceForm: tappedSurface ?? "", lemmaId, result: fallbackResult, markState: nextMark, showMeaning: true });
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
        gotIt: prev.gotIt - (snapshot.signal === "understood" || snapshot.signal === "grammar_confused" ? 1 : 0),
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
    const slots: SessionSlot[] = fresh.items.map((_, i) => ({
      type: "sentence" as const,
      itemIndex: i,
    }));
    if (fresh.intro_candidates && fresh.intro_candidates.length > 0 && mode === "reading") {
      setAutoIntroduced(fresh.intro_candidates);
      for (let ci = fresh.intro_candidates.length - 1; ci >= 0; ci--) {
        const insertPos = Math.min(fresh.intro_candidates[ci].insert_at, slots.length);
        slots.splice(insertPos, 0, { type: "intro" as const, candidateIndex: ci });
      }
    } else {
      setAutoIntroduced([]);
    }
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
      gotIt: prev.gotIt + (signal === "understood" || signal === "grammar_confused" ? 1 : 0),
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

  function buildExplainPrompt(): string | null {
    if (sentenceSession) {
      const item = isIntroSlot ? null : sentenceSession.items[sentenceItemIndex];
      if (!item) return null;

      const marked = [
        ...Array.from(missedIndices).map((i) => ({ index: i, mark: "missed" })),
        ...Array.from(confusedIndices).map((i) => ({
          index: i,
          mark: "did_not_recognize",
        })),
      ].sort((a, b) => a.index - b.index);

      if (marked.length === 0) return null;

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

  function buildExplainSentencePrompt(): string | null {
    if (!sentenceSession) return null;
    const item = isIntroSlot ? null : sentenceSession.items[sentenceItemIndex];
    if (!item) return null;

    const wordLines = item.words.map((w, i) => {
      const known = w.knowledge_state === "known" || w.knowledge_state === "learning";
      const status = w.is_function_word ? "function_word" : (known ? "known" : "unknown/new");
      const parts = [`${i + 1}. ${w.surface_form} — ${status}`];
      if (w.gloss_en) parts.push(`gloss: "${w.gloss_en}"`);
      if (w.root) parts.push(`root: ${w.root}`);
      if (w.grammar_tags?.length) parts.push(`grammar: ${w.grammar_tags.join(", ")}`);
      return parts.join(", ");
    }).join("\n");

    return [
      "Explain this Arabic sentence word by word.",
      "For each word:",
      "1) Give the base lemma (Arabic + transliteration)",
      "2) Explain what prefixes, suffixes, or clitics are attached and what they mean",
      "3) Identify the grammar pattern (verb form, case ending, إضافة, حال, etc.)",
      "4) Briefly note how it fits into the overall sentence structure",
      "",
      "Then give a one-line summary of the full sentence's grammatical structure.",
      "",
      "Words (my knowledge level indicated):",
      wordLines,
    ].join("\n");
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
          contentContainerStyle={styles.sentenceArea}
          showsVerticalScrollIndicator={false}
        >
          <Text style={styles.wordOnlyArabic}>{wc.lemma_ar}</Text>
          {wc.transliteration && (
            <Text style={styles.sentenceTranslit}>{wc.transliteration}</Text>
          )}
          {wrapUpRevealed && (
            <>
              <View style={styles.divider} />
              <Text style={styles.wordOnlyGloss}>{wc.gloss_en || ""}</Text>
              {wc.root && (
                <Text style={[styles.sentenceTranslit, { marginTop: 4 }]}>
                  Root: {wc.root}{wc.root_meaning ? ` \u2014 ${wc.root_meaning}` : ""}
                </Text>
              )}
              {wc.etymology_json?.derivation && (
                <Text style={[styles.sentenceTranslit, { fontStyle: "italic", marginTop: 4 }]}>
                  {wc.etymology_json.derivation}
                </Text>
              )}
              {wc.memory_hooks_json?.mnemonic && (
                <Text style={[styles.sentenceTranslit, { fontStyle: "italic", marginTop: 2 }]}>
                  {wc.memory_hooks_json.mnemonic}
                </Text>
              )}
            </>
          )}
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

  // Re-introduction cards shown before sentence review
  const showingReintro = reintroCards.length > 0 && reintroIndex < reintroCards.length;
  if (showingReintro) {
    const card = reintroCards[reintroIndex];
    const knownSiblings = card.root_family.filter(
      (s) => s.state === "known" || s.state === "learning"
    );
    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <View style={styles.progressContainer}>
          <Text style={styles.progressText}>
            Re-learning {reintroIndex + 1} of {reintroCards.length}
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
          contentContainerStyle={styles.reintroScrollContent}
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.reintroCard}>
            <Text style={styles.reintroLabel}>
              Seen {card.times_seen} times, never recalled
            </Text>
            <View style={styles.reintroWordHeader}>
              <Text style={styles.reintroArabic}>{card.lemma_ar}</Text>
              <PlayButton audioUrl={card.audio_url} word={card.lemma_ar} />
            </View>
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
            <GrammarRow details={card.grammar_details} />

            {card.example_ar && (
              <View style={styles.reintroExample}>
                <Text style={styles.reintroExampleAr}>{card.example_ar}</Text>
                {card.example_en && (
                  <Text style={styles.reintroExampleEn}>{card.example_en}</Text>
                )}
              </View>
            )}

            {card.root && (
              <View style={styles.reintroRoot}>
                <Text style={styles.reintroRootText}>
                  {card.root}
                  {card.root_meaning ? ` \u2014 ${card.root_meaning}` : ""}
                </Text>
                {card.root_family.length > 0 && (
                  <Text style={styles.reintroRootSiblings}>
                    {knownSiblings.length}/{card.root_family.length}
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
            style={styles.reintroRememberBtn}
            onPress={async () => {
              try {
                await submitReintroResult(
                  card.lemma_id,
                  "remember",
                  sentenceSession?.session_id,
                );
              } catch {}
              if (reintroIndex + 1 < reintroCards.length) {
                setReintroIndex(reintroIndex + 1);
              } else {
                setReintroCards([]);
                setReintroIndex(0);
              }
            }}
          >
            <Text style={styles.reintroRememberText}>I remember</Text>
          </Pressable>
          <Pressable
            style={styles.reintroShowAgainBtn}
            onPress={async () => {
              try {
                await submitReintroResult(
                  card.lemma_id,
                  "show_again",
                  sentenceSession?.session_id,
                );
              } catch {}
              if (reintroIndex + 1 < reintroCards.length) {
                setReintroIndex(reintroIndex + 1);
              } else {
                setReintroCards([]);
                setReintroIndex(0);
              }
            }}
          >
            <Text style={styles.reintroShowAgainText}>Show again</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // Intro card mid-session
  if (sentenceSession && isIntroSlot) {
    const candidate = autoIntroduced[currentSlot!.type === "intro" ? currentSlot!.candidateIndex : 0];
    const knownSiblings = candidate.root_family?.filter(
      (s) => (s.state === "known" || s.state === "learning") && s.lemma_id !== candidate.lemma_id
    ) ?? [];

    async function handleIntroLearn() {
      try {
        await introduceWord(candidate.lemma_id);
        setIntroducedLemmaIds(prev => new Set([...prev, candidate.lemma_id]));
      } catch {}
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
            askAIExplainPrompt={buildExplainPrompt}
            askAIExplainSentencePrompt={buildExplainSentencePrompt}
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
          onPrev={handleLookupPrev}
          onNext={handleLookupNext}
          hasPrev={tappedCursor > 0}
          hasNext={tappedCursor < tappedOrder.length - 1}
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
                onPress={() => onWordTap(i, word.lemma_id ?? null)}
                style={wordStyle}
              >
                {word.surface_form}
              </Text>
            </Text>
          );
        })}
      </Text>

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
              {w.root}
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
  onNewSession,
}: {
  results: SessionResults;
  mode: ReviewMode;
  autoIntroduced: IntroCandidate[];
  introducedLemmaIds: Set<number>;
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
    "Solid Effort!";

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
    prefetchSessions(mode).catch(() => {});
  }, []);

  const accuracyColor =
    accuracy >= 80 ? colors.gotIt :
    accuracy >= 60 ? colors.accent :
    colors.noIdea;

  return (
    <ScrollView
      style={styles.sessionCompleteScroll}
      contentContainerStyle={styles.sessionCompleteContent}
      showsVerticalScrollIndicator={false}
    >
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

      <Pressable style={styles.nextSessionHeroButton} onPress={onNewSession}>
        <Text style={styles.nextSessionHeroTitle}>Next Session</Text>
        <Text style={styles.nextSessionHeroSubtitle}>
          Keep momentum while these words are fresh
        </Text>
      </Pressable>

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
              <Text style={styles.wordOutcomeSupportText}>
                This is normal. Many good practice sentences include one new word.
              </Text>
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
                {analytics.pace.current_streak}d streak
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
      {introducedLemmaIds.size > 0 && (
        <View style={styles.autoIntroSection}>
          <Text style={styles.autoIntroTitle}>
            {introducedLemmaIds.size === 1 ? "New word learned" : `${introducedLemmaIds.size} new words learned`}
          </Text>
          <View style={styles.autoIntroPills}>
            {autoIntroduced.filter((w) => introducedLemmaIds.has(w.lemma_id)).map((w) => (
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
    paddingTop: 24,
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
    fontStyle: "italic",
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
});
