import React, { useRef } from "react";
import { ActivityIndicator, Animated, PanResponder, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily, ltr } from "../theme";
import { WordLookupResult, ConfusionAnalysis } from "../types";
import { getCefrColor } from "../frequency";
import { getGrammarParticleInfo, GrammarParticleInfo } from "../grammar-particles";
import { bareArabicForm, bestVocalizedDisplayForm, stripArabicDiacritics } from "../arabic-display";
import { showMnemonic } from "../feature-flags";
import { UNTRUSTED_FORM_KEYS, sliceSurface } from "./surface-parts";

export type FocusWordMark = "missed" | "did_not_recognize";

interface WordInfoCardProps {
  loading: boolean;
  surfaceForm: string | null;
  markState: FocusWordMark | null;
  result: WordLookupResult | null;
  showMeaning?: boolean;
  onShowMeaning?: () => void;
  reserveSpace?: boolean;
  onNavigateToDetail?: (lemmaId: number) => void;
  onNavigateToPattern?: (wazn: string) => void;
  onNavigateToRoot?: (rootId: number) => void;
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  surfaceTranslit?: string | null;
  confusionData?: ConfusionAnalysis | null;
  onPickConfusion?: (lemmaId: number) => void;
  pickedConfusionLemmaId?: number | null;
  /** Candidate lists ("Easily confused" / "Sounds similar") are opt-in — the
   *  review screen only reveals them once the learner says they mixed the word
   *  up. Morphology, prefix hints and decomposition are unaffected. */
  showConfusionCandidates?: boolean;
}

function stemWord(w: string): string {
  let s = w.toLowerCase().replace(/[^a-z]/g, "");
  // strip common English suffixes to catch big/bigger/biggest, write/writer/writing etc.
  s = s.replace(/(iest|iest|ness|tion|ing|ers|est|er|ed|ly|s)$/, "");
  return s;
}

function glossStems(gloss: string | null | undefined): Set<string> {
  const raw = (gloss ?? "").toLowerCase().replace(/[()]/g, "");
  const words = raw.split(/[\s,/·]+/).filter((w) => w.length > 2);
  const stems = new Set<string>();
  for (const w of words) {
    const st = stemWord(w);
    if (st.length > 2) stems.add(st);
  }
  return stems;
}

function glossesOverlap(a: string | null | undefined, b: string | null | undefined): boolean {
  const stemsA = glossStems(a);
  const stemsB = glossStems(b);
  if (stemsA.size === 0 || stemsB.size === 0) return false;
  for (const s of stemsA) {
    if (stemsB.has(s)) return true;
  }
  return false;
}

const DIACRITICS_RE = /[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]/g;

/** Split Arabic text into grapheme clusters (base char + following diacritics) */
function splitArabicGraphemes(text: string): string[] {
  const groups: string[] = [];
  let current = "";
  for (const ch of text) {
    if (DIACRITICS_RE.test(ch)) {
      current += ch;
    } else {
      if (current) groups.push(current);
      current = ch;
    }
    // Reset regex lastIndex (global flag)
    DIACRITICS_RE.lastIndex = 0;
  }
  if (current) groups.push(current);
  return groups;
}

/** Render Arabic word with diff positions highlighted */
function HighlightedArabic({
  text,
  diffPositions,
  highlightColor,
  fontSize,
}: {
  text: string;
  diffPositions: Set<number>;
  highlightColor: string;
  fontSize?: number;
}) {
  const graphemes = splitArabicGraphemes(text);
  // Strip al-prefix from position mapping if present
  const bare = stripArabicDiacritics(text).replace(/\u0640/g, "");
  const hasAl = bare.startsWith("\u0627\u0644");
  const alOffset = hasAl ? 2 : 0; // al-prefix takes 2 graphemes (alif + lam)

  return (
    <Text style={[cfStyles.highlightedWord, fontSize ? { fontSize, lineHeight: fontSize * 1.5 } : undefined]}>
      {graphemes.map((g, i) => {
        const bareIdx = i - alOffset;
        const isHighlighted = bareIdx >= 0 && diffPositions.has(bareIdx);
        return (
          <Text
            key={i}
            style={[
              cfStyles.highlightedChar,
              isHighlighted ? { color: highlightColor } : cfStyles.dimChar,
            ]}
          >
            {g}
          </Text>
        );
      })}
    </Text>
  );
}
function bareForm(text: string): string {
  return bareArabicForm(text);
}

export default function WordInfoCard({
  loading,
  surfaceForm,
  markState,
  result,
  showMeaning,
  onShowMeaning,
  reserveSpace = true,
  onNavigateToDetail,
  onNavigateToPattern,
  onNavigateToRoot,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
  surfaceTranslit,
  confusionData,
  onPickConfusion,
  pickedConfusionLemmaId,
  showConfusionCandidates = true,
}: WordInfoCardProps) {
  const hasFocus = !!surfaceForm && (markState !== null || !!result || loading);
  const showNav = hasPrev || hasNext;

  // Check if this is a grammar particle
  const particleInfo = surfaceForm ? getGrammarParticleInfo(surfaceForm) : null;

  const translateX = useRef(new Animated.Value(0)).current;
  const SWIPE_THRESHOLD = 50;

  const navRef = useRef({ hasPrev, hasNext, onPrev, onNext });
  navRef.current = { hasPrev, hasNext, onPrev, onNext };

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => false,
      onMoveShouldSetPanResponder: (_, gestureState) => {
        return Math.abs(gestureState.dx) > 15 && Math.abs(gestureState.dx) > Math.abs(gestureState.dy * 2);
      },
      onMoveShouldSetPanResponderCapture: (_, gestureState) => {
        // Capture horizontal swipes so ScrollView doesn't steal them
        return Math.abs(gestureState.dx) > 20 && Math.abs(gestureState.dx) > Math.abs(gestureState.dy * 3);
      },
      onPanResponderTerminationRequest: () => false,
      onPanResponderMove: (_, gestureState) => {
        translateX.setValue(gestureState.dx);
      },
      onPanResponderRelease: (_, gestureState) => {
        const { hasPrev: hp, hasNext: hn, onPrev: op, onNext: on } = navRef.current;
        if (gestureState.dx > SWIPE_THRESHOLD && hp) {
          op?.();
        } else if (gestureState.dx < -SWIPE_THRESHOLD && hn) {
          on?.();
        }
        Animated.spring(translateX, {
          toValue: 0,
          useNativeDriver: true,
          tension: 120,
          friction: 8,
        }).start();
      },
    })
  ).current;

  if (!hasFocus && !loading) {
    return reserveSpace ? <View style={styles.spacer} /> : null;
  }

  const nav = showNav ? (
    <View style={styles.navRow}>
      <Pressable
        onPress={onPrev}
        disabled={!hasPrev}
        hitSlop={12}
        style={[styles.navBtn, !hasPrev && styles.navBtnDisabled]}
      >
        <Ionicons name="chevron-back" size={18} color={hasPrev ? colors.textSecondary : colors.border} />
      </Pressable>
      <Pressable
        onPress={onNext}
        disabled={!hasNext}
        hitSlop={12}
        style={[styles.navBtn, !hasNext && styles.navBtnDisabled]}
      >
        <Ionicons name="chevron-forward" size={18} color={hasNext ? colors.textSecondary : colors.border} />
      </Pressable>
    </View>
  ) : null;
  const yellow = markState === "did_not_recognize";

  return (
    <Animated.View
      style={[styles.card, { transform: [{ translateX }] }]}
      {...(showNav ? panResponder.panHandlers : {})}
    >
      {loading || particleInfo ? <View style={styles.navFloat}>{nav}</View> : null}
      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="small" color={colors.accent} />
        </View>
      ) : particleInfo ? (
        <GrammarParticleView info={particleInfo} />
      ) : (
        <ScrollView style={{ maxHeight: 280 }} showsVerticalScrollIndicator={true} nestedScrollEnabled>
          <RevealedView
            result={result}
            surfaceForm={surfaceForm}
            markState={markState}
            onNavigateToDetail={onNavigateToDetail}
            onNavigateToPattern={onNavigateToPattern}
            onNavigateToRoot={onNavigateToRoot}
            surfaceTranslit={surfaceTranslit}
            confusionData={confusionData}
            onPickConfusion={yellow ? onPickConfusion : undefined}
            pickedConfusionLemmaId={yellow ? pickedConfusionLemmaId ?? null : null}
            showConfusionCandidates={yellow && showConfusionCandidates}
            nav={nav}
          />
        </ScrollView>
      )}
    </Animated.View>
  );
}


function GrammarParticleView({ info }: { info: GrammarParticleInfo }) {
  return (
    <ScrollView style={{ maxHeight: 160 }} showsVerticalScrollIndicator={false}>
      <View style={styles.particleHeader}>
        <Text style={styles.particleArabic}>{info.ar}</Text>
        <Text style={styles.particleTranslit}>{info.transliteration}</Text>
        <View style={styles.particleCategoryPill}>
          <Text style={styles.particleCategoryText}>{info.category}</Text>
        </View>
      </View>
      <Text style={styles.particleMeaning}>{info.meaning}</Text>
      <Text style={styles.particleDesc}>{info.description}</Text>
      {info.examples.map((ex, i) => (
        <View key={i} style={styles.particleExample}>
          <Text style={styles.particleExAr}>{ex.ar}</Text>
          <Text style={styles.particleExEn}>{ex.en}</Text>
        </View>
      ))}
      <Text style={styles.particleGrammar}>{info.grammar_note}</Text>
    </ScrollView>
  );
}


const FORM_CHIP_LABELS: Record<string, string> = {
  plural: "pl",
  feminine: "f",
  elative: "more",
  present: "pres",
  masdar: "noun",
  active_participle: "doer",
  passive_participle: "done",
  imperative: "imp",
};

const FORM_CHIP_ORDER: Record<string, string[]> = {
  verb: ["present", "masdar", "active_participle"],
  noun: ["plural", "feminine"],
  adj: ["feminine", "plural", "elative"],
  adjective: ["feminine", "plural", "elative"],
};

function lemmaChipLabel(pos: string | null): string {
  const p = (pos || "").toLowerCase();
  if (p === "verb") return "past";
  if (p === "adj" || p === "adjective") return "m";
  return "sg";
}

const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];

/** Verb wazn is stored as "form_5"; show it the way grammars do ("Form V"). */
function patternLabel(wazn: string): string {
  const m = /^form_(\d+)$/i.exec(wazn.trim());
  return m ? `Form ${ROMAN[Number(m[1])] ?? m[1]}` : wazn;
}

function shortGloss(gloss: string | null | undefined): string {
  return (gloss ?? "").split(/[,;]/)[0].trim();
}

const FAMILY_DOT: Record<string, string> = {
  known: colors.gotIt,
  learning: colors.stateLearning,
  acquiring: colors.stateLearning,
  lapsed: colors.stateLearning,
};

function familyRank(state: string): number {
  if (state === "known") return 0;
  if (FAMILY_DOT[state]) return 1;
  return 2;
}

function PanelRow({ label, children, onPress }: { label: React.ReactNode; children: React.ReactNode; onPress?: () => void }) {
  const body = (
    <>
      <View style={rowStyles.labelCol}>
        {typeof label === "string" ? <Text style={rowStyles.label} numberOfLines={1}>{label}</Text> : label}
      </View>
      <View style={rowStyles.value}>{children}</View>
    </>
  );
  return onPress ? (
    <Pressable onPress={onPress} style={({ pressed }) => [rowStyles.row, pressed && { opacity: 0.6 }]}>
      {body}
    </Pressable>
  ) : (
    <View style={rowStyles.row}>{body}</View>
  );
}

function RevealedView({
  result,
  surfaceForm,
  markState,
  onNavigateToDetail,
  onNavigateToPattern,
  onNavigateToRoot,
  surfaceTranslit,
  confusionData,
  onPickConfusion,
  pickedConfusionLemmaId,
  showConfusionCandidates = true,
  nav,
}: {
  result: WordLookupResult | null;
  surfaceForm?: string | null;
  markState: FocusWordMark | null;
  onNavigateToDetail?: (lemmaId: number) => void;
  onNavigateToPattern?: (wazn: string) => void;
  onNavigateToRoot?: (rootId: number) => void;
  surfaceTranslit?: string | null;
  confusionData?: ConfusionAnalysis | null;
  onPickConfusion?: (lemmaId: number) => void;
  pickedConfusionLemmaId?: number | null;
  showConfusionCandidates?: boolean;
  nav?: React.ReactNode;
}) {
  if (!result) return null;

  // The headword is the dictionary form: borrow the sentence's vowels only
  // when the sentence shows that same form.
  const surfaceIsLemma = !!surfaceForm && bareForm(surfaceForm) === bareForm(result.lemma_ar);
  const displayForm = bestVocalizedDisplayForm({
    lemmaAr: result.lemma_ar,
    lemmaTransliteration: result.transliteration,
    surfaceForm: surfaceIsLemma ? surfaceForm : null,
    surfaceTranslit: surfaceIsLemma ? surfaceTranslit : null,
    forms: result.forms_json,
    formsTranslit: result.forms_translit,
  });
  const lemmaAr = displayForm.arabic || result.lemma_ar;
  const lemmaTranslit = displayForm.transliteration || result.transliteration;
  const posLabel = result.pos ? result.pos.replace(/_/g, " ") : null;
  const markColor = markState === "missed" ? colors.missed : colors.confused;

  // Analysis arrives asynchronously; ignore one that belongs to another tapped word.
  const analysis = confusionData && confusionData.surface_form === surfaceForm ? confusionData : null;
  const decomp = analysis?.decomposition ?? null;
  const prefixHint = analysis?.prefix_hint;
  const morphExplanation = analysis?.morphology?.explanation ?? null;
  const similarWords = showConfusionCandidates ? analysis?.similar_words : null;
  const phoneticSimilar = showConfusionCandidates ? analysis?.phonetic_similar : null;

  // Which form of the lemma appears in the sentence: the decomposition's match
  // when there are clitics, otherwise a bare comparison against forms_json.
  const forms = (result.forms_json ?? {}) as Record<string, string | undefined>;
  const decompKey = decomp?.matched_form_key ?? null;
  const trustedDecompKey = decompKey && !UNTRUSTED_FORM_KEYS.has(decompKey) ? decompKey : null;
  // "lemma" = dictionary form; null = the sentence form is not among the chips.
  let currentFormKey: string | null = decomp ? (decompKey ? trustedDecompKey : "lemma") : null;
  if (!decomp && surfaceForm) {
    const surfBare = bareForm(surfaceForm);
    if (surfBare === bareForm(result.lemma_ar)) currentFormKey = "lemma";
    for (const [key, value] of Object.entries(forms)) {
      if (typeof value === "string" && FORM_CHIP_LABELS[key] && bareForm(value) === surfBare) {
        currentFormKey = key;
        break;
      }
    }
  }
  const formChips: { key: string; ar: string; tr: string | null; label: string }[] = [
    { key: "lemma", ar: lemmaAr, tr: lemmaTranslit, label: lemmaChipLabel(result.pos) },
  ];
  const order = FORM_CHIP_ORDER[(result.pos || "").toLowerCase()] ?? ["plural", "feminine"];
  for (const key of order) {
    const val = forms[key];
    if (typeof val === "string" && val) {
      formChips.push({ key, ar: val, tr: result.forms_translit?.[key] ?? null, label: FORM_CHIP_LABELS[key] });
    }
  }
  const hasFormMatch = currentFormKey != null && formChips.some((c) => c.key === currentFormKey);
  const activeChip = hasFormMatch ? currentFormKey : null;

  const parts: { ar: string; en: string }[] = [];
  if (decomp && surfaceForm && (decomp.prefix_clitics.length > 0 || decomp.suffix_clitics.length > 0)) {
    const sliced = sliceSurface(
      surfaceForm,
      decomp.prefix_clitics.map((c) => c.text),
      decomp.suffix_clitics.map((c) => c.text),
    );
    if (sliced) {
      decomp.prefix_clitics.forEach((c, i) => parts.push({ ar: sliced.prefixes[i], en: c.label }));
      const stemLabel = trustedDecompKey ? decomp.matched_form_label : shortGloss(result.gloss_en);
      parts.push({ ar: sliced.stem, en: stemLabel });
      decomp.suffix_clitics.forEach((c, i) => parts.push({ ar: `\u0640${sliced.suffixes[i]}`, en: c.label }));
    }
  }
  const partsText = parts.length === 0
    ? (prefixHint && !prefixHint.is_prefix ? prefixHint.hint_text : !hasFormMatch ? morphExplanation : null)
    : null;

  const patternExamples = (result.pattern_examples ?? [])
    .filter((e) => e.lemma_id !== result.lemma_id && e.knowledge_state === "known")
    .slice(0, 2);

  const family = result.root_family
    .filter((s) => s.lemma_id !== result.lemma_id && !glossesOverlap(s.gloss_en, result.gloss_en))
    .sort((a, b) => familyRank(a.state) - familyRank(b.state))
    .slice(0, 5);

  const note = showMnemonic(result.memory_hooks_json)
    ? result.memory_hooks_json!.mnemonic!
    : result.etymology_json?.derivation ?? null;

  return (
    <View>
      <View style={rowStyles.head}>
        <Pressable
          onPress={onNavigateToDetail ? () => onNavigateToDetail(result.lemma_id) : undefined}
          style={rowStyles.headMain}
          accessibilityRole={onNavigateToDetail ? "link" : undefined}
        >
          <Text style={rowStyles.lemma}>{lemmaAr}</Text>
          <View style={rowStyles.headText}>
            <Text style={rowStyles.gloss} numberOfLines={1}>{result.gloss_en ?? "?"}</Text>
            <Text style={rowStyles.meta} numberOfLines={1}>
              {lemmaTranslit ? <Text style={rowStyles.metaTranslit}>{lemmaTranslit}</Text> : null}
              {posLabel ? ` · ${posLabel}` : ""}
              {result.word_category ? ` · ${result.word_category === "proper_name" ? "name" : "sound"}` : ""}
              {result.cefr_level ? (
                <Text style={{ color: getCefrColor(result.cefr_level), fontWeight: "700" }}>
                  {` · ${result.cefr_level.toUpperCase()}`}
                </Text>
              ) : null}
            </Text>
          </View>
        </Pressable>
        {nav}
      </View>

      {parts.length > 0 && (
        <PanelRow label="parts">
          <View style={rowStyles.partsRow}>
            {parts.map((p, i) => (
              <React.Fragment key={i}>
                {i > 0 && <Text style={rowStyles.plus}>+</Text>}
                <View style={rowStyles.part}>
                  <Text style={[rowStyles.partAr, { color: markColor }]}>{p.ar}</Text>
                  <Text style={rowStyles.partEn} numberOfLines={1}>{p.en}</Text>
                </View>
              </React.Fragment>
            ))}
          </View>
        </PanelRow>
      )}
      {partsText && (
        <PanelRow label="form">
          <Text style={rowStyles.text}>{ltr(partsText)}</Text>
        </PanelRow>
      )}

      {formChips.length > 1 && (
        <PanelRow label="forms">
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={rowStyles.chips}>
            {formChips.map((c) => {
              const active = c.key === activeChip;
              return (
                <View key={c.key} style={[rowStyles.chip, active && rowStyles.chipActive]}>
                  <View style={rowStyles.chipCap}>
                    {c.tr ? <Text style={rowStyles.chipTr} numberOfLines={1}>{c.tr}</Text> : null}
                    <Text style={[rowStyles.chipLabel, active && rowStyles.chipLabelActive]}>{c.label}</Text>
                  </View>
                  <Text style={rowStyles.chipAr}>{c.ar}</Text>
                </View>
              );
            })}
          </ScrollView>
        </PanelRow>
      )}

      {result.wazn && !/^form_1$/i.test(result.wazn) && (
        <PanelRow
          label="pattern"
          onPress={onNavigateToPattern ? () => onNavigateToPattern(result.wazn!) : undefined}
        >
          <Text style={rowStyles.text}>
            {/^form_\d+$/i.test(result.wazn) ? (
              <Text style={rowStyles.waznLatin}>{patternLabel(result.wazn)}</Text>
            ) : (
              <Text style={rowStyles.wazn}>{result.wazn}</Text>
            )}
            {result.wazn_meaning ? `  ${result.wazn_meaning}` : ""}
            {patternExamples.length > 0 ? "  · like " : ""}
            {patternExamples.map((e, i) => (
              <Text key={e.lemma_id}>
                {i > 0 ? ", " : ""}
                <Text style={rowStyles.inlineAr}>{e.lemma_ar}</Text>
              </Text>
            ))}
          </Text>
        </PanelRow>
      )}

      {result.root && (
        <PanelRow label="root">
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={rowStyles.family}>
            <Pressable
              onPress={onNavigateToRoot && result.root_id ? () => onNavigateToRoot(result.root_id!) : undefined}
              style={rowStyles.rootKey}
            >
              <Text style={rowStyles.rootLetters}>{result.root}</Text>
              {result.root_meaning ? <Text style={rowStyles.rootMeaning}>{result.root_meaning}</Text> : null}
            </Pressable>
            {family.length > 0 && <View style={rowStyles.sep} />}
            {family.map((s) => (
              <Pressable
                key={s.lemma_id}
                onPress={onNavigateToDetail ? () => onNavigateToDetail(s.lemma_id) : undefined}
                style={rowStyles.sibling}
              >
                <View style={[rowStyles.dot, { backgroundColor: FAMILY_DOT[s.state] ?? colors.stateEncountered }]} />
                <Text style={rowStyles.siblingAr}>{s.lemma_ar}</Text>
                <Text style={rowStyles.siblingEn}>{shortGloss(s.gloss_en) || "?"}</Text>
              </Pressable>
            ))}
          </ScrollView>
        </PanelRow>
      )}

      {note && (
        <PanelRow label={<Ionicons name="bulb-outline" size={13} color="#c9a54a" />}>
          <Text style={[rowStyles.text, rowStyles.note]} numberOfLines={3}>{ltr(note)}</Text>
        </PanelRow>
      )}

      {/* Confusion analysis — similar words (inline highlighting) */}
      {similarWords && similarWords.length > 0 && (
        <View style={styles.confusionSection}>
          <View style={styles.confusionHeader}>
            <Ionicons name="eye-outline" size={14} color={colors.confused} />
            <Text style={styles.confusionTitle}>Easily confused</Text>
            {onPickConfusion && (
              <Text style={styles.pickHint}>tap one if that's what you thought</Text>
            )}
          </View>
          {similarWords.slice(0, 6).map((sw) => {
            const diffSet = new Set(sw.diff_positions.map(d => d.pos));
            const formHint = sw.matched_form ? `: ${sw.matched_form}` : sw.matched_target ? ` near ${sw.matched_target}` : "";
            const diffHint = sw.diff_positions.length > 0
              ? `has ${sw.diff_positions.slice(0, 2).map(d => d.similar).join(", ")} not ${sw.diff_positions.slice(0, 2).map(d => d.original).join(", ")}`
              : null;
            const hint = sw.match_reason ? `${sw.match_reason}${formHint}` : diffHint;
            const picked = pickedConfusionLemmaId === sw.lemma_id;
            const tappable = !!onPickConfusion;
            const itemContent = (
              <View style={styles.confusionItemRow}>
                <HighlightedArabic
                  text={sw.lemma_ar}
                  diffPositions={diffSet}
                  highlightColor={colors.confused}
                  fontSize={26}
                />
                <View style={styles.confusionTextCol}>
                  <Text style={styles.confusionGloss} numberOfLines={1}>{sw.gloss_en ?? "?"}</Text>
                  {hint && (
                    <Text style={styles.confusionHint} numberOfLines={1}>
                      {hint}
                    </Text>
                  )}
                </View>
                {sw.rasm_distance === 0 && (
                  <View style={styles.dotsPill}>
                    <Text style={styles.dotsPillText}>dots only</Text>
                  </View>
                )}
                {tappable && (
                  <Ionicons
                    name={picked ? "checkmark-circle" : "add-circle-outline"}
                    size={22}
                    color={picked ? colors.confused : "rgba(243, 156, 18, 0.55)"}
                  />
                )}
              </View>
            );
            return tappable ? (
              <Pressable
                key={sw.lemma_id}
                onPress={() => onPickConfusion!(sw.lemma_id)}
                style={({ pressed }) => [
                  styles.confusionItemTappable,
                  picked && styles.confusionItemPicked,
                  pressed && { opacity: 0.6 },
                ]}
                accessibilityRole="button"
              >
                {itemContent}
              </Pressable>
            ) : (
              <View key={sw.lemma_id} style={styles.confusionItem}>
                {itemContent}
              </View>
            );
          })}
        </View>
      )}

      {/* Confusion analysis — phonetic similarity */}
      {phoneticSimilar && phoneticSimilar.length > 0 && (
        <View style={styles.phoneticSection}>
          <View style={styles.confusionHeader}>
            <Ionicons name="ear-outline" size={14} color="#8e44ad" />
            <Text style={[styles.confusionTitle, { color: "#8e44ad" }]}>Sounds similar</Text>
            {onPickConfusion && (
              <Text style={styles.pickHint}>tap one if that's what you thought</Text>
            )}
          </View>
          {phoneticSimilar.slice(0, 3).map((pw) => {
            const picked = pickedConfusionLemmaId === pw.lemma_id;
            const tappable = !!onPickConfusion;
            const itemContent = (
              <View style={styles.phoneticItem}>
                <Text style={styles.phoneticAr}>{pw.lemma_ar}</Text>
                <Text style={styles.confusionGloss} numberOfLines={1}>{pw.gloss_en ?? "?"}</Text>
                {pw.confused_pairs.length > 0 && (
                  <View style={styles.phoneticPairsPill}>
                    <Text style={styles.phoneticPairsText}>{pw.confused_pairs.join(" ")}</Text>
                  </View>
                )}
                {tappable && (
                  <Ionicons
                    name={picked ? "checkmark-circle" : "add-circle-outline"}
                    size={22}
                    color={picked ? "#8e44ad" : "rgba(142, 68, 173, 0.55)"}
                    style={{ marginLeft: "auto" }}
                  />
                )}
              </View>
            );
            return tappable ? (
              <Pressable
                key={pw.lemma_id}
                onPress={() => onPickConfusion!(pw.lemma_id)}
                style={({ pressed }) => [
                  styles.phoneticItemTappable,
                  picked && styles.phoneticItemPicked,
                  pressed && { opacity: 0.6 },
                ]}
                accessibilityRole="button"
              >
                {itemContent}
              </Pressable>
            ) : (
              <View key={pw.lemma_id}>{itemContent}</View>
            );
          })}
        </View>
      )}

    </View>
  );
}

const styles = StyleSheet.create({
  spacer: {
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
    minHeight: 90,
    marginVertical: 6,
  },
  card: {
    width: "100%",
    maxWidth: 500,
    alignSelf: "center",
    minHeight: 56,
    backgroundColor: colors.surface,
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingTop: 6,
    paddingBottom: 4,
    marginVertical: 6,
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  loadingWrap: {
    alignItems: "center",
    justifyContent: "center",
    minHeight: 50,
  },


  /* Grammar particle info */
  particleHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  particleArabic: {
    fontFamily: fontFamily.arabic,
    fontSize: 22,
    color: colors.text,
  },
  particleTranslit: {
    color: colors.textSecondary,
    fontSize: 13,
    fontFamily: fontFamily.translit,
  },
  particleCategoryPill: {
    backgroundColor: "rgba(100, 140, 180, 0.25)",
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  particleCategoryText: {
    color: colors.textSecondary,
    fontSize: 10,
    fontWeight: "600",
  },
  particleMeaning: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "600",
    marginBottom: 4,
  },
  particleDesc: {
    color: colors.textSecondary,
    fontSize: 12,
    lineHeight: 17,
    marginBottom: 6,
  },
  particleExample: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 3,
    paddingLeft: 8,
  },
  particleExAr: {
    fontFamily: fontFamily.arabic,
    fontSize: 14,
    color: colors.text,
  },
  particleExEn: {
    color: colors.textSecondary,
    fontSize: 12,
    fontStyle: "italic",
    flex: 1,
  },
  particleGrammar: {
    color: colors.accent,
    fontSize: 11,
    fontStyle: "italic",
    marginTop: 4,
    lineHeight: 16,
  },




  /* Back/forward nav arrows */
  navRow: {
    flexDirection: "row",
    gap: 6,
    marginLeft: 8,
  },
  navFloat: {
    position: "absolute",
    top: 6,
    right: 6,
    zIndex: 1,
  },
  navBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.surfaceLight,
    alignItems: "center",
    justifyContent: "center",
  },
  navBtnDisabled: {
    opacity: 0.3,
  },





  /* Confusion analysis — similar words */
  confusionSection: {
    backgroundColor: "rgba(243, 156, 18, 0.06)",
    borderRadius: 10,
    borderLeftWidth: 3,
    borderLeftColor: "rgba(243, 156, 18, 0.4)",
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 6,
  },
  confusionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 2,
  },
  confusionTitle: {
    fontSize: 12,
    color: colors.confused,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  confusionItem: {
    paddingBottom: 5,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "rgba(243, 156, 18, 0.12)",
  },
  confusionItemTappable: {
    borderWidth: 1,
    borderColor: "rgba(243, 156, 18, 0.35)",
    borderRadius: 10,
    backgroundColor: "rgba(243, 156, 18, 0.05)",
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  confusionItemPicked: {
    backgroundColor: "rgba(243, 156, 18, 0.18)",
    borderRadius: 8,
    paddingHorizontal: 6,
    paddingTop: 4,
    paddingBottom: 4,
    borderBottomWidth: 0,
  },
  confusionItemRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  pickHint: {
    marginLeft: "auto",
    color: colors.textSecondary,
    fontSize: 10,
    fontStyle: "italic",
  },
  confusionTextCol: {
    flex: 1,
    gap: 1,
  },
  confusionGloss: {
    fontSize: 13,
    color: colors.text,
  },
  dotsPill: {
    backgroundColor: "rgba(243, 156, 18, 0.15)",
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  dotsPillText: {
    fontSize: 9,
    color: colors.confused,
    fontWeight: "700",
    textTransform: "uppercase",
  },
  confusionHint: {
    fontSize: 10,
    color: colors.textSecondary,
    fontFamily: fontFamily.arabic,
  },

  /* Confusion analysis — phonetic similarity */
  phoneticSection: {
    backgroundColor: "rgba(142, 68, 173, 0.06)",
    borderRadius: 10,
    borderLeftWidth: 3,
    borderLeftColor: "rgba(142, 68, 173, 0.4)",
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 6,
  },
  phoneticItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  phoneticItemTappable: {
    borderWidth: 1,
    borderColor: "rgba(142, 68, 173, 0.35)",
    borderRadius: 10,
    backgroundColor: "rgba(142, 68, 173, 0.05)",
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  phoneticItemPicked: {
    backgroundColor: "rgba(142, 68, 173, 0.18)",
    borderRadius: 8,
    paddingHorizontal: 6,
    paddingVertical: 4,
  },
  phoneticAr: {
    fontFamily: fontFamily.arabic,
    fontSize: 18,
    color: colors.text,
    writingDirection: "rtl",
  },
  phoneticPairsPill: {
    backgroundColor: "rgba(142, 68, 173, 0.12)",
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 2,
  },
  phoneticPairsText: {
    fontSize: 10,
    color: "#8e44ad",
    fontWeight: "700",
    fontFamily: fontFamily.arabic,
  },

});

const rowStyles = StyleSheet.create({
  head: {
    flexDirection: "row",
    alignItems: "center",
    paddingBottom: 4,
  },
  headMain: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minWidth: 0,
  },
  lemma: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 30,
    lineHeight: 44,
    writingDirection: "rtl",
  },
  headText: {
    flex: 1,
    minWidth: 0,
  },
  gloss: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "700",
  },
  meta: {
    color: colors.textSecondary,
    fontSize: 11.5,
    marginTop: 1,
  },
  metaTranslit: {
    fontFamily: fontFamily.translit,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    minHeight: 34,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    gap: 10,
  },
  labelCol: {
    width: 54,
    flexShrink: 0,
  },
  label: {
    color: "#6e6e8c",
    fontSize: 9.5,
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  value: {
    flex: 1,
    minWidth: 0,
    paddingVertical: 3,
  },
  text: {
    color: "#c8c8dc",
    fontSize: 12.5,
    lineHeight: 18,
  },
  note: {
    color: "#b4b4c8",
  },
  inlineAr: {
    fontFamily: fontFamily.arabic,
    fontSize: 16,
    color: colors.arabic,
  },
  partsRow: {
    flexDirection: "row-reverse",
    alignSelf: "flex-start",
    alignItems: "center",
    gap: 2,
  },
  part: {
    alignItems: "center",
    paddingHorizontal: 4,
  },
  partAr: {
    fontFamily: fontFamily.arabic,
    fontSize: 20,
    lineHeight: 28,
  },
  partEn: {
    color: "#a8a8c0",
    fontSize: 10.5,
    marginTop: -2,
  },
  plus: {
    color: "#4a4a6a",
    fontSize: 12,
  },
  chips: {
    alignItems: "center",
    gap: 2,
  },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingLeft: 6,
    paddingRight: 7,
    borderRadius: 9,
    borderWidth: 1,
    borderColor: "transparent",
  },
  chipActive: {
    backgroundColor: "#26264c",
    borderColor: "#3c3c6c",
  },
  chipCap: {
    alignItems: "flex-start",
  },
  chipTr: {
    color: "#9090aa",
    fontFamily: fontFamily.translit,
    fontSize: 10,
    lineHeight: 12,
  },
  chipLabel: {
    color: "#7a7a98",
    fontSize: 10.5,
    fontWeight: "600",
    lineHeight: 13,
  },
  chipLabelActive: {
    color: "#b8b8ff",
  },
  chipAr: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 20,
    lineHeight: 30,
  },
  wazn: {
    color: colors.accent,
    fontFamily: fontFamily.arabic,
    fontSize: 17,
  },
  waznLatin: {
    color: colors.accent,
    fontWeight: "600",
  },
  family: {
    alignItems: "center",
    gap: 10,
  },
  rootKey: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  rootLetters: {
    color: "#7cb8ff",
    fontFamily: fontFamily.arabic,
    fontSize: 17,
    lineHeight: 24,
  },
  rootMeaning: {
    color: "#7cb8ff",
    fontSize: 12.5,
  },
  sep: {
    width: 1,
    height: 14,
    backgroundColor: "#33334f",
  },
  sibling: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  siblingAr: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 17,
    lineHeight: 24,
  },
  siblingEn: {
    color: "#c8c8dc",
    fontSize: 12.5,
  },
});

const cfStyles = StyleSheet.create({
  highlightedWord: {
    fontFamily: fontFamily.arabic,
    fontSize: 20,
    writingDirection: "rtl",
    lineHeight: 30,
  },
  highlightedChar: {
    fontFamily: fontFamily.arabic,
  },
  dimChar: {
    color: "rgba(232, 232, 240, 0.25)",
  },
});
