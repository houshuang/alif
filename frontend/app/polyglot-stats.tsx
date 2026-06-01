/**
 * Polyglot stats — reading-engine dashboard.
 *
 * Organized around ONE goal number — VERIFIED words: lemmas you've actually
 * proven, either by meeting them in real text (clean, un-flagged exposure) or
 * recalling them cold on a card. This is distinct from "assumed known" words
 * (bulk-marked at warm-start, never yet seen) which are shown dimmer.
 *
 * Two kinds of number live here and must never be conflated:
 *   - STATE counts (words): verified / assumed / in-acquisition / unseen.
 *   - ACTIVITY counts (events): reviews, sentences, gaps found, etc.
 * Units are labelled everywhere so "342 reviews" is never read as 342 words.
 *
 * Reading is the primary learning mode, so raw FSRS-review counts are NOT the
 * headline; the verified word count and how it grows are. Layout: a dense
 * editorial grid — verified hero + today, knowledge breakdown, promoted-today,
 * the Leitner + gap-recovery flows, reviews/day (with graduations + gaps
 * overlaid), frequency coverage, and lifetime totals.
 *
 * Language-agnostic: every number comes from the language-scoped /api/stats.
 * See research/experiment-log.md (2026-05-29) and polyglot/CLAUDE.md Hard
 * Invariant 6 for the trust-gradient model this renders.
 */
import { useCallback, useState } from "react";
import {
  View, Text, ScrollView, StyleSheet, ActivityIndicator,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { getLanguageStats, type LanguageStats } from "../lib/polyglot-api";
import { useLanguage } from "../lib/language-context";
import { POLYGLOT_COLORS as P } from "../lib/polyglot-design-colors";
import { POLYGLOT_FONTS } from "../lib/polyglot-design-tokens";

const LANGUAGE_NAMES: Record<string, string> = {
  el: "Modern Greek", grc: "Ancient Greek", la: "Latin",
};
const NATIVE_NAME: Record<string, string> = {
  el: "ελληνικά", grc: "ἑλληνικά", la: "Latina",
};

// Tier / accent colors (built on the canonical polyglot palette).
const C = {
  verified: "#2e7d6b",   // proven
  recall: "#4f9683",     // recalled-cold subset
  assumed: "#e7c9a6",    // marked known, not yet seen
  practice: P.etymology, // in acquisition (orange)
  unseen: "#e7e1d4",     // never encountered
  warning: P.warning,    // gaps
};

const num = (n: number | null | undefined) => (n ?? 0).toLocaleString();

export default function PolyglotStats() {
  const [stats, setStats] = useState<LanguageStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const insets = useSafeAreaInsets();
  const { language } = useLanguage();
  const languageCode = language === "ar" ? "el" : language;

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      setLoading(true);
      setError(null);
      getLanguageStats(languageCode)
        .then((s) => { if (!cancelled) { setStats(s); setLoading(false); } })
        .catch((e) => { if (!cancelled) { setError(String(e)); setLoading(false); } });
      return () => { cancelled = true; };
    }, [languageCode]),
  );

  if (loading && !stats) {
    return (
      <View style={[s.screen, s.center]}>
        <ActivityIndicator color={P.accent} />
      </View>
    );
  }
  if (error || !stats) {
    return (
      <View style={[s.screen, s.center]}>
        <Text style={s.error}>Couldn’t load stats{error ? `\n${error}` : ""}</Text>
      </View>
    );
  }

  const ks = stats.known_summary;
  const rc = stats.recovery;
  const lt = stats.leitner;
  const td = stats.today;
  const ov = stats.overall;

  const verified = ks.fsrs_known + ks.exposure_confirmed;
  const assumed = ks.assumed_unconfirmed;
  const inAcq = lt.total_acquiring;
  const unseen = stats.new;

  // Gap recovery flow: found → in-recovery → closed, with still-open broken out.
  const gapsFound = rc.ever_failed;
  const gapsClosed = rc.graduated_after_failure;
  const gapsOpen = rc.failed_not_yet_recovered;
  const gapsInRecovery = Math.max(0, rc.recovered_once - rc.graduated_after_failure);

  const code = stats.language_code;
  const native = NATIVE_NAME[code] ?? LANGUAGE_NAMES[code] ?? code;

  // Reviews/day: last 7 days, scaled so a one-time import spike doesn't crush
  // the recent days — an outlier (>2.5× the next-highest) is capped + hatched.
  const days = stats.history_14d.slice(-7);
  const sorted = [...days.map((d) => d.reviews)].sort((a, b) => b - a);
  const top = sorted[0] ?? 0;
  const second = sorted[1] ?? 0;
  const outlier = top > 2.5 * second && second > 0;
  const displayMax = Math.max(1, outlier ? second : top);

  return (
    <ScrollView
      style={s.screen}
      contentContainerStyle={[s.content, { paddingTop: insets.top + 16 }]}
    >
      <View style={s.headerRow}>
        <View>
          <Text style={s.eyebrow}>{(LANGUAGE_NAMES[code] ?? code).toUpperCase()}</Text>
          <Text style={s.h1}>{native}</Text>
        </View>
        {td.streak >= 2 && (
          <View style={s.streak}><Text style={s.streakText}>🔥 {td.streak}d streak</Text></View>
        )}
      </View>

      {/* ── Verified hero + Today ─────────────────────────────────────── */}
      <View style={s.row}>
        <Panel style={{ flex: 1 }}>
          <PanelTitle label="Verified words" />
          <Text style={s.heroNum}>{num(verified)}</Text>
          <Text style={s.heroSub}>proven, not assumed</Text>
          <Text style={s.heroCompo}>{num(ks.exposure_confirmed)} read · {num(ks.fsrs_known)} recall</Text>
          {inAcq > 0 && <Text style={s.heroAcq}>+{num(inAcq)} in acquisition</Text>}
        </Panel>
        <Panel style={{ flex: 1 }}>
          <PanelTitle label="Today" />
          <KV label="reviews" value={num(td.reviews)} unit="evt" />
          <KV label="sentences" value={num(td.sentence_reviews)} />
          <KV label="confirmed" labelHint="read" value={`+${num(td.confirmed)}`} unit="wd" valueColor={C.verified} />
          <KV label="graduated" labelHint="recall" value={`+${num(td.graduated)}`} unit="wd" valueColor={C.verified} />
          <KV label="new gaps" value={`+${num(td.marked_unknown)}`} unit="wd" valueColor={C.warning} last />
        </Panel>
      </View>

      {/* ── Knowledge breakdown ───────────────────────────────────────── */}
      <Panel span>
        <PanelTitle label="Knowledge" right={`${num(stats.total_lemmas)} lemmas`} />
        <View style={s.splitbar}>
          {verified > 0 && <View style={{ flex: verified, backgroundColor: C.verified }} />}
          {assumed > 0 && <View style={{ flex: assumed, backgroundColor: C.assumed }} />}
          {inAcq > 0 && <View style={{ flex: inAcq, backgroundColor: C.practice }} />}
          {unseen > 0 && <View style={{ flex: unseen, backgroundColor: C.unseen }} />}
        </View>
        <KVDot color={C.verified} label="Verified — met in text / recalled" value={num(verified)} />
        <KVDot color={C.assumed} label="Assumed known, not yet seen" value={num(assumed)} />
        <KVDot color={C.practice} label="In practice (acquiring)" value={num(inAcq)} />
        <KVDot color={C.unseen} label="Unseen" value={num(unseen)} last />
      </Panel>

      {/* ── Promoted today ────────────────────────────────────────────── */}
      {td.graduated_words.length > 0 && (
        <Panel span>
          <PanelTitle label="Promoted to verified today" />
          <View style={s.chipRow}>
            {td.graduated_words.map((w, i) => (
              <View key={`${w.lemma}-${i}`} style={s.chip}>
                <Text style={s.chipLemma}>{w.lemma}{w.gloss ? <Text style={s.chipGloss}>  {w.gloss}</Text> : null}</Text>
              </View>
            ))}
          </View>
        </Panel>
      )}

      {/* ── Leitner flow ──────────────────────────────────────────────── */}
      <Panel span>
        <PanelTitle label="In practice — Leitner flow" right={`${num(lt.due_now)} due now`} />
        <View style={s.funnel}>
          <FunnelCell n={lt.box_1} label="Box 1 · 4h" color={C.warning} />
          <Sep />
          <FunnelCell n={lt.box_2} label="Box 2 · 1d" color={C.practice} />
          <Sep />
          <FunnelCell n={lt.box_3} label="Box 3 · 3d" color={P.textTertiary} />
          <Sep />
          <FunnelCell n={ov.words_graduated} label="graduated" color={C.verified} />
        </View>
        <Text style={s.flowFoot}>words climb 4h → 1d → 3d, then graduate into verified</Text>
      </Panel>

      {/* ── Gap recovery flow ─────────────────────────────────────────── */}
      {gapsFound > 0 && (
        <Panel span>
          <PanelTitle label="Gaps — flagged unknown, working back" />
          <View style={s.funnel}>
            <FunnelCell n={gapsFound} label="found ever" color={P.text} />
            <Sep />
            <FunnelCell n={gapsInRecovery} label="in recovery" color={C.practice} />
            <Sep />
            <FunnelCell n={gapsClosed} label="closed" color={C.verified} />
            <View style={s.openDivider} />
            <FunnelCell n={gapsOpen} label="still open" color={C.warning} />
          </View>
        </Panel>
      )}

      {/* ── Reviews per day (with graduations + gaps overlay) ──────────── */}
      <Panel span>
        <PanelTitle label="Reviews per day" right="last 7 days" />
        <View style={s.chart}>
          {days.map((d, i) => {
            const isOutlier = outlier && d.reviews === top;
            const h = Math.max(d.reviews > 0 ? 3 : 0, Math.min(1, d.reviews / displayMax) * 80);
            const isToday = i === days.length - 1;
            return (
              <View key={d.date} style={s.dayCol}>
                <View style={s.dayMarks}>
                  {d.graduated > 0 && <Text style={s.markG}>▲{d.graduated}</Text>}
                  {d.gaps_found > 0 && <Text style={s.markX}>✕{d.gaps_found}</Text>}
                </View>
                <View
                  style={[
                    s.dayBar,
                    { height: isOutlier ? 80 : h },
                    isOutlier ? s.dayBarOutlier : isToday ? s.dayBarToday : s.dayBarNormal,
                  ]}
                />
                <Text style={s.dayLab}>{dayLabel(i, days.length)}</Text>
              </View>
            );
          })}
        </View>
        <View style={s.legend}>
          <LegendDot color="#9ec7ba" label="reviews" />
          <LegendDot color={C.verified} label="▲ graduated" />
          <LegendDot color={C.warning} label="✕ new gaps" />
        </View>
        {outlier && (
          <Text style={s.note}>
            The hatched bar is a one-time import/backfill ({num(top)}), not a daily rate.
          </Text>
        )}
      </Panel>

      {/* ── Frequency coverage ────────────────────────────────────────── */}
      {stats.frequency && stats.frequency.bands.length > 0 && (
        <Panel span>
          <PanelTitle label="Frequency coverage" right={stats.frequency.source} />
          {stats.frequency.bands.map((b) => {
            const pct = Math.min(100, b.coverage_pct);
            return (
              <View key={b.top_n} style={{ marginBottom: 9 }}>
                <View style={s.fqTop}>
                  <Text style={s.fqLabel}>Top {num(b.top_n)}</Text>
                  <Text style={s.fqPct}>{num(b.learned)} reached · {b.coverage_pct}%</Text>
                </View>
                <View style={s.fqTrack}>
                  <View style={{ width: `${pct}%`, backgroundColor: C.recall, height: "100%" }} />
                </View>
              </View>
            );
          })}
        </Panel>
      )}

      {/* ── All time ──────────────────────────────────────────────────── */}
      <Panel span>
        <PanelTitle label="All time" />
        <KV label="total reviews" value={num(ov.total_reviews)} />
        <KV
          label="recall accuracy"
          labelHint="real tests only"
          value={ov.recall_accuracy != null ? `${ov.recall_accuracy}%` : "—"}
        />
        <KV label="sentences read" value={num(ov.sentences_read)} />
        <KV label="story pages read" value={num(ov.pages_read)} />
        <KV label="distinct words seen" value={num(ov.words_seen)} />
        <KV label="words graduated" value={num(ov.words_graduated)} />
        <KV
          label="avg reviews to graduate"
          value={ov.avg_reviews_to_graduate != null ? String(ov.avg_reviews_to_graduate) : "—"}
        />
        <KV label="days studied · best streak" value={`${num(ov.study_days)} · ${num(ov.best_streak)}d`} last />
      </Panel>

      {/* ── Texts ─────────────────────────────────────────────────────── */}
      {stats.stories.length > 0 && (
        <Panel span>
          <PanelTitle label="Texts" />
          {stats.stories.map((st) => (
            <View key={st.id} style={s.textRow}>
              <Text style={s.textTitle} numberOfLines={1}>{st.title ?? "Untitled"}</Text>
              <Text style={s.textMeta}>{num(st.viewed_pages)}/{num(st.page_count ?? 0)} read</Text>
            </View>
          ))}
        </Panel>
      )}

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

// ─── Small presentational pieces ───────────────────────────────────────────

function dayLabel(i: number, len: number): string {
  if (i === len - 1) return "today";
  return `−${len - 1 - i}`;
}

function Panel({ children, span, style }: { children: React.ReactNode; span?: boolean; style?: object }) {
  return <View style={[s.panel, span && s.panelSpan, style]}>{children}</View>;
}

function PanelTitle({ label, right }: { label: string; right?: string }) {
  return (
    <View style={s.panelTitleRow}>
      <Text style={s.panelTitle}>{label.toUpperCase()}</Text>
      {right ? <Text style={s.panelTitleRight}>{right}</Text> : null}
    </View>
  );
}

function KV(
  { label, labelHint, value, unit, valueColor, last }:
  { label: string; labelHint?: string; value: string; unit?: string; valueColor?: string; last?: boolean },
) {
  return (
    <View style={[s.kv, last && s.kvLast]}>
      <Text style={s.kvLabel}>
        {label}{labelHint ? <Text style={s.kvHint}>  {labelHint}</Text> : null}
      </Text>
      <Text style={s.kvValue}>
        <Text style={[s.kvNum, valueColor ? { color: valueColor } : null]}>{value}</Text>
        {unit ? <Text style={s.kvUnit}> {unit}</Text> : null}
      </Text>
    </View>
  );
}

function KVDot(
  { color, label, value, last }:
  { color: string; label: string; value: string; last?: boolean },
) {
  return (
    <View style={[s.kv, last && s.kvLast]}>
      <View style={s.kvDotLabel}>
        <View style={[s.kvDot, { backgroundColor: color }]} />
        <Text style={s.kvLabel}>{label}</Text>
      </View>
      <Text style={s.kvNum}>{value}</Text>
    </View>
  );
}

function FunnelCell({ n, label, color }: { n: number; label: string; color: string }) {
  return (
    <View style={s.funnelCell}>
      <Text style={[s.funnelN, { color }]}>{num(n)}</Text>
      <Text style={s.funnelL}>{label}</Text>
    </View>
  );
}
const Sep = () => <Text style={s.sep}>›</Text>;

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <View style={s.legendItem}>
      <View style={[s.legendDot, { backgroundColor: color }]} />
      <Text style={s.legendText}>{label}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: P.bg },
  content: { padding: 13 },
  center: { alignItems: "center", justifyContent: "center" },
  error: { color: P.warning, textAlign: "center", paddingHorizontal: 24 },

  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 12 },
  eyebrow: { fontSize: 9.5, letterSpacing: 1.3, color: P.textTertiary, fontWeight: "600" },
  h1: { fontFamily: POLYGLOT_FONTS.greekDisplay, fontSize: 29, color: P.text, lineHeight: 31 },
  streak: { backgroundColor: "#fbeede", borderRadius: 20, paddingHorizontal: 11, paddingVertical: 5 },
  streakText: { fontSize: 11, color: C.warning, fontWeight: "700" },

  row: { flexDirection: "row", gap: 8 },
  panel: { backgroundColor: P.surface, borderWidth: 1, borderColor: P.border, borderRadius: 12, padding: 13, marginBottom: 8 },
  panelSpan: { },
  panelTitleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 },
  panelTitle: { fontSize: 9.5, letterSpacing: 0.8, color: P.textTertiary, fontWeight: "700" },
  panelTitleRight: { fontSize: 10.5, color: P.textTertiary },

  heroNum: { fontSize: 50, fontWeight: "800", letterSpacing: -2.5, color: C.verified, lineHeight: 46 },
  heroSub: { fontSize: 12, color: P.textSecondary, marginTop: 5 },
  heroCompo: { fontSize: 11.5, color: C.verified, fontWeight: "600", marginTop: 3 },
  heroAcq: { fontSize: 11.5, color: C.practice, fontWeight: "600", marginTop: 2 },

  kv: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 5, borderBottomWidth: 1, borderBottomColor: "#f1ece1" },
  kvLast: { borderBottomWidth: 0 },
  kvLabel: { fontSize: 13, color: P.textSecondary },
  kvHint: { fontSize: 10, color: P.textTertiary },
  kvValue: { },
  kvNum: { fontSize: 14, fontWeight: "700", color: P.text },
  kvUnit: { fontSize: 10, color: P.textTertiary, fontWeight: "400" },
  kvDotLabel: { flexDirection: "row", alignItems: "center", gap: 7, flex: 1 },
  kvDot: { width: 9, height: 9, borderRadius: 2 },

  splitbar: { height: 11, borderRadius: 6, overflow: "hidden", flexDirection: "row", marginBottom: 8 },

  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { backgroundColor: "#eef4f1", borderRadius: 7, paddingHorizontal: 8, paddingVertical: 4 },
  chipLemma: { fontSize: 12.5, color: C.verified, fontFamily: POLYGLOT_FONTS.greekBody },
  chipGloss: { fontSize: 10.5, color: P.textTertiary },

  funnel: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  funnelCell: { flex: 1, alignItems: "center" },
  funnelN: { fontSize: 22, fontWeight: "800", letterSpacing: -0.5 },
  funnelL: { fontSize: 8.5, color: P.textTertiary, textTransform: "uppercase", marginTop: 2, textAlign: "center" },
  sep: { color: "#cbbfa6", fontSize: 15, paddingHorizontal: 1 },
  openDivider: { width: 1, alignSelf: "stretch", backgroundColor: "#efe7d6", marginHorizontal: 4 },
  flowFoot: { fontSize: 10.5, color: P.textTertiary, marginTop: 9, textAlign: "center" },

  chart: { flexDirection: "row", alignItems: "flex-end", gap: 6, height: 110, marginTop: 4 },
  dayCol: { flex: 1, alignItems: "center", justifyContent: "flex-end" },
  dayMarks: { flexDirection: "row", gap: 3, height: 13, alignItems: "flex-end" },
  markG: { fontSize: 9, fontWeight: "700", color: C.verified },
  markX: { fontSize: 9, fontWeight: "700", color: C.warning },
  dayBar: { width: "62%", borderTopLeftRadius: 3, borderTopRightRadius: 3, minHeight: 2 },
  dayBarNormal: { backgroundColor: "#9ec7ba" },
  dayBarToday: { backgroundColor: C.verified },
  dayBarOutlier: { backgroundColor: "#cdbf9f" },
  dayLab: { fontSize: 9, color: P.textTertiary, marginTop: 4 },

  legend: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 8 },
  legendItem: { flexDirection: "row", alignItems: "center" },
  legendDot: { width: 9, height: 9, borderRadius: 2, marginRight: 4 },
  legendText: { fontSize: 10, color: P.textSecondary },
  note: { fontSize: 9.5, color: P.textTertiary, fontStyle: "italic", marginTop: 6 },

  fqTop: { flexDirection: "row", justifyContent: "space-between", marginBottom: 3 },
  fqLabel: { fontSize: 11.5, color: P.text },
  fqPct: { fontSize: 11.5, color: P.textTertiary },
  fqTrack: { height: 7, backgroundColor: "#f0ece4", borderRadius: 5, overflow: "hidden" },

  textRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 7, borderTopWidth: 1, borderTopColor: "#f1eee7" },
  textTitle: { flex: 1, fontSize: 12.5, color: P.text },
  textMeta: { fontSize: 11, color: P.textTertiary },
});
