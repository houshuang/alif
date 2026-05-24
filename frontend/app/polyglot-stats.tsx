/**
 * Polyglot stats — per-language progress dashboard.
 *
 * Modelled on Alif's `stats.tsx` (Today / Vocabulary / Progress sections) but
 * pared down to what polyglot actually exposes: no roots, no textbook
 * benchmarks, no Quran, no audio. See `polyglot/CLAUDE.md` § "Ground design
 * and code in Alif".
 */
import { useCallback, useState } from "react";
import {
  View, Text, ScrollView, StyleSheet, ActivityIndicator,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { getLanguageStats, type LanguageStats } from "../lib/polyglot-api";

const C = {
  bg: "#0f0f1a",
  surface: "#1a1a2e",
  surfaceAlt: "#22223a",
  border: "#2a2a40",
  text: "#e0e0f0",
  textDim: "#9090a8",
  textFaint: "#606078",
  accent: "#7aa2f7",
  known: "#5fb27a",
  learning: "#a6c879",
  acquiring: "#d4a06b",
  encountered: "#506a8e",
  lapsed: "#c95f6f",
  unknown: "#c95f6f",
  warn: "#e0b060",
  good: "#5fb27a",
};

const STABILITY_COLORS: Record<string, string> = {
  "<1d": "#e74c3c",
  "1-3d": "#f1c40f",
  "3-7d": "#f39c12",
  "7-21d": "#5fb27a",
  "21-60d": "#27ae60",
  "60d+": "#1abc9c",
};

const LANGUAGE_NAMES: Record<string, string> = {
  el: "Modern Greek",
  grc: "Ancient Greek",
  la: "Latin",
};

export default function PolyglotStats() {
  const [stats, setStats] = useState<LanguageStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      setLoading(true);
      setError(null);
      getLanguageStats("el")
        .then((s) => { if (!cancelled) { setStats(s); setLoading(false); } })
        .catch((e) => { if (!cancelled) { setError(String(e)); setLoading(false); } });
      return () => { cancelled = true; };
    }, []),
  );

  if (loading && !stats) {
    return (
      <View style={s.screen}>
        <ActivityIndicator color={C.accent} style={{ marginTop: 80 }} />
      </View>
    );
  }

  if (error || !stats) {
    return (
      <View style={s.screen}>
        <Text style={s.error}>Failed to load stats{error ? `: ${error}` : ""}</Text>
      </View>
    );
  }

  const languageName = LANGUAGE_NAMES[stats.language_code] ?? stats.language_code;
  const knownPct = stats.total_lemmas > 0
    ? Math.round((stats.known_summary.total / stats.total_lemmas) * 100)
    : 0;

  return (
    <View style={s.screen}>
      <ScrollView contentContainerStyle={s.body}>
        <Text style={s.h1}>{languageName}</Text>
        <Text style={s.h2}>
          {stats.known_summary.total.toLocaleString()} known total {"·"} {stats.judged_progress.to_learn.toLocaleString()} to learn {"·"} {stats.judged_progress.learnt.toLocaleString()} learnt {"·"} {knownPct}% known
        </Text>

        <SectionHeader label="Today" />
        <TodayCard today={stats.today} />

        <SectionHeader label="Known" />
        <KnownSummaryCard known={stats.known_summary} total={stats.total_lemmas} />

        <SectionHeader label="To learn / learnt" />
        <JudgedProgressCard progress={stats.judged_progress} />

        <SectionHeader label="Recovery" />
        <RecoveryCard recovery={stats.recovery} />

        <SectionHeader label="Vocabulary" />
        <LifecycleCard byState={stats.by_state} total={stats.total_lemmas} unseen={stats.new} />

        {stats.leitner.total_acquiring > 0 && (
          <LeitnerCard leitner={stats.leitner} />
        )}

        {stats.fsrs.tracked > 0 && (
          <FsrsStabilityCard fsrs={stats.fsrs} />
        )}

        {stats.frequency && stats.frequency.total_entries > 0 && (
          <FrequencyCard freq={stats.frequency} />
        )}

        <SectionHeader label="Activity" />
        <History14dCard history={stats.history_14d} />

        {stats.stories.length > 0 && (
          <>
            <SectionHeader label="Texts" />
            <StoriesCard stories={stats.stories} />
          </>
        )}

        {stats.activity.length > 0 && (
          <>
            <SectionHeader label="Recent" />
            <ActivityFeedCard activity={stats.activity} />
          </>
        )}

        <Text style={s.footer}>
          Counts update as you read — pages tokenize lazily on first view.
        </Text>
      </ScrollView>
    </View>
  );
}

// ── Section header ────────────────────────────────────────────────────────

function SectionHeader({ label }: { label: string }) {
  return (
    <View style={s.sectionHeader}>
      <Text style={s.sectionHeaderText}>{label.toUpperCase()}</Text>
      <View style={s.sectionHeaderLine} />
    </View>
  );
}

// ── Known inventory ──────────────────────────────────────────────────────

function KnownSummaryCard({
  known, total,
}: {
  known: LanguageStats["known_summary"]; total: number;
}) {
  const knownPct = total > 0 ? Math.min(Math.round((known.total / total) * 100), 100) : 0;
  const chips = [
    { label: "Pre-known", count: known.pre_known, color: C.known },
    { label: "Cognates", count: known.cognate_known, color: C.accent },
    { label: "FSRS", count: known.fsrs_known, color: C.learning },
    { label: "Judged", count: known.judged_known, color: C.good },
    { label: "Auto", count: known.unjudged_known, color: C.textDim },
  ].filter((chip) => chip.count > 0);

  return (
    <View style={s.card}>
      <View style={s.heroRow}>
        <Text style={s.heroNum}>{known.total.toLocaleString()}</Text>
        <Text style={s.heroLabel}>known words</Text>
      </View>
      <View style={s.knownMeter}>
        <View style={[s.knownMeterFill, { width: `${knownPct}%` }]} />
      </View>
      <View style={s.chipRow}>
        {chips.map((chip) => (
          <View key={chip.label} style={s.chip}>
            <Text style={s.chipLabel}>{chip.label}</Text>
            <Text style={[s.chipValue, { color: chip.color }]}>
              {chip.count.toLocaleString()}
            </Text>
          </View>
        ))}
        {known.lapsed_from_assumed_known > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>Lapsed known</Text>
            <Text style={[s.chipValue, { color: C.lapsed }]}>
              {known.lapsed_from_assumed_known.toLocaleString()}
            </Text>
          </View>
        )}
      </View>
    </View>
  );
}

// ── Judged study progress ────────────────────────────────────────────────

function JudgedProgressCard({ progress }: { progress: LanguageStats["judged_progress"] }) {
  const p = progress.pipeline;
  const stages = [
    { label: "Box 1", count: p.box_1, color: C.unknown },
    { label: "Box 2", count: p.box_2, color: C.warn },
    { label: "Box 3", count: p.box_3, color: C.acquiring },
    { label: "FSRS", count: p.learning, color: C.learning },
    { label: "Known", count: p.known, color: C.known },
    { label: "Lapsed", count: p.lapsed, color: C.lapsed },
  ].filter((stage) => stage.count > 0);
  const total = Math.max(stages.reduce((sum, stage) => sum + stage.count, 0), 1);

  return (
    <View style={s.card}>
      <View style={s.duoHero}>
        <View style={s.duoCell}>
          <Text style={[s.duoNum, { color: C.warn }]}>
            {progress.to_learn.toLocaleString()}
          </Text>
          <Text style={s.duoLabel}>to learn</Text>
        </View>
        <View style={s.duoCell}>
          <Text style={[s.duoNum, { color: C.good }]}>
            {progress.learnt.toLocaleString()}
          </Text>
          <Text style={s.duoLabel}>learnt</Text>
        </View>
      </View>

      {stages.length > 0 ? (
        <>
          <View style={s.pipelineTrack}>
            {stages.map((stage) => (
              <View
                key={stage.label}
                style={{
                  flex: Math.max(stage.count / total, 0.04),
                  backgroundColor: stage.color,
                  height: "100%",
                }}
              />
            ))}
          </View>
          <View style={s.pipelineLegend}>
            {stages.map((stage) => (
              <View key={stage.label} style={s.pipelineLegendItem}>
                <View style={[s.legendDot, { backgroundColor: stage.color }]} />
                <Text style={s.pipelineLegendText}>{stage.label}</Text>
                <Text style={s.pipelineLegendCount}>{stage.count}</Text>
              </View>
            ))}
          </View>
        </>
      ) : (
        <Text style={s.emptyText}>No red or green judgments yet.</Text>
      )}

      <View style={s.chipRow}>
        <View style={s.chip}>
          <Text style={s.chipLabel}>Judged</Text>
          <Text style={s.chipValue}>{progress.total.toLocaleString()}</Text>
        </View>
        {p.acquisition_due_now > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>Box due</Text>
            <Text style={[s.chipValue, { color: C.warn }]}>
              {p.acquisition_due_now.toLocaleString()}
            </Text>
          </View>
        )}
        {p.fsrs_due_now > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>FSRS due</Text>
            <Text style={[s.chipValue, { color: C.warn }]}>
              {p.fsrs_due_now.toLocaleString()}
            </Text>
          </View>
        )}
        <View style={s.chip}>
          <Text style={s.chipLabel}>Red</Text>
          <Text style={[s.chipValue, { color: C.unknown }]}>
            {progress.ever_red.toLocaleString()}
          </Text>
        </View>
        <View style={s.chip}>
          <Text style={s.chipLabel}>Green</Text>
          <Text style={[s.chipValue, { color: C.good }]}>
            {progress.ever_green.toLocaleString()}
          </Text>
        </View>
        {progress.yellow_only > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>Yellow only</Text>
            <Text style={[s.chipValue, { color: C.warn }]}>
              {progress.yellow_only.toLocaleString()}
            </Text>
          </View>
        )}
        {progress.lapsed_from_known > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>Known lapsed</Text>
            <Text style={[s.chipValue, { color: C.lapsed }]}>
              {progress.lapsed_from_known.toLocaleString()}
            </Text>
          </View>
        )}
      </View>
    </View>
  );
}

// ── Recovery ──────────────────────────────────────────────────────────────

function RecoveryCard({ recovery }: { recovery: LanguageStats["recovery"] }) {
  const recoveredPct = recovery.ever_failed > 0
    ? Math.round((recovery.recovered_once / recovery.ever_failed) * 100)
    : 0;
  const stages = [
    { label: "Failed", count: recovery.ever_failed, color: C.unknown },
    { label: "Correct", count: recovery.recovered_once, color: C.learning },
    { label: "Graduated", count: recovery.graduated_after_failure, color: C.good },
    { label: "21d+", count: recovery.stable_after_failure_21d, color: STABILITY_COLORS["21-60d"] },
  ];
  const flowTotal = Math.max(recovery.ever_failed, 1);

  return (
    <View style={s.card}>
      <View style={s.heroRow}>
        <Text style={[s.heroNum, { color: C.good }]}>
          {recovery.recovered_once.toLocaleString()}
        </Text>
        <Text style={s.heroLabel}>recovered words</Text>
      </View>

      <View style={s.recoveryBars}>
        {stages.slice(1).map((stage, i) => {
          const pct = Math.min(stage.count / flowTotal, 1);
          return (
            <View key={stage.label} style={s.recoveryTrack}>
              <View
                style={[
                  s.recoveryFill,
                  {
                    width: `${Math.max(pct * 100, stage.count > 0 ? 3 : 0)}%`,
                    backgroundColor: stage.color,
                    opacity: i === 0 ? 0.85 : 0.65,
                  },
                ]}
              />
            </View>
          );
        })}
      </View>

      <View style={s.flowLabels}>
        {stages.map((stage) => (
          <View key={stage.label} style={s.flowLabelCell}>
            <Text style={[s.flowCount, { color: stage.color }]}>{stage.count}</Text>
            <Text style={s.flowName}>{stage.label}</Text>
          </View>
        ))}
      </View>

      <View style={s.chipRow}>
        <View style={s.chip}>
          <Text style={s.chipLabel}>Rate</Text>
          <Text style={[s.chipValue, { color: C.good }]}>{recoveredPct}%</Text>
        </View>
        <View style={s.chip}>
          <Text style={s.chipLabel}>Pre-known</Text>
          <Text style={s.chipValue}>{recovery.pre_known}</Text>
        </View>
        {recovery.cognate_known > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>Cognates</Text>
            <Text style={s.chipValue}>{recovery.cognate_known}</Text>
          </View>
        )}
        {recovery.failed_not_yet_recovered > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>Open</Text>
            <Text style={[s.chipValue, { color: C.warn }]}>
              {recovery.failed_not_yet_recovered}
            </Text>
          </View>
        )}
        {recovery.stable_after_failure_60d > 0 && (
          <View style={s.chip}>
            <Text style={s.chipLabel}>60d+</Text>
            <Text style={[s.chipValue, { color: STABILITY_COLORS["60d+"] }]}>
              {recovery.stable_after_failure_60d}
            </Text>
          </View>
        )}
      </View>
    </View>
  );
}

// ── Today ────────────────────────────────────────────────────────────────

function TodayCard({ today }: { today: LanguageStats["today"] }) {
  const tiles: { value: number; label: string; color?: string }[] = [];
  tiles.push({ value: today.reviews, label: "reviews" });
  tiles.push({ value: today.sentence_reviews, label: "sentences" });
  tiles.push({ value: today.pages_read, label: "pages read" });
  tiles.push({ value: today.new_lemmas, label: "new lemmas", color: C.accent });
  tiles.push({ value: today.graduated, label: "graduated", color: C.good });
  if (today.marked_unknown > 0) {
    tiles.push({ value: today.marked_unknown, label: "marked ?", color: C.warn });
  }
  tiles.push({ value: today.streak, label: "day streak" });

  const allZero =
    today.reviews === 0 && today.pages_read === 0 &&
    today.new_lemmas === 0 && today.streak === 0;
  if (allZero) {
    return (
      <View style={s.card}>
        <Text style={s.emptyText}>
          Nothing today yet — open a text or run a review to get started.
        </Text>
      </View>
    );
  }

  return (
    <View style={s.card}>
      <View style={s.tileGrid}>
        {tiles.map((t, i) => (
          <View key={i} style={s.tile}>
            <Text style={[s.tileValue, t.color ? { color: t.color } : null]}>
              {t.value.toLocaleString()}
            </Text>
            <Text style={s.tileLabel}>{t.label}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// ── Vocabulary lifecycle ─────────────────────────────────────────────────

function LifecycleCard({
  byState, total, unseen,
}: {
  byState: LanguageStats["by_state"]; total: number; unseen: number;
}) {
  const stages = [
    { label: "Seen", count: byState.encountered, color: C.encountered },
    { label: "Acq", count: byState.acquiring_only, color: C.acquiring },
    { label: "Learn", count: byState.learning, color: C.learning },
    { label: "Known", count: byState.known, color: C.known },
  ];
  const flowTotal = Math.max(stages.reduce((sum, st) => sum + st.count, 0), 1);

  const extras: { label: string; count: number; color: string }[] = [];
  if (byState.lapsed > 0) extras.push({ label: "Lapsed", count: byState.lapsed, color: C.lapsed });
  if (byState.unknown > 0) extras.push({ label: "Marked ?", count: byState.unknown, color: C.unknown });
  if (byState.ignored > 0) extras.push({ label: "Ignored", count: byState.ignored, color: C.textFaint });
  if (byState.suspended > 0) extras.push({ label: "Leech", count: byState.suspended, color: C.warn });

  return (
    <View style={s.card}>
      <View style={s.heroRow}>
        <Text style={s.heroNum}>{byState.known.toLocaleString()}</Text>
        <Text style={s.heroLabel}>known words</Text>
      </View>

      <View style={s.flowStrip}>
        {stages.map((stage, i) => (
          <View
            key={stage.label}
            style={{
              flex: Math.max(stage.count / flowTotal, 0.04),
              backgroundColor: stage.color + "40",
              height: 10,
              borderTopLeftRadius: i === 0 ? 5 : 0,
              borderBottomLeftRadius: i === 0 ? 5 : 0,
              borderTopRightRadius: i === stages.length - 1 ? 5 : 0,
              borderBottomRightRadius: i === stages.length - 1 ? 5 : 0,
            }}
          />
        ))}
      </View>
      <View style={s.flowLabels}>
        {stages.map((stage) => (
          <View key={stage.label} style={s.flowLabelCell}>
            <Text style={[s.flowCount, { color: stage.color }]}>{stage.count}</Text>
            <Text style={s.flowName}>{stage.label}</Text>
          </View>
        ))}
      </View>

      {(extras.length > 0 || unseen > 0) && (
        <View style={s.chipRow}>
          {extras.map((e) => (
            <View key={e.label} style={s.chip}>
              <Text style={s.chipLabel}>{e.label}</Text>
              <Text style={[s.chipValue, { color: e.color }]}>{e.count}</Text>
            </View>
          ))}
          {unseen > 0 && (
            <View style={s.chip}>
              <Text style={s.chipLabel}>Unseen</Text>
              <Text style={[s.chipValue, { color: C.textFaint }]}>{unseen}</Text>
            </View>
          )}
          <View style={s.chip}>
            <Text style={s.chipLabel}>Total</Text>
            <Text style={s.chipValue}>{total}</Text>
          </View>
        </View>
      )}
    </View>
  );
}

// ── Leitner ──────────────────────────────────────────────────────────────

function LeitnerCard({ leitner }: { leitner: LanguageStats["leitner"] }) {
  const boxes = [
    { label: "Box 1", count: leitner.box_1, interval: "4h" },
    { label: "Box 2", count: leitner.box_2, interval: "1d" },
    { label: "Box 3", count: leitner.box_3, interval: "3d" },
  ];
  return (
    <View style={s.card}>
      <View style={s.cardHeaderRow}>
        <Text style={s.cardTitle}>Acquisition (Leitner)</Text>
        {leitner.due_now > 0 && (
          <View style={s.duePill}>
            <Text style={s.duePillText}>{leitner.due_now} due now</Text>
          </View>
        )}
      </View>
      <View style={s.leitnerRow}>
        {boxes.map((b) => (
          <View key={b.label} style={s.leitnerBox}>
            <Text style={s.leitnerNum}>{b.count}</Text>
            <Text style={s.leitnerLabel}>{b.label}</Text>
            <Text style={s.leitnerInterval}>every {b.interval}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// ── FSRS stability ───────────────────────────────────────────────────────

function FsrsStabilityCard({ fsrs }: { fsrs: LanguageStats["fsrs"] }) {
  const total = fsrs.stability_buckets.reduce((sum, b) => sum + b.count, 0);
  if (total === 0) return null;
  const fragile = fsrs.stability_buckets
    .filter((b) => b.label === "<1d" || b.label === "1-3d")
    .reduce((s, b) => s + b.count, 0);
  const growing = fsrs.stability_buckets
    .filter((b) => b.label === "3-7d" || b.label === "7-21d")
    .reduce((s, b) => s + b.count, 0);
  const solid = fsrs.stability_buckets
    .filter((b) => b.label === "21-60d" || b.label === "60d+")
    .reduce((s, b) => s + b.count, 0);

  return (
    <View style={s.card}>
      <View style={s.cardHeaderRow}>
        <Text style={s.cardTitle}>FSRS stability</Text>
        <Text style={s.cardSub}>{fsrs.tracked} tracked</Text>
      </View>

      <View style={s.stackedBar}>
        {fsrs.stability_buckets.map((b) => {
          if (b.count === 0) return null;
          const pct = (b.count / total) * 100;
          return (
            <View
              key={b.label}
              style={{
                width: `${Math.max(pct, 2)}%`,
                backgroundColor: STABILITY_COLORS[b.label] ?? C.border,
                height: "100%",
              }}
            />
          );
        })}
      </View>

      <View style={s.summaryRow}>
        {fragile > 0 && (
          <Text style={[s.summaryItem, { color: STABILITY_COLORS["<1d"] }]}>{fragile} fragile</Text>
        )}
        {growing > 0 && (
          <Text style={[s.summaryItem, { color: STABILITY_COLORS["3-7d"] }]}>{growing} growing</Text>
        )}
        {solid > 0 && (
          <Text style={[s.summaryItem, { color: STABILITY_COLORS["60d+"] }]}>{solid} solid</Text>
        )}
      </View>

      <View style={s.bucketGrid}>
        {fsrs.stability_buckets.filter((b) => b.count > 0).map((b) => (
          <View key={b.label} style={s.bucketRow}>
            <View style={[s.bucketDot, { backgroundColor: STABILITY_COLORS[b.label] }]} />
            <Text style={s.bucketLabel}>{b.label}</Text>
            <Text style={s.bucketCount}>{b.count}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// ── Frequency core ───────────────────────────────────────────────────────

function FrequencyCard({ freq }: { freq: NonNullable<LanguageStats["frequency"]> }) {
  return (
    <View style={s.card}>
      <View style={s.cardHeaderRow}>
        <Text style={s.cardTitle}>Frequency core</Text>
        <Text style={s.cardSub}>{freq.source}</Text>
      </View>

      {freq.bands.map((band) => {
        const learnedPct = Math.min(band.coverage_pct, 100);
        const acquiringPct = Math.min((band.acquiring / band.top_n) * 100, 100);
        const encounteredPct = Math.min((band.encountered / band.top_n) * 100, 100);
        return (
          <View key={band.top_n} style={s.freqBand}>
            <View style={s.freqBandTop}>
              <Text style={s.freqBandLabel}>Top {band.top_n.toLocaleString()}</Text>
              <Text style={s.freqBandPct}>{learnedPct.toFixed(1)}%</Text>
            </View>
            <View style={s.freqTrack}>
              <View style={[s.freqLearned, { width: `${learnedPct}%` }]} />
              <View style={[s.freqAcquiring, {
                width: `${acquiringPct}%`,
                left: `${learnedPct}%`,
              }]} />
              <View style={[s.freqEncountered, {
                width: `${encounteredPct}%`,
                left: `${Math.min(learnedPct + acquiringPct, 100)}%`,
              }]} />
            </View>
            <Text style={s.freqDetail}>
              {band.learned} learned {"·"} {band.acquiring} acquiring {"·"} {band.encountered} seen
              {band.unmapped > 0 ? ` · ${band.unmapped} unmapped` : ""}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

// ── 14-day activity strip ────────────────────────────────────────────────

function History14dCard({ history }: { history: LanguageStats["history_14d"] }) {
  const maxReviews = Math.max(1, ...history.map((d) => d.reviews));
  const maxPages = Math.max(1, ...history.map((d) => d.pages_read));
  const hasAny = history.some((d) => d.reviews > 0 || d.pages_read > 0 || d.new_lemmas > 0);

  if (!hasAny) {
    return (
      <View style={s.card}>
        <Text style={s.emptyText}>No activity in the last 14 days.</Text>
      </View>
    );
  }

  return (
    <View style={s.card}>
      <Text style={s.cardTitle}>Last 14 days</Text>
      <View style={s.chartArea}>
        {history.map((d) => {
          const rh = d.reviews > 0 ? Math.max((d.reviews / maxReviews) * 70, 3) : 0;
          const ph = d.pages_read > 0 ? Math.max((d.pages_read / maxPages) * 70, 3) : 0;
          const dayNum = d.date.slice(8);
          return (
            <View key={d.date} style={s.barCol}>
              <View style={s.barColInner}>
                {d.new_lemmas > 0 && <View style={s.barNewMark} />}
                {rh > 0 && <View style={[s.barReviews, { height: rh }]} />}
                {ph > 0 && <View style={[s.barPages, { height: ph }]} />}
              </View>
              <Text style={s.barDayLabel}>{dayNum}</Text>
            </View>
          );
        })}
      </View>
      <View style={s.legendRow}>
        <View style={s.legendItem}>
          <View style={[s.legendDot, { backgroundColor: C.accent }]} />
          <Text style={s.legendText}>reviews</Text>
        </View>
        <View style={s.legendItem}>
          <View style={[s.legendDot, { backgroundColor: C.known }]} />
          <Text style={s.legendText}>pages read</Text>
        </View>
        <View style={s.legendItem}>
          <View style={[s.legendDot, { backgroundColor: C.warn }]} />
          <Text style={s.legendText}>new lemma day</Text>
        </View>
      </View>
    </View>
  );
}

// ── Stories ──────────────────────────────────────────────────────────────

function StoriesCard({ stories }: { stories: LanguageStats["stories"] }) {
  return (
    <View style={s.card}>
      {stories.map((st) => {
        const total = st.page_count ?? 0;
        const processedPct = total > 0 ? (st.processed_pages / total) * 100 : 0;
        const viewedPct = total > 0 ? (st.viewed_pages / total) * 100 : 0;
        return (
          <View key={st.id} style={s.storyRow}>
            <View style={s.storyHead}>
              <Text style={s.storyTitle} numberOfLines={1}>
                {st.title || `Untitled #${st.id}`}
              </Text>
              <Text style={s.storyMeta}>
                {st.viewed_pages}/{total || "?"} read
              </Text>
            </View>
            <View style={s.storyTrack}>
              <View style={[s.storyProcessed, { width: `${processedPct}%` }]} />
              <View style={[s.storyViewed, { width: `${viewedPct}%` }]} />
            </View>
            {(st.known_count > 0 || st.unknown_count > 0) && (
              <Text style={s.storyDetail}>
                {st.known_count} known {"·"} {st.unknown_count} unknown
                {st.total_words > 0 ? ` · ${st.total_words.toLocaleString()} words` : ""}
              </Text>
            )}
          </View>
        );
      })}
    </View>
  );
}

// ── Activity feed ────────────────────────────────────────────────────────

function ActivityFeedCard({ activity }: { activity: LanguageStats["activity"] }) {
  return (
    <View style={s.card}>
      {activity.map((a, i) => (
        <View key={i} style={s.activityRow}>
          <Text style={s.activityType}>{a.event_type.replace(/_/g, " ")}</Text>
          <Text style={s.activitySummary} numberOfLines={2}>{a.summary}</Text>
          {a.created_at && (
            <Text style={s.activityTime}>{relativeTime(a.created_at)}</Text>
          )}
        </View>
      ))}
    </View>
  );
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return iso.slice(0, 10);
}

// ── Styles ───────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  body: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 80 },

  h1: { fontSize: 28, fontWeight: "700", color: C.text },
  h2: { fontSize: 13, color: C.textDim, marginTop: 4, marginBottom: 18 },

  sectionHeader: { flexDirection: "row", alignItems: "center", marginTop: 18, marginBottom: 8 },
  sectionHeaderText: {
    fontSize: 11, color: C.textDim, letterSpacing: 1.2, fontWeight: "700",
  },
  sectionHeaderLine: { flex: 1, height: 1, backgroundColor: C.border, marginLeft: 8 },

  card: {
    backgroundColor: C.surface, borderRadius: 12, padding: 14, marginBottom: 12,
    borderWidth: 1, borderColor: C.border,
  },
  cardHeaderRow: {
    flexDirection: "row", justifyContent: "space-between",
    alignItems: "baseline", marginBottom: 10,
  },
  cardTitle: { color: C.text, fontSize: 14, fontWeight: "600" },
  cardSub: { color: C.textDim, fontSize: 12 },
  emptyText: { color: C.textDim, fontSize: 13 },

  // Today tiles
  tileGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  tile: {
    backgroundColor: C.surfaceAlt, borderRadius: 8, paddingVertical: 10,
    paddingHorizontal: 12, minWidth: 84, flex: 1, alignItems: "flex-start",
  },
  tileValue: { fontSize: 22, fontWeight: "700", color: C.text },
  tileLabel: { fontSize: 11, color: C.textDim, marginTop: 2 },

  // Vocabulary hero + flow
  heroRow: { flexDirection: "row", alignItems: "baseline", gap: 10, marginBottom: 14 },
  heroNum: { fontSize: 38, fontWeight: "700", color: C.text },
  heroLabel: { fontSize: 14, color: C.textDim },
  knownMeter: {
    height: 8, borderRadius: 4, backgroundColor: C.surfaceAlt,
    overflow: "hidden", marginBottom: 2,
  },
  knownMeterFill: { height: "100%", backgroundColor: C.known },
  duoHero: {
    flexDirection: "row", gap: 10, marginBottom: 12,
  },
  duoCell: {
    flex: 1, backgroundColor: C.surfaceAlt, borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10,
  },
  duoNum: { fontSize: 32, fontWeight: "700" },
  duoLabel: { color: C.textDim, fontSize: 12, marginTop: 2 },
  pipelineTrack: {
    height: 12, borderRadius: 6, backgroundColor: C.surfaceAlt,
    overflow: "hidden", flexDirection: "row", marginTop: 2,
  },
  pipelineLegend: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginTop: 9 },
  pipelineLegendItem: { flexDirection: "row", alignItems: "center", gap: 4 },
  pipelineLegendText: { color: C.textDim, fontSize: 11 },
  pipelineLegendCount: { color: C.text, fontSize: 11, fontWeight: "700" },
  recoveryBars: { gap: 4, marginBottom: 8 },
  recoveryTrack: {
    height: 5, borderRadius: 3, backgroundColor: C.surfaceAlt, overflow: "hidden",
  },
  recoveryFill: { height: "100%", borderRadius: 3 },
  flowStrip: { flexDirection: "row", borderRadius: 5, overflow: "hidden", marginBottom: 6 },
  flowLabels: { flexDirection: "row", justifyContent: "space-between", marginTop: 4 },
  flowLabelCell: { alignItems: "center", flex: 1 },
  flowCount: { fontSize: 16, fontWeight: "700" },
  flowName: { fontSize: 11, color: C.textDim, marginTop: 2 },

  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 14 },
  chip: {
    backgroundColor: C.surfaceAlt, borderRadius: 6,
    paddingHorizontal: 10, paddingVertical: 6,
  },
  chipLabel: { fontSize: 10, color: C.textDim, textTransform: "uppercase", letterSpacing: 0.6 },
  chipValue: { fontSize: 14, fontWeight: "600", color: C.text },

  // Leitner
  leitnerRow: { flexDirection: "row", gap: 8 },
  leitnerBox: {
    flex: 1, backgroundColor: C.surfaceAlt, borderRadius: 8,
    padding: 12, alignItems: "center",
  },
  leitnerNum: { fontSize: 26, fontWeight: "700", color: C.acquiring },
  leitnerLabel: { fontSize: 12, color: C.text, marginTop: 2 },
  leitnerInterval: { fontSize: 10, color: C.textFaint, marginTop: 2 },
  duePill: {
    backgroundColor: C.warn + "33", borderRadius: 12,
    paddingHorizontal: 10, paddingVertical: 3,
  },
  duePillText: { color: C.warn, fontSize: 12, fontWeight: "600" },

  // FSRS stability
  stackedBar: {
    height: 14, borderRadius: 7, backgroundColor: C.surfaceAlt,
    overflow: "hidden", flexDirection: "row",
  },
  summaryRow: { flexDirection: "row", gap: 12, marginTop: 8 },
  summaryItem: { fontSize: 12, fontWeight: "600" },
  bucketGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 8 },
  bucketRow: { flexDirection: "row", alignItems: "center", gap: 5 },
  bucketDot: { width: 8, height: 8, borderRadius: 4 },
  bucketLabel: { fontSize: 11, color: C.textDim },
  bucketCount: { fontSize: 12, color: C.text, fontWeight: "600" },

  // Frequency
  freqBand: { marginBottom: 12 },
  freqBandTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" },
  freqBandLabel: { color: C.text, fontSize: 13, fontWeight: "600" },
  freqBandPct: { color: C.accent, fontSize: 13, fontWeight: "700" },
  freqTrack: {
    height: 6, borderRadius: 3, backgroundColor: C.surfaceAlt,
    overflow: "hidden", position: "relative", marginTop: 4, marginBottom: 4,
  },
  freqLearned: { position: "absolute", left: 0, top: 0, height: "100%", backgroundColor: C.known },
  freqAcquiring: { position: "absolute", top: 0, height: "100%", backgroundColor: C.acquiring + "AA" },
  freqEncountered: { position: "absolute", top: 0, height: "100%", backgroundColor: C.encountered + "AA" },
  freqDetail: { color: C.textDim, fontSize: 11 },

  // Activity chart
  chartArea: {
    flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between",
    height: 90, marginTop: 4,
  },
  barCol: { flex: 1, alignItems: "center" },
  barColInner: {
    flexDirection: "row", alignItems: "flex-end", justifyContent: "center",
    height: 75, gap: 1, position: "relative",
  },
  barReviews: { width: 6, backgroundColor: C.accent, borderRadius: 1 },
  barPages: { width: 6, backgroundColor: C.known, borderRadius: 1 },
  barNewMark: {
    position: "absolute", top: -3, alignSelf: "center",
    width: 5, height: 5, borderRadius: 2.5, backgroundColor: C.warn,
  },
  barDayLabel: { color: C.textFaint, fontSize: 9, marginTop: 4 },

  legendRow: { flexDirection: "row", gap: 14, marginTop: 8, flexWrap: "wrap" },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 4 },
  legendDot: { width: 8, height: 8, borderRadius: 4 },
  legendText: { color: C.textDim, fontSize: 11 },

  // Stories
  storyRow: { paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: C.border },
  storyHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" },
  storyTitle: { color: C.text, fontSize: 14, flex: 1, marginRight: 8 },
  storyMeta: { color: C.textDim, fontSize: 11 },
  storyTrack: {
    height: 4, borderRadius: 2, backgroundColor: C.surfaceAlt,
    overflow: "hidden", marginTop: 6, position: "relative",
  },
  storyProcessed: { position: "absolute", left: 0, top: 0, height: "100%", backgroundColor: C.encountered },
  storyViewed: { position: "absolute", left: 0, top: 0, height: "100%", backgroundColor: C.known },
  storyDetail: { color: C.textFaint, fontSize: 11, marginTop: 4 },

  // Activity feed
  activityRow: { paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: C.border },
  activityType: {
    color: C.accent, fontSize: 11, fontWeight: "700",
    textTransform: "uppercase", letterSpacing: 0.5,
  },
  activitySummary: { color: C.text, fontSize: 13, marginTop: 2 },
  activityTime: { color: C.textFaint, fontSize: 10, marginTop: 2 },

  error: { color: C.unknown, padding: 20 },
  footer: { color: C.textFaint, fontSize: 11, marginTop: 14, textAlign: "center" },
});
