import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily } from "../theme";
import { WordLookupResult, WordForms } from "../types";
import { getFrequencyBand, getCefrColor } from "../frequency";
import { getGrammarParticleInfo, GrammarParticleInfo } from "../grammar-particles";

export type FocusWordMark = "missed" | "did_not_recognize";

interface WordInfoCardProps {
  loading: boolean;
  surfaceForm: string | null;
  markState: FocusWordMark | null;
  result: WordLookupResult | null;
  showMeaning: boolean;
  onShowMeaning: () => void;
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
  const knownSiblings = result?.root_family.filter((s) => {
    if (s.lemma_id === result.lemma_id) return false;
    if (s.state !== "known" && s.state !== "learning") return false;
    if (glossesOverlap(s.gloss_en, result.gloss_en)) return false;
    return true;
  }) ?? [];

  const needsReveal = result != null && knownSiblings.length >= 1 && !showMeaning;
  const hasFocus = !!surfaceForm && markState !== null;
  const showNav = hasPrev || hasNext;

  // Check if this is a grammar particle
  const particleInfo = surfaceForm ? getGrammarParticleInfo(surfaceForm) : null;

  if (!hasFocus && !loading) {
    return reserveSpace ? <View style={styles.spacer} /> : null;
  }

  return (
    <View style={styles.card}>
      {showNav && (
        <View style={styles.navRow}>
          <Pressable
            onPress={onPrev}
            disabled={!hasPrev}
            hitSlop={8}
            style={[styles.navBtn, !hasPrev && styles.navBtnDisabled]}
          >
            <Text style={[styles.navIcon, !hasPrev && styles.navIconDisabled]}>‹</Text>
          </Pressable>
          <Pressable
            onPress={onNext}
            disabled={!hasNext}
            hitSlop={8}
            style={[styles.navBtn, !hasNext && styles.navBtnDisabled]}
          >
            <Text style={[styles.navIcon, !hasNext && styles.navIconDisabled]}>›</Text>
          </Pressable>
        </View>
      )}
      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="small" color={colors.accent} />
        </View>
      ) : particleInfo ? (
        <GrammarParticleView info={particleInfo} />
      ) : needsReveal ? (
        <RootGateView
          siblings={knownSiblings}
          root={result?.root ?? null}
          rootMeaning={result?.root_meaning ?? null}
          transliteration={result?.transliteration ?? null}
          onReveal={onShowMeaning}
        />
      ) : (
        <RevealedView result={result} siblings={knownSiblings} onNavigateToDetail={onNavigateToDetail} />
      )}
    </View>
  );
}

function RootGateView({
  siblings,
  root,
  rootMeaning,
  transliteration,
  onReveal,
}: {
  siblings: WordLookupResult["root_family"];
  root: string | null;
  rootMeaning: string | null;
  transliteration: string | null;
  onReveal: () => void;
}) {
  return (
    <View style={styles.gateWrap}>
      {transliteration && (
        <Text style={styles.translitText}>{transliteration}</Text>
      )}
      {root && (
        <View style={styles.rootLine}>
          <Text style={styles.rootLetters}>{root}</Text>
          {rootMeaning && <Text style={styles.rootMeaning}>{rootMeaning}</Text>}
        </View>
      )}

      <Text style={styles.gatePrompt}>You know words from this root:</Text>

      <View style={styles.siblingRow}>
        {siblings.slice(0, 4).map((s) => (
          <View key={s.lemma_id} style={styles.siblingPill}>
            <Text style={styles.siblingAr}>{s.lemma_ar}</Text>
            <Text style={styles.siblingEn} numberOfLines={1}>{s.gloss_en ?? "?"}</Text>
          </View>
        ))}
      </View>

      <Pressable onPress={onReveal} hitSlop={8} style={styles.revealButton}>
        <Text style={styles.revealText}>Show meaning</Text>
      </Pressable>
    </View>
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

function buildFormsText(forms: WordForms, pos: string | null): string | null {
  const parts: string[] = [];
  if (pos === "verb") {
    if (forms.present) parts.push(forms.present);
    if (forms.masdar) parts.push(forms.masdar);
  } else if (pos === "noun") {
    if (forms.plural) parts.push(`pl. ${forms.plural}`);
    if (forms.gender) parts.push(forms.gender);
  } else if (pos === "adj") {
    if (forms.feminine) parts.push(`f. ${forms.feminine}`);
    if (forms.plural) parts.push(`pl. ${forms.plural}`);
    if (forms.elative) parts.push(`elat. ${forms.elative}`);
  }
  return parts.length > 0 ? parts.join(" \u00b7 ") : null;
}

function RevealedView({
  result,
  siblings,
  onNavigateToDetail,
}: {
  result: WordLookupResult | null;
  siblings: WordLookupResult["root_family"];
  onNavigateToDetail?: (lemmaId: number) => void;
}) {
  if (!result) return null;

  const lemmaAr = result.lemma_ar?.trim() || null;
  const posLabel = result.pos ? result.pos.replace(/_/g, " ") : null;
  const hasExample = !!(result.example_ar && result.example_en);
  const formsText = result.forms_json ? buildFormsText(result.forms_json, result.pos) : null;

  // All root family members except self, known first
  const stateOrder: Record<string, number> = { known: 0, learning: 1, new: 2 };
  const sortedFamily = result.root_family
    .filter((s) => s.lemma_id !== result.lemma_id)
    .sort((a, b) => (stateOrder[a.state] ?? 2) - (stateOrder[b.state] ?? 2));

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

      {/* Root info */}
      {result.root && (
        <View style={styles.rootLine}>
          <Text style={styles.rootLetters}>{result.root}</Text>
          {result.root_meaning && <Text style={styles.rootMeaning}>{result.root_meaning}</Text>}
        </View>
      )}

      {/* Root family — all siblings, styled by state */}
      {sortedFamily.length > 0 && (
        <View style={styles.siblingRow}>
          {sortedFamily.slice(0, 5).map((s) => (
            <View
              key={s.lemma_id}
              style={[
                styles.siblingPill,
                s.state === "new" && styles.siblingPillNew,
              ]}
            >
              <Text style={[styles.siblingAr, s.state === "new" && styles.siblingArDim]}>
                {s.lemma_ar}
              </Text>
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

      {/* Example sentence */}
      {hasExample && (
        <View style={styles.exampleWrap}>
          <Text style={styles.exampleAr}>{result.example_ar}</Text>
          <Text style={styles.exampleEn}>{result.example_en}</Text>
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

  /* Forms */
  formsText: {
    color: colors.textSecondary,
    fontSize: 12,
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
  siblingPillNew: {
    opacity: 0.45,
    borderWidth: 1,
    borderColor: colors.border,
    borderStyle: "dashed",
  },
  siblingAr: {
    fontSize: 16,
    fontFamily: fontFamily.arabic,
    color: colors.arabic,
    writingDirection: "rtl",
    lineHeight: 22,
  },
  siblingArDim: {
    color: colors.textSecondary,
  },
  siblingEn: {
    fontSize: 10,
    color: colors.textSecondary,
    flexShrink: 1,
  },

  /* Example sentence */
  exampleWrap: {
    paddingTop: 4,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    gap: 1,
  },
  exampleAr: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 16,
    writingDirection: "rtl",
    lineHeight: 24,
    opacity: 0.85,
  },
  exampleEn: {
    color: colors.textSecondary,
    fontSize: 11,
    lineHeight: 15,
  },

  /* Root gate (prediction mode) */
  gateWrap: {
    gap: 6,
    alignItems: "flex-start",
  },
  gatePrompt: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  revealButton: {
    alignSelf: "flex-start",
  },
  revealText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "600",
  },

  /* Back/forward nav arrows */
  navRow: {
    position: "absolute",
    top: 6,
    right: 6,
    flexDirection: "row",
    gap: 2,
    zIndex: 1,
  },
  navBtn: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: colors.surfaceLight,
    alignItems: "center",
    justifyContent: "center",
  },
  navBtnDisabled: {
    opacity: 0.3,
  },
  navIcon: {
    color: colors.textSecondary,
    fontSize: 16,
    fontWeight: "700",
    lineHeight: 20,
    marginTop: -1,
  },
  navIconDisabled: {
    color: colors.textSecondary,
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
