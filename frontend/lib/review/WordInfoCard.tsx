import { useRef } from "react";
import { ActivityIndicator, Animated, PanResponder, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily } from "../theme";
import { WordLookupResult } from "../types";
import { getFrequencyBand, getCefrColor } from "../frequency";
import { getGrammarParticleInfo, GrammarParticleInfo } from "../grammar-particles";

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
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
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
function stripArabicDiacritics(text: string): string {
  return text.replace(DIACRITICS_RE, "");
}
function bareForm(text: string): string {
  let s = stripArabicDiacritics(text).replace(/\u0640/g, ""); // strip tatweel
  if (s.startsWith("\u0627\u0644")) s = s.slice(2); // strip al-prefix
  return s;
}

const FORM_LABELS: Record<string, string> = {
  plural: "plural",
  feminine: "fem.",
  elative: "comparative",
  present: "present",
  masdar: "verbal noun",
  active_participle: "active part.",
  passive_participle: "passive part.",
  imperative: "imperative",
  past_3fs: "past fem.",
  past_3p: "past pl.",
};

function getFormLabel(
  surfaceForm: string | null,
  lemmaAr: string | null,
  forms: import("../types").WordForms | null,
): string | null {
  if (!surfaceForm || !lemmaAr || !forms) return null;
  const surfBare = bareForm(surfaceForm);
  const lemmaBare = bareForm(lemmaAr);
  if (surfBare === lemmaBare) return null; // same as lemma, no label needed
  for (const [key, value] of Object.entries(forms)) {
    if (!value || typeof value !== "string") continue;
    if (bareForm(value) === surfBare) return FORM_LABELS[key] ?? key;
  }
  return null;
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
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
}: WordInfoCardProps) {
  const hasFocus = !!surfaceForm && markState !== null;
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

  return (
    <Animated.View
      style={[styles.card, { transform: [{ translateX }] }]}
      {...(showNav ? panResponder.panHandlers : {})}
    >
      {showNav && (
        <View style={styles.navRow}>
          <Pressable
            onPress={onPrev}
            disabled={!hasPrev}
            hitSlop={16}
            style={[styles.navBtn, !hasPrev && styles.navBtnDisabled]}
          >
            <Ionicons name="chevron-back" size={18} color={hasPrev ? colors.textSecondary : colors.border} />
          </Pressable>
          <Pressable
            onPress={onNext}
            disabled={!hasNext}
            hitSlop={16}
            style={[styles.navBtn, !hasNext && styles.navBtnDisabled]}
          >
            <Ionicons name="chevron-forward" size={18} color={hasNext ? colors.textSecondary : colors.border} />
          </Pressable>
        </View>
      )}
      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="small" color={colors.accent} />
        </View>
      ) : particleInfo ? (
        <GrammarParticleView info={particleInfo} />
      ) : (
        <RevealedView result={result} surfaceForm={surfaceForm} onNavigateToDetail={onNavigateToDetail} />
      )}
    </Animated.View>
  );
}


function GrammarParticleView({ info }: { info: GrammarParticleInfo }) {
  return (
    <ScrollView style={{ maxHeight: 200 }} showsVerticalScrollIndicator={false}>
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


function RevealedView({
  result,
  surfaceForm,
  onNavigateToDetail,
}: {
  result: WordLookupResult | null;
  surfaceForm?: string | null;
  onNavigateToDetail?: (lemmaId: number) => void;
}) {
  if (!result) return null;

  const lemmaAr = result.lemma_ar?.trim() || null;
  const posLabel = result.pos ? result.pos.replace(/_/g, " ") : null;
  const formLabel = getFormLabel(surfaceForm ?? null, lemmaAr, result.forms_json ?? null);

  // Known/learning siblings only, deduplicate by gloss overlap
  const knownSiblings = result.root_family.filter((s) => {
    if (s.lemma_id === result.lemma_id) return false;
    if (s.state !== "known" && s.state !== "learning") return false;
    if (glossesOverlap(s.gloss_en, result.gloss_en)) return false;
    return true;
  });

  return (
    <View style={styles.revealedWrap}>
      {/* Combined: meaning + lemma + POS */}
      <View style={styles.headRow}>
        {result.gloss_en && (
          <Text style={styles.glossText}>{result.gloss_en}</Text>
        )}
        {lemmaAr && (
          <Text style={styles.lemmaAr}>{lemmaAr}</Text>
        )}
        {result.transliteration && (
          <Text style={styles.translitText}>{result.transliteration}</Text>
        )}
        {posLabel && (
          <View style={styles.posPill}>
            <Text style={styles.posText}>{posLabel}</Text>
          </View>
        )}
        {formLabel && (
          <View style={styles.formPill}>
            <Text style={styles.formPillText}>{formLabel}</Text>
          </View>
        )}
        {result.word_category && (
          <View style={[styles.posPill, { backgroundColor: "rgba(243, 156, 18, 0.2)" }]}>
            <Text style={[styles.posText, { color: colors.confused }]}>
              {result.word_category === "proper_name" ? "Name" : "Sound"}
            </Text>
          </View>
        )}
        {result.cefr_level && (
          <View style={[styles.posPill, { backgroundColor: getCefrColor(result.cefr_level) }]}>
            <Text style={[styles.posText, { color: "#fff", fontWeight: "700" }]}>{result.cefr_level}</Text>
          </View>
        )}
        {result.frequency_rank != null && (
          <Text style={{ color: getFrequencyBand(result.frequency_rank).color, fontSize: 10 }}>
            #{result.frequency_rank.toLocaleString()}
          </Text>
        )}
      </View>

      {/* Pattern decomposition */}
      {result.wazn && (
        <View style={styles.patternLine}>
          <Text style={styles.patternText}>
            {result.wazn}
            {result.wazn_meaning ? ` — ${result.wazn_meaning}` : ""}
          </Text>
          {result.root && (
            <Text style={styles.patternDecomp}>
              {result.wazn} + {result.root}
              {result.root_meaning ? ` (${result.root_meaning})` : ""}
            </Text>
          )}
        </View>
      )}

      {/* Root info (when no pattern already shows it) */}
      {!result.wazn && result.root && (
        <View style={styles.rootLine}>
          <Text style={styles.rootLetters}>{result.root}</Text>
          {result.root_meaning && <Text style={styles.rootMeaning}>{result.root_meaning}</Text>}
        </View>
      )}

      {/* Known root siblings */}
      {knownSiblings.length > 0 && (
        <View style={styles.siblingRow}>
          {knownSiblings.slice(0, 5).map((s) => (
            <View key={s.lemma_id} style={styles.siblingPill}>
              <Text style={styles.siblingAr}>{s.lemma_ar}</Text>
              <Text style={styles.siblingEn} numberOfLines={1}>{s.gloss_en ?? "?"}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Mnemonic */}
      {result.memory_hooks_json?.mnemonic && (
        <View style={styles.mnemonicLine}>
          <Ionicons name="bulb-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.mnemonicSmall} numberOfLines={2}>
            {result.memory_hooks_json.mnemonic}
          </Text>
        </View>
      )}

      {onNavigateToDetail && (
        <Pressable
          onPress={() => onNavigateToDetail(result.lemma_id)}
          hitSlop={8}
          style={styles.detailLink}
        >
          <Text style={styles.detailLinkText}>View details ›</Text>
        </Pressable>
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
    minHeight: 90,
    backgroundColor: colors.surface,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
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

  /* Revealed state */
  revealedWrap: {
    gap: 5,
  },

  /* Combined head row: meaning + lemma + translit + POS */
  headRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  glossText: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "700",
    flexShrink: 1,
  },
  lemmaAr: {
    color: colors.arabic,
    fontSize: 20,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
    lineHeight: 28,
    flexShrink: 1,
  },
  translitText: {
    color: colors.textSecondary,
    fontSize: 13,
    fontStyle: "italic",
    flexShrink: 1,
  },
  posPill: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 6,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  posText: {
    color: colors.textSecondary,
    fontSize: 10,
    fontWeight: "600",
    textTransform: "lowercase",
  },
  formPill: {
    backgroundColor: "rgba(243, 156, 18, 0.15)",
    borderRadius: 6,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  formPillText: {
    color: "#e5a117",
    fontSize: 10,
    fontWeight: "600",
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
    fontStyle: "italic",
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

  /* Root */
  rootLine: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  rootLetters: {
    color: colors.accent,
    fontSize: 15,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
    fontWeight: "600",
  },
  rootMeaning: {
    color: colors.textSecondary,
    fontSize: 12,
    flexShrink: 1,
  },

  /* Root family siblings */
  siblingRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 5,
  },
  siblingPill: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 7,
    paddingVertical: 2,
    paddingHorizontal: 7,
    alignItems: "center",
    flexDirection: "row",
    gap: 5,
    maxWidth: "48%",
  },
  siblingAr: {
    fontSize: 16,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
    lineHeight: 22,
  },
  siblingEn: {
    fontSize: 10,
    color: colors.textSecondary,
    flexShrink: 1,
  },

  /* Pattern decomposition */
  patternLine: {
    gap: 2,
  },
  patternText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "600",
  },
  patternDecomp: {
    color: colors.textSecondary,
    fontSize: 12,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },

  /* Back/forward nav arrows */
  navRow: {
    position: "absolute",
    top: 6,
    right: 6,
    flexDirection: "row",
    gap: 4,
    zIndex: 1,
  },
  navBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.surfaceLight,
    alignItems: "center",
    justifyContent: "center",
  },
  navBtnDisabled: {
    opacity: 0.3,
  },

  /* Mnemonic */
  mnemonicLine: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 4,
    paddingTop: 4,
  },
  mnemonicSmall: {
    color: colors.textSecondary,
    fontSize: 12,
    fontStyle: "italic",
    flex: 1,
    lineHeight: 16,
  },

  /* Detail navigation link */
  detailLink: {
    alignSelf: "flex-end",
    paddingTop: 2,
  },
  detailLinkText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "600",
  },
});
