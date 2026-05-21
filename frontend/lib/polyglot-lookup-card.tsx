/**
 * Middle-density lemma card for the reader's tap state and the review
 * screen's word-tap state. Shows more than the existing one-line lookup bar
 * (just `surface + gloss`) but far less than the full Modern Editorial detail
 * page — the goal is to answer "what is this word?" in 3-5 seconds, with a
 * "View details ›" link to the full page when curiosity is bigger.
 *
 * Content tiers (each optional, omitted gracefully if data absent):
 *   1. Always: lemma form + gloss + POS
 *   2. Compact diachrony peek: 1-line "Classical X → Modern Y" if available
 *   3. Ancient Greek cognate hint
 *   4. One literary quote (the first one — usually the most famous)
 *   5. Top 2-3 collocations as pills
 *   6. View-details link
 *
 * Mirrors the design vocabulary in polyglot-design-tokens.ts. Used by both
 * polyglot.tsx (reader tap bar) and polyglot-review.tsx (word-tap inline).
 *
 * For Alif's analogue, see frontend/lib/review/WordInfoCard.tsx — much richer
 * because Arabic has root families, wazn patterns, confusion analysis. The
 * polyglot version is intentionally leaner.
 */
import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { LemmaDiachronyStage, LemmaEnrichment, LemmaQuote } from "./polyglot-api";
import {
  POLYGLOT_COLORS,
  POLYGLOT_RADIUS,
  POLYGLOT_SPACING,
  POLYGLOT_TYPE,
  eraColor,
} from "./polyglot-design-colors";
import { POLYGLOT_FONTS } from "./polyglot-design-tokens";

export type LookupCardProps = {
  /** Greek lemma form to display (with monotonic accents). */
  lemmaForm: string;
  /** English gloss. May be null while still loading from the backend. */
  glossEn: string | null;
  /** Part of speech (e.g. "noun (neuter)", "verb"). */
  pos: string | null;
  /** Ancient Greek cognate form (e.g. "ἄλογος" for "άλογο"). */
  ancientForm: string | null;
  /** Optional enrichment payload. When null, the card collapses to the core
   *  head row and just shows the cognate hint if present. */
  enrichment: LemmaEnrichment | null;
  /** Frequency rank for display ("freq #N"). Null = unknown. */
  frequencyRank?: number | null;
  /** Tap-cycle state indicator color (red / yellow / clear). Null = no dot. */
  cycleColor?: string | null;
  /** Surface form actually tapped (e.g. "άλογα" when the lemma is "άλογο").
   *  Shown as a smaller label on the head row so the user can see *what they
   *  tapped* vs. the citation form. */
  surfaceForm?: string | null;
  /** Render a "View details ›" affordance routing to the full detail page. */
  onViewDetails?: () => void;
  /** Close-the-card affordance (× in the head row). */
  onClose?: () => void;
};

export default function PolyglotLookupCard(props: LookupCardProps) {
  const {
    lemmaForm,
    glossEn,
    pos,
    ancientForm,
    enrichment,
    frequencyRank,
    cycleColor,
    surfaceForm,
    onViewDetails,
    onClose,
  } = props;

  const driftPeek = enrichment ? pickDriftPeek(enrichment.diachrony) : null;
  const topQuote: LemmaQuote | null =
    enrichment && enrichment.quotes.length > 0 ? enrichment.quotes[0] : null;
  const topCollocations: string[] = enrichment?.register?.collocations.slice(0, 3) ?? [];
  const falseFriend = enrichment?.register?.false_friends_en?.[0];

  return (
    <View style={styles.card}>
      {/* Head row — always present */}
      <View style={styles.headRow}>
        {cycleColor ? <View style={[styles.cycleDot, { backgroundColor: cycleColor }]} /> : null}
        <Text style={styles.lemma}>{lemmaForm}</Text>
        {surfaceForm && surfaceForm !== lemmaForm ? (
          <Text style={styles.surface}>{surfaceForm}</Text>
        ) : null}
        {pos ? (
          <View style={styles.posPill}>
            <Text style={styles.posText}>{pos}</Text>
          </View>
        ) : null}
        {frequencyRank != null ? (
          <Text style={styles.freq}>#{frequencyRank}</Text>
        ) : null}
        {onClose ? (
          <Pressable onPress={onClose} hitSlop={10} style={styles.close}>
            <Text style={styles.closeText}>×</Text>
          </Pressable>
        ) : null}
      </View>

      {/* Gloss — appears below the head row on its own line for breathing room */}
      {glossEn ? <Text style={styles.gloss}>{glossEn}</Text> : null}

      {/* Ancient Greek cognate chip + drift peek live on the same row */}
      {ancientForm || driftPeek ? (
        <View style={styles.metaRow}>
          {ancientForm ? (
            <View style={styles.ancChip}>
              <Text style={styles.ancLabel}>Ancient</Text>
              <Text style={styles.ancForm}>{ancientForm}</Text>
            </View>
          ) : null}
          {driftPeek ? <Text style={styles.driftPeek}>{driftPeek}</Text> : null}
        </View>
      ) : null}

      {/* One literary quote — the first one. The era pill is colored. */}
      {topQuote ? (
        <View style={styles.quoteBlock}>
          <Text style={styles.quoteText}>{topQuote.text}</Text>
          <Text style={styles.quoteTrans}>"{topQuote.translation_en}"</Text>
          <View style={styles.quoteSourceRow}>
            <View style={[styles.eraPill, { backgroundColor: eraColor(topQuote.era) + "22", borderColor: eraColor(topQuote.era) }]}>
              <Text style={[styles.eraPillText, { color: eraColor(topQuote.era) }]}>
                {topQuote.era}
              </Text>
            </View>
            <Text style={styles.quoteSource}>{topQuote.source}</Text>
          </View>
        </View>
      ) : null}

      {/* Top collocations as serif pills */}
      {topCollocations.length > 0 ? (
        <View style={styles.collRow}>
          {topCollocations.map((c) => (
            <View key={c} style={styles.collPill}>
              <Text style={styles.collText}>{c}</Text>
            </View>
          ))}
        </View>
      ) : null}

      {/* False-friend warning — compact one-liner */}
      {falseFriend ? (
        <Text style={styles.falseFriend}>
          ⚠ False friend: <Text style={styles.falseFriendStrong}>{falseFriend}</Text>
        </Text>
      ) : null}

      {/* View-details affordance — surfaces only when enrichment exists OR the
       *  caller wants to push the page anyway. */}
      {onViewDetails ? (
        <Pressable onPress={onViewDetails} hitSlop={8} style={styles.viewDetails}>
          <Text style={styles.viewDetailsText}>View full philology ›</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

/**
 * Compact diachrony summary: "Classical 'irrational' → Modern 'horse'".
 * Picks first and last stage when there's drift; collapses when stages have
 * the same meaning (no story to tell).
 */
function pickDriftPeek(stages: LemmaDiachronyStage[]): string | null {
  if (stages.length < 2) return null;
  const first = stages[0];
  const last = stages[stages.length - 1];
  if (first.meaning.toLowerCase() === last.meaning.toLowerCase()) return null;
  return `${first.era} "${shortMeaning(first.meaning)}" → ${last.era} "${shortMeaning(last.meaning)}"`;
}

function shortMeaning(m: string): string {
  // Take the leading clause up to the first comma so the peek stays one line
  // on a 390px viewport. Strip trailing whitespace.
  const idx = m.indexOf(",");
  return (idx > 0 ? m.slice(0, idx) : m).trim();
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: POLYGLOT_COLORS.surface,
    borderRadius: POLYGLOT_RADIUS.card,
    borderWidth: 1,
    borderColor: POLYGLOT_COLORS.border,
    padding: 14,
    gap: 8,
  },
  headRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  cycleDot: {
    width: 9,
    height: 9,
    borderRadius: 5,
  },
  lemma: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: 26,
    color: POLYGLOT_COLORS.text,
    lineHeight: 30,
  },
  surface: {
    fontFamily: POLYGLOT_FONTS.greekBody,
    fontSize: POLYGLOT_TYPE.body,
    color: POLYGLOT_COLORS.textTertiary,
    fontStyle: "italic",
  },
  posPill: {
    backgroundColor: POLYGLOT_COLORS.surfaceMuted,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: POLYGLOT_RADIUS.pill,
  },
  posText: {
    fontSize: POLYGLOT_TYPE.micro,
    color: POLYGLOT_COLORS.textSecondary,
    fontWeight: "600",
    letterSpacing: 0.4,
    textTransform: "lowercase",
  },
  freq: {
    fontSize: POLYGLOT_TYPE.micro,
    color: POLYGLOT_COLORS.textTertiary,
  },
  close: {
    marginLeft: "auto",
    width: 22,
    height: 22,
    borderRadius: 11,
    alignItems: "center",
    justifyContent: "center",
  },
  closeText: {
    fontSize: 22,
    color: POLYGLOT_COLORS.textTertiary,
    lineHeight: 22,
  },
  gloss: {
    fontSize: POLYGLOT_TYPE.glossInline,
    color: POLYGLOT_COLORS.text,
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontStyle: "italic",
  },
  metaRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  ancChip: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: POLYGLOT_RADIUS.pill,
    backgroundColor: POLYGLOT_COLORS.cognateTint,
  },
  ancLabel: {
    fontSize: POLYGLOT_TYPE.micro,
    color: POLYGLOT_COLORS.cognate,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  ancForm: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: 16,
    color: POLYGLOT_COLORS.cognate,
    fontStyle: "italic",
  },
  driftPeek: {
    flex: 1,
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.textSecondary,
    fontStyle: "italic",
  },
  quoteBlock: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: POLYGLOT_COLORS.quoteTint,
    borderLeftWidth: 3,
    borderLeftColor: POLYGLOT_COLORS.quote,
    borderRadius: POLYGLOT_RADIUS.callout,
    gap: 4,
  },
  quoteText: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: 17,
    fontStyle: "italic",
    color: POLYGLOT_COLORS.text,
    lineHeight: 22,
  },
  quoteTrans: {
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.textSecondary,
  },
  quoteSourceRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 2,
  },
  eraPill: {
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: POLYGLOT_RADIUS.pill,
    borderWidth: 1,
  },
  eraPillText: {
    fontSize: POLYGLOT_TYPE.micro,
    fontWeight: "700",
    letterSpacing: 0.6,
    textTransform: "uppercase",
  },
  quoteSource: {
    fontSize: POLYGLOT_TYPE.micro,
    color: POLYGLOT_COLORS.textTertiary,
    letterSpacing: 0.3,
  },
  collRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: POLYGLOT_SPACING.chipGap,
  },
  collPill: {
    backgroundColor: POLYGLOT_COLORS.surface,
    borderColor: POLYGLOT_COLORS.border,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: POLYGLOT_RADIUS.chip,
  },
  collText: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: 14,
    color: POLYGLOT_COLORS.text,
  },
  falseFriend: {
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.warning,
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: POLYGLOT_COLORS.warningTint,
    borderRadius: POLYGLOT_RADIUS.pill,
  },
  falseFriendStrong: {
    fontWeight: "700",
  },
  viewDetails: {
    alignSelf: "flex-start",
    paddingTop: 2,
  },
  viewDetailsText: {
    color: POLYGLOT_COLORS.accent,
    fontSize: POLYGLOT_TYPE.bodySmall,
    fontWeight: "600",
  },
});
