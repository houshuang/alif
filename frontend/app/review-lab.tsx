import { useCallback, useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { colors, fontFamily, fonts } from "../lib/theme";
import WordInfoCard, { FocusWordMark } from "../lib/review/WordInfoCard";
import { WordLookupResult, SentenceWordMeta } from "../lib/types";

/* ── Mock data ─────────────────────────────────────────────── */

const mockWords: SentenceWordMeta[] = [
  { lemma_id: 101, surface_form: "\u0630\u064E\u0647\u064E\u0628\u064E", gloss_en: "went", stability: 12, is_due: true, is_function_word: false, knowledge_state: "known", root: "\u0630 \u0647 \u0628", root_meaning: "going/gold", root_id: 5 },
  { lemma_id: 102, surface_form: "\u0627\u0644\u0648\u064E\u0644\u064E\u062F\u064F", gloss_en: "the boy", stability: 8, is_due: false, is_function_word: false, knowledge_state: "learning", root: "\u0648 \u0644 \u062F", root_meaning: "birth/child", root_id: 6 },
  { lemma_id: null, surface_form: "\u0625\u0650\u0644\u064E\u0649", gloss_en: "to", stability: null, is_due: false, is_function_word: true, knowledge_state: "known", root: null, root_meaning: null, root_id: null },
  { lemma_id: 103, surface_form: "\u0627\u0644\u0645\u064E\u062F\u0652\u0631\u064E\u0633\u064E\u0629\u0650", gloss_en: "the school", stability: 3.2, is_due: true, is_function_word: false, knowledge_state: "learning", root: "\u062F \u0631 \u0633", root_meaning: "study/learn", root_id: 7 },
  { lemma_id: 104, surface_form: "\u0627\u0644\u0643\u064E\u0628\u0650\u064A\u0631\u064E\u0629\u0650", gloss_en: "the big (f)", stability: 15, is_due: false, is_function_word: false, knowledge_state: "known", root: "\u0643 \u0628 \u0631", root_meaning: "greatness/size", root_id: 8 },
];

const mockLookups: Record<number, WordLookupResult> = {
  101: {
    lemma_id: 101,
    lemma_ar: "\u0630\u064E\u0647\u064E\u0628\u064E",
    gloss_en: "to go",
    transliteration: "dhahaba",
    root: "\u0630 \u0647 \u0628",
    root_meaning: "going/gold",
    root_id: 5,
    pos: "verb",
    forms_json: { present: "\u064A\u064E\u0630\u0652\u0647\u064E\u0628\u064F", masdar: "\u0630\u064E\u0647\u064E\u0627\u0628" },
    example_ar: "\u0630\u064E\u0647\u064E\u0628\u064E \u0623\u064E\u062D\u0652\u0645\u064E\u062F\u064F \u0625\u0650\u0644\u064E\u0649 \u0627\u0644\u0633\u0651\u064F\u0648\u0642\u0650",
    example_en: "Ahmed went to the market",
    grammar_details: [],
    root_family: [
      { lemma_id: 101, lemma_ar: "\u0630\u064E\u0647\u064E\u0628\u064E", gloss_en: "to go", pos: "verb", transliteration: "dhahaba", state: "known" },
      { lemma_id: 201, lemma_ar: "\u0630\u064E\u0647\u064E\u0628", gloss_en: "gold", pos: "noun", transliteration: "dhahab", state: "known" },
      { lemma_id: 202, lemma_ar: "\u0645\u064E\u0630\u0652\u0647\u064E\u0628", gloss_en: "doctrine", pos: "noun", transliteration: "madhhab", state: "new" },
    ],
  },
  102: {
    lemma_id: 102,
    lemma_ar: "\u0648\u064E\u0644\u064E\u062F",
    gloss_en: "boy, child",
    transliteration: "walad",
    root: "\u0648 \u0644 \u062F",
    root_meaning: "birth/child",
    root_id: 6,
    pos: "noun",
    forms_json: { plural: "\u0623\u064E\u0648\u0652\u0644\u064E\u0627\u062F", gender: "masculine" },
    example_ar: null,
    example_en: null,
    grammar_details: [],
    root_family: [
      { lemma_id: 102, lemma_ar: "\u0648\u064E\u0644\u064E\u062F", gloss_en: "boy", pos: "noun", transliteration: "walad", state: "learning" },
      { lemma_id: 203, lemma_ar: "\u0648\u064E\u0627\u0644\u0650\u062F", gloss_en: "father", pos: "noun", transliteration: "w\u0101lid", state: "known" },
      { lemma_id: 204, lemma_ar: "\u0648\u064E\u0627\u0644\u0650\u062F\u064E\u0629", gloss_en: "mother", pos: "noun", transliteration: "w\u0101lida", state: "known" },
      { lemma_id: 205, lemma_ar: "\u0645\u064E\u0648\u0652\u0644\u0650\u062F", gloss_en: "birthday", pos: "noun", transliteration: "mawlid", state: "new" },
    ],
  },
  103: {
    lemma_id: 103,
    lemma_ar: "\u0645\u064E\u062F\u0652\u0631\u064E\u0633\u064E\u0629",
    gloss_en: "school",
    transliteration: "madrasa",
    root: "\u062F \u0631 \u0633",
    root_meaning: "study/learn",
    root_id: 7,
    pos: "noun",
    forms_json: { plural: "\u0645\u064E\u062F\u064E\u0627\u0631\u0650\u0633", gender: "feminine" },
    example_ar: null,
    example_en: null,
    grammar_details: [],
    root_family: [
      { lemma_id: 103, lemma_ar: "\u0645\u064E\u062F\u0652\u0631\u064E\u0633\u064E\u0629", gloss_en: "school", pos: "noun", transliteration: "madrasa", state: "learning" },
      { lemma_id: 206, lemma_ar: "\u062F\u064E\u0631\u064E\u0633\u064E", gloss_en: "to study", pos: "verb", transliteration: "darasa", state: "known" },
      { lemma_id: 207, lemma_ar: "\u062F\u064E\u0631\u0652\u0633", gloss_en: "lesson", pos: "noun", transliteration: "dars", state: "known" },
      { lemma_id: 208, lemma_ar: "\u0645\u064F\u062F\u064E\u0631\u0651\u0650\u0633", gloss_en: "teacher", pos: "noun", transliteration: "mudarris", state: "known" },
    ],
  },
  104: {
    lemma_id: 104,
    lemma_ar: "\u0643\u064E\u0628\u0650\u064A\u0631",
    gloss_en: "big, large",
    transliteration: "kab\u012br",
    root: "\u0643 \u0628 \u0631",
    root_meaning: "greatness/size",
    root_id: 8,
    pos: "adj",
    forms_json: { feminine: "\u0643\u064E\u0628\u0650\u064A\u0631\u064E\u0629", plural: "\u0643\u0650\u0628\u064E\u0627\u0631", elative: "\u0623\u064E\u0643\u0652\u0628\u064E\u0631" },
    example_ar: null,
    example_en: null,
    grammar_details: [],
    root_family: [
      { lemma_id: 104, lemma_ar: "\u0643\u064E\u0628\u0650\u064A\u0631", gloss_en: "big", pos: "adj", transliteration: "kab\u012br", state: "known" },
      { lemma_id: 209, lemma_ar: "\u0623\u064E\u0643\u0652\u0628\u064E\u0631", gloss_en: "bigger/biggest", pos: "adj", transliteration: "akbar", state: "known" },
    ],
  },
};

/* ── Word Info Card presets ─────────────────────────────────── */

type CardPresetId = "rich" | "root_gate" | "minimal" | "loading" | "cleared";

interface CardPresetConfig {
  id: CardPresetId;
  label: string;
  surfaceForm: string | null;
  markState: FocusWordMark | null;
  loading: boolean;
  showMeaning: boolean;
  result: WordLookupResult | null;
}

const CARD_PRESETS: CardPresetConfig[] = [
  {
    id: "rich",
    label: "Rich",
    surfaceForm: "\u0644\u0650\u0644\u0652\u0643\u064E\u0644\u0652\u0628\u0650",
    markState: "missed",
    loading: false,
    showMeaning: true,
    result: mockLookups[102],
  },
  {
    id: "root_gate",
    label: "Root Gate",
    surfaceForm: "\u064A\u064E\u0643\u0652\u062A\u064F\u0628\u064F",
    markState: "did_not_recognize",
    loading: false,
    showMeaning: false,
    result: mockLookups[103],
  },
  {
    id: "minimal",
    label: "Minimal",
    surfaceForm: "\u0645\u064E\u0639\u064E",
    markState: "missed",
    loading: false,
    showMeaning: true,
    result: { lemma_id: 3302, lemma_ar: "", gloss_en: "with", transliteration: null, root: null, root_meaning: null, root_id: null, pos: "prep", forms_json: null, example_ar: null, example_en: null, grammar_details: [], root_family: [] },
  },
  {
    id: "loading",
    label: "Loading",
    surfaceForm: "\u0644\u0650\u0643\u064E\u0644\u0652\u0628\u064D",
    markState: "missed",
    loading: true,
    showMeaning: false,
    result: null,
  },
  {
    id: "cleared",
    label: "Cleared",
    surfaceForm: null,
    markState: null,
    loading: false,
    showMeaning: false,
    result: null,
  },
];

/* ── Sentence state presets ─────────────────────────────────── */

type SentencePresetId = "front" | "front_tapped" | "back_clean" | "back_marked";

const SENTENCE_PRESETS: { id: SentencePresetId; label: string }[] = [
  { id: "front", label: "Front" },
  { id: "front_tapped", label: "Front + tapped" },
  { id: "back_clean", label: "Back clean" },
  { id: "back_marked", label: "Back + marked" },
];

/* ── Sentence Review Card Preview ───────────────────────────── */

function SentencePreview() {
  const [presetId, setPresetId] = useState<SentencePresetId>("front");
  const [missedIndices, setMissedIndices] = useState<Set<number>>(new Set());
  const [confusedIndices, setConfusedIndices] = useState<Set<number>>(new Set());
  const [lookupResult, setLookupResult] = useState<WordLookupResult | null>(null);
  const [lookupSurface, setLookupSurface] = useState<string | null>(null);
  const [focusMark, setFocusMark] = useState<FocusWordMark | null>(null);
  const [showMeaning, setShowMeaning] = useState(false);

  const showBack = presetId === "back_clean" || presetId === "back_marked";

  useEffect(() => {
    setMissedIndices(new Set());
    setConfusedIndices(new Set());
    setLookupResult(null);
    setLookupSurface(null);
    setFocusMark(null);
    setShowMeaning(false);

    if (presetId === "front_tapped") {
      setMissedIndices(new Set([3]));
      setFocusMark("missed");
      setLookupSurface(mockWords[3].surface_form);
      setLookupResult(mockLookups[103]);
      setShowMeaning(false); // root gate: has known siblings
    } else if (presetId === "back_marked") {
      setMissedIndices(new Set([1]));
      setConfusedIndices(new Set([3]));
    }
  }, [presetId]);

  const handleWordTap = useCallback((index: number) => {
    const word = mockWords[index];
    if (!word.lemma_id) return;

    const isMissed = missedIndices.has(index);
    const isConfused = confusedIndices.has(index);

    // Cycle: off → missed → confused → off
    if (!isMissed && !isConfused) {
      setMissedIndices((prev) => new Set(prev).add(index));
      setFocusMark("missed");
      setLookupSurface(word.surface_form);
      setLookupResult(mockLookups[word.lemma_id] ?? null);
      setShowMeaning(false);
    } else if (isMissed) {
      setMissedIndices((prev) => { const n = new Set(prev); n.delete(index); return n; });
      setConfusedIndices((prev) => new Set(prev).add(index));
      setFocusMark("did_not_recognize");
      setLookupSurface(word.surface_form);
      setLookupResult(mockLookups[word.lemma_id] ?? null);
      setShowMeaning(false);
    } else {
      setConfusedIndices((prev) => { const n = new Set(prev); n.delete(index); return n; });
      setFocusMark(null);
      setLookupSurface(null);
      setLookupResult(null);
      setShowMeaning(false);
    }
  }, [missedIndices, confusedIndices]);

  const hasMarked = missedIndices.size + confusedIndices.size > 0;

  return (
    <View style={styles.sentencePreview}>
      {/* Preset selector */}
      <View style={styles.rowWrap}>
        {SENTENCE_PRESETS.map((p) => {
          const active = p.id === presetId;
          return (
            <Pressable
              key={p.id}
              style={[styles.chip, active && styles.chipActive]}
              onPress={() => setPresetId(p.id)}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{p.label}</Text>
            </Pressable>
          );
        })}
      </View>

      {/* Simulated review card area */}
      <View style={styles.reviewArea}>
        {/* Progress bar */}
        <View style={styles.progressContainer}>
          <Text style={styles.progressText}>Card 3 of 8</Text>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: "37.5%" }]} />
          </View>
        </View>

        {/* Sentence */}
        <View style={styles.sentenceContent}>
          <Text style={styles.sentenceArabic}>
            {mockWords.map((word, i) => {
              const isMissed = missedIndices.has(i);
              const isConfused = confusedIndices.has(i);
              const wordStyle = isMissed
                ? styles.missedWord
                : isConfused
                  ? styles.confusedWord
                  : undefined;
              const canTap = word.lemma_id != null;
              return (
                <Text key={`t-${i}`}>
                  {i > 0 && " "}
                  <Text
                    onPress={canTap ? () => handleWordTap(i) : undefined}
                    style={wordStyle}
                  >
                    {word.surface_form}
                  </Text>
                </Text>
              );
            })}
          </Text>

          {/* Answer section — always reserve space */}
          <View style={[styles.answerSection, !showBack && styles.answerHidden]}>
            <View style={styles.divider} />
            <Text style={styles.sentenceEnglish}>
              {showBack ? "The boy went to the big school." : " "}
            </Text>
            <Text style={[styles.sentenceTranslit, !showBack && { color: "transparent" }]}>
              dhahaba al-waladu il\u0101 al-madrasati al-kab\u012brati
            </Text>
          </View>
        </View>

        {/* Word info card */}
        <WordInfoCard
          loading={false}
          surfaceForm={lookupSurface}
          markState={focusMark}
          result={lookupResult}
          showMeaning={showMeaning}
          onShowMeaning={() => setShowMeaning(true)}
          reserveSpace={false}
        />

        {/* Action buttons */}
        <View style={styles.actionRow}>
          {!showBack ? (
            <>
              <View style={[styles.actionButton, styles.noIdeaButton]}>
                <Text style={styles.noIdeaButtonText}>No idea</Text>
              </View>
              <View style={[styles.actionButton, styles.gotItButton]}>
                <Text style={styles.actionButtonText}>Know All</Text>
              </View>
              <View style={[styles.actionButton, styles.showButton]}>
                <Text style={styles.showButtonText}>Show Translation</Text>
              </View>
            </>
          ) : (
            <>
              <View style={[styles.actionButton, styles.noIdeaButton]}>
                <Text style={styles.noIdeaButtonText}>No idea</Text>
              </View>
              <View style={[styles.actionButton, hasMarked ? styles.continueButton : styles.gotItButton]}>
                <Text style={styles.actionButtonText}>
                  {hasMarked ? "Continue" : "Know All"}
                </Text>
              </View>
              <View style={styles.actionButtonSpacer} />
            </>
          )}
        </View>
      </View>
    </View>
  );
}

/* ── Main Lab Screen ────────────────────────────────────────── */

export default function ReviewLabScreen() {
  const [tab, setTab] = useState<"sentence" | "card">("sentence");
  const [cardPresetId, setCardPresetId] = useState<CardPresetId>("rich");
  const [showMeaning, setShowMeaning] = useState(true);
  const [markState, setMarkState] = useState<FocusWordMark | null>("missed");

  const cardPreset = useMemo(
    () => CARD_PRESETS.find((p) => p.id === cardPresetId) ?? CARD_PRESETS[0],
    [cardPresetId]
  );

  useEffect(() => {
    setShowMeaning(cardPreset.showMeaning);
    setMarkState(cardPreset.markState);
  }, [cardPreset.id]);

  const isHidden = markState === null || !cardPreset.surfaceForm;

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Review Lab</Text>

      {/* Tab selector */}
      <View style={styles.rowWrap}>
        <Pressable
          style={[styles.chip, tab === "sentence" && styles.chipActive]}
          onPress={() => setTab("sentence")}
        >
          <Text style={[styles.chipText, tab === "sentence" && styles.chipTextActive]}>Sentence Card</Text>
        </Pressable>
        <Pressable
          style={[styles.chip, tab === "card" && styles.chipActive]}
          onPress={() => setTab("card")}
        >
          <Text style={[styles.chipText, tab === "card" && styles.chipTextActive]}>Word Info Card</Text>
        </Pressable>
      </View>

      {tab === "sentence" ? (
        <SentencePreview />
      ) : (
        <>
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Preset</Text>
            <View style={styles.rowWrap}>
              {CARD_PRESETS.map((p) => {
                const active = p.id === cardPresetId;
                return (
                  <Pressable
                    key={p.id}
                    style={[styles.chip, active && styles.chipActive]}
                    onPress={() => setCardPresetId(p.id)}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>{p.label}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Mark state</Text>
            <View style={styles.rowWrap}>
              {(["missed", "did_not_recognize", null] as const).map((ms) => {
                const active = markState === ms;
                const label = ms === "missed" ? "Missed" : ms === "did_not_recognize" ? "Didn't recognize" : "Cleared";
                return (
                  <Pressable
                    key={String(ms)}
                    style={[styles.chip, active && styles.chipActive]}
                    onPress={() => setMarkState(ms)}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          <WordInfoCard
            loading={cardPreset.loading}
            surfaceForm={isHidden ? null : cardPreset.surfaceForm}
            markState={isHidden ? null : markState}
            result={cardPreset.result}
            showMeaning={showMeaning}
            onShowMeaning={() => setShowMeaning(true)}
            reserveSpace={false}
          />
        </>
      )}
    </ScrollView>
  );
}

/* ── Styles ─────────────────────────────────────────────────── */

const styles = StyleSheet.create({
  container: {
    padding: 16,
    paddingBottom: 40,
    backgroundColor: colors.bg,
    minHeight: "100%",
    gap: 12,
  },
  title: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "700",
  },
  section: { gap: 6 },
  sectionLabel: {
    color: colors.textSecondary,
    fontSize: 11,
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  rowWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  chip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
    backgroundColor: colors.surface,
  },
  chipActive: {
    borderColor: colors.accent,
    backgroundColor: colors.accent + "1F",
  },
  chipText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "600",
  },
  chipTextActive: {
    color: colors.accent,
  },

  /* Sentence preview */
  sentencePreview: { gap: 10 },
  reviewArea: {
    backgroundColor: colors.bg,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 14,
    padding: 16,
    gap: 8,
  },
  sentenceContent: {
    alignItems: "center",
    paddingVertical: 12,
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
  answerSection: {
    width: "100%",
    alignItems: "center",
    minHeight: 80,
    justifyContent: "flex-start",
  },
  answerHidden: { opacity: 0 },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    width: "80%",
    marginVertical: 12,
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

  /* Action buttons */
  actionRow: {
    flexDirection: "row",
    gap: 10,
    width: "100%",
  },
  actionButton: {
    flex: 1,
    minHeight: 48,
    paddingVertical: 10,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  actionButtonSpacer: { flex: 1 },
  gotItButton: { backgroundColor: colors.gotIt },
  continueButton: { backgroundColor: colors.accent },
  showButton: { backgroundColor: colors.accent },
  noIdeaButton: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.noIdea + "55",
  },
  actionButtonText: { color: "#fff", fontSize: 15, fontWeight: "600" },
  showButtonText: { color: "#fff", fontSize: 15, fontWeight: "600", textAlign: "center" },
  noIdeaButtonText: { color: colors.noIdea, fontSize: 15, fontWeight: "600", textAlign: "center" },

  /* Progress bar */
  progressContainer: { width: "100%", marginBottom: 4 },
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
});
