import { useState, useCallback } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  Pressable,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { colors } from "../lib/theme";
import { getAnalytics, getDeepAnalytics } from "../lib/api";
import type { Analytics, DeepAnalytics, AcquisitionPipeline, ComprehensionBreakdown, GraduatedWord, IntroducedBySource, IntroducedWordDetail, InsightsData, StateTransitions } from "../lib/types";
import { IntroducedWordsTable } from "../lib/IntroducedWordsTable";
import { GraduatedWordsTable } from "../lib/GraduatedWordsTable";

export default function StatsScreen() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [deepAnalytics, setDeepAnalytics] = useState<DeepAnalytics | null>(null);
  const [loading, setLoading] = useState(true);

  useFocusEffect(
    useCallback(() => {
      loadAnalytics();
    }, [])
  );

  async function loadAnalytics() {
    setLoading(true);
    try {
      const [data, deep] = await Promise.all([
        getAnalytics(),
        getDeepAnalytics().catch(() => null),
      ]);
      setAnalytics(data);
      setDeepAnalytics(deep);
    } catch (e) {
      console.warn("Failed to load analytics:", e);
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (!analytics) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>Failed to load stats</Text>
        <Pressable style={styles.retryButton} onPress={loadAnalytics}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  const { stats, pace, cefr, daily_history } = analytics;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Your Progress</Text>

      {/* ═══ SECTION 1: TODAY ═══ */}
      <SectionHeader label="Today" />

      <TodayHeroCard
        comprehension={analytics.comprehension_today}
        graduated={analytics.graduated_today}
        introduced={analytics.introduced_today}
        introducedWords={analytics.introduced_words_today}
        calibration={analytics.calibration_signal}
        reviewsToday={stats.reviews_today}
        dueToday={stats.due_today}
        fsrsReviewedToday={stats.fsrs_reviewed_today || 0}
        streak={pace.current_streak}
        transitionsToday={deepAnalytics?.transitions_today}
        retentionPct={deepAnalytics?.retention_7d?.retention_pct ?? null}
      />

      {/* ═══ SECTION 2: VOCABULARY ═══ */}
      <SectionHeader label="Vocabulary" />

      <WordLifecycleFunnel
        encountered={stats.encountered}
        acquiring={stats.acquiring}
        learning={stats.learning}
        known={stats.known}
        lapsed={stats.lapsed}
        weeklyKnown={deepAnalytics?.transitions_7d?.learning_to_known ?? 0}
        weeklyLearning={deepAnalytics?.transitions_7d?.new_to_learning ?? 0}
        netPerDay={pace.words_per_day_7d}
        retentionPct={deepAnalytics?.retention_7d?.retention_pct ?? null}
        flowHistory={deepAnalytics?.acquisition_pipeline?.flow_history}
      />

      {/* Progress Benchmarks */}
      {analytics.benchmarks && (
        <View style={styles.cefrCard}>
          <Text style={styles.cefrLevel}>{analytics.benchmarks.total_words}</Text>
          <Text style={styles.cefrLabel}>Words Learned</Text>
          <Text style={styles.cefrCoverage}>{analytics.benchmarks.total_roots} root families</Text>

          {/* Textbook comparisons */}
          {analytics.benchmarks.textbooks.length > 0 && (
            <View style={{ width: "100%", marginTop: 16, gap: 12 }}>
              <Text style={{ fontSize: 13, color: colors.textSecondary, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.5 }}>
                Textbook Comparison
              </Text>
              {analytics.benchmarks.textbooks.map((tb: any) => (
                <View key={tb.name} style={{ gap: 4 }}>
                  <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" }}>
                    <Text style={{ fontSize: 14, color: colors.text, fontWeight: "600" }}>{tb.name}</Text>
                    <Text style={{ fontSize: 13, color: colors.accent, fontWeight: "700" }}>{tb.coverage_pct}%</Text>
                  </View>
                  <View style={styles.coverageTrack}>
                    <View style={[styles.coverageFill, { width: `${Math.min(tb.coverage_pct, 100)}%` }]} />
                  </View>
                  <Text style={{ fontSize: 11, color: colors.textSecondary }}>{tb.known_count}/{tb.total_words} words — {tb.description}</Text>
                </View>
              ))}
            </View>
          )}

          {/* Quran progress */}
          {analytics.benchmarks.quran && analytics.benchmarks.quran.verses_studied > 0 && (
            <View style={{ width: "100%", marginTop: 16, gap: 8 }}>
              <Text style={{ fontSize: 13, color: "#d4a056", fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.5 }}>
                Quran Progress
              </Text>
              {analytics.benchmarks.quran.current_surah ? (
                <Text style={{ fontSize: 13, color: colors.textSecondary }}>
                  Currently at: {analytics.benchmarks.quran.current_surah} ayah {analytics.benchmarks.quran.current_ayah}
                </Text>
              ) : null}
              <View style={{ flexDirection: "row", gap: 16, marginTop: 4 }}>
                <View style={{ alignItems: "center" }}>
                  <Text style={{ fontSize: 24, color: "#d4a056", fontWeight: "700" }}>{analytics.benchmarks.quran.verses_studied}</Text>
                  <Text style={{ fontSize: 11, color: colors.textSecondary }}>verses studied</Text>
                </View>
                <View style={{ alignItems: "center" }}>
                  <Text style={{ fontSize: 24, color: "#2ecc71", fontWeight: "700" }}>{analytics.benchmarks.quran.unique_words_in_studied}</Text>
                  <Text style={{ fontSize: 11, color: colors.textSecondary }}>mastered</Text>
                </View>
                {analytics.benchmarks.quran.verses_graduated > 0 && (
                  <View style={{ alignItems: "center" }}>
                    <Text style={{ fontSize: 24, color: colors.accent, fontWeight: "700" }}>{analytics.benchmarks.quran.verses_graduated}</Text>
                    <Text style={{ fontSize: 11, color: colors.textSecondary }}>graduated</Text>
                  </View>
                )}
              </View>
            </View>
          )}
        </View>
      )}

      {/* Acquisition Pipeline */}
      {deepAnalytics?.acquisition_pipeline && (
        <AcquisitionPipelineCard pipeline={deepAnalytics.acquisition_pipeline} insights={deepAnalytics?.insights} />
      )}

      {/* ═══ SECTION 3: PROGRESS ═══ */}
      <SectionHeader label="Progress" />

      {/* Known Words Growth */}
      {daily_history.length > 0 && (
        <KnownWordsGrowth history={daily_history} known={stats.known + stats.learning} />
      )}

      {/* Activity Chart (14-day reviews + words_learned overlay) */}
      {daily_history.length > 0 && (
        <View style={styles.historyCard}>
          <Text style={styles.sectionTitle}>Recent Activity</Text>
          <View style={styles.chartArea}>
            {daily_history.slice(-14).map((day) => {
              const last14 = daily_history.slice(-14);
              const maxReviews = Math.max(...last14.map((d) => d.reviews));
              const height = maxReviews > 0
                ? Math.max((day.reviews / maxReviews) * 80, 4)
                : 4;
              const learnedH = day.words_learned > 0 && maxReviews > 0
                ? Math.max((day.words_learned / maxReviews) * 80, 3)
                : 0;
              return (
                <View key={day.date} style={styles.barContainer}>
                  <View
                    style={[
                      styles.bar,
                      { height, backgroundColor: colors.accent },
                    ]}
                  />
                  {learnedH > 0 && (
                    <View
                      style={[
                        styles.barOverlay,
                        { height: learnedH, backgroundColor: colors.stateKnown },
                      ]}
                    />
                  )}
                  <Text style={styles.barLabel}>{day.date.slice(8)}</Text>
                </View>
              );
            })}
          </View>
          <View style={styles.historyLegend}>
            <Text style={styles.legendText}>
              Last {Math.min(daily_history.length, 14)} days
            </Text>
            <View style={{ flexDirection: "row", gap: 8 }}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
                <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent }} />
                <Text style={styles.legendText}>reviews</Text>
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
                <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.stateKnown }} />
                <Text style={styles.legendText}>new known</Text>
              </View>
            </View>
          </View>
        </View>
      )}

      {/* Learning Pace with 30d values */}
      <View style={styles.paceCard}>
        <Text style={styles.sectionTitle}>Learning Pace</Text>
        <View style={styles.paceGrid}>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.words_per_day_7d}</Text>
            <Text style={styles.paceLabel}>new known/day (7d)</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.words_per_day_30d}</Text>
            <Text style={styles.paceLabel}>new known/day (30d)</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.reviews_per_day_7d}</Text>
            <Text style={styles.paceLabel}>reviews/day (7d)</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.reviews_per_day_30d}</Text>
            <Text style={styles.paceLabel}>reviews/day (30d)</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.current_streak}</Text>
            <Text style={styles.paceLabel}>streak</Text>
          </View>
          {pace.longest_streak > pace.current_streak && (
            <View style={styles.paceItem}>
              <Text style={styles.paceValue}>{pace.longest_streak}</Text>
              <Text style={styles.paceLabel}>best</Text>
            </View>
          )}
          {pace.accuracy_7d !== null && (
            <View style={styles.paceItem}>
              <Text style={[styles.paceValue, {
                color: pace.accuracy_7d >= 80 ? colors.good : pace.accuracy_7d >= 60 ? colors.accent : colors.missed,
              }]}>{pace.accuracy_7d}%</Text>
              <Text style={styles.paceLabel}>7d acc.</Text>
            </View>
          )}
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.total_study_days}</Text>
            <Text style={styles.paceLabel}>total days</Text>
          </View>
          {stats.total_reviews > 0 && (
            <View style={styles.paceItem}>
              <Text style={styles.paceValue}>{stats.total_reviews}</Text>
              <Text style={styles.paceLabel}>total reviews</Text>
            </View>
          )}
          {(analytics.total_words_reviewed_7d ?? 0) > 0 && (
            <View style={styles.paceItem}>
              <Text style={styles.paceValue}>
                {((analytics.total_words_reviewed_7d ?? 0) / 200).toFixed(1)}
              </Text>
              <Text style={styles.paceLabel}>pages this week</Text>
            </View>
          )}
          {(analytics.total_words_reviewed_alltime ?? 0) > 0 && (
            <View style={styles.paceItem}>
              <Text style={styles.paceValue}>
                {analytics.total_words_reviewed_alltime ?? 0}
              </Text>
              <Text style={styles.paceLabel}>words read</Text>
            </View>
          )}
          {(analytics.unique_words_recognized_7d ?? 0) > 0 && (() => {
            const current = analytics.unique_words_recognized_7d ?? 0;
            const prior = analytics.unique_words_recognized_prior_7d ?? 0;
            const delta = current - prior;
            return (
              <View style={styles.paceItem}>
                <Text style={styles.paceValue}>{current}</Text>
                <Text style={styles.paceLabel}>
                  recognized
                  {delta !== 0 && prior > 0 && (
                    <Text style={{ color: delta > 0 ? colors.good : colors.missed }}>
                      {" "}{delta > 0 ? "+" : ""}{delta}
                    </Text>
                  )}
                </Text>
              </View>
            );
          })()}
        </View>
      </View>

      {/* Transitions: 7d and 30d side-by-side */}
      {deepAnalytics && (
        <TransitionsSection t7={deepAnalytics.transitions_7d} t30={deepAnalytics.transitions_30d} />
      )}

      {/* Retention: 7d and 30d */}
      {deepAnalytics && (
        <RetentionSection r7={deepAnalytics.retention_7d} r30={deepAnalytics.retention_30d} />
      )}

      {/* Comprehension: 7d and 30d side-by-side */}
      {deepAnalytics && (
        <ComprehensionSection data={deepAnalytics} />
      )}

      {/* ═══ SECTION 4: SESSIONS ═══ */}
      {deepAnalytics && deepAnalytics.recent_sessions.length > 0 && (
        <>
          <SectionHeader label="Sessions" />
          <SessionHistoryCard sessions={deepAnalytics.recent_sessions} />
        </>
      )}

      {/* ═══ SECTION 5: DEEP DIVE ═══ */}
      {deepAnalytics && (
        <>
          <SectionHeader label="Deep Dive" />
          <VocabularyHealthSection data={deepAnalytics} />
          {deepAnalytics.insights && (
            <InsightsCard insights={deepAnalytics.insights} />
          )}
          {deepAnalytics.struggling_words.length > 0 && (
            <StrugglingWordsSection words={deepAnalytics.struggling_words} />
          )}
          <RootProgressSection data={deepAnalytics.root_coverage} />
        </>
      )}
    </ScrollView>
    </View>
  );
}

// --- Section Header ---

function SectionHeader({ label }: { label: string }) {
  return (
    <View style={styles.sectionHeaderWrap}>
      <Text style={styles.sectionHeaderText}>{label}</Text>
    </View>
  );
}

// --- New Components ---

function WordLifecycleFunnel({
  encountered, acquiring, learning, known, lapsed,
  weeklyKnown, weeklyLearning, netPerDay, retentionPct, flowHistory,
}: {
  encountered: number; acquiring: number; learning: number; known: number; lapsed: number;
  weeklyKnown: number; weeklyLearning: number; netPerDay: number;
  retentionPct: number | null;
  flowHistory?: Array<{ date: string; entered: number; graduated: number }>;
}) {
  const stages = [
    { label: "Seen", count: encountered, color: colors.stateEncountered },
    { label: "Acq", count: acquiring, color: colors.stateAcquiring },
    { label: "Learn", count: learning, color: colors.stateLearning },
    { label: "Known", count: known, color: colors.good },
  ];

  // Compute flow rates from pipeline history
  let enteringPerDay = 0;
  let graduatingPerDay = 0;
  if (flowHistory?.length) {
    enteringPerDay = flowHistory.reduce((s, d) => s + d.entered, 0) / flowHistory.length;
    graduatingPerDay = flowHistory.reduce((s, d) => s + d.graduated, 0) / flowHistory.length;
  }

  // Hero trend text
  const trendParts: string[] = [];
  if (weeklyKnown > 0) trendParts.push(`+${weeklyKnown} this week`);
  if (netPerDay > 0) trendParts.push(`+${netPerDay.toFixed(1)}/day`);
  const trendText = trendParts.join(" \u00b7 ");

  if (stages.every(s => s.count === 0)) return null;

  return (
    <View style={styles.funnelCard}>
      {/* Hero: known words count */}
      <View style={vocStyles.hero}>
        <Text style={vocStyles.heroNum}>{known}</Text>
        <View>
          <Text style={vocStyles.heroLabel}>known words</Text>
          {trendText ? <Text style={vocStyles.heroTrend}>{trendText}</Text> : null}
        </View>
      </View>

      {/* Proportional flow strip */}
      <View style={vocStyles.flowStrip}>
        {stages.map((s, i) => (
          <View
            key={s.label}
            style={{
              flex: Math.max(s.count, 1),
              backgroundColor: s.color + "25",
              height: 8,
              borderTopLeftRadius: i === 0 ? 4 : 0,
              borderBottomLeftRadius: i === 0 ? 4 : 0,
              borderTopRightRadius: i === stages.length - 1 ? 4 : 0,
              borderBottomRightRadius: i === stages.length - 1 ? 4 : 0,
            }}
          />
        ))}
      </View>
      <View style={vocStyles.flowLabels}>
        {stages.map(s => (
          <View key={s.label} style={vocStyles.flowLabelItem}>
            <Text style={[vocStyles.flowCount, { color: s.color }]}>{s.count}</Text>
            <Text style={vocStyles.flowName}>{s.label}</Text>
          </View>
        ))}
      </View>

      {/* Flow rates */}
      {(enteringPerDay > 0 || graduatingPerDay > 0) && (
        <View style={vocStyles.flowRates}>
          {enteringPerDay > 0 && (
            <Text style={[vocStyles.flowRateText, { color: colors.stateAcquiring }]}>
              {"\u2192"} {enteringPerDay.toFixed(0)}/d intro
            </Text>
          )}
          {graduatingPerDay > 0 && (
            <Text style={[vocStyles.flowRateText, { color: colors.good }]}>
              {"\u2192"} {graduatingPerDay.toFixed(0)}/d grad
            </Text>
          )}
          <Text style={[vocStyles.flowRateText, { color: colors.textSecondary, opacity: 0.5 }]}>
            stab{"\u2265"}21d
          </Text>
        </View>
      )}

      {/* Detail cells: Acquiring + Learning */}
      <View style={vocStyles.sparkRow}>
        <View style={[vocStyles.sparkCell, { borderTopColor: colors.stateAcquiring + "60" }]}>
          <Text style={[vocStyles.sparkVal, { color: colors.stateAcquiring }]}>{acquiring}</Text>
          <Text style={vocStyles.sparkLabel}>Acquiring</Text>
        </View>
        <View style={[vocStyles.sparkCell, { borderTopColor: colors.stateLearning + "60" }]}>
          <Text style={[vocStyles.sparkVal, { color: colors.stateLearning }]}>{learning}</Text>
          <Text style={vocStyles.sparkLabel}>Learning</Text>
          {weeklyLearning > 0 && (
            <Text style={[vocStyles.sparkDelta, { color: colors.stateLearning }]}>+{weeklyLearning}/wk</Text>
          )}
        </View>
      </View>

      {/* Bottom chips */}
      <View style={vocStyles.chips}>
        {lapsed > 0 && (
          <View style={vocStyles.chip}>
            <Text style={vocStyles.chipLabel}>Lapsed</Text>
            <Text style={[vocStyles.chipVal, { color: colors.missed }]}>{lapsed}</Text>
          </View>
        )}
        <View style={vocStyles.chip}>
          <Text style={vocStyles.chipLabel}>Retention</Text>
          <Text style={vocStyles.chipVal}>
            {retentionPct != null && retentionPct > 0 ? `${Math.round(retentionPct)}%` : "\u2014"}
          </Text>
        </View>
      </View>
    </View>
  );
}

function KnownWordsGrowth({
  history, known,
}: {
  history: { date: string; words_learned: number; cumulative_known: number }[];
  known: number;
}) {
  const today = new Date();
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 7);
  const monthAgo = new Date(today);
  monthAgo.setDate(monthAgo.getDate() - 30);
  const weekStr = weekAgo.toISOString().slice(0, 10);
  const monthStr = monthAgo.toISOString().slice(0, 10);

  let weekDelta = 0;
  let monthDelta = 0;
  for (const day of history) {
    if (day.date >= weekStr) weekDelta += day.words_learned;
    if (day.date >= monthStr) monthDelta += day.words_learned;
  }

  // Use current state count, not cumulative (which never decreases even when words lapse)
  const latest = known;

  return (
    <View style={styles.growthCard}>
      <Text style={styles.growthNumber}>{latest}</Text>
      <Text style={styles.growthLabel}>known words</Text>
      <Text style={styles.growthSubtitle}>FSRS stability {"\u2265"} 21 days</Text>
      <View style={styles.growthDeltas}>
        {weekDelta > 0 && (
          <View style={styles.growthDelta}>
            <Text style={[styles.growthDeltaText, { color: colors.good }]}>+{weekDelta}</Text>
            <Text style={styles.growthDeltaPeriod}>this week</Text>
          </View>
        )}
        {monthDelta > 0 && (
          <View style={styles.growthDelta}>
            <Text style={[styles.growthDeltaText, { color: colors.good }]}>+{monthDelta}</Text>
            <Text style={styles.growthDeltaPeriod}>this month</Text>
          </View>
        )}
      </View>
    </View>
  );
}

function InsightsCard({ insights }: { insights: InsightsData }) {
  const tiles: { big: string; label: string; context?: string }[] = [];

  if (insights.avg_encounters_to_graduation != null) {
    const cmp = insights.avg_encounters_to_graduation <= 12 ? "efficient" : "above avg";
    tiles.push({ big: `~${insights.avg_encounters_to_graduation}`, label: "reviews to graduate", context: `Research says 8-12 · ${cmp}` });
  }
  if (insights.graduation_rate_pct != null) {
    tiles.push({ big: `${insights.graduation_rate_pct}%`, label: "graduated", context: `${100 - insights.graduation_rate_pct}% still acquiring` });
  }
  if (insights.best_weekday) {
    tiles.push({ big: insights.best_weekday.day_name.slice(0, 3), label: "best day", context: `${insights.best_weekday.accuracy_pct}% accuracy` });
  }
  if (insights.dark_horse_root) {
    const r = insights.dark_horse_root;
    tiles.push({ big: r.root, label: "untapped root", context: `${r.known}/${r.total} known · ${r.meaning || ""}` });
  }
  if (insights.record_intro_day) {
    tiles.push({ big: `${insights.record_intro_day.count}`, label: "record intros/day", context: insights.record_intro_day.date });
  }
  if (insights.record_graduation_day) {
    tiles.push({ big: `${insights.record_graduation_day.count}`, label: "record grads/day", context: insights.record_graduation_day.date });
  }
  if (insights.total_sentence_reviews > 0) {
    tiles.push({ big: `${insights.total_sentence_reviews}`, label: "total reviews", context: `${insights.unique_sentences_reviewed} unique sentences` });
  }
  const fc = insights.forgetting_forecast;
  if (fc && fc.skip_7d > 0) {
    tiles.push({ big: `${fc.skip_7d}`, label: "would lapse in 7d", context: `1d: ${fc.skip_1d} · 3d: ${fc.skip_3d}` });
  }

  if (tiles.length === 0) return null;

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Insights</Text>
      <View style={styles.insightsGrid}>
        {tiles.map((t, i) => (
          <View key={i} style={styles.insightTile}>
            <Text style={styles.insightBig}>{t.big}</Text>
            <Text style={styles.insightLabel}>{t.label}</Text>
            {t.context && <Text style={styles.insightContext}>{t.context}</Text>}
          </View>
        ))}
      </View>
    </View>
  );
}

function TransitionsSection({ t7, t30 }: { t7: StateTransitions; t30: StateTransitions }) {
  const has7d = t7.new_to_learning + t7.learning_to_known + t7.known_to_lapsed > 0;
  const has30d = t30.new_to_learning + t30.learning_to_known + t30.known_to_lapsed > 0;
  if (!has7d && !has30d) return null;

  const renderRow = (label: string, t: StateTransitions) => {
    const items: { text: string; color: string }[] = [];
    if (t.learning_to_known > 0) items.push({ text: `+${t.learning_to_known} known`, color: colors.good });
    if (t.new_to_learning > 0) items.push({ text: `+${t.new_to_learning} learning`, color: colors.accent });
    if (t.known_to_lapsed > 0) items.push({ text: `${t.known_to_lapsed} lapsed`, color: colors.missed });
    if (items.length === 0) return null;
    return (
      <View style={styles.velocityRow}>
        <Text style={styles.velocityPeriod}>{label}</Text>
        <View style={styles.velocityItems}>
          {items.map((it, i) => (
            <Text key={i} style={[styles.velocityItem, { color: it.color }]}>{it.text}</Text>
          ))}
        </View>
      </View>
    );
  };

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Transitions</Text>
      {has7d && renderRow("7 days", t7)}
      {has30d && renderRow("30 days", t30)}
    </View>
  );
}

function RetentionSection({ r7, r30 }: { r7: DeepAnalytics["retention_7d"]; r30: DeepAnalytics["retention_30d"] }) {
  if (r7.retention_pct === null && r30.retention_pct === null) return null;

  const renderRetention = (label: string, r: typeof r7) => {
    if (r.retention_pct === null) return null;
    return (
      <View style={styles.retentionRow}>
        <Text style={styles.retentionLabel}>{label}</Text>
        <Text style={[styles.retentionValue, {
          color: r.retention_pct >= 80 ? colors.good : r.retention_pct >= 60 ? colors.accent : colors.missed,
        }]}>{r.retention_pct}%</Text>
        <Text style={styles.retentionDetail}>({r.correct_reviews}/{r.total_reviews})</Text>
      </View>
    );
  };

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Retention</Text>
      {renderRetention("7-day", r7)}
      {renderRetention("30-day", r30)}
    </View>
  );
}

// --- Deep Analytics Components ---

const STABILITY_COLORS: Record<string, string> = {
  "<1h": "#e74c3c", "1h-12h": "#e67e22", "12h-1d": "#f39c12",
  "1-3d": "#f1c40f", "3-7d": "#2ecc71", "7-30d": "#27ae60", "30d+": "#1abc9c",
};

function VocabularyHealthSection({ data }: { data: DeepAnalytics }) {
  const buckets = data.stability_distribution;
  const total = buckets.reduce((s, b) => s + b.count, 0);
  if (total === 0) return null;

  const solid = buckets.filter(b => (b.min_days ?? 0) >= 7).reduce((s, b) => s + b.count, 0);
  const growing = buckets.filter(b => (b.min_days ?? 0) >= 1 && (b.max_days ?? Infinity) < 7).reduce((s, b) => s + b.count, 0);
  const fragile = buckets.filter(b => (b.max_days ?? Infinity) <= 1).reduce((s, b) => s + b.count, 0);

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Vocabulary Health</Text>
      <View style={styles.stabilityBar}>
        {buckets.map((b) => {
          if (b.count === 0) return null;
          const pct = (b.count / total) * 100;
          return (
            <View key={b.label} style={[styles.stabilitySegment, { width: `${Math.max(pct, 2)}%`, backgroundColor: STABILITY_COLORS[b.label] || colors.border }]} />
          );
        })}
      </View>
      <View style={styles.stabilityLegend}>
        {solid > 0 && <Text style={[styles.stabilityLabel, { color: "#27ae60" }]}>{solid} solid</Text>}
        {growing > 0 && <Text style={[styles.stabilityLabel, { color: "#f1c40f" }]}>{growing} growing</Text>}
        {fragile > 0 && <Text style={[styles.stabilityLabel, { color: "#e74c3c" }]}>{fragile} fragile</Text>}
      </View>
      <View style={styles.stabilityDetail}>
        {buckets.filter(b => b.count > 0).map((b) => (
          <View key={b.label} style={styles.stabilityDetailRow}>
            <View style={[styles.stabilityDot, { backgroundColor: STABILITY_COLORS[b.label] }]} />
            <Text style={styles.stabilityDetailLabel}>{b.label}</Text>
            <Text style={styles.stabilityDetailCount}>{b.count}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function ComprehensionSection({ data }: { data: DeepAnalytics }) {
  const c7 = data.comprehension_7d;
  const c30 = data.comprehension_30d;
  if (c7.total === 0 && c30.total === 0) return null;

  const renderBar = (label: string, c: ComprehensionBreakdown) => {
    if (c.total === 0) return null;
    const pctU = Math.round((c.understood / c.total) * 100);
    const pctP = Math.round((c.partial / c.total) * 100);
    const pctN = Math.round((c.no_idea / c.total) * 100);
    return (
      <View style={{ marginBottom: 12 }}>
        <Text style={styles.compPeriodLabel}>{label}</Text>
        <View style={styles.compBar}>
          {pctU > 0 && <View style={[styles.compSegment, { width: `${pctU}%`, backgroundColor: colors.good }]} />}
          {pctP > 0 && <View style={[styles.compSegment, { width: `${pctP}%`, backgroundColor: colors.accent }]} />}
          {pctN > 0 && <View style={[styles.compSegment, { width: `${pctN}%`, backgroundColor: colors.missed }]} />}
        </View>
        <View style={styles.compLegend}>
          <Text style={[styles.compLabel, { color: colors.good }]}>{pctU}%</Text>
          <Text style={[styles.compLabel, { color: colors.accent }]}>{pctP}%</Text>
          {pctN > 0 && <Text style={[styles.compLabel, { color: colors.missed }]}>{pctN}%</Text>}
          <Text style={styles.compTotal}>{c.total} reviews</Text>
        </View>
      </View>
    );
  };

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Comprehension</Text>
      <View style={styles.compLegend}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.good }} />
          <Text style={styles.legendText}>understood</Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent }} />
          <Text style={styles.legendText}>partial</Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.missed }} />
          <Text style={styles.legendText}>no idea</Text>
        </View>
      </View>
      {renderBar("7-day", c7)}
      {renderBar("30-day", c30)}
    </View>
  );
}

function StrugglingWordsSection({ words }: { words: DeepAnalytics["struggling_words"] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? words : words.slice(0, 5);
  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Needs Re-introduction</Text>
      <Text style={styles.strugglingHint}>{words.length} words, 3+ attempts, no success</Text>
      {shown.map((w) => (
        <View key={w.lemma_id} style={styles.strugglingRow}>
          <Text style={styles.strugglingAr}>{w.lemma_ar}</Text>
          <Text style={styles.strugglingEn}>{w.gloss_en}</Text>
          <Text style={styles.strugglingSeen}>{w.times_seen}x</Text>
        </View>
      ))}
      {words.length > 5 && (
        <Pressable onPress={() => setExpanded(!expanded)}>
          <Text style={styles.showMoreText}>{expanded ? "Show less" : `Show all ${words.length}`}</Text>
        </Pressable>
      )}
    </View>
  );
}

function RootProgressSection({ data }: { data: DeepAnalytics["root_coverage"] }) {
  if (data.total_roots === 0) return null;
  const pct = Math.round((data.roots_with_known / data.total_roots) * 100);
  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Root Progress</Text>
      <View style={styles.rootProgressHeader}>
        <Text style={styles.rootProgressCount}>{data.roots_with_known}/{data.total_roots} roots</Text>
        <Text style={styles.rootProgressPct}>{pct}%</Text>
      </View>
      <View style={styles.rootProgressTrack}>
        <View style={[styles.rootProgressFill, { width: `${pct}%` }]} />
      </View>
      {data.roots_fully_mastered > 0 && (
        <Text style={styles.rootProgressDetail}>{data.roots_fully_mastered} fully mastered</Text>
      )}
      {data.top_partial_roots.length > 0 && (
        <View style={styles.partialRoots}>
          <Text style={styles.partialRootsTitle}>Growing roots</Text>
          {data.top_partial_roots.slice(0, 3).map((r, i) => (
            <View key={i} style={styles.partialRootRow}>
              <Text style={styles.partialRootAr}>{r.root}</Text>
              <Text style={styles.partialRootMeaning}>{r.root_meaning}</Text>
              <Text style={styles.partialRootCount}>{r.known}/{r.total}</Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

// --- Existing Components ---

function TodayHeroCard({
  comprehension, graduated, introduced, introducedWords, calibration, reviewsToday, dueToday, fsrsReviewedToday, streak, transitionsToday, retentionPct,
}: {
  comprehension?: ComprehensionBreakdown; graduated?: GraduatedWord[]; introduced?: IntroducedBySource[]; introducedWords?: IntroducedWordDetail[];
  calibration?: string; reviewsToday: number; dueToday: number; fsrsReviewedToday: number; streak: number; transitionsToday?: StateTransitions; retentionPct?: number | null;
}) {
  if (reviewsToday === 0 && dueToday === 0) return null;

  const total = comprehension?.total || 0;
  const understood = comprehension?.understood || 0;
  const partial = comprehension?.partial || 0;
  const noIdea = comprehension?.no_idea || 0;

  const calibLabel = { well_calibrated: "Well calibrated", too_easy: "Sentences may be too easy", too_hard: "Sentences may be too hard", not_enough_data: "Keep going..." }[calibration || "not_enough_data"] || "Keep going...";
  const calibColor = { well_calibrated: colors.good, too_easy: colors.accent, too_hard: colors.missed, not_enough_data: colors.textSecondary }[calibration || "not_enough_data"] || colors.textSecondary;

  const hasTransitions = transitionsToday && (transitionsToday.learning_to_known + transitionsToday.new_to_learning + transitionsToday.known_to_lapsed) > 0;

  return (
    <View style={styles.heroCard}>
      <View style={styles.heroTop}>
        <View>
          {total > 0 && <Text style={styles.heroSentenceCount}>{total} sentences</Text>}
          {streak >= 2 && <Text style={styles.heroStreak}>{streak}d streak</Text>}
        </View>
        <Text style={[styles.heroCalibrLabel, { color: calibColor }]}>{calibLabel}</Text>
      </View>

      <View style={styles.heroStatus}>
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <Text style={[styles.heroStatusText, { color: colors.good }]}>
            {fsrsReviewedToday > 0 ? `${fsrsReviewedToday} reviewed today` : reviewsToday > 0 ? `${reviewsToday} reviews` : "No reviews yet"}
          </Text>
          {retentionPct != null && (
            <Text style={[styles.heroStatusText, { color: retentionPct >= 90 ? colors.good : retentionPct >= 85 ? colors.accent : colors.missed }]}>
              {retentionPct}% retention
            </Text>
          )}
        </View>
      </View>

      {total > 0 && (
        <>
          <View style={styles.heroCompBar}>
            {understood > 0 && <View style={[styles.heroCompSeg, { flex: understood, backgroundColor: colors.good }]} />}
            {partial > 0 && <View style={[styles.heroCompSeg, { flex: partial, backgroundColor: colors.accent }]} />}
            {noIdea > 0 && <View style={[styles.heroCompSeg, { flex: noIdea, backgroundColor: colors.missed }]} />}
          </View>
          <View style={styles.heroCompLegend}>
            <Text style={[styles.heroCompLabel, { color: colors.good }]}>{understood} understood</Text>
            <Text style={[styles.heroCompLabel, { color: colors.accent }]}>{partial} partial</Text>
            {noIdea > 0 && <Text style={[styles.heroCompLabel, { color: colors.missed }]}>{noIdea} no idea</Text>}
          </View>
        </>
      )}

      {hasTransitions && (
        <View style={styles.heroTransitions}>
          {transitionsToday!.learning_to_known > 0 && <Text style={[styles.heroTransText, { color: colors.good }]}>+{transitionsToday!.learning_to_known} known</Text>}
          {transitionsToday!.new_to_learning > 0 && <Text style={[styles.heroTransText, { color: colors.accent }]}>+{transitionsToday!.new_to_learning} learning</Text>}
          {transitionsToday!.known_to_lapsed > 0 && <Text style={[styles.heroTransText, { color: colors.missed }]}>{transitionsToday!.known_to_lapsed} lapsed</Text>}
        </View>
      )}

      {graduated && graduated.length > 0 && (
        <View style={styles.heroGrads}>
          <GraduatedWordsTable words={graduated} />
        </View>
      )}

      {introducedWords && introducedWords.length > 0 ? (
        <View style={styles.heroGrads}>
          <IntroducedWordsTable words={introducedWords} />
        </View>
      ) : introduced && introduced.length > 0 && (() => {
        const total = introduced.reduce((s, i) => s + i.count, 0);
        return (
          <View style={styles.heroGrads}>
            <Text style={styles.heroGradsLabel}>{total} new {total === 1 ? "word" : "words"} started:</Text>
            <View style={styles.heroGradPills}>
              {introduced.map((i) => (
                <View key={i.source} style={styles.heroIntroPill}>
                  <Text style={styles.heroIntroText}>{i.source} {i.count}</Text>
                </View>
              ))}
            </View>
          </View>
        );
      })()}
    </View>
  );
}

function AcquisitionPipelineCard({ pipeline, insights }: { pipeline: AcquisitionPipeline; insights?: InsightsData }) {
  const total = pipeline.box_1_count + pipeline.box_2_count + pipeline.box_3_count;
  const [expanded, setExpanded] = useState(false);
  if (total === 0 && pipeline.recent_graduations.length === 0) return null;

  // Compute flow rates from history
  const hasFlow = pipeline.flow_history?.length > 0;
  let enteringPerDay = 0;
  let graduatingPerDay = 0;
  if (hasFlow) {
    const days = pipeline.flow_history.length;
    enteringPerDay = pipeline.flow_history.reduce((s, d) => s + d.entered, 0) / days;
    graduatingPerDay = pipeline.flow_history.reduce((s, d) => s + d.graduated, 0) / days;
  }
  const maxFlow = hasFlow ? Math.max(...pipeline.flow_history.map(d => Math.max(d.entered, d.graduated)), 1) : 1;

  const gradRate = insights?.graduation_rate_pct;
  const avgReviews = insights?.avg_encounters_to_graduation;
  const ratio = graduatingPerDay > 0 ? enteringPerDay / graduatingPerDay : 0;
  const isHealthy = ratio > 0 && ratio <= 3;

  const boxes = [
    { label: "Box 1", count: pipeline.box_1_count, due: pipeline.box_1_due ?? 0, interval: "4h" },
    { label: "Box 2", count: pipeline.box_2_count, due: pipeline.box_2_due ?? 0, interval: "1d" },
    { label: "Box 3", count: pipeline.box_3_count, due: pipeline.box_3_due ?? 0, interval: "3d" },
  ];

  return (
    <View style={styles.deepCard}>
      <Pressable style={styles.pipeTitleRow} onPress={() => setExpanded(!expanded)}>
        <Text style={styles.sectionTitle}>Acquisition Pipeline</Text>
        <Text style={pipeStyles.count}>{total} words</Text>
      </Pressable>

      {/* Pressure bar rows */}
      {boxes.map(box => {
        const pct = total > 0 ? (box.count / total) * 100 : 0;
        return (
          <View key={box.label} style={pipeStyles.boxRow}>
            <Text style={pipeStyles.boxLabel}>{box.label}</Text>
            <View style={pipeStyles.barBg}>
              <View style={[pipeStyles.barFill, { width: `${Math.max(pct, 8)}%` }]}>
                <Text style={pipeStyles.barNum}>{box.count}</Text>
              </View>
              <Text style={pipeStyles.barInterval}>{box.interval}</Text>
            </View>
            {box.due > 0 && <Text style={pipeStyles.boxDue}>{box.due} due</Text>}
          </View>
        );
      })}

      {/* Throughput metrics */}
      <View style={pipeStyles.throughput}>
        {enteringPerDay > 0 && (
          <View style={pipeStyles.tpItem}>
            <Text style={[pipeStyles.tpVal, { color: colors.stateAcquiring }]}>{enteringPerDay.toFixed(1)}/d</Text>
            <Text style={pipeStyles.tpLabel}>Entering</Text>
          </View>
        )}
        {graduatingPerDay > 0 && (
          <View style={pipeStyles.tpItem}>
            <Text style={[pipeStyles.tpVal, { color: colors.good }]}>{graduatingPerDay.toFixed(1)}/d</Text>
            <Text style={pipeStyles.tpLabel}>Graduating</Text>
          </View>
        )}
        {gradRate != null && (
          <View style={pipeStyles.tpItem}>
            <Text style={pipeStyles.tpVal}>{Math.round(gradRate)}%</Text>
            <Text style={pipeStyles.tpLabel}>Grad Rate</Text>
          </View>
        )}
        {avgReviews != null && (
          <View style={pipeStyles.tpItem}>
            <Text style={pipeStyles.tpVal}>~{Math.round(avgReviews)}</Text>
            <Text style={pipeStyles.tpLabel}>To Grad</Text>
          </View>
        )}
      </View>

      {/* Balance bar */}
      {enteringPerDay > 0 && graduatingPerDay > 0 && (
        <>
          <View style={pipeStyles.balanceBar}>
            <View style={[pipeStyles.balanceSeg, { flex: enteringPerDay, backgroundColor: colors.stateAcquiring + "40" }]}>
              <Text style={[pipeStyles.balanceText, { color: colors.stateAcquiring }]}>
                {enteringPerDay.toFixed(0)} in
              </Text>
            </View>
            <View style={[pipeStyles.balanceSeg, { flex: graduatingPerDay, backgroundColor: colors.good + "40" }]}>
              <Text style={[pipeStyles.balanceText, { color: colors.good }]}>
                {graduatingPerDay.toFixed(0)} out
              </Text>
            </View>
          </View>
          <View style={pipeStyles.balanceLabels}>
            <Text style={pipeStyles.balanceLabelText}>entering</Text>
            <Text style={pipeStyles.balanceLabelText}>graduating</Text>
          </View>
        </>
      )}

      {/* Health banner */}
      {ratio > 0 && (
        <View style={[pipeStyles.healthBanner, { backgroundColor: (isHealthy ? colors.good : colors.missed) + "10" }]}>
          <View style={[pipeStyles.healthDot, { backgroundColor: isHealthy ? colors.good : colors.missed }]} />
          <Text style={[pipeStyles.healthText, { color: isHealthy ? colors.good : colors.missed }]}>
            {ratio.toFixed(0)}:1 ratio {"\u2014"} {isHealthy ? "pipeline balanced" : "pipeline building up"}
          </Text>
        </View>
      )}

      {/* 7-day flow chart */}
      {hasFlow && pipeline.flow_history.some(d => d.entered > 0 || d.graduated > 0) && (
        <View style={pipeStyles.flowSection}>
          <Text style={pipeStyles.flowTitle}>7-day flow</Text>
          <View style={pipeStyles.flowBars}>
            {pipeline.flow_history.map((d, i) => (
              <View key={i} style={pipeStyles.flowCol}>
                <View style={pipeStyles.flowBarGroup}>
                  {d.entered > 0 && <View style={[pipeStyles.flowBar, { height: Math.max((d.entered / maxFlow) * 32, 2), backgroundColor: colors.stateAcquiring }]} />}
                  {d.graduated > 0 && <View style={[pipeStyles.flowBar, { height: Math.max((d.graduated / maxFlow) * 32, 2), backgroundColor: colors.good }]} />}
                  {d.entered === 0 && d.graduated === 0 && <View style={[pipeStyles.flowBar, { height: 2, backgroundColor: colors.border }]} />}
                </View>
                <Text style={pipeStyles.flowDayLabel}>{d.date}</Text>
              </View>
            ))}
          </View>
          <View style={pipeStyles.flowLegend}>
            <View style={[pipeStyles.flowLegendDot, { backgroundColor: colors.stateAcquiring }]} />
            <Text style={pipeStyles.flowLegendText}>entered</Text>
            <View style={[pipeStyles.flowLegendDot, { backgroundColor: colors.good }]} />
            <Text style={pipeStyles.flowLegendText}>graduated</Text>
          </View>
        </View>
      )}

      {/* Recent graduations */}
      {pipeline.recent_graduations.length > 0 && (
        <View style={pipeStyles.gradsSection}>
          <Text style={pipeStyles.gradsTitle}>Recently graduated</Text>
          <View style={pipeStyles.gradsList}>
            {pipeline.recent_graduations.slice(0, expanded ? 15 : 5).map((g) => (
              <View key={g.lemma_id} style={pipeStyles.gradPill}>
                <Text style={pipeStyles.gradAr}>{g.lemma_ar}</Text>
                <Text style={pipeStyles.gradTime}>{_relTime(g.graduated_at)}</Text>
              </View>
            ))}
          </View>
        </View>
      )}
    </View>
  );
}

function SessionHistoryCard({ sessions }: { sessions: DeepAnalytics["recent_sessions"] }) {
  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Recent Sessions</Text>
      {sessions.slice(0, 7).map((s) => {
        const total = Object.values(s.comprehension).reduce((a, b) => a + b, 0);
        const understood = s.comprehension.understood || 0;
        const partial = s.comprehension.partial || 0;
        const noIdea = s.comprehension.no_idea || 0;
        const avgSec = s.avg_response_ms ? (s.avg_response_ms / 1000).toFixed(0) : null;
        return (
          <View key={s.session_id} style={styles.sessRow}>
            <Text style={styles.sessTime}>{_relTime(s.reviewed_at)}</Text>
            <Text style={styles.sessCount}>{s.sentence_count}</Text>
            {total > 0 && (
              <View style={styles.sessMiniBar}>
                {understood > 0 && <View style={[styles.sessMiniSeg, { flex: understood, backgroundColor: colors.good }]} />}
                {partial > 0 && <View style={[styles.sessMiniSeg, { flex: partial, backgroundColor: colors.accent }]} />}
                {noIdea > 0 && <View style={[styles.sessMiniSeg, { flex: noIdea, backgroundColor: colors.missed }]} />}
              </View>
            )}
            {avgSec && <Text style={styles.sessAvg}>{avgSec}s</Text>}
          </View>
        );
      })}
    </View>
  );
}

function _relTime(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  if (isNaN(then)) return iso;
  const diffMs = now - then;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  content: { padding: 20, alignItems: "center" },
  title: { fontSize: 24, color: colors.text, fontWeight: "700", marginBottom: 20 },

  // Section Header
  sectionHeaderWrap: { width: "100%", maxWidth: 500, marginTop: 24, marginBottom: 12, paddingLeft: 4 },
  sectionHeaderText: { fontSize: 11, color: colors.textSecondary, fontWeight: "700", textTransform: "uppercase", letterSpacing: 1.5 },

  // Today Hero
  heroCard: { backgroundColor: colors.surface, borderRadius: 16, padding: 20, width: "100%", maxWidth: 500, marginBottom: 16 },
  heroTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 },
  heroSentenceCount: { fontSize: 18, color: colors.text, fontWeight: "700" },
  heroStreak: { fontSize: 13, color: colors.accent, fontWeight: "600", marginTop: 2 },
  heroCalibrLabel: { fontSize: 12, fontWeight: "600" },
  heroCompBar: { flexDirection: "row", height: 20, borderRadius: 10, overflow: "hidden", backgroundColor: colors.surfaceLight, marginBottom: 8 },
  heroCompSeg: { height: "100%" },
  heroCompLegend: { flexDirection: "row", justifyContent: "center", gap: 12, marginBottom: 4 },
  heroCompLabel: { fontSize: 12, fontWeight: "600" },
  heroTransitions: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.border },
  heroTransText: { fontSize: 13, fontWeight: "600" },
  heroGrads: { marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.border },
  heroGradsLabel: { fontSize: 12, color: colors.textSecondary, marginBottom: 6 },
  heroGradPills: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  heroGradPill: { backgroundColor: colors.good + "20", borderRadius: 8, paddingHorizontal: 10, paddingVertical: 4 },
  heroGradAr: { fontSize: 14, color: colors.good, fontWeight: "600" },
  heroIntroPill: { backgroundColor: colors.accent + "20", borderRadius: 8, paddingHorizontal: 10, paddingVertical: 4 },
  heroIntroText: { fontSize: 12, color: colors.accent, fontWeight: "600" },
  heroStatus: { marginTop: 6, marginBottom: 4 },
  heroProgressBar: { flexDirection: "row", height: 6, borderRadius: 3, overflow: "hidden", backgroundColor: colors.surfaceLight, marginBottom: 6 },
  heroProgressFill: { height: "100%", backgroundColor: colors.good, borderRadius: 3 },
  heroProgressLabels: { flexDirection: "row", justifyContent: "space-between" },
  heroStatusText: { fontSize: 13 },
  heroStatusDetail: { fontSize: 11, marginTop: 2 },

  // Word Lifecycle Funnel
  funnelCard: { backgroundColor: colors.surface, borderRadius: 16, padding: 20, width: "100%", maxWidth: 500, marginBottom: 16 },
  funnelPipeline: { flexDirection: "row", alignItems: "center", marginBottom: 8 },
  funnelArrow: { fontSize: 14, color: colors.textSecondary, marginHorizontal: 2 },
  funnelStage: { flex: 1, alignItems: "center", borderWidth: 1, borderRadius: 10, paddingVertical: 8, paddingHorizontal: 4 },
  funnelCount: { fontSize: 20, fontWeight: "700" },
  funnelLabel: { fontSize: 9, color: colors.textSecondary, textTransform: "uppercase", letterSpacing: 0.5, marginTop: 2 },
  funnelLapsed: { alignItems: "flex-end", marginBottom: 4 },
  funnelLapsedText: { fontSize: 12, fontWeight: "600" },
  funnelSummary: { fontSize: 12, color: colors.textSecondary, textAlign: "center", marginTop: 4 },

  // CEFR Card
  cefrCard: { backgroundColor: colors.surface, borderRadius: 16, padding: 28, width: "100%", maxWidth: 500, alignItems: "center", marginBottom: 16 },
  cefrLevel: { fontSize: 42, color: colors.accent, fontWeight: "800" },
  cefrLabel: { fontSize: 14, color: colors.textSecondary, marginTop: 2, textTransform: "uppercase", letterSpacing: 1 },
  cefrCoverage: { fontSize: 13, color: colors.good, fontWeight: "600", marginTop: 6 },
  cefrMeta: { flexDirection: "row", flexWrap: "wrap", gap: 6, columnGap: 16, marginTop: 12 },
  cefrDetail: { fontSize: 13, color: colors.textSecondary },
  cefrPrediction: { fontSize: 12, color: colors.accent, flexBasis: "100%" },
  coverageBar: { width: "100%", marginTop: 16 },
  coverageTrack: { height: 8, backgroundColor: colors.surfaceLight, borderRadius: 4, overflow: "hidden", flexDirection: "row" },
  coverageFill: { height: "100%", backgroundColor: colors.accent },
  coverageFillAcquiring: { height: "100%", backgroundColor: colors.stateAcquiring, opacity: 0.5 },
  coverageLabel: { fontSize: 12, color: colors.textSecondary, marginTop: 6, textAlign: "center" },

  // Known Words Growth
  growthCard: { backgroundColor: colors.surface, borderRadius: 16, padding: 24, width: "100%", maxWidth: 500, alignItems: "center", marginBottom: 16 },
  growthNumber: { fontSize: 48, fontWeight: "800", color: colors.good },
  growthLabel: { fontSize: 14, color: colors.textSecondary, textTransform: "uppercase", letterSpacing: 1, marginTop: 2 },
  growthSubtitle: { fontSize: 11, color: colors.textSecondary, opacity: 0.6, marginTop: 2 },
  growthDeltas: { flexDirection: "row", gap: 16, marginTop: 12 },
  growthDelta: { alignItems: "center", backgroundColor: colors.good + "15", borderRadius: 10, paddingHorizontal: 14, paddingVertical: 6 },
  growthDeltaText: { fontSize: 18, fontWeight: "700" },
  growthDeltaPeriod: { fontSize: 11, color: colors.textSecondary, marginTop: 1 },

  // Activity Chart
  historyCard: { backgroundColor: colors.surface, borderRadius: 16, padding: 20, width: "100%", maxWidth: 500, marginBottom: 16 },
  chartArea: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-around", height: 100, gap: 4 },
  barContainer: { flex: 1, alignItems: "center", justifyContent: "flex-end" },
  bar: { width: "80%", borderRadius: 3, minHeight: 4 },
  barOverlay: { width: "40%", borderRadius: 2, marginTop: 2 },
  barLabel: { fontSize: 9, color: colors.textSecondary, marginTop: 4 },
  historyLegend: { flexDirection: "row", justifyContent: "space-between", marginTop: 10 },
  legendText: { fontSize: 12, color: colors.textSecondary },

  // Pace Card
  paceCard: { backgroundColor: colors.surface, borderRadius: 16, padding: 20, width: "100%", maxWidth: 500, marginBottom: 16 },
  paceGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  paceItem: { flexBasis: "47%", flexGrow: 1, backgroundColor: colors.surfaceLight, borderRadius: 10, padding: 14, alignItems: "center" },
  paceValue: { fontSize: 24, color: colors.text, fontWeight: "700" },
  paceLabel: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },

  // Transitions / Velocity
  velocityRow: { flexDirection: "row", alignItems: "center", marginBottom: 8, gap: 12 },
  velocityPeriod: { fontSize: 12, color: colors.textSecondary, fontWeight: "600", width: 55 },
  velocityItems: { flexDirection: "row", flexWrap: "wrap", gap: 8, flex: 1 },
  velocityItem: { fontSize: 13, fontWeight: "600" },

  // Retention
  retentionRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 },
  retentionLabel: { fontSize: 12, color: colors.textSecondary, width: 55 },
  retentionValue: { fontSize: 18, fontWeight: "700" },
  retentionDetail: { fontSize: 12, color: colors.textSecondary },

  // Comprehension
  compBar: { flexDirection: "row", height: 16, borderRadius: 8, overflow: "hidden", backgroundColor: colors.surfaceLight, marginBottom: 6 },
  compSegment: { height: "100%" },
  compLegend: { flexDirection: "row", justifyContent: "center", gap: 12, marginBottom: 8 },
  compLabel: { fontSize: 12, fontWeight: "600" },
  compTotal: { fontSize: 12, color: colors.textSecondary },
  compPeriodLabel: { fontSize: 12, color: colors.textSecondary, fontWeight: "600", marginBottom: 4 },

  // Deep card (shared)
  deepCard: { backgroundColor: colors.surface, borderRadius: 16, padding: 20, width: "100%", maxWidth: 500, marginBottom: 16 },
  sectionTitle: { fontSize: 18, color: colors.text, fontWeight: "600", marginBottom: 14 },

  // Stability / Vocabulary Health
  stabilityBar: { flexDirection: "row", height: 20, borderRadius: 10, overflow: "hidden", backgroundColor: colors.surfaceLight, marginBottom: 8 },
  stabilitySegment: { height: "100%" },
  stabilityLegend: { flexDirection: "row", justifyContent: "center", gap: 16, marginBottom: 10 },
  stabilityLabel: { fontSize: 13, fontWeight: "600" },
  stabilityDetail: { gap: 4 },
  stabilityDetailRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  stabilityDot: { width: 8, height: 8, borderRadius: 4 },
  stabilityDetailLabel: { fontSize: 12, color: colors.textSecondary, flex: 1 },
  stabilityDetailCount: { fontSize: 12, color: colors.text, fontWeight: "600" },

  // Insights
  insightsGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  insightTile: { flexBasis: "47%", flexGrow: 1, backgroundColor: colors.surfaceLight, borderRadius: 10, padding: 12 },
  insightBig: { fontSize: 20, color: colors.text, fontWeight: "700" },
  insightLabel: { fontSize: 11, color: colors.textSecondary, marginTop: 2 },
  insightContext: { fontSize: 10, color: colors.textSecondary, marginTop: 4, opacity: 0.7 },

  // Struggling words
  strugglingHint: { fontSize: 12, color: colors.textSecondary, marginBottom: 10 },
  strugglingRow: { flexDirection: "row", alignItems: "center", paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: colors.border, gap: 8 },
  strugglingAr: { fontSize: 16, color: colors.text, fontWeight: "600", width: 80, textAlign: "right" },
  strugglingEn: { fontSize: 13, color: colors.textSecondary, flex: 1 },
  strugglingSeen: { fontSize: 12, color: colors.missed, fontWeight: "600" },
  showMoreText: { fontSize: 13, color: colors.accent, fontWeight: "600", textAlign: "center", marginTop: 8 },

  // Root progress
  rootProgressHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 },
  rootProgressCount: { fontSize: 13, color: colors.textSecondary },
  rootProgressPct: { fontSize: 18, color: colors.accent, fontWeight: "700" },
  rootProgressTrack: { height: 8, backgroundColor: colors.surfaceLight, borderRadius: 4, overflow: "hidden", marginBottom: 8 },
  rootProgressFill: { height: "100%", backgroundColor: colors.accent, borderRadius: 4 },
  rootProgressDetail: { fontSize: 12, color: colors.good, marginBottom: 8 },
  partialRoots: { marginTop: 8, paddingTop: 8, borderTopWidth: 1, borderTopColor: colors.border },
  partialRootsTitle: { fontSize: 12, color: colors.textSecondary, fontWeight: "600", marginBottom: 6 },
  partialRootRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 4 },
  partialRootAr: { fontSize: 14, color: colors.text, fontWeight: "600", width: 50, textAlign: "right" },
  partialRootMeaning: { fontSize: 12, color: colors.textSecondary, flex: 1 },
  partialRootCount: { fontSize: 12, color: colors.accent, fontWeight: "600" },

  // Acquisition Pipeline
  pipeTitleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  pipeTotal: { fontSize: 13, color: colors.textSecondary, fontWeight: "600" },
  pipeBoxes: { flexDirection: "row", alignItems: "flex-start", gap: 4, marginTop: 8 },
  pipeBox: { flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 10, padding: 10 },
  pipeBoxHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  pipeBoxLabel: { fontSize: 11, color: colors.textSecondary, fontWeight: "600" },
  pipeBoxCount: { fontSize: 16, color: colors.text, fontWeight: "700" },
  pipeWord: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 3 },
  pipeWordAr: { fontSize: 14, color: colors.text, flex: 1 },
  pipeWordAcc: { fontSize: 11, color: colors.textSecondary, marginLeft: 4 },
  pipeMore: { fontSize: 11, color: colors.accent, marginTop: 4, textAlign: "center" as const },
  pipeDeltaNeg: { color: "#4ade80" },
  pipeDeltaPos: { color: "#60a5fa" },
  pipeArrow: { fontSize: 16, color: colors.textSecondary, marginTop: 30 },
  pipeDueBadge: { backgroundColor: colors.accent, borderRadius: 8, paddingHorizontal: 5, paddingVertical: 1, minWidth: 18, alignItems: "center" },
  pipeDueText: { color: colors.bg, fontSize: 10, fontWeight: "700" },
  pipeGrads: { marginTop: 12, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.border },
  pipeGradsTitle: { fontSize: 12, color: colors.textSecondary, fontWeight: "600", marginBottom: 6 },
  pipeGradRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 4 },
  pipeGradAr: { fontSize: 14, color: colors.text, fontWeight: "600", width: 70, textAlign: "right" },
  pipeGradEn: { fontSize: 12, color: colors.textSecondary, flex: 1 },
  pipeGradTime: { fontSize: 11, color: colors.textSecondary },
  pipeFlowChart: { marginTop: 12, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.border },
  pipeFlowTitle: { fontSize: 11, color: colors.textSecondary, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 },
  pipeFlowBars: { flexDirection: "row", alignItems: "flex-end", height: 56, gap: 3 },
  pipeFlowCol: { flex: 1, alignItems: "center", justifyContent: "flex-end", height: 56 },
  pipeFlowBarGroup: { flexDirection: "row", alignItems: "flex-end", gap: 2, justifyContent: "center" },
  pipeFlowBar: { width: 5, borderRadius: 2 },
  pipeFlowLabel: { fontSize: 9, color: colors.textSecondary, marginTop: 2 },
  pipeFlowLegend: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 6 },
  pipeFlowDot: { width: 8, height: 8, borderRadius: 4 },
  pipeFlowLegendText: { fontSize: 11, color: colors.textSecondary, marginRight: 8 },

  // Session History
  sessRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.border },
  sessTime: { fontSize: 12, color: colors.textSecondary, width: 70 },
  sessCount: { fontSize: 13, color: colors.text, fontWeight: "600", width: 24, textAlign: "center" },
  sessMiniBar: { flex: 1, flexDirection: "row", height: 8, borderRadius: 4, overflow: "hidden", backgroundColor: colors.surfaceLight },
  sessMiniSeg: { height: "100%" },
  sessAvg: { fontSize: 11, color: colors.textSecondary, width: 28, textAlign: "right" },

  // Error / Retry
  errorText: { color: colors.textSecondary, fontSize: 18, marginBottom: 16 },
  retryButton: { backgroundColor: colors.accent, paddingVertical: 10, paddingHorizontal: 24, borderRadius: 10 },
  retryText: { color: "#fff", fontSize: 16, fontWeight: "600" },
});

// Vocabulary card styles (mockup 17)
const vocStyles = StyleSheet.create({
  hero: { flexDirection: "row", alignItems: "baseline", gap: 10, marginBottom: 14 },
  heroNum: { fontSize: 40, fontWeight: "900", color: colors.good, lineHeight: 44 },
  heroLabel: { fontSize: 12, color: colors.good },
  heroTrend: { fontSize: 11, color: colors.good, opacity: 0.7 },
  flowStrip: { flexDirection: "row", overflow: "hidden", marginBottom: 6 },
  flowLabels: { flexDirection: "row", justifyContent: "space-between", marginBottom: 10 },
  flowLabelItem: { flex: 1, alignItems: "center" },
  flowCount: { fontSize: 14, fontWeight: "800" },
  flowName: { fontSize: 8, color: colors.textSecondary, textTransform: "uppercase", letterSpacing: 0.3 },
  flowRates: { flexDirection: "row", justifyContent: "space-around", marginBottom: 12, paddingVertical: 4 },
  flowRateText: { fontSize: 10, fontWeight: "600" },
  sparkRow: { flexDirection: "row", gap: 6, marginBottom: 10 },
  sparkCell: { flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 10, padding: 10, borderTopWidth: 2 },
  sparkVal: { fontSize: 20, fontWeight: "800" },
  sparkLabel: { fontSize: 9, color: colors.textSecondary, marginTop: 1 },
  sparkDelta: { fontSize: 9, fontWeight: "600", marginTop: 2 },
  chips: { flexDirection: "row", gap: 6 },
  chip: { flex: 1, flexDirection: "row", justifyContent: "space-between", alignItems: "center", backgroundColor: colors.surfaceLight, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 7 },
  chipLabel: { fontSize: 10, color: colors.textSecondary },
  chipVal: { fontSize: 14, fontWeight: "700", color: colors.text },
});

// Acquisition pipeline styles (mockup 17)
const pipeStyles = StyleSheet.create({
  count: { fontSize: 13, color: colors.stateAcquiring, fontWeight: "700" },
  boxRow: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 6 },
  boxLabel: { width: 42, fontSize: 11, fontWeight: "600", color: colors.stateAcquiring },
  barBg: { flex: 1, height: 24, backgroundColor: colors.surfaceLight, borderRadius: 8, overflow: "hidden", justifyContent: "center" },
  barFill: { height: "100%", backgroundColor: colors.stateAcquiring + "30", borderRadius: 8, justifyContent: "center", paddingLeft: 8 },
  barNum: { fontSize: 12, fontWeight: "700", color: colors.stateAcquiring },
  barInterval: { position: "absolute", right: 8, fontSize: 9, color: colors.textSecondary },
  boxDue: { width: 40, textAlign: "right", fontSize: 10, color: colors.missed, fontWeight: "600" },
  throughput: { flexDirection: "row", gap: 4, marginTop: 10, marginBottom: 8 },
  tpItem: { flex: 1, alignItems: "center", backgroundColor: colors.surfaceLight, borderRadius: 6, paddingVertical: 6 },
  tpVal: { fontSize: 13, fontWeight: "700", color: colors.text },
  tpLabel: { fontSize: 7, color: colors.textSecondary, textTransform: "uppercase", letterSpacing: 0.2, marginTop: 1 },
  balanceBar: { flexDirection: "row", height: 22, borderRadius: 6, overflow: "hidden", marginBottom: 3 },
  balanceSeg: { justifyContent: "center", alignItems: "center" },
  balanceText: { fontSize: 10, fontWeight: "600" },
  balanceLabels: { flexDirection: "row", justifyContent: "space-between", marginBottom: 8 },
  balanceLabelText: { fontSize: 8, color: colors.textSecondary },
  healthBanner: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8, marginBottom: 10 },
  healthDot: { width: 6, height: 6, borderRadius: 3 },
  healthText: { fontSize: 11 },
  flowSection: { marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.border },
  flowTitle: { fontSize: 11, color: colors.textSecondary, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 },
  flowBars: { flexDirection: "row", alignItems: "flex-end", height: 40, gap: 3 },
  flowCol: { flex: 1, alignItems: "center", justifyContent: "flex-end", height: 40 },
  flowBarGroup: { flexDirection: "row", alignItems: "flex-end", gap: 2, justifyContent: "center" },
  flowBar: { width: 5, borderRadius: 2 },
  flowDayLabel: { fontSize: 9, color: colors.textSecondary, marginTop: 2 },
  flowLegend: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 6 },
  flowLegendDot: { width: 8, height: 8, borderRadius: 4 },
  flowLegendText: { fontSize: 11, color: colors.textSecondary, marginRight: 8 },
  gradsSection: { marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.border },
  gradsTitle: { fontSize: 12, color: colors.textSecondary, fontWeight: "600", marginBottom: 6 },
  gradsList: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  gradPill: { backgroundColor: colors.good + "15", borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4, flexDirection: "row", alignItems: "center", gap: 4 },
  gradAr: { fontSize: 13, color: colors.good, fontWeight: "600" },
  gradTime: { fontSize: 9, color: colors.textSecondary },
});
