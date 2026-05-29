/**
 * Polyglot sentence-review screen.
 *
 * Ports Alif's sentence-review UX (`frontend/app/index.tsx`, SentenceReadingCard
 * + ReadingActions + handleWordTap + handleSentenceSubmit) to Modern Greek,
 * following `polyglot/CLAUDE.md` § "Ground design and code in Alif".
 *
 * Two-stage reveal: front shows the Greek sentence alone; "Show Translation"
 * flips to the back with the English. On either side the learner can:
 *   - tap a word to cycle off → missed (red) → confused (yellow) → off
 *   - tap "No idea" → comprehension_signal="no_idea"
 *   - tap the middle button (label "Know All" with no marks → "Continue" with
 *     any marks) → comprehension_signal derived from marks
 *
 * No word carries a pre-applied highlight — not the scheduling target, not
 * the function-word/proper-name class, not the most-recently-tapped word.
 * Mirrors Alif's index.tsx: only the user's own mark cycle is visible.
 * Function words and proper names can be tapped (so the gloss card appears)
 * and visually cycle alongside content words, but `lemmaIdsFromMarks` filters
 * them out of the submission payload — same content-lemma filter as
 * sentence_review_service on the backend. The gloss / missed-word card is
 * driven entirely by the last word currently in red/yellow; cycle that word
 * back to off and the card disappears.
 *
 * Cut vs Alif (with reasons):
 *   - tashkeel toggle dot — Greek has no analogous opt-out diacritics
 *   - transliteration line — polyglot doesn't compute it
 *   - lookup panel with root/etymology/memory hooks — polyglot has no such infra
 *   - confusion-help fetch — polyglot has no /confusion-help endpoint
 *   - intro cards / passages / verses — deferred per Hard Invariant #12
 *   - audio controls / listening mode — no TTS in polyglot yet
 *   - wrap-up quiz / session-end journey — deferred
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  View, Text, Pressable, StyleSheet, ActivityIndicator, ScrollView, Modal,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLanguage } from "../lib/language-context";
import {
  askPolyglotAI,
  ackExperimentIntro,
  flagPolyglotContent,
  getReviewSessionResilient,
  prefetchReviewSession,
  submitSentenceReview,
  getReviewStats,
  getLemmaDetail,
  type IntroCard,
  type LemmaDetail,
  type ReviewSessionBundle,
  type SentencePayload,
  type TokenView,
  type AcquisitionStats,
  type ComprehensionSignal,
} from "../lib/polyglot-api";
import PolyglotLookupCard from "../lib/polyglot-lookup-card";
import { POLYGLOT_COLORS, POLYGLOT_RADIUS } from "../lib/polyglot-design-colors";
import { POLYGLOT_FONTS } from "../lib/polyglot-design-tokens";
import {
  buildInterleavedSlots,
  cycleMark,
  deriveSignal,
  emptyMarks,
  generateClientReviewId,
  generateSessionId,
  hasAnyMarks,
  lemmaIdsFromMarks,
  markStateAt,
  middleButtonLabel,
  type MarkSets,
  type SessionSlot,
} from "../lib/polyglot-review-helpers";
import { renderTokens } from "../lib/polyglot-render-helpers";
import {
  isReviewSnapshotValid,
  reviewSnapshotKey,
  type ReviewSnapshot,
} from "../lib/polyglot-review-snapshot";
import ActionMenu from "../lib/review/ActionMenu";

// 2026-05-21 round 2: Renaissance Folio palette — picked + iterated through
// 3 rounds of design-explorer (session b0b63950). Cream parchment ground,
// burnt-umber text, italic burnt-orange target word, no function-word fade.
// Local to this screen on purpose — POLYGLOT_COLORS still drives the reader
// (polyglot.tsx) and lemma-detail page until those screens get their own
// Folio pass. See /Users/stian/.claude/design-explorer/mockups/polyglot-folio/.
const C = {
  bg: "#f4ecd6",          // parchment cream
  surface: "#efe5c8",     // cream, slightly darker for nested cards
  border: "#dbcfae",      // hairline tan
  borderStrong: "#c4a878",
  text: "#2a1c0c",        // burnt umber
  textDim: "#5a3a1a",     // warm brown — used for English translation
  textMuted: "#8a6a3a",   // tan — used for chrome labels only, NEVER for words
  accent: "#8a4a1a",      // burnt orange — primary button, progress fill
  missed: "#b85a3a",      // burnt red — marked-missed underline
  confused: "#c79858",    // amber — marked-confused underline
  good: "#7a8a4a",        // moss
  noIdea: "#b85a3a",
};

type CardState = "front" | "back";

// In-flight session snapshot: pure types + key + validator live in
// ../lib/polyglot-review-snapshot. The lemma-detail screen is a hidden sibling
// tab (no <Stack> anywhere — see polyglot/CLAUDE.md), so opening it tears this
// screen down; on return React rebuilds it and the mount would otherwise
// loadSession() a *fresh* server-generated session, dropping the learner's
// place — sentence, reveal state, and word marks. We snapshot the whole session
// and rehydrate it on remount. The key is per-language and the snapshot is
// tagged with its language so Greek↔Latin can't cross-pollinate.

export default function PolyglotReview() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { language } = useLanguage();
  // This screen serves the Polyglot surface (Greek + Latin); Arabic never
  // routes here. Pass the active polyglot language through to the backend.
  const languageCode = language === "la" ? "la" : "el";

  const [bundle, setBundle] = useState<ReviewSessionBundle>({
    sentences: [],
    intro_cards: [],
  });
  const [slots, setSlots] = useState<SessionSlot[]>([]);
  const [stats, setStats] = useState<AcquisitionStats | null>(null);
  const [index, setIndex] = useState(0);
  const [cardState, setCardState] = useState<CardState>("front");
  const [marks, setMarks] = useState<MarkSets>(emptyMarks);
  const [glossWordIdx, setGlossWordIdx] = useState<number | null>(null);
  // Lazy-fetched lemma detail for the tapped word + intro card. Same pattern
  // as polyglot.tsx — render the head row immediately from what we have, then
  // stream enrichment in. detailRequestRef guards against stale responses.
  const [lemmaDetailCache, setLemmaDetailCache] = useState<Record<number, LemmaDetail>>({});
  const detailRequestRef = useRef(0);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sessionIdRef = useRef<string>(generateSessionId());
  const shownAtRef = useRef<number>(Date.now());
  // Lemmas whose intro card was displayed this UI session, used to suppress
  // re-firing the same card on a prefetch reload before the server's ack has
  // propagated. Mirrors Alif's `alreadyShownIntroLemmaIds` parameter.
  const shownIntroLemmaIdsRef = useRef<Set<number>>(new Set());
  // The language whose session is currently in state. Starts null (nothing
  // loaded). Set only once fresh/rehydrated data for a language has landed, so
  // it doubles as the guard that (a) triggers a reload when the active language
  // changes and (b) blocks persistSnapshot from writing the previous language's
  // session under the new language's key mid-switch.
  const loadedLanguageRef = useRef<string | null>(null);

  const currentSlot: SessionSlot | undefined = slots[index];
  const currentSentence: SentencePayload | undefined =
    currentSlot?.type === "sentence"
      ? bundle.sentences[currentSlot.sentenceIndex]
      : undefined;
  const currentIntro: IntroCard | undefined =
    currentSlot?.type === "intro"
      ? bundle.intro_cards[currentSlot.introIndex]
      : undefined;

  const loadSession = useCallback(async (opts: { forceFresh?: boolean } = {}) => {
    setLoading(true);
    setError(null);
    try {
      // Session load is the critical path (cache-first, resilient); stats is
      // best-effort so a stats hiccup can't block the session→next transition.
      const next = await getReviewSessionResilient(languageCode, 15, opts);
      const s = await getReviewStats(languageCode).catch(() => null);
      sessionIdRef.current = generateSessionId();
      const nextSlots = buildInterleavedSlots(
        next.sentences,
        next.intro_cards,
        shownIntroLemmaIdsRef.current,
      );
      setBundle(next);
      setSlots(nextSlots);
      if (s) setStats(s);
      setIndex(0);
      setCardState("front");
      setMarks(emptyMarks());
      setGlossWordIdx(null);
      shownAtRef.current = Date.now();
      loadedLanguageRef.current = languageCode;
      // An empty fresh session means any prior snapshot is spent — drop it so a
      // later remount doesn't rehydrate an already-finished session.
      if (nextSlots.length === 0) {
        AsyncStorage.removeItem(reviewSnapshotKey(languageCode)).catch(() => {});
      }
    } catch (e: any) {
      setError(e?.message ?? "Failed to load review session");
    } finally {
      setLoading(false);
    }
  }, [languageCode]);

  // Rehydrate an in-flight session for the active language if one was
  // snapshotted recently (the lemma-detail round-trip remounts this screen —
  // see persistSnapshot); otherwise pull a fresh session from the server. The
  // snapshot must match the active language on both the key and the embedded
  // tag, so a Greek snapshot never rehydrates under Latin.
  const restoreOrLoad = useCallback(async () => {
    setLoading(true);
    try {
      const raw = await AsyncStorage.getItem(reviewSnapshotKey(languageCode));
      if (raw) {
        const snap: ReviewSnapshot = JSON.parse(raw);
        if (isReviewSnapshotValid(snap, languageCode)) {
          setBundle(snap.bundle);
          setSlots(snap.slots);
          setStats(snap.stats);
          setIndex(snap.index);
          setCardState(snap.cardState);
          setMarks({
            missed: new Set(snap.marks?.missed ?? []),
            confused: new Set(snap.marks?.confused ?? []),
          });
          setGlossWordIdx(snap.glossWordIdx);
          sessionIdRef.current = snap.sessionId;
          shownIntroLemmaIdsRef.current = new Set(snap.shownIntroLemmaIds ?? []);
          // Restart the response-time clock on resume. Deliberate: response_ms
          // is analytics-only (no scheduling impact), and the alternative —
          // restoring the original shownAt — would fold the (often multi-minute)
          // philology-reading detour into response_ms, polluting fast/slow
          // signals far worse than under-counting pre-detour time does.
          shownAtRef.current = Date.now();
          loadedLanguageRef.current = languageCode;
          setLoading(false);
          // The restore path doesn't go through loadSession, so warm the next
          // session here too — otherwise a resumed session's finish→next would
          // be a cold fetch (the exact failure this prefetch layer removes).
          void prefetchReviewSession(languageCode);
          return;
        }
      }
    } catch {
      // ignore — fall through to a fresh session
    }
    void loadSession();
  }, [languageCode, loadSession]);

  // Load (or rehydrate) a session whenever the active language changes — and on
  // first mount. Switching Greek↔Latin (which leaves this screen mounted, since
  // both share the polyglot tabs) must pull the new language's session instead
  // of keeping the previous one on screen. The ref guard makes this a no-op for
  // unrelated re-renders, and `loadedLanguageRef` only catches up once new data
  // has actually landed (set inside restoreOrLoad / loadSession).
  useEffect(() => {
    if (loadedLanguageRef.current === languageCode) return;
    void restoreOrLoad();
  }, [languageCode, restoreOrLoad]);

  // Snapshot the in-flight session so the mount effect can rehydrate it after a
  // remount. Fires on any session-state change, and again with the freshest
  // state right before navigating into the detail screen. Skipped when there's
  // no session yet.
  const persistSnapshot = useCallback(() => {
    if (slots.length === 0) return;
    // Don't write while a language switch is in flight: state still holds the
    // previous language's session until restoreOrLoad/loadSession lands, and
    // loadedLanguageRef only equals languageCode once that fresh data is in
    // state. Without this guard the switch render would write the old session
    // under the new language's key (tagged with the wrong language).
    if (loadedLanguageRef.current !== languageCode) return;
    const snap: ReviewSnapshot = {
      language: languageCode,
      bundle,
      slots,
      stats,
      index,
      cardState,
      marks: { missed: Array.from(marks.missed), confused: Array.from(marks.confused) },
      glossWordIdx,
      sessionId: sessionIdRef.current,
      shownIntroLemmaIds: Array.from(shownIntroLemmaIdsRef.current),
      savedAt: Date.now(),
    };
    AsyncStorage.setItem(reviewSnapshotKey(languageCode), JSON.stringify(snap)).catch(() => {});
  }, [bundle, slots, stats, index, cardState, marks, glossWordIdx, languageCode]);

  useEffect(() => { persistSnapshot(); }, [persistSnapshot]);

  // Stamp the server timestamp the instant we land on an intro slot. This is
  // what arms the working-memory gate: a correct review on this lemma within
  // FAST_GRAD_INTRO_GAP (10 min) is treated as working memory, not learning.
  useEffect(() => {
    if (!currentIntro) return;
    if (shownIntroLemmaIdsRef.current.has(currentIntro.lemma_id)) return;
    shownIntroLemmaIdsRef.current.add(currentIntro.lemma_id);
    void ackExperimentIntro(currentIntro.lemma_id, sessionIdRef.current).catch(() => {
      // Best-effort: a transient ack failure means the next session may
      // re-show the card, which is benign. The gate timing is also "best
      // effort" — if the ack didn't land, no working-memory gate fires this
      // time, but the user still gets the intro card UI.
    });
  }, [currentIntro]);

  // Lazy-fetch full lemma detail for the current intro card AND the currently
  // glossed word. Same dedup pattern as polyglot.tsx: detailRequestRef guards
  // stale responses, lemmaDetailCache memoises by lemma_id.
  useEffect(() => {
    const targetLemmaId =
      currentIntro?.lemma_id ??
      (glossWordIdx != null ? currentSentence?.words[glossWordIdx]?.lemma_id ?? null : null);
    if (targetLemmaId == null) return;
    if (lemmaDetailCache[targetLemmaId]) return;
    const reqId = ++detailRequestRef.current;
    getLemmaDetail(targetLemmaId)
      .then((detail) => {
        if (detailRequestRef.current !== reqId) return;
        setLemmaDetailCache((prev) => ({ ...prev, [targetLemmaId]: detail }));
      })
      .catch(() => {});
  }, [currentIntro, glossWordIdx, currentSentence, lemmaDetailCache]);

  // Reveal the translation. Mirrors Alif: the reveal is one-way (no "Hide"),
  // and the tapped-word gloss card stays open across the flip — keep
  // glossWordIdx so the missed/confused word the learner opened on the front
  // is still explained on the back.
  const advanceCard = useCallback(() => {
    setCardState("back");
  }, []);

  const handleWordTap = useCallback((wordIdx: number) => {
    if (!currentSentence) return;
    const word = currentSentence.words[wordIdx];
    if (!word || word.lemma_id == null) return;
    setMarks((prev) => {
      const next = cycleMark(prev, wordIdx);
      const nextState = markStateAt(next, wordIdx);
      setGlossWordIdx(nextState === "off" ? null : wordIdx);
      return next;
    });
  }, [currentSentence]);

  const advanceSlot = useCallback(() => {
    if (index + 1 >= slots.length) {
      // Final slot consumed — drop the snapshot before loading the next session.
      // Non-final slots advance the snapshot past the submitted card via the
      // index bump below, but on the last slot we don't bump index; without this
      // clear, a failed/interrupted reload would leave the snapshot pointing at
      // the just-submitted card, and a remount could rehydrate and let the user
      // re-submit it (a fresh client_review_id bypasses backend idempotency).
      AsyncStorage.removeItem(reviewSnapshotKey(languageCode)).catch(() => {});
      void loadSession();
    } else {
      setIndex(index + 1);
      setCardState("front");
      setMarks(emptyMarks());
      setGlossWordIdx(null);
      shownAtRef.current = Date.now();
    }
  }, [index, slots.length, loadSession, languageCode]);

  const handleSubmit = useCallback(async (signal: ComprehensionSignal) => {
    if (!currentSentence || submitting) return;
    setSubmitting(true);
    try {
      const { missed, confused } = lemmaIdsFromMarks(marks, currentSentence.words);
      const responseMs = Date.now() - shownAtRef.current;
      await submitSentenceReview({
        sentence_id: currentSentence.sentence_id,
        primary_lemma_id: currentSentence.target_lemma_id,
        comprehension_signal: signal,
        missed_lemma_ids: missed,
        confused_lemma_ids: confused,
        response_ms: responseMs,
        session_id: sessionIdRef.current,
        client_review_id: generateClientReviewId(),
        review_mode: "reading",
      });
      advanceSlot();
    } catch (e: any) {
      setError(e?.message ?? "Submit failed");
    } finally {
      setSubmitting(false);
    }
  }, [currentSentence, submitting, marks, advanceSlot]);

  const handleIntroContinue = useCallback(() => {
    advanceSlot();
  }, [advanceSlot]);

  // Manual "Refresh session" — discard the in-flight session (and its snapshot)
  // and pull a fresh one from the server. loadSession clears the snapshot when
  // the new session is empty; a non-empty one is re-persisted by the snapshot
  // effect. Mirrors Alif's "Refresh session" overflow action (index.tsx).
  const handleRefreshSession = useCallback(() => {
    AsyncStorage.removeItem(reviewSnapshotKey(languageCode)).catch(() => {});
    void loadSession({ forceFresh: true });
  }, [loadSession, languageCode]);

  const handleBackToReader = useCallback(() => {
    if (router.canGoBack()) router.back();
    else router.push("/polyglot");
  }, [router]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={C.accent} />
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>{error}</Text>
        <Pressable style={styles.button} onPress={() => loadSession()}>
          <Text style={styles.buttonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  if (!currentSlot) {
    return (
      <View style={styles.center}>
        <Ionicons name="checkmark-circle-outline" size={64} color={C.good} />
        <Text style={styles.emptyTitle}>No sentences ready</Text>
        <Text style={styles.emptySubtitle}>
          {stats
            ? `${stats.total_acquiring} word${stats.total_acquiring === 1 ? "" : "s"} in the acquisition pipeline`
            : ""}
        </Text>
        <View style={styles.statsRow}>
          {stats ? (
            <>
              <Stat label="Box 1" value={stats.box_1} />
              <Stat label="Box 2" value={stats.box_2} />
              <Stat label="Box 3" value={stats.box_3} />
            </>
          ) : null}
        </View>
        <Pressable
          style={styles.button}
          onPress={() => {
            if (router.canGoBack()) router.back();
            else router.push("/polyglot");
          }}
        >
          <Text style={styles.buttonText}>Back</Text>
        </Pressable>
        <Pressable style={[styles.button, styles.buttonGhost]} onPress={() => loadSession({ forceFresh: true })}>
          <Text style={[styles.buttonText, styles.buttonGhostText]}>Refresh</Text>
        </Pressable>
      </View>
    );
  }

  if (currentIntro) {
    return (
      <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
        <ProgressHeader
          label={currentIntro.intro_kind === "rescue" ? "rescue card" : "new word"}
          current={index + 1}
          total={slots.length}
          trailing={
            <OverflowMenu onRefresh={handleRefreshSession} onBack={handleBackToReader} />
          }
        />
        <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
          <IntroCardView
            card={currentIntro}
            detail={lemmaDetailCache[currentIntro.lemma_id] ?? null}
            onViewDetails={() => { persistSnapshot(); router.push(`/polyglot-lemma/${currentIntro.lemma_id}`); }}
          />
        </ScrollView>
        <View style={styles.bottomActions}>
          <View style={styles.actionRow}>
            <Pressable
              style={[styles.actionButton, styles.continueButton]}
              onPress={handleIntroContinue}
            >
              <Text style={styles.actionButtonText}>Got it — continue</Text>
            </Pressable>
          </View>
        </View>
      </View>
    );
  }

  if (!currentSentence) {
    // Defensive: a slot pointed at a sentence index we don't have. Treat as
    // end-of-session rather than crash; loadSession() resets.
    return (
      <View style={styles.center}>
        <Pressable style={styles.button} onPress={() => loadSession({ forceFresh: true })}>
          <Text style={styles.buttonText}>Refresh</Text>
        </Pressable>
      </View>
    );
  }

  const sentence = currentSentence;
  const hasMarks = hasAnyMarks(marks);
  const glossWord = glossWordIdx != null ? sentence.words[glossWordIdx] : null;
  const wordsForContext = [...sentence.words].sort((a, b) => a.position - b.position);
  const languageName = languageCode === "la" ? "Latin" : "Modern Greek";
  const focusedLemmaId = glossWord?.lemma_id ?? sentence.target_lemma_id ?? null;
  const focusedLemmaForm = glossWord?.lemma_form ?? glossWord?.surface_form ?? null;

  function buildAskAIContext(): string {
    const parts: string[] = [
      "Screen: polyglot review",
      `Language: ${languageName} (${languageCode})`,
      `Card: ${index + 1}/${slots.length}`,
      `Phase: ${cardState}`,
      `Sentence ID: ${sentence.sentence_id}`,
      `Sentence: ${sentence.text}`,
      `English: ${sentence.translation_en ?? "(none)"}`,
      `Target lemma ID: ${sentence.target_lemma_id}`,
    ];

    parts.push("Words:");
    wordsForContext.forEach((word, i) => {
      const mark = markStateAt(marks, i);
      const status =
        word.is_function_word ? "function"
        : word.is_proper_name ? "proper_name"
        : word.knowledge_state || "new";
      parts.push([
        `${i + 1}. ${word.surface_form}`,
        `lemma_id=${word.lemma_id ?? "none"}`,
        `lemma=${word.lemma_form ?? "unknown"}`,
        `gloss=${word.gloss_en ?? "unknown"}`,
        `status=${status}`,
        `mark=${mark}`,
      ].join(" | "));
    });

    if (glossWord) {
      parts.push(
        `Lookup focus: surface=${glossWord.surface_form}, lemma_id=${glossWord.lemma_id ?? "none"}, lemma=${glossWord.lemma_form ?? "unknown"}, gloss=${glossWord.gloss_en ?? "unknown"}`,
      );
    }

    return parts.join("\n");
  }

  function buildAskAIAutoExplainPrompt(): string | null {
    const parts: string[] = [
      `Explain how this ${languageName} sentence works — how the words combine to produce the meaning.`,
      "",
      "Focus on:",
      "- What the sentence is really saying, including tone and nuance",
      "- Why the English translation works, and any better alternatives",
      "- Grammar or word forms worth noticing only when they affect the meaning",
      "- Cognates, older forms, or etymology only when they clarify the sentence",
      "",
      "Don't list every word mechanically. I can see the glosses already.",
    ];

    const marked = wordsForContext
      .map((word, i) => ({ word, i, mark: markStateAt(marks, i) }))
      .filter((entry) => entry.mark !== "off");

    if (marked.length > 0) {
      parts.push(
        "",
        "I marked some words below. For each marked word, identify the lemma, explain the surface form, and give one short recognition tip.",
      );
    }

    parts.push("", "Words:");
    wordsForContext.forEach((word, i) => {
      const mark = markStateAt(marks, i);
      const prefix = mark === "off" ? "" : `[${mark.toUpperCase()}] `;
      const role =
        word.is_function_word ? "function"
        : word.is_proper_name ? "proper name"
        : word.knowledge_state || "content";
      parts.push(
        `${prefix}${i + 1}. ${word.surface_form} — lemma ${word.lemma_form ?? "unknown"}, gloss ${word.gloss_en ?? "unknown"}, ${role}`,
      );
    });

    parts.push("", `Sentence: ${sentence.text}`);
    if (sentence.translation_en) {
      parts.push(`Given translation: ${sentence.translation_en}`);
    }

    return parts.join("\n");
  }

  return (
    <View style={[styles.container, { paddingTop: Math.max(insets.top, 12) }]}>
      <ProgressHeader
        current={index + 1}
        total={slots.length}
        trailing={
          <ActionMenu
            focusedLemmaId={focusedLemmaId}
            focusedLemmaAr={focusedLemmaForm}
            sentenceId={sentence.sentence_id}
            askAIContextBuilder={buildAskAIContext}
            askAIScreen="polyglot-review"
            askAIAutoExplainPrompt={buildAskAIAutoExplainPrompt}
            askAIClient={askPolyglotAI}
            flagContentClient={flagPolyglotContent}
            onBack={handleBackToReader}
            extraActions={[
              {
                icon: "refresh-outline" as const,
                label: "Refresh session",
                onPress: handleRefreshSession,
              },
            ]}
            showFocusedWordActions={false}
            sentenceReportLabel="Report sentence"
            sentenceReportContentType="sentence"
          />
        }
      />

      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        <View style={styles.card}>
          <SentenceCard
            payload={sentence}
            cardState={cardState}
            marks={marks}
            onWordTap={handleWordTap}
          />
          {glossWord && glossWord.lemma_id != null ? (
            <View style={styles.lookupSlot}>
              <PolyglotLookupCard
                lemmaForm={glossWord.lemma_form ?? glossWord.surface_form}
                glossEn={glossWord.gloss_en}
                pos={null}
                ancientForm={
                  lemmaDetailCache[glossWord.lemma_id]?.cognate_lemma_form ??
                  lemmaDetailCache[glossWord.lemma_id]?.enrichment?.etymology?.ancient_form ??
                  null
                }
                enrichment={lemmaDetailCache[glossWord.lemma_id]?.enrichment ?? null}
                frequencyRank={lemmaDetailCache[glossWord.lemma_id]?.frequency_rank ?? null}
                surfaceForm={glossWord.surface_form}
                onViewDetails={() => { persistSnapshot(); router.push(`/polyglot-lemma/${glossWord.lemma_id}`); }}
              />
            </View>
          ) : null}
        </View>
      </ScrollView>

      <View style={styles.bottomActions}>
        <ReadingActions
          cardState={cardState}
          hasMarks={hasMarks}
          onAdvance={advanceCard}
          onSubmit={handleSubmit}
          submitting={submitting}
        />
      </View>
    </View>
  );
}

/**
 * Modern Editorial intro card. Renders the head row + chips immediately from
 * the IntroCard payload (gloss, POS, cognate). When `detail` arrives via the
 * lazy fetch in PolyglotReview, three optional sections show below the hero:
 * an etymology paragraph, a mini 3-column "across time" peek, and the top
 * literary quote. Mirrors the "Teach iPhone · Refined Alif Stack" mockup
 * thumbs-up'd in design-explorer round 1.
 *
 * Intentionally lean compared to the full lemma detail screen — the goal is
 * 10-20 seconds of reading. A "View full philology ›" link takes the curious
 * reader to the detail page when they want to go deeper.
 */
function IntroCardView({
  card,
  detail,
  onViewDetails,
}: {
  card: IntroCard;
  detail: LemmaDetail | null;
  onViewDetails: () => void;
}) {
  const isRescue = card.intro_kind === "rescue";
  const enrichment = detail?.enrichment;
  const ancientForm = card.cognate_lemma_form ?? enrichment?.etymology?.ancient_form ?? null;
  const pieRoot = enrichment?.etymology?.pie_root ?? null;
  const morph = enrichment?.etymology?.morphology ?? null;
  const originNote = enrichment?.etymology?.origin_note ?? null;
  const drift = enrichment?.diachrony ?? [];
  const topQuote = enrichment && enrichment.quotes.length > 0 ? enrichment.quotes[0] : null;

  return (
    <View style={styles.introWrap}>
      {/* Hero card */}
      <View style={styles.introHero}>
        <Text style={styles.introHeroLemma}>{card.lemma_form}</Text>
        <Text style={styles.introHeroLabel}>Meaning</Text>
        <Text style={styles.introHeroGloss}>{card.gloss_en ?? "(no gloss)"}</Text>
        <View style={styles.introChips}>
          {card.pos ? (
            <View style={[styles.introChip, { backgroundColor: POLYGLOT_COLORS.surfaceMuted }]}>
              <Text style={[styles.introChipText, { color: POLYGLOT_COLORS.textSecondary }]}>
                {card.pos}
              </Text>
            </View>
          ) : null}
          {ancientForm ? (
            <View style={[styles.introChip, { backgroundColor: POLYGLOT_COLORS.cognateTint }]}>
              <Text style={[styles.introChipText, { color: POLYGLOT_COLORS.cognate }]}>
                Ancient{"  "}
                <Text style={[styles.introChipGreek, { color: POLYGLOT_COLORS.cognate }]}>
                  {ancientForm}
                </Text>
              </Text>
            </View>
          ) : null}
          {pieRoot ? (
            <View style={[styles.introChip, { backgroundColor: POLYGLOT_COLORS.etymologyTint }]}>
              <Text style={[styles.introChipText, { color: POLYGLOT_COLORS.etymology }]}>
                {pieRoot}
              </Text>
            </View>
          ) : null}
        </View>
        {isRescue ? (
          <Text style={styles.introRescueHint}>
            You've seen this {card.times_seen} times but it hasn't stuck yet.
          </Text>
        ) : null}
      </View>

      {/* Etymology — only when enrichment loaded */}
      {originNote ? (
        <View style={[styles.introSection, { borderColor: POLYGLOT_COLORS.etymology + "33" }]}>
          <Text style={[styles.introSectionLabel, { color: POLYGLOT_COLORS.etymology }]}>
            Etymology
          </Text>
          <Text style={styles.introSectionBody}>{originNote}</Text>
          {morph ? (
            <View style={styles.introMorph}>
              <Text style={styles.introMorphText}>{morph}</Text>
            </View>
          ) : null}
        </View>
      ) : null}

      {/* Mini-drift — 2-3 stages inline */}
      {drift.length >= 2 ? (
        <View style={[styles.introSection, { borderColor: POLYGLOT_COLORS.border }]}>
          <Text style={[styles.introSectionLabel, { color: POLYGLOT_COLORS.text }]}>
            Across time
          </Text>
          <View style={styles.miniDrift}>
            {drift.slice(0, 3).map((stage, idx) => (
              <View
                key={`mini-${idx}`}
                style={[
                  styles.miniStage,
                  idx < Math.min(drift.length, 3) - 1 && styles.miniStageDivider,
                ]}
              >
                <Text style={[styles.miniEra, { color: POLYGLOT_COLORS.text }]}>{stage.era}</Text>
                <Text style={styles.miniForm}>{stage.form}</Text>
                <Text style={styles.miniMeaning} numberOfLines={2}>
                  {stage.meaning}
                </Text>
              </View>
            ))}
          </View>
        </View>
      ) : null}

      {/* Top quote */}
      {topQuote ? (
        <View style={[styles.introSection, { borderColor: POLYGLOT_COLORS.quote + "33" }]}>
          <Text style={[styles.introSectionLabel, { color: POLYGLOT_COLORS.quote }]}>
            In the literature
          </Text>
          <Text style={styles.introQuoteText}>{topQuote.text}</Text>
          <Text style={styles.introQuoteSource}>
            {topQuote.source} · {topQuote.era}
          </Text>
          <Text style={styles.introQuoteTrans}>"{topQuote.translation_en}"</Text>
        </View>
      ) : null}

      {/* View details — always offered (even when enrichment hasn't streamed
       *  in yet — the detail page will eagerly load it). */}
      <Pressable onPress={onViewDetails} hitSlop={8} style={styles.viewDetailsRow}>
        <Text style={styles.viewDetailsLink}>View full philology ›</Text>
      </Pressable>
    </View>
  );
}

function SentenceCard({
  payload,
  cardState,
  marks,
  onWordTap,
}: {
  payload: SentencePayload;
  cardState: CardState;
  marks: MarkSets;
  onWordTap: (idx: number) => void;
}) {
  const showAnswer = cardState === "back";
  const words = useMemo(
    () => [...payload.words].sort((a, b) => a.position - b.position),
    [payload.words],
  );
  const spans = useMemo(() => {
    const tokens = words.map((word, index) => ({
      position: word.position,
      surface: word.surface_form,
      is_punctuation: Boolean(word.is_punctuation),
      sentence_index: 0,
      lemma_id: word.lemma_id,
      lemma_form: word.lemma_form,
      lemma_bare: null,
      pos: null,
      gloss_en: word.gloss_en,
      is_function_word: word.is_function_word,
      is_heading: false,
      is_known: word.knowledge_state === "known",
      is_acquiring: word.knowledge_state === "acquiring",
      is_encountered: word.knowledge_state === "encountered",
      is_unknown: word.knowledge_state === "unknown",
      is_ignored: word.knowledge_state === "ignored",
      is_new: word.knowledge_state === "new",
      is_oov: word.lemma_id == null && !word.is_punctuation,
      originalWordIndex: index,
    })) as Array<TokenView & { originalWordIndex: number }>;
    return renderTokens(tokens);
  }, [words]);

  return (
    <>
      <Text style={styles.sentenceGreek}>
        {spans.map((span, i) => {
          const wordIdx = (span.token as TokenView & { originalWordIndex: number }).originalWordIndex;
          const state = markStateAt(marks, wordIdx);
          // Nothing in the sentence carries a pre-applied highlight — not the
          // scheduling target, not the currently-glossed word, not function
          // words. The only visible signal is the user's own mark cycle:
          // missed (red underline) or confused (yellow underline). See
          // Alif's index.tsx ~line 2877 for the source pattern.
          const wordStyle =
            state === "missed" ? styles.missedWord
            : state === "confused" ? styles.confusedWord
            : undefined;

          return (
            <Text key={`w-${i}`}>
              {span.leadingSpace}
              <Text
                onPress={
                  !span.isPunctuation && span.token.lemma_id != null
                    ? () => onWordTap(wordIdx)
                    : undefined
                }
                style={wordStyle}
              >
                {span.surface}
              </Text>
            </Text>
          );
        })}
      </Text>

      <View
        style={[
          styles.answerSection,
          !showAnswer && styles.answerSectionHidden,
        ]}
      >
        <View style={styles.divider} />
        {showAnswer && payload.translation_en ? (
          <Text style={styles.sentenceEnglish}>{payload.translation_en}</Text>
        ) : (
          <Text style={styles.sentenceEnglish}> </Text>
        )}
      </View>
    </>
  );
}

function ReadingActions({
  cardState,
  hasMarks,
  onAdvance,
  onSubmit,
  submitting,
}: {
  cardState: CardState;
  hasMarks: boolean;
  onAdvance: () => void;
  onSubmit: (signal: ComprehensionSignal) => Promise<void> | void;
  submitting: boolean;
}) {
  const signal = deriveSignal(hasMarks);
  const middleLabel = middleButtonLabel(hasMarks);
  const isFront = cardState === "front";
  // Mirrors Alif's ReadingActions (index.tsx) verbatim: left "No idea", a
  // middle button whose label toggles "Know All" (no marks) → "Continue"
  // (any marks), and a right slot that holds "Show Translation" on the front
  // and an empty spacer on the back. The reveal is one-way — no "Hide".
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
          hasMarks ? styles.continueButton : styles.gotItButton,
          submitting && styles.actionButtonDisabled,
        ]}
        onPress={() => void onSubmit(signal)}
        disabled={submitting}
      >
        <Text style={styles.actionButtonText}>{middleLabel}</Text>
      </Pressable>
      {isFront ? (
        <Pressable
          style={[styles.actionButton, styles.showButton, submitting && styles.actionButtonDisabled]}
          onPress={onAdvance}
          disabled={submitting}
        >
          <Text style={styles.showButtonText}>Show Translation</Text>
        </Pressable>
      ) : (
        <View style={styles.actionButtonSpacer} />
      )}
    </View>
  );
}

/**
 * Folio progress header — a "Card X of Y" caption above a hairline track,
 * mirroring Alif's ProgressBar (index.tsx). `label` is an optional small
 * tag shown on the left (used by intro cards: "new word" / "rescue card");
 * sentence cards pass none, so the header is just the count + track — no
 * internal selection_reason diagnostics leak into the UI.
 */
function ProgressHeader({
  label,
  current,
  total,
  trailing,
}: { label?: string; current: number; total: number; trailing?: ReactNode }) {
  const ratio = total > 0 ? Math.max(0, Math.min(1, current / total)) : 0;
  return (
    <View style={styles.header}>
      <View style={styles.progressRow}>
        <Text style={styles.progressLabel}>{label ?? ""}</Text>
        <View style={styles.progressRight}>
          <Text style={styles.progressCount}>Card {current} of {total}</Text>
          {trailing}
        </View>
      </View>
      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${ratio * 100}%` }]} />
      </View>
    </View>
  );
}

/**
 * Overflow ("...") menu for intro cards, where there is no sentence to report
 * or explain yet. Sentence cards use the shared Alif ActionMenu with polyglot
 * chat/report clients.
 */
function OverflowMenu({
  onRefresh,
  onBack,
}: { onRefresh: () => void; onBack: () => void }) {
  const [visible, setVisible] = useState(false);
  const run = (fn: () => void) => () => { setVisible(false); fn(); };
  return (
    <View>
      <Pressable onPress={() => setVisible(true)} hitSlop={10} style={styles.menuTrigger}>
        <Ionicons name="ellipsis-horizontal" size={20} color={C.textMuted} />
      </Pressable>
      <Modal
        visible={visible}
        transparent
        animationType="fade"
        onRequestClose={() => setVisible(false)}
      >
        <Pressable style={styles.menuBackdrop} onPress={() => setVisible(false)}>
          <View style={styles.menuSheet} onStartShouldSetResponder={() => true}>
            <View style={styles.menuHandle} />
            <Pressable style={styles.menuItem} onPress={run(onRefresh)}>
              <Ionicons name="refresh-outline" size={20} color={C.text} />
              <Text style={styles.menuLabel}>Refresh session</Text>
            </Pressable>
            <Pressable style={styles.menuItem} onPress={run(onBack)}>
              <Ionicons name="arrow-back-outline" size={20} color={C.text} />
              <Text style={styles.menuLabel}>Back to reader</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  // Flex column so the action row pins to the bottom of the screen (mirrors
  // Alif index.tsx: container → ProgressBar → ScrollView(flex) → bottomActions).
  // paddingTop is applied inline from safe-area insets.
  container: {
    flex: 1, backgroundColor: C.bg, paddingHorizontal: 24, paddingBottom: 16,
  },
  scroll: { flex: 1 },
  scrollContent: { flexGrow: 1, paddingTop: 8, paddingBottom: 12 },
  bottomActions: { paddingTop: 8 },
  center: {
    flex: 1, alignItems: "center", justifyContent: "center",
    backgroundColor: C.bg, padding: 24,
  },
  // Header: progress count row + filled-track bar (Alif-style progress bar).
  header: { marginBottom: 12 },
  progressRow: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    marginBottom: 8,
  },
  progressRight: { flexDirection: "row", alignItems: "center", gap: 12 },
  progressLabel: {
    color: C.textMuted, fontSize: 12, fontWeight: "600",
    letterSpacing: 0.3, textTransform: "lowercase",
  },
  progressCount: {
    color: C.textMuted, fontSize: 12, fontWeight: "600", letterSpacing: 0.3,
  },
  // Overflow ("...") menu — bottom-sheet styled to the Folio palette, mirroring
  // Alif's ActionMenu sheet (rounded top, drag handle, full-width rows).
  menuTrigger: { width: 28, height: 28, alignItems: "center", justifyContent: "center" },
  menuBackdrop: {
    flex: 1, backgroundColor: "rgba(0,0,0,0.45)", justifyContent: "flex-end",
  },
  menuSheet: {
    backgroundColor: C.surface, borderTopLeftRadius: 16, borderTopRightRadius: 16,
    paddingTop: 8, paddingBottom: 34, borderWidth: 1, borderColor: C.border,
  },
  menuHandle: {
    width: 36, height: 4, borderRadius: 2, backgroundColor: C.textMuted,
    alignSelf: "center", marginBottom: 8, opacity: 0.5,
  },
  menuItem: {
    flexDirection: "row", alignItems: "center", gap: 14,
    paddingVertical: 14, paddingHorizontal: 20,
  },
  menuLabel: { color: C.text, fontSize: 16 },
  progressTrack: {
    height: 3, backgroundColor: C.border, borderRadius: 1.5, overflow: "hidden",
  },
  progressFill: { height: "100%", backgroundColor: C.accent, borderRadius: 1.5 },
  // Card: no chrome — the parchment IS the surface. Padding-only layout per
  // Folio mockup (no border, no rounded corners on the sentence container).
  card: { paddingTop: 16, minHeight: 220 },
  /* Intro card — Modern Editorial. Hero card stacked with optional etymology /
   * mini-drift / quote sections, each shown only when enrichment loaded. */
  introWrap: { gap: 10 },
  introHero: {
    backgroundColor: C.surface, padding: 18, borderRadius: POLYGLOT_RADIUS.card,
    borderWidth: 1, borderColor: C.border, alignItems: "center", gap: 6,
  },
  introHeroLemma: {
    fontFamily: POLYGLOT_FONTS.greekDisplay, fontSize: 48, color: C.text, lineHeight: 52,
  },
  introHeroLabel: {
    fontSize: 10, letterSpacing: 1.6, textTransform: "uppercase",
    color: C.textMuted, marginTop: 6,
  },
  introHeroGloss: {
    fontSize: 21, fontFamily: POLYGLOT_FONTS.greekBody, color: C.text,
  },
  introChips: {
    flexDirection: "row", gap: 5, marginTop: 10, flexWrap: "wrap", justifyContent: "center",
  },
  introChip: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: POLYGLOT_RADIUS.chip },
  introChipText: { fontSize: 11, fontWeight: "600" },
  introChipGreek: { fontFamily: POLYGLOT_FONTS.greekDisplay, fontSize: 13 },
  introRescueHint: {
    color: POLYGLOT_COLORS.warning, fontSize: 12, marginTop: 6, textAlign: "center",
  },

  introSection: {
    backgroundColor: C.surface, padding: 14, borderRadius: POLYGLOT_RADIUS.card,
    borderWidth: 1, gap: 6,
  },
  introSectionLabel: {
    fontSize: 10, letterSpacing: 1.4, textTransform: "uppercase", fontWeight: "700",
  },
  introSectionBody: { fontSize: 13, color: C.text, lineHeight: 19 },
  introMorph: {
    marginTop: 6, padding: 8, backgroundColor: POLYGLOT_COLORS.etymologyTint,
    borderRadius: 6, alignItems: "center",
  },
  introMorphText: {
    fontFamily: POLYGLOT_FONTS.greekDisplay, fontSize: 14, color: POLYGLOT_COLORS.etymology,
  },
  miniDrift: { flexDirection: "row", gap: 0, marginTop: 2 },
  miniStage: { flex: 1, paddingHorizontal: 6, paddingVertical: 6, alignItems: "center" },
  miniStageDivider: { borderRightWidth: 1, borderRightColor: C.border },
  miniEra: {
    fontSize: 9, letterSpacing: 1.2, textTransform: "uppercase",
    fontWeight: "700", marginBottom: 4,
  },
  miniForm: {
    fontFamily: POLYGLOT_FONTS.greekDisplay, fontSize: 17, color: C.text,
    lineHeight: 21, marginBottom: 3,
  },
  miniMeaning: { fontSize: 10, color: C.textDim, textAlign: "center", lineHeight: 13 },
  introQuoteText: {
    fontFamily: POLYGLOT_FONTS.greekBody, fontSize: 18,
    color: C.text, lineHeight: 25, marginTop: 2,
  },
  introQuoteSource: {
    fontSize: 10, letterSpacing: 0.8, textTransform: "uppercase",
    color: C.textMuted, marginTop: 4,
  },
  introQuoteTrans: { fontSize: 12, color: C.textDim, marginTop: 4 },
  viewDetailsRow: { alignSelf: "center", paddingVertical: 8 },
  viewDetailsLink: { color: C.accent, fontSize: 13, fontWeight: "600" },
  sentenceGreek: {
    color: C.text, fontSize: 32, lineHeight: 48, textAlign: "left",
    fontFamily: POLYGLOT_FONTS.greekBody, letterSpacing: 0.2,
  },
  missedWord: {
    color: C.missed,
    textDecorationLine: "underline",
    textDecorationColor: C.missed,
  },
  confusedWord: {
    color: C.confused,
    textDecorationLine: "underline",
    textDecorationColor: C.confused,
  },
  // Answer reveal: 48px hairline rule + italic translation.
  answerSection: { marginTop: 24, alignItems: "flex-start" },
  answerSectionHidden: { opacity: 0 },
  divider: { height: 1, width: 48, backgroundColor: C.textMuted, marginBottom: 16 },
  sentenceEnglish: {
    color: C.textDim, fontSize: 21, lineHeight: 29, textAlign: "left",
    fontFamily: POLYGLOT_FONTS.greekBody,
  },
  /* Tapped-word lookup card slot — sits below the sentence inside the same
   * sentence-card container. The PolyglotLookupCard component carries its
   * own visual frame, so the slot just provides separation from the sentence
   * above. */
  lookupSlot: { marginTop: 14, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border },
  // Action row mirrors Alif's ReadingActions: three equal filled buttons,
  // 14px radius, 52px min height, pinned at the bottom. "No idea" is a soft
  // bordered ghost; the middle is moss-green ("Know All") → burnt-orange
  // ("Continue"); "Show Translation" is burnt-orange. Back side drops the
  // right button to a flex spacer (no "Hide Translation").
  actionRow: { flexDirection: "row", gap: 10, width: "100%" },
  actionButton: {
    flex: 1, minHeight: 52, paddingVertical: 10, borderRadius: 14,
    alignItems: "center", justifyContent: "center",
  },
  actionButtonDisabled: { opacity: 0.5 },
  actionButtonSpacer: { flex: 1 },
  actionButtonText: { color: "#fff", fontSize: 15, fontWeight: "600", textAlign: "center" },
  gotItButton: { backgroundColor: C.good },
  continueButton: { backgroundColor: C.accent },
  showButton: { backgroundColor: C.accent },
  showButtonText: { color: "#fff", fontSize: 15, fontWeight: "600", textAlign: "center" },
  noIdeaButton: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.noIdea + "55",
  },
  noIdeaButtonText: { color: C.noIdea, fontSize: 15, fontWeight: "600", textAlign: "center" },
  button: {
    backgroundColor: C.accent, paddingHorizontal: 24, paddingVertical: 12,
    borderRadius: 4, marginTop: 16,
  },
  buttonGhost: {
    backgroundColor: "transparent", borderWidth: 1, borderColor: C.accent,
  },
  buttonText: { color: C.bg, fontWeight: "600" },
  buttonGhostText: { color: C.accent },
  errorText: { color: C.missed, marginBottom: 16 },
  emptyTitle: { color: C.text, fontSize: 20, marginTop: 16, fontWeight: "600" },
  emptySubtitle: { color: C.textDim, marginTop: 8 },
  statsRow: { flexDirection: "row", marginTop: 24, gap: 24 },
  stat: { alignItems: "center" },
  statValue: { color: C.text, fontSize: 24, fontWeight: "600" },
  statLabel: { color: C.textDim, fontSize: 12, marginTop: 4 },
});
