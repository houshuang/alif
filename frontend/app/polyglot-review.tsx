/**
 * Polyglot sentence-review screen.
 *
 * Ports Alif's sentence-review UX (`frontend/app/index.tsx`, SentenceReadingCard
 * + ReadingActions + handleWordTap + handleSentenceSubmit) to Modern Greek,
 * following `polyglot/CLAUDE.md` § "Ground design and code in Alif".
 *
 * Two-stage reveal: front shows the Greek sentence alone; "Show Translation"
 * flips to the back with the English. On either side the learner can:
 *   - tap a content word to cycle off → missed (red) → confused (yellow) → off
 *   - tap "No idea" → comprehension_signal="no_idea"
 *   - tap the middle button (label "Know All" with no marks → "Continue" with
 *     any marks) → comprehension_signal derived from marks
 *
 * Function words and proper names are tappable for gloss reveal but never
 * accumulate marks — same content-lemma filter as sentence_review_service.
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, Pressable, StyleSheet, ActivityIndicator, ScrollView,
} from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useLanguage } from "../lib/language-context";
import {
  ackExperimentIntro,
  getReviewSession,
  submitSentenceReview,
  getReviewStats,
  type IntroCard,
  type ReviewSessionBundle,
  type SentencePayload,
  type WordRender,
  type AcquisitionStats,
  type ComprehensionSignal,
} from "../lib/polyglot-api";
import {
  buildInterleavedSlots,
  cycleMark,
  deriveSignal,
  emptyMarks,
  generateClientReviewId,
  generateSessionId,
  hasAnyMarks,
  isContentWord,
  lemmaIdsFromMarks,
  markStateAt,
  middleButtonLabel,
  type MarkSets,
  type SessionSlot,
} from "../lib/polyglot-review-helpers";

const C = {
  bg: "#0f0f1a",
  surface: "#1a1a2e",
  border: "#2a2a40",
  text: "#e0e0f0",
  textDim: "#9090a8",
  textMuted: "#6a6a82",
  accent: "#7aa2f7",
  target: "#bb9af7",
  missed: "#c95f6f",
  confused: "#d4a06b",
  good: "#74c096",
  noIdea: "#c95f6f",
};

type CardState = "front" | "back";

export default function PolyglotReview() {
  const router = useRouter();
  const { language } = useLanguage();
  const languageCode = language === "el" ? "el" : "el";

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

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sessionIdRef = useRef<string>(generateSessionId());
  const shownAtRef = useRef<number>(Date.now());
  // Lemmas whose intro card was displayed this UI session, used to suppress
  // re-firing the same card on a prefetch reload before the server's ack has
  // propagated. Mirrors Alif's `alreadyShownIntroLemmaIds` parameter.
  const shownIntroLemmaIdsRef = useRef<Set<number>>(new Set());

  const currentSlot: SessionSlot | undefined = slots[index];
  const currentSentence: SentencePayload | undefined =
    currentSlot?.type === "sentence"
      ? bundle.sentences[currentSlot.sentenceIndex]
      : undefined;
  const currentIntro: IntroCard | undefined =
    currentSlot?.type === "intro"
      ? bundle.intro_cards[currentSlot.introIndex]
      : undefined;

  const loadSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [next, s] = await Promise.all([
        getReviewSession(languageCode, 15),
        getReviewStats(),
      ]);
      sessionIdRef.current = generateSessionId();
      setBundle(next);
      setSlots(
        buildInterleavedSlots(
          next.sentences,
          next.intro_cards,
          shownIntroLemmaIdsRef.current,
        ),
      );
      setStats(s);
      setIndex(0);
      setCardState("front");
      setMarks(emptyMarks());
      setGlossWordIdx(null);
      shownAtRef.current = Date.now();
    } catch (e: any) {
      setError(e?.message ?? "Failed to load review session");
    } finally {
      setLoading(false);
    }
  }, [languageCode]);

  useEffect(() => { void loadSession(); }, [loadSession]);

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

  const advanceCard = useCallback(() => {
    setCardState("back");
    setGlossWordIdx(null);
  }, []);

  const flipToFront = useCallback(() => {
    setCardState("front");
    setGlossWordIdx(null);
  }, []);

  const handleWordTap = useCallback((wordIdx: number) => {
    if (!currentSentence) return;
    const word = currentSentence.words[wordIdx];
    if (!word) return;
    if (isContentWord(word)) {
      setMarks((prev) => cycleMark(prev, wordIdx));
    }
    setGlossWordIdx((cur) => (cur === wordIdx ? null : wordIdx));
  }, [currentSentence]);

  const advanceSlot = useCallback(() => {
    if (index + 1 >= slots.length) {
      void loadSession();
    } else {
      setIndex(index + 1);
      setCardState("front");
      setMarks(emptyMarks());
      setGlossWordIdx(null);
      shownAtRef.current = Date.now();
    }
  }, [index, slots.length, loadSession]);

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
        <Pressable style={styles.button} onPress={loadSession}>
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
        <Pressable style={styles.button} onPress={() => router.back()}>
          <Text style={styles.buttonText}>Back</Text>
        </Pressable>
        <Pressable style={[styles.button, styles.buttonGhost]} onPress={loadSession}>
          <Text style={styles.buttonText}>Refresh</Text>
        </Pressable>
      </View>
    );
  }

  if (currentIntro) {
    return (
      <ScrollView contentContainerStyle={styles.root}>
        <View style={styles.header}>
          <Text style={styles.progress}>
            {index + 1} / {slots.length}
          </Text>
          <Text style={styles.stateBadge}>
            {currentIntro.intro_kind === "rescue" ? "rescue card" : "new word"}
          </Text>
        </View>
        <IntroCardView card={currentIntro} />
        <View style={styles.actionRow}>
          <Pressable
            style={[styles.actionButton, styles.gotItButton]}
            onPress={handleIntroContinue}
          >
            <Text style={styles.actionButtonText}>Got it — continue</Text>
          </Pressable>
        </View>
      </ScrollView>
    );
  }

  if (!currentSentence) {
    // Defensive: a slot pointed at a sentence index we don't have. Treat as
    // end-of-session rather than crash; loadSession() resets.
    return (
      <View style={styles.center}>
        <Pressable style={styles.button} onPress={loadSession}>
          <Text style={styles.buttonText}>Refresh</Text>
        </Pressable>
      </View>
    );
  }

  const hasMarks = hasAnyMarks(marks);
  const glossWord = glossWordIdx != null ? currentSentence.words[glossWordIdx] : null;

  return (
    <ScrollView contentContainerStyle={styles.root}>
      <View style={styles.header}>
        <Text style={styles.progress}>
          {index + 1} / {slots.length}
        </Text>
        <Text style={styles.stateBadge}>
          {currentSentence.selection_reason || "review"}
        </Text>
      </View>

      <View style={styles.card}>
        <SentenceCard
          payload={currentSentence}
          cardState={cardState}
          marks={marks}
          onWordTap={handleWordTap}
          glossWordIdx={glossWordIdx}
        />
        {glossWord ? <GlossLine word={glossWord} /> : null}
      </View>

      <ReadingActions
        cardState={cardState}
        hasMarks={hasMarks}
        onAdvance={advanceCard}
        onFlipBack={flipToFront}
        onSubmit={handleSubmit}
        submitting={submitting}
      />

      {submitting && (
        <ActivityIndicator color={C.accent} style={{ marginTop: 16 }} />
      )}
    </ScrollView>
  );
}

function IntroCardView({ card }: { card: IntroCard }) {
  const isRescue = card.intro_kind === "rescue";
  return (
    <View style={styles.introCard}>
      <Text style={styles.introHeading}>
        {isRescue ? "Let's revisit this word" : "New word"}
      </Text>
      <Text style={styles.introLemma}>{card.lemma_form}</Text>
      {card.pos ? <Text style={styles.introPos}>{card.pos}</Text> : null}
      <Text style={styles.introGloss}>{card.gloss_en ?? "(no gloss)"}</Text>
      {card.cognate_lemma_form ? (
        <Text style={styles.introCognate}>
          cognate: {card.cognate_lemma_form}
        </Text>
      ) : null}
      {isRescue ? (
        <Text style={styles.introHint}>
          You've seen this {card.times_seen} times but it hasn't stuck yet.
        </Text>
      ) : null}
    </View>
  );
}

function SentenceCard({
  payload,
  cardState,
  marks,
  onWordTap,
  glossWordIdx,
}: {
  payload: SentencePayload;
  cardState: CardState;
  marks: MarkSets;
  onWordTap: (idx: number) => void;
  glossWordIdx: number | null;
}) {
  const showAnswer = cardState === "back";
  const words = useMemo(
    () => [...payload.words].sort((a, b) => a.position - b.position),
    [payload.words],
  );

  return (
    <>
      <Text style={styles.sentenceGreek}>
        {words.map((word, i) => {
          const state = markStateAt(marks, i);
          const isContent = isContentWord(word);
          const isFocused = glossWordIdx === i;
          let wordStyle = undefined;
          if (state === "missed") wordStyle = styles.missedWord;
          else if (state === "confused") wordStyle = styles.confusedWord;
          else if (isFocused) wordStyle = styles.focusedWord;
          else if (word.is_target) wordStyle = styles.targetWord;
          else if (!isContent) wordStyle = styles.functionWord;

          return (
            <Text key={`w-${i}`}>
              {i > 0 ? " " : null}
              <Text
                onPress={() => onWordTap(i)}
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

function GlossLine({ word }: { word: WordRender }) {
  const tag = word.is_function_word
    ? "function word"
    : word.is_proper_name
      ? "proper name"
      : word.is_target
        ? "target"
        : word.knowledge_state;
  return (
    <View style={styles.glossLine}>
      <Text style={styles.glossLemma}>
        {word.lemma_form ?? word.surface_form}
      </Text>
      <Text style={styles.glossTag}>{tag}</Text>
      <Text style={styles.glossText}>
        {word.gloss_en ?? "(no gloss)"}
      </Text>
    </View>
  );
}

function ReadingActions({
  cardState,
  hasMarks,
  onAdvance,
  onFlipBack,
  onSubmit,
  submitting,
}: {
  cardState: CardState;
  hasMarks: boolean;
  onAdvance: () => void;
  onFlipBack: () => void;
  onSubmit: (signal: ComprehensionSignal) => Promise<void> | void;
  submitting: boolean;
}) {
  const signal = deriveSignal(hasMarks);
  const middleLabel = middleButtonLabel(hasMarks);

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
            hasMarks ? styles.continueButton : styles.gotItButton,
            submitting && styles.actionButtonDisabled,
          ]}
          onPress={() => void onSubmit(signal)}
          disabled={submitting}
        >
          <Text style={styles.actionButtonText}>{middleLabel}</Text>
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
          hasMarks ? styles.continueButton : styles.gotItButton,
          submitting && styles.actionButtonDisabled,
        ]}
        onPress={() => void onSubmit(signal)}
        disabled={submitting}
      >
        <Text style={styles.actionButtonText}>{middleLabel}</Text>
      </Pressable>
      <Pressable
        style={[styles.actionButton, styles.showButton, submitting && styles.actionButtonDisabled]}
        onPress={onFlipBack}
        disabled={submitting}
      >
        <Text style={styles.showButtonText}>Hide Translation</Text>
      </Pressable>
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
  root: {
    backgroundColor: C.bg, flexGrow: 1, padding: 16, paddingTop: 24,
  },
  center: {
    flex: 1, alignItems: "center", justifyContent: "center",
    backgroundColor: C.bg, padding: 24,
  },
  header: {
    flexDirection: "row", alignItems: "center", marginBottom: 24, gap: 12,
  },
  progress: { color: C.textDim, fontSize: 14, flex: 1 },
  stateBadge: {
    color: C.textDim, fontSize: 12,
    backgroundColor: C.surface, paddingHorizontal: 8, paddingVertical: 4,
    borderRadius: 8, borderColor: C.border, borderWidth: 1,
  },
  card: {
    backgroundColor: C.surface, padding: 24, borderRadius: 16,
    borderWidth: 1, borderColor: C.border, minHeight: 220,
  },
  introCard: {
    backgroundColor: C.surface, padding: 28, borderRadius: 16,
    borderWidth: 1, borderColor: C.target, minHeight: 220,
    alignItems: "center", justifyContent: "center", gap: 14,
  },
  introHeading: { color: C.target, fontSize: 13, letterSpacing: 1.4, textTransform: "uppercase" },
  introLemma: { color: C.text, fontSize: 36, fontWeight: "600" },
  introPos: { color: C.textMuted, fontSize: 13, fontStyle: "italic" },
  introGloss: { color: C.accent, fontSize: 20, textAlign: "center" },
  introCognate: { color: C.textDim, fontSize: 14, marginTop: 4 },
  introHint: { color: C.confused, fontSize: 12, marginTop: 6, textAlign: "center" },
  sentenceGreek: {
    color: C.text, fontSize: 26, lineHeight: 40, textAlign: "center",
  },
  targetWord: { color: C.target, fontWeight: "600" },
  functionWord: { color: C.textMuted },
  focusedWord: { color: C.accent, fontWeight: "600" },
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
  answerSection: { marginTop: 20 },
  answerSectionHidden: { opacity: 0 },
  divider: {
    height: 1, backgroundColor: C.border, marginBottom: 16,
  },
  sentenceEnglish: {
    color: C.textDim, fontSize: 18, lineHeight: 26, textAlign: "center",
  },
  glossLine: {
    marginTop: 16, paddingTop: 12,
    borderTopWidth: 1, borderTopColor: C.border,
    flexDirection: "row", alignItems: "baseline", gap: 8, flexWrap: "wrap",
  },
  glossLemma: { color: C.text, fontSize: 18, fontWeight: "600" },
  glossTag: {
    color: C.textMuted, fontSize: 11,
    backgroundColor: C.bg, paddingHorizontal: 6, paddingVertical: 2,
    borderRadius: 6, overflow: "hidden",
  },
  glossText: { color: C.accent, fontSize: 16, flex: 1 },
  actionRow: {
    flexDirection: "row", marginTop: 24, gap: 8,
  },
  actionButton: {
    flex: 1, paddingVertical: 14, borderRadius: 12,
    alignItems: "center", justifyContent: "center",
    backgroundColor: C.surface, borderWidth: 1.5,
  },
  actionButtonDisabled: { opacity: 0.5 },
  actionButtonText: { color: C.text, fontWeight: "600", fontSize: 14 },
  noIdeaButton: { borderColor: C.noIdea },
  noIdeaButtonText: { color: C.noIdea, fontWeight: "600", fontSize: 14 },
  gotItButton: { borderColor: C.good },
  continueButton: { borderColor: C.accent },
  showButton: { borderColor: C.border },
  showButtonText: { color: C.textDim, fontWeight: "600", fontSize: 14 },
  button: {
    backgroundColor: C.accent, paddingHorizontal: 24, paddingVertical: 12,
    borderRadius: 12, marginTop: 16,
  },
  buttonGhost: {
    backgroundColor: "transparent", borderWidth: 1, borderColor: C.border,
  },
  buttonText: { color: C.text, fontWeight: "600" },
  errorText: { color: C.missed, marginBottom: 16 },
  emptyTitle: { color: C.text, fontSize: 20, marginTop: 16, fontWeight: "600" },
  emptySubtitle: { color: C.textDim, marginTop: 8 },
  statsRow: { flexDirection: "row", marginTop: 24, gap: 24 },
  stat: { alignItems: "center" },
  statValue: { color: C.text, fontSize: 24, fontWeight: "600" },
  statLabel: { color: C.textDim, fontSize: 12, marginTop: 4 },
});
