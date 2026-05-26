/**
 * Polyglot stats — "Ledger" reading-engine dashboard.
 *
 * Organized by EVIDENCE STRENGTH for a warm-start learner: you've either
 * proven a word by meeting it in real text (a page OR a sentence — reader and
 * review count identically), or you're still guessing. Reading is framed as the
 * review engine: every finished page confirms knowns and surfaces gaps.
 *
 * Language-agnostic: every number comes from the language-scoped /api/stats, so
 * the same screen serves Modern Greek, Latin, and any future polyglot language
 * (the active one is read from the language context). Modern Editorial palette,
 * matching the reader / review / lemma-detail screens.
 *
 * See research/experiment-log.md (2026-05-25) and polyglot/CLAUDE.md Hard
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

// English display names + native-script headline. Fallbacks keep a brand-new
// language working before it gets a hand-tuned entry.
const LANGUAGE_NAMES: Record<string, string> = {
  el: "Modern Greek", grc: "Ancient Greek", la: "Latin",
};
const NATIVE_NAME: Record<string, string> = {
  el: "ελληνικά", grc: "ἑλληνικά", la: "Latina",
};

// Gradient tier colors (Modern Editorial green → sand).
const TIER = {
  recall: "#2e7d6b",     // recall-tested (FSRS card)
  confirmed: "#4f9683",  // confirmed by exposure (reader OR review)
  guess: "#e7c9a6",      // unconfirmed cognate guess
};

const num = (n: number | null | undefined) => (n ?? 0).toLocaleString();

export default function PolyglotStats() {
  const [stats, setStats] = useState<LanguageStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const insets = useSafeAreaInsets();
  const { language } = useLanguage();
  // This screen only mounts for polyglot languages; pass the active one
  // straight through so any future language works without a code change.
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
  const recall = ks.fsrs_known;
  const confirmed = ks.exposure_confirmed;
  const guess = ks.assumed_unconfirmed;
  const confirmedTotal = recall + confirmed;
  const totalKnown = ks.total;

  const rc = stats.recovery;
  const lt = stats.leitner;
  const td = stats.today;
  const fh = stats.flow_history ?? [];
  const lastWeek = fh.length ? fh[fh.length - 1] : null;

  const code = stats.language_code;
  const native = NATIVE_NAME[code] ?? LANGUAGE_NAMES[code] ?? code;

  return (
    <ScrollView
      style={s.screen}
      contentContainerStyle={[s.content, { paddingTop: insets.top + 16 }]}
    >
      <Text style={s.eyebrow}>
        {(LANGUAGE_NAMES[code] ?? code).toUpperCase()} · WHAT YOU KNOW, BY HOW IT WAS PROVEN
      </Text>
      <Text style={s.h1}>{native}</Text>

      {/* ── Reading engine ─────────────────────────────────────── */}
      <View style={s.engine}>
        <Text style={s.engineText}>
          Reading <Text style={{ fontWeight: "700" }}>is</Text> your review. Every page
          you finish confirms the words you knew and surfaces the ones you didn’t.
        </Text>
        <View style={s.row}>
          <Pill n={`+${lastWeek?.confirmed ?? 0}`} label="confirmed / wk" color={TIER.recall} />
          <Pill n={`+${lastWeek?.gaps_discovered ?? 0}`} label="gaps / wk" color={P.warning} />
          <Pill n={`${td.streak}`} label="🔥 streak" color={P.text} />
        </View>
      </View>

      {/* ── Hero: the honest split ─────────────────────────────── */}
      <View style={s.heroRow}>
        <Text style={s.heroBig}>{num(confirmedTotal)}</Text>
        <Text style={s.heroLab}>
          confirmed · <Text style={{ color: P.etymology, fontWeight: "600" }}>{num(guess)}</Text> still a guess
        </Text>
      </View>
      <View style={s.gbar}>
        {recall > 0 && <View style={{ flex: recall, backgroundColor: TIER.recall }} />}
        {confirmed > 0 && <View style={{ flex: confirmed, backgroundColor: TIER.confirmed }} />}
        {guess > 0 && <View style={{ flex: guess, backgroundColor: TIER.guess }} />}
      </View>
      <View style={s.keyRow}>
        <KeyDot color={TIER.recall} label={`Recall-tested ${num(recall)}`} />
        <KeyDot color={TIER.confirmed} label={`Confirmed ${num(confirmed)}`} />
        <KeyDot color={TIER.guess} label={`Guess ${num(guess)}`} />
        <Text style={s.keyMeta}>· {num(totalKnown)} credited · {num(stats.new)} unseen</Text>
      </View>

      {/* ── Evidence ladder ────────────────────────────────────── */}
      <SectionHeader label="The evidence ladder" right={`${num(totalKnown)} credited known`} />
      <Tier color={TIER.recall} name="Recall-tested" how="recalled under spacing · FSRS card" n={recall} nColor={TIER.recall} />
      <Tier color={TIER.confirmed} name="Confirmed by exposure" how="met in a page or a sentence, not flagged · reader = review" n={confirmed} nColor="#3f8f7c" />
      <Tier color="#d9b48f" ghost name="Unconfirmed guess" how="cognate guess · you’ve never been shown it" n={guess} nColor={P.etymology} />

      {/* ── Gaps reading surfaced ──────────────────────────────── */}
      <SectionHeader label="Gaps reading surfaced" right="what you’re actually learning" />
      <View style={s.card}>
        <View style={s.funnel}>
          <Funnel n={rc.ever_failed} label="found" color={P.warning} />
          <Sep />
          <Funnel n={stats.judged_progress.pipeline.acquiring} label="in practice" color={P.etymology} />
          <Sep />
          <Funnel n={stats.by_state.learning} label="learning" color={P.accent} />
          <Sep />
          <Funnel n={rc.graduated_after_failure} label="closed" color={TIER.recall} />
        </View>
        <View style={[s.row, { marginTop: 9 }]}>
          <Box n={lt.box_1} label="Box 1 · 4h" />
          <Box n={lt.box_2} label="Box 2 · 1d" />
          <Box n={lt.box_3} label="Box 3 · 3d" />
          <Box n={rc.failed_not_yet_recovered} label="open gaps" danger />
        </View>
      </View>
      {stats.fsrs.tracked > 0 && (
        <View style={s.card}>
          <Text style={s.cardLabel}>FSRS stability · {num(stats.fsrs.tracked)} verified cards</Text>
          <StabilityBar buckets={stats.fsrs.stability_buckets} />
        </View>
      )}

      {/* ── Conversion over time ───────────────────────────────── */}
      {fh.some((w) => w.confirmed || w.gaps_discovered) && (
        <>
          <SectionHeader label="Conversion over time" right="last 8 weeks" />
          <View style={s.card}>
            <WeeklyChart weeks={fh} />
            <View style={[s.keyRow, { marginTop: 8 }]}>
              <KeyDot color={TIER.confirmed} label="confirmed" />
              <KeyDot color={P.warning} label="gaps found" />
            </View>
            <Text style={s.note}>
              A tall earlier week is the one-time seed import + history backfill, not your weekly rate.
            </Text>
          </View>
        </>
      )}

      {/* ── Frequency coverage ─────────────────────────────────── */}
      {stats.frequency && stats.frequency.bands.length > 0 && (
        <>
          <SectionHeader label="Frequency coverage" right={stats.frequency.source} />
          <View style={s.card}>
            {stats.frequency.bands.map((b) => (
              <View key={b.top_n} style={{ marginBottom: 9 }}>
                <View style={s.fqTop}>
                  <Text style={s.fqLabel}>Top {num(b.top_n)}</Text>
                  <Text style={s.fqPct}>{b.coverage_pct}% reached</Text>
                </View>
                <View style={s.fqTrack}>
                  <View style={{ width: `${Math.min(100, b.coverage_pct)}%`, backgroundColor: TIER.confirmed, height: "100%" }} />
                </View>
                <Text style={s.fqDetail}>
                  {num(b.learned)} learned · {num(b.acquiring)} acquiring · {num(b.encountered)} seen
                </Text>
              </View>
            ))}
          </View>
        </>
      )}

      {/* ── Today ──────────────────────────────────────────────── */}
      <SectionHeader label="Today" />
      <View style={s.row}>
        <TodayCell n={td.reviews} label="reviews" />
        <TodayCell n={td.sentence_reviews} label="sentences" />
        <TodayCell n={td.pages_read} label="pages" />
        <TodayCell n={td.new_lemmas} label="new" color={TIER.recall} />
        <TodayCell n={td.marked_unknown} label="marked ?" color={P.warning} />
      </View>

      {/* ── Texts ──────────────────────────────────────────────── */}
      {stats.stories.length > 0 && (
        <>
          <SectionHeader label="Texts" />
          <View style={s.card}>
            {stats.stories.map((st) => (
              <View key={st.id} style={s.textRow}>
                <Text style={s.textTitle} numberOfLines={1}>{st.title ?? "Untitled"}</Text>
                <Text style={s.textMeta}>
                  {num(st.viewed_pages)}/{num(st.page_count ?? 0)} read
                </Text>
              </View>
            ))}
          </View>
        </>
      )}

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

// ─── Small presentational pieces ───────────────────────────────────────────

function SectionHeader({ label, right }: { label: string; right?: string }) {
  return (
    <View style={s.sec}>
      <Text style={s.secLabel}>{label.toUpperCase()}</Text>
      {right ? <Text style={s.secRight}>{right}</Text> : null}
    </View>
  );
}

function Pill({ n, label, color }: { n: string; label: string; color: string }) {
  return (
    <View style={s.pill}>
      <Text style={[s.pillN, { color }]}>{n}</Text>
      <Text style={s.pillL}>{label}</Text>
    </View>
  );
}

function KeyDot({ color, label }: { color: string; label: string }) {
  return (
    <View style={s.keyItem}>
      <View style={[s.dot, { backgroundColor: color }]} />
      <Text style={s.keyText}>{label}</Text>
    </View>
  );
}

function Tier(
  { color, name, how, n, nColor, ghost }:
  { color: string; name: string; how: string; n: number; nColor: string; ghost?: boolean },
) {
  return (
    <View style={[s.tier, ghost && s.tierGhost]}>
      <View style={[s.tierBar, { backgroundColor: color }]} />
      <View style={{ flex: 1 }}>
        <Text style={[s.tierName, ghost && { color: "#a06a2c" }]}>{name}</Text>
        <Text style={s.tierHow}>{how}</Text>
      </View>
      <Text style={[s.tierN, { color: nColor }]}>{num(n)}</Text>
    </View>
  );
}

function Funnel({ n, label, color }: { n: number; label: string; color: string }) {
  return (
    <View style={s.funnelCell}>
      <Text style={[s.funnelN, { color }]}>{num(n)}</Text>
      <Text style={s.funnelL}>{label}</Text>
    </View>
  );
}
const Sep = () => <Text style={s.sep}>›</Text>;

function Box({ n, label, danger }: { n: number; label: string; danger?: boolean }) {
  return (
    <View style={[s.box, danger && s.boxDanger]}>
      <Text style={[s.boxN, { color: danger ? P.warning : P.etymology }]}>{num(n)}</Text>
      <Text style={[s.boxL, danger && { color: "#9c4133" }]}>{label}</Text>
    </View>
  );
}

function TodayCell({ n, label, color }: { n: number; label: string; color?: string }) {
  return (
    <View style={s.tcell}>
      <Text style={[s.tcellN, color ? { color } : null]}>{num(n)}</Text>
      <Text style={s.tcellL}>{label}</Text>
    </View>
  );
}

const STABILITY_COLORS: Record<string, string> = {
  "<1d": P.warning, "1-3d": P.etymology, "3-7d": P.etymology,
  "7-21d": TIER.recall, "21-60d": "#27ae60", "60d+": "#1abc9c",
};
function StabilityBar({ buckets }: { buckets: { label: string; count: number }[] }) {
  const total = buckets.reduce((a, b) => a + b.count, 0) || 1;
  return (
    <>
      <View style={s.stab}>
        {buckets.map((b) => b.count > 0 ? (
          <View key={b.label} style={{ flex: b.count, backgroundColor: STABILITY_COLORS[b.label] ?? P.textTertiary }} />
        ) : null)}
      </View>
      <Text style={s.note}>
        {buckets.filter((b) => b.count > 0).map((b) => `${b.count} ${b.label}`).join(" · ") || "—"}
      </Text>
      <Text style={[s.note, { marginTop: 0 }]}>{total} cards · nothing past 21d means memory is still young</Text>
    </>
  );
}

function WeeklyChart({ weeks }: { weeks: LanguageStats["flow_history"] }) {
  const max = Math.max(1, ...weeks.map((w) => Math.max(w.confirmed, w.gaps_discovered)));
  return (
    <View style={s.wk}>
      {weeks.map((w, i) => (
        <View key={w.week_start} style={s.wcol}>
          <View style={s.wbars}>
            <View style={{ width: 9, height: `${(w.confirmed / max) * 100}%`, backgroundColor: TIER.confirmed, borderTopLeftRadius: 2, borderTopRightRadius: 2 }} />
            <View style={{ width: 9, height: `${(w.gaps_discovered / max) * 100}%`, backgroundColor: P.warning, borderTopLeftRadius: 2, borderTopRightRadius: 2 }} />
          </View>
          <Text style={s.wlab}>{i === weeks.length - 1 ? "now" : `−${weeks.length - 1 - i}w`}</Text>
        </View>
      ))}
    </View>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: P.bg },
  content: { padding: 16 },
  center: { alignItems: "center", justifyContent: "center" },
  error: { color: P.warning, textAlign: "center", paddingHorizontal: 24 },
  eyebrow: { fontSize: 10.5, letterSpacing: 1.2, color: P.textTertiary, fontWeight: "600" },
  h1: { fontFamily: POLYGLOT_FONTS.greekDisplay, fontSize: 28, color: P.text, marginTop: 3, marginBottom: 2 },

  engine: { backgroundColor: "#eef4f1", borderWidth: 1, borderColor: "#cfe2da", borderRadius: 14, padding: 14, marginTop: 12, marginBottom: 6 },
  engineText: { fontFamily: POLYGLOT_FONTS.greekBody, fontSize: 16, color: P.text, lineHeight: 22, marginBottom: 11 },
  row: { flexDirection: "row", gap: 8 },
  pill: { flex: 1, backgroundColor: P.surface, borderWidth: 1, borderColor: P.border, borderRadius: 10, paddingVertical: 9, alignItems: "center" },
  pillN: { fontSize: 19, fontWeight: "700", letterSpacing: -0.3 },
  pillL: { fontSize: 9, color: P.textTertiary, textTransform: "uppercase", letterSpacing: 0.3, marginTop: 2 },

  heroRow: { flexDirection: "row", alignItems: "flex-end", gap: 8, marginTop: 12, marginBottom: 6 },
  heroBig: { fontSize: 34, fontWeight: "800", letterSpacing: -0.5, color: TIER.recall },
  heroLab: { fontSize: 12.5, color: P.textSecondary, paddingBottom: 4 },
  gbar: { height: 14, borderRadius: 7, overflow: "hidden", flexDirection: "row", marginBottom: 8 },
  keyRow: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 10 },
  keyItem: { flexDirection: "row", alignItems: "center" },
  dot: { width: 8, height: 8, borderRadius: 2, marginRight: 4 },
  keyText: { fontSize: 11, color: P.textSecondary },
  keyMeta: { fontSize: 11, color: P.textTertiary },

  sec: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", marginTop: 22, marginBottom: 9 },
  secLabel: { fontSize: 11, letterSpacing: 1, color: P.textTertiary, fontWeight: "700" },
  secRight: { fontSize: 11, color: P.textTertiary },

  card: { backgroundColor: P.surface, borderWidth: 1, borderColor: P.border, borderRadius: 13, padding: 13, marginBottom: 9 },
  cardLabel: { fontSize: 11, color: P.textTertiary, marginBottom: 6 },
  note: { fontSize: 10.5, color: P.textTertiary, fontStyle: "italic", marginTop: 6, lineHeight: 14 },

  tier: { flexDirection: "row", alignItems: "center", gap: 11, backgroundColor: P.surface, borderWidth: 1, borderColor: P.border, borderRadius: 12, padding: 11, paddingLeft: 14, marginBottom: 7, overflow: "hidden" },
  tierGhost: { backgroundColor: "#fdfaf5", borderColor: "#e6d3bb" },
  tierBar: { position: "absolute", left: 0, top: 0, bottom: 0, width: 4 },
  tierName: { fontSize: 13, fontWeight: "600", color: P.text },
  tierHow: { fontSize: 11, color: P.textTertiary, marginTop: 1 },
  tierN: { fontSize: 21, fontWeight: "700", letterSpacing: -0.3 },

  funnel: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  funnelCell: { flex: 1, alignItems: "center" },
  funnelN: { fontSize: 19, fontWeight: "700", letterSpacing: -0.3 },
  funnelL: { fontSize: 9, color: P.textTertiary, textTransform: "uppercase", marginTop: 3 },
  sep: { color: "#d2d8da", fontSize: 14 },

  box: { flex: 1, backgroundColor: "#fbf3ea", borderWidth: 1, borderColor: "#efd9bf", borderRadius: 9, paddingVertical: 8, alignItems: "center" },
  boxDanger: { backgroundColor: "#fdebe7", borderColor: "#f3c9bf" },
  boxN: { fontSize: 16, fontWeight: "700" },
  boxL: { fontSize: 8.5, color: "#a06a2c", marginTop: 1 },

  stab: { height: 9, borderRadius: 5, overflow: "hidden", flexDirection: "row", marginVertical: 6 },

  wk: { flexDirection: "row", alignItems: "flex-end", gap: 10, height: 96, paddingTop: 6 },
  wcol: { flex: 1, alignItems: "center", justifyContent: "flex-end", height: "100%" },
  wbars: { flexDirection: "row", alignItems: "flex-end", gap: 3, height: "100%", justifyContent: "center" },
  wlab: { fontSize: 9, color: P.textTertiary, marginTop: 5 },

  fqTop: { flexDirection: "row", justifyContent: "space-between", marginBottom: 3 },
  fqLabel: { fontSize: 11.5, color: P.text },
  fqPct: { fontSize: 11.5, color: P.textTertiary },
  fqTrack: { height: 7, backgroundColor: "#f0ece4", borderRadius: 5, overflow: "hidden" },
  fqDetail: { fontSize: 10.5, color: P.textSecondary, marginTop: 4 },

  tcell: { flex: 1, backgroundColor: P.surface, borderWidth: 1, borderColor: P.border, borderRadius: 10, paddingVertical: 9, alignItems: "center" },
  tcellN: { fontSize: 17, fontWeight: "700", color: P.text },
  tcellL: { fontSize: 8.5, color: P.textTertiary, textTransform: "uppercase", letterSpacing: 0.3, marginTop: 2 },

  textRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 8, borderTopWidth: 1, borderTopColor: "#f1eee7" },
  textTitle: { flex: 1, fontSize: 12.5, color: P.text },
  textMeta: { fontSize: 11, color: P.textTertiary },
});
