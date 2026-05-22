/**
 * Polyglot lemma detail screen — Modern Editorial.
 *
 * The full philological deep-dive: etymology + morphology callout, vertical
 * diachrony timeline (3-5 stages, color-coded by era), cognates table with
 * language pills, literary quotes with purple left-rule, and a register
 * section with collocations + false-friend warning. iPhone-first column
 * (page H padding = POLYGLOT_SPACING.pageH so the layout breathes at 390px
 * width but stretches gracefully on larger screens).
 *
 * Locked design from 2026-05-21 design-explorer round 2 — see
 * `~/.claude/design-explorer/mockups/alif/mockup-detail-iphone-editorial.html`
 * and the λόγος stress-test variant. Most styling choices map 1:1 from those
 * mockups to React Native; comments only mark places where RN forced a
 * divergence.
 *
 * Empty states: the screen still renders the header (lemma + gloss + chips)
 * even when enrichment is null. Each enrichment section unmounts when its
 * slice is empty rather than rendering a skeleton — a missing diachrony list
 * usually means the cron just hasn't enriched this lemma yet.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  PanResponder,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { getLemmaDetail, LemmaDetail } from "../../lib/polyglot-api";
import {
  POLYGLOT_COLORS,
  POLYGLOT_RADIUS,
  POLYGLOT_SPACING,
  POLYGLOT_TYPE,
  eraColor,
} from "../../lib/polyglot-design-colors";
import { POLYGLOT_FONTS } from "../../lib/polyglot-design-tokens";

const SCREEN_WIDTH = Dimensions.get("window").width;
const SWIPE_BACK_THRESHOLD = 90; // px of rightward drag that commits to "back"

export default function PolyglotLemmaDetailScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const lemmaId = Number(params.id);

  const [detail, setDetail] = useState<LemmaDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    getLemmaDetail(lemmaId)
      .then((d) => {
        if (alive) setDetail(d);
      })
      .catch((e) => {
        if (alive) setError(String(e?.message ?? e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [lemmaId]);

  const goBack = useCallback(() => {
    if (router.canGoBack()) router.back();
    else router.replace("/polyglot");
  }, [router]);
  const goBackRef = useRef(goBack);
  goBackRef.current = goBack;

  // Swipe-right-to-go-back. This screen lives in the Tabs navigator (not a
  // native stack), so it gets no built-in edge-swipe gesture — we add one with
  // PanResponder, mirroring the horizontal-pan pattern in WordInfoCard. Only a
  // clearly-horizontal rightward drag is captured, so vertical scrolling of the
  // philology body still works. translateX is reset whenever the lemma changes
  // so a tab that's reused (rather than remounted) doesn't stay slid off-screen.
  const translateX = useRef(new Animated.Value(0)).current;
  useEffect(() => { translateX.setValue(0); }, [lemmaId, translateX]);
  const panResponder = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_, g) =>
        g.dx > 12 && Math.abs(g.dx) > Math.abs(g.dy) * 2,
      onMoveShouldSetPanResponderCapture: (_, g) =>
        g.dx > 20 && Math.abs(g.dx) > Math.abs(g.dy) * 3,
      onPanResponderMove: (_, g) => {
        if (g.dx > 0) translateX.setValue(g.dx);
      },
      // Don't relinquish the gesture once we've claimed it.
      onPanResponderTerminationRequest: () => false,
      onPanResponderRelease: (_, g) => {
        if (g.dx > SWIPE_BACK_THRESHOLD) {
          Animated.timing(translateX, {
            toValue: SCREEN_WIDTH,
            duration: 160,
            useNativeDriver: true,
          }).start(() => {
            goBackRef.current();
            // Reset for the reuse case: if this tab is kept alive (not
            // remounted) and the same lemma is reopened, the lemmaId effect
            // won't refire, so leave translateX at 0 rather than off-screen.
            translateX.setValue(0);
          });
        } else {
          Animated.spring(translateX, {
            toValue: 0,
            useNativeDriver: true,
            tension: 120,
            friction: 9,
          }).start();
        }
      },
      // If the gesture is interrupted mid-drag (release never fires), spring the
      // half-dragged screen back into place instead of leaving it offset.
      onPanResponderTerminate: () => {
        Animated.spring(translateX, {
          toValue: 0,
          useNativeDriver: true,
          tension: 120,
          friction: 9,
        }).start();
      },
    }),
  ).current;

  if (loading) {
    return (
      <SafeAreaView style={styles.loadingWrap} edges={["top"]}>
        <Stack.Screen options={{ headerShown: false }} />
        <ActivityIndicator size="small" color={POLYGLOT_COLORS.accent} />
      </SafeAreaView>
    );
  }

  if (error || !detail) {
    return (
      <Animated.View
        style={[styles.root, { transform: [{ translateX }] }]}
        {...panResponder.panHandlers}
      >
        <SafeAreaView style={styles.loadingWrap} edges={["top"]}>
          <Stack.Screen options={{ headerShown: false }} />
          <View style={styles.topbar}>
            <Pressable onPress={goBack} hitSlop={10}>
              <Text style={styles.backLink}>‹ Back</Text>
            </Pressable>
          </View>
          <View style={{ padding: 24, alignItems: "center", gap: 8 }}>
            <Text style={{ color: POLYGLOT_COLORS.text, fontSize: 16 }}>
              Couldn't load lemma {lemmaId}.
            </Text>
            {error ? (
              <Text style={{ color: POLYGLOT_COLORS.textSecondary, fontSize: 12 }}>
                {error}
              </Text>
            ) : null}
          </View>
        </SafeAreaView>
      </Animated.View>
    );
  }

  const enrichment = detail.enrichment;
  const etymology = enrichment?.etymology;
  const diachrony = enrichment?.diachrony ?? [];
  const cognates = enrichment?.cognates ?? [];
  const quotes = enrichment?.quotes ?? [];
  const register = enrichment?.register;

  // Ancient form: prefer the cognate Lemma's form (cognate_lemma_form), fall
  // back to etymology.ancient_form. Both can be present; cognate is more
  // authoritative because it's a DB link, not LLM output.
  const ancientForm = detail.cognate_lemma_form ?? etymology?.ancient_form ?? null;

  return (
    <Animated.View
      style={[styles.root, { transform: [{ translateX }] }]}
      {...panResponder.panHandlers}
    >
    <SafeAreaView style={styles.root} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.topbar}>
        <Pressable onPress={goBack} hitSlop={10}>
          <Text style={styles.backLink}>‹ Back</Text>
        </Pressable>
        <Text style={styles.breadcrumb}>
          {detail.language_code === "el" ? "Modern Greek" : detail.language_code} · lemma · #{detail.lemma_id}
        </Text>
      </View>

      <ScrollView contentContainerStyle={styles.scrollBody} showsVerticalScrollIndicator={false}>
        {/* Header — always renders, even when enrichment is null */}
        <View style={styles.head}>
          <View style={styles.wordRow}>
            <Text style={styles.greekForm}>{detail.lemma_form}</Text>
          </View>
          {detail.gloss_en ? (
            <Text style={styles.gloss}>{detail.gloss_en}</Text>
          ) : null}
          <View style={styles.chips}>
            {detail.pos ? <Chip kind="pos" label={detail.pos} /> : null}
            {ancientForm ? <Chip kind="cog" label="Ancient" greek={ancientForm} /> : null}
            {etymology?.pie_root ? <Chip kind="pie" label={etymology.pie_root} /> : null}
            {detail.frequency_rank != null ? (
              <Chip kind="freq" label={`freq #${detail.frequency_rank}`} />
            ) : null}
          </View>
        </View>

        {/* Etymology */}
        {etymology ? (
          <Section accent={POLYGLOT_COLORS.etymology} title="Etymology">
            <Text style={styles.etyProse}>{etymology.origin_note}</Text>
            {etymology.morphology ? (
              <View style={styles.morph}>
                <Text style={styles.morphText}>{etymology.morphology}</Text>
              </View>
            ) : null}
          </Section>
        ) : null}

        {/* Diachrony — vertical timeline */}
        {diachrony.length > 0 ? (
          <Section
            accent={POLYGLOT_COLORS.etymology}
            title="Across time"
            count={`${diachrony.length} stages`}
          >
            <View style={styles.timeline}>
              {/* Vertical rail — extends from first to last dot. */}
              <View style={styles.timelineRail} />
              {diachrony.map((stage, idx) => (
                <View key={`stage-${idx}`} style={styles.stage}>
                  <View
                    style={[
                      styles.stageDot,
                      { backgroundColor: eraColor(stage.era), borderColor: POLYGLOT_COLORS.bg },
                    ]}
                  />
                  <View style={styles.stageBody}>
                    <Text style={[styles.stageEra, { color: eraColor(stage.era) }]}>
                      {stage.era}
                    </Text>
                    <Text style={styles.stageForm}>{stage.form}</Text>
                    <Text style={styles.stageMeaning}>{stage.meaning}</Text>
                    {stage.note ? <Text style={styles.stageNote}>{stage.note}</Text> : null}
                  </View>
                </View>
              ))}
            </View>
          </Section>
        ) : null}

        {/* Cognates */}
        {cognates.length > 0 ? (
          <Section
            accent={POLYGLOT_COLORS.cognate}
            title="Cognates"
            count={`${cognates.length} across ${countLanguages(cognates)} language${countLanguages(cognates) === 1 ? "" : "s"}`}
          >
            {cognates.map((cog, idx) => (
              <View key={`cog-${idx}`} style={[styles.cogRow, idx === cognates.length - 1 && { borderBottomWidth: 0 }]}>
                <View style={styles.cogLeft}>
                  <Text
                    style={[
                      styles.cogForm,
                      isGreekLatin(cog.language) && {
                        fontFamily: POLYGLOT_FONTS.greekDisplay,
                        fontSize: POLYGLOT_TYPE.cogGreek,
                      },
                    ]}
                  >
                    {cog.form}
                  </Text>
                  {cog.note ? <Text style={styles.cogNote}>{cog.note}</Text> : null}
                </View>
                <View style={styles.cogTag}>
                  <View style={styles.cogLangPill}>
                    <Text style={styles.cogLangText}>{shortLang(cog.language)}</Text>
                  </View>
                  <Text style={styles.cogRel}>{shortRel(cog.relation)}</Text>
                </View>
              </View>
            ))}
          </Section>
        ) : null}

        {/* Quotes */}
        {quotes.length > 0 ? (
          <Section
            accent={POLYGLOT_COLORS.quote}
            title="In the literature"
            count={`${quotes.length} attestation${quotes.length === 1 ? "" : "s"}`}
          >
            {quotes.map((q, idx) => (
              <View key={`q-${idx}`} style={styles.quoteCard}>
                <Text style={styles.qtext}>{q.text}</Text>
                <Text style={styles.qtrans}>"{q.translation_en}"</Text>
                <View style={styles.qsourceRow}>
                  <View
                    style={[
                      styles.eraPill,
                      { backgroundColor: eraColor(q.era) + "22", borderColor: eraColor(q.era) },
                    ]}
                  >
                    <Text style={[styles.eraPillText, { color: eraColor(q.era) }]}>
                      {q.era}
                    </Text>
                  </View>
                  <Text style={styles.qsource}>{q.source}</Text>
                </View>
              </View>
            ))}
          </Section>
        ) : null}

        {/* Register / modern usage */}
        {register ? (
          <Section
            accent={POLYGLOT_COLORS.accent}
            title="Modern usage"
            count={register.formality ? `${register.formality} register` : undefined}
          >
            {register.usage_note ? (
              <Text style={styles.usageNote}>{register.usage_note}</Text>
            ) : null}
            {register.collocations.length > 0 ? (
              <View style={styles.collList}>
                {register.collocations.map((c) => (
                  <View key={c} style={styles.collPill}>
                    <Text style={styles.collText}>{c}</Text>
                  </View>
                ))}
              </View>
            ) : null}
            {register.false_friends_en.length > 0 ? (
              <View style={styles.ffNote}>
                <Text style={styles.ffNoteText}>
                  ⚠ <Text style={styles.ffStrong}>False friend{register.false_friends_en.length > 1 ? "s" : ""}:</Text>{" "}
                  {register.false_friends_en.join(", ")}.
                </Text>
              </View>
            ) : null}
          </Section>
        ) : null}

        {/* Empty-state hint when no enrichment yet */}
        {!enrichment ? (
          <View style={styles.emptyHint}>
            <Text style={styles.emptyHintText}>
              No philology yet for this lemma — it will appear after the next enrichment pass.
            </Text>
          </View>
        ) : null}
      </ScrollView>
    </SafeAreaView>
    </Animated.View>
  );
}

function Section({
  title,
  count,
  accent,
  children,
}: {
  title: string;
  count?: string;
  accent: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionLabel}>
        <Text style={[styles.sectionLabelText, { color: accent }]}>{title}</Text>
        {count ? <Text style={styles.sectionCount}>{count}</Text> : null}
      </View>
      {children}
    </View>
  );
}

function Chip({
  kind,
  label,
  greek,
}: {
  kind: "pos" | "cog" | "pie" | "freq";
  label: string;
  greek?: string;
}) {
  const palette = {
    pos: { bg: POLYGLOT_COLORS.surfaceMuted, fg: "#555" },
    cog: { bg: POLYGLOT_COLORS.cognateTint, fg: POLYGLOT_COLORS.cognate },
    pie: { bg: POLYGLOT_COLORS.etymologyTint, fg: POLYGLOT_COLORS.etymology },
    freq: { bg: POLYGLOT_COLORS.surfaceMuted, fg: POLYGLOT_COLORS.textSecondary },
  }[kind];
  return (
    <View style={[styles.chip, { backgroundColor: palette.bg }]}>
      <Text style={[styles.chipText, { color: palette.fg }]}>
        {label}
        {greek ? <Text style={styles.chipGreek}>{" "}{greek}</Text> : null}
      </Text>
    </View>
  );
}

function countLanguages(cogs: { language: string }[]): number {
  return new Set(cogs.map((c) => c.language)).size;
}

function isGreekLatin(language: string): boolean {
  const l = language.toLowerCase();
  return l.startsWith("greek") || l.startsWith("latin") || l === "la" || l === "el";
}

function shortLang(language: string): string {
  const map: Record<string, string> = {
    english: "EN",
    latin: "LA",
    german: "DE",
    french: "FR",
    italian: "IT",
    spanish: "ES",
    russian: "RU",
    sanskrit: "SA",
    greek: "GR",
  };
  const key = language.toLowerCase().split(/[\s(]/)[0];
  return map[key] ?? language.slice(0, 2).toUpperCase();
}

function shortRel(rel: string): string {
  return rel
    .replace(/-/g, " ")
    .replace(/^from greek$/i, "from Greek")
    .replace(/loanword from greek/i, "loanword");
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: POLYGLOT_COLORS.bg },
  loadingWrap: {
    flex: 1,
    backgroundColor: POLYGLOT_COLORS.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  topbar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: POLYGLOT_SPACING.pageH,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: POLYGLOT_COLORS.border,
    backgroundColor: POLYGLOT_COLORS.bg,
  },
  backLink: {
    color: POLYGLOT_COLORS.accent,
    fontSize: POLYGLOT_TYPE.body,
    fontWeight: "500",
  },
  breadcrumb: {
    fontSize: POLYGLOT_TYPE.meta,
    color: POLYGLOT_COLORS.textTertiary,
  },
  scrollBody: {
    paddingBottom: POLYGLOT_SPACING.bottomFloat,
  },

  /* Header */
  head: {
    paddingHorizontal: POLYGLOT_SPACING.pageH,
    paddingTop: 18,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: POLYGLOT_COLORS.border,
  },
  wordRow: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 10,
  },
  greekForm: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: POLYGLOT_TYPE.heroGreek,
    color: POLYGLOT_COLORS.text,
    lineHeight: POLYGLOT_TYPE.heroGreek,
  },
  gloss: {
    fontSize: POLYGLOT_TYPE.gloss,
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontStyle: "italic",
    marginTop: 6,
    color: POLYGLOT_COLORS.text,
  },
  chips: {
    flexDirection: "row",
    gap: 5,
    marginTop: 10,
    flexWrap: "wrap",
  },
  chip: {
    paddingHorizontal: 9,
    paddingVertical: 4,
    borderRadius: POLYGLOT_RADIUS.chip,
  },
  chipText: {
    fontSize: 11,
    fontWeight: "600",
  },
  chipGreek: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontStyle: "italic",
    fontSize: 13,
  },

  /* Sections */
  section: {
    paddingHorizontal: POLYGLOT_SPACING.pageH,
    paddingTop: POLYGLOT_SPACING.sectionV,
    paddingBottom: 4,
    borderBottomWidth: 1,
    borderBottomColor: POLYGLOT_COLORS.border,
  },
  sectionLabel: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  sectionLabelText: {
    fontSize: POLYGLOT_TYPE.micro,
    letterSpacing: 1.6,
    textTransform: "uppercase",
    fontWeight: "700",
  },
  sectionCount: {
    fontSize: POLYGLOT_TYPE.micro,
    color: POLYGLOT_COLORS.textTertiary,
    letterSpacing: 0.4,
  },

  /* Etymology */
  etyProse: {
    fontSize: POLYGLOT_TYPE.body,
    color: POLYGLOT_COLORS.text,
    lineHeight: 22,
  },
  morph: {
    marginTop: 10,
    padding: 10,
    backgroundColor: POLYGLOT_COLORS.surface,
    borderRadius: POLYGLOT_RADIUS.callout,
    borderWidth: 1,
    borderColor: POLYGLOT_COLORS.border,
    alignItems: "center",
  },
  morphText: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: 14,
    color: POLYGLOT_COLORS.textSecondary,
  },

  /* Timeline */
  timeline: {
    position: "relative",
    paddingLeft: 28,
    paddingBottom: 4,
  },
  timelineRail: {
    position: "absolute",
    left: 7,
    top: 6,
    bottom: 6,
    width: 2,
    backgroundColor: POLYGLOT_COLORS.borderStrong,
    opacity: 0.5,
  },
  stage: {
    paddingBottom: 14,
    position: "relative",
  },
  stageDot: {
    position: "absolute",
    left: -25,
    top: 6,
    width: 10,
    height: 10,
    borderRadius: 5,
    borderWidth: 3,
  },
  stageBody: {},
  stageEra: {
    fontSize: POLYGLOT_TYPE.micro,
    fontWeight: "700",
    letterSpacing: 1.4,
    textTransform: "uppercase",
  },
  stageForm: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: POLYGLOT_TYPE.stageForm,
    marginTop: 2,
    color: POLYGLOT_COLORS.text,
  },
  stageMeaning: {
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.text,
    marginTop: 4,
    lineHeight: 19,
  },
  stageNote: {
    fontSize: POLYGLOT_TYPE.meta,
    fontStyle: "italic",
    color: POLYGLOT_COLORS.textSecondary,
    marginTop: 4,
    lineHeight: 17,
  },

  /* Cognates */
  cogRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: POLYGLOT_COLORS.border,
  },
  cogLeft: { flex: 1, minWidth: 0 },
  cogForm: {
    fontSize: POLYGLOT_TYPE.cogForm,
    fontWeight: "600",
    color: POLYGLOT_COLORS.text,
  },
  cogNote: {
    fontSize: POLYGLOT_TYPE.meta,
    color: POLYGLOT_COLORS.textSecondary,
    marginTop: 3,
    lineHeight: 17,
  },
  cogTag: { alignItems: "flex-end", gap: 3 },
  cogLangPill: {
    backgroundColor: POLYGLOT_COLORS.cognateTint,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  cogLangText: {
    fontSize: 9,
    letterSpacing: 1.2,
    textTransform: "uppercase",
    color: POLYGLOT_COLORS.cognate,
    fontWeight: "700",
  },
  cogRel: {
    fontSize: POLYGLOT_TYPE.micro,
    color: POLYGLOT_COLORS.textTertiary,
  },

  /* Quotes */
  quoteCard: {
    backgroundColor: POLYGLOT_COLORS.surface,
    borderLeftWidth: 3,
    borderLeftColor: POLYGLOT_COLORS.quote,
    padding: 10,
    borderRadius: POLYGLOT_RADIUS.callout,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: POLYGLOT_COLORS.border,
  },
  qtext: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: 17,
    fontStyle: "italic",
    color: POLYGLOT_COLORS.text,
    lineHeight: 23,
  },
  qtrans: {
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.textSecondary,
    marginTop: 4,
  },
  qsourceRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 6,
  },
  eraPill: {
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: 4,
    borderWidth: 1,
  },
  eraPillText: {
    fontSize: POLYGLOT_TYPE.micro,
    fontWeight: "700",
    letterSpacing: 0.6,
    textTransform: "uppercase",
  },
  qsource: {
    fontSize: POLYGLOT_TYPE.micro,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    color: POLYGLOT_COLORS.textTertiary,
  },

  /* Register */
  usageNote: {
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.text,
    lineHeight: 19,
    marginBottom: 10,
  },
  collList: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 5,
    marginBottom: 10,
  },
  collPill: {
    backgroundColor: POLYGLOT_COLORS.surface,
    borderWidth: 1,
    borderColor: POLYGLOT_COLORS.border,
    paddingHorizontal: 11,
    paddingVertical: 5,
    borderRadius: POLYGLOT_RADIUS.chip,
  },
  collText: {
    fontFamily: POLYGLOT_FONTS.greekDisplay,
    fontSize: 14,
    color: POLYGLOT_COLORS.text,
  },
  ffNote: {
    padding: 10,
    backgroundColor: POLYGLOT_COLORS.warningTint,
    borderRadius: POLYGLOT_RADIUS.callout,
    marginBottom: 10,
  },
  ffNoteText: {
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.warning,
    lineHeight: 19,
  },
  ffStrong: { fontWeight: "700" },

  emptyHint: {
    padding: 24,
    alignItems: "center",
  },
  emptyHintText: {
    fontSize: POLYGLOT_TYPE.bodySmall,
    color: POLYGLOT_COLORS.textTertiary,
    fontStyle: "italic",
    textAlign: "center",
  },
});
