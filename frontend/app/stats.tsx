import { useState, useCallback, useEffect } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  Pressable,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { colors, fonts, fontFamily } from "../lib/theme";
import { getAnalytics, getDeepAnalytics } from "../lib/api";
import { Analytics, DeepAnalytics, AcquisitionPipeline, ComprehensionBreakdown, GraduatedWord, IntroducedBySource } from "../lib/types";
import { syncEvents } from "../lib/sync-events";

export default function StatsScreen() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [deepAnalytics, setDeepAnalytics] = useState<DeepAnalytics | null>(null);
  const [loading, setLoading] = useState(true);

  useFocusEffect(
    useCallback(() => {
      loadAnalytics();
    }, [])
  );

  useEffect(() => {
    return syncEvents.on("synced", () => {
      loadAnalytics();
    });
  }, []);

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
      console.error("Failed to load analytics:", e);
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

      {/* Today Hero Card */}
      <TodayHeroCard
        comprehension={analytics.comprehension_today}
        graduated={analytics.graduated_today}
        introduced={analytics.introduced_today}
        calibration={analytics.calibration_signal}
        reviewsToday={stats.reviews_today}
        dueToday={stats.due_today}
        streak={pace.current_streak}
      />

      {/* Acquisition Pipeline */}
      {deepAnalytics?.acquisition_pipeline && (
        <AcquisitionPipelineCard pipeline={deepAnalytics.acquisition_pipeline} />
      )}

      {/* Session History */}
      {deepAnalytics && deepAnalytics.recent_sessions.length > 0 && (
        <SessionHistoryCard sessions={deepAnalytics.recent_sessions} />
      )}

      {/* CEFR Level Card */}
      <View style={styles.cefrCard}>
        <Text style={styles.cefrLevel}>{cefr.sublevel}</Text>
        <Text style={styles.cefrLabel}>Reading Level</Text>
        <View style={styles.cefrMeta}>
          <Text style={styles.cefrDetail}>
            {cefr.known_words} words known
            {cefr.acquiring_known > 0 && ` + ${cefr.acquiring_known} acquiring`}
          </Text>
          {cefr.next_level && (
            <Text style={styles.cefrDetail}>
              {cefr.words_to_next} words to {cefr.next_level}
            </Text>
          )}
          {cefr.days_to_next_weekly_pace != null && cefr.days_to_next_weekly_pace > 0 && (
            <Text style={styles.cefrPrediction}>
              ~{cefr.days_to_next_weekly_pace > 365
                ? `${Math.round(cefr.days_to_next_weekly_pace / 30)} months`
                : cefr.days_to_next_weekly_pace > 60
                  ? `${Math.round(cefr.days_to_next_weekly_pace / 7)} weeks`
                  : `${cefr.days_to_next_weekly_pace} days`
              } at this week's pace
            </Text>
          )}
          {cefr.days_to_next_today_pace != null && cefr.days_to_next_today_pace > 0 &&
           cefr.days_to_next_today_pace !== cefr.days_to_next_weekly_pace && (
            <Text style={styles.cefrPrediction}>
              ~{cefr.days_to_next_today_pace > 365
                ? `${Math.round(cefr.days_to_next_today_pace / 30)} months`
                : cefr.days_to_next_today_pace > 60
                  ? `${Math.round(cefr.days_to_next_today_pace / 7)} weeks`
                  : `${cefr.days_to_next_today_pace} days`
              } at today's pace
            </Text>
          )}
        </View>
        {cefr.next_level && cefr.words_to_next != null && (() => {
          const threshold = cefr.known_words + cefr.words_to_next;
          const knownPct = Math.min((cefr.known_words / threshold) * 100, 100);
          const acqPct = Math.min((cefr.acquiring_known / threshold) * 100, 100 - knownPct);
          return (
            <View style={styles.coverageBar}>
              <View style={styles.coverageTrack}>
                <View
                  style={[
                    styles.coverageFill,
                    { width: `${knownPct}%` },
                  ]}
                />
                {acqPct > 0 && (
                  <View
                    style={[
                      styles.coverageFillAcquiring,
                      { width: `${acqPct}%` },
                    ]}
                  />
                )}
              </View>
              <Text style={styles.coverageLabel}>
                {cefr.known_words}{cefr.acquiring_known > 0 ? ` + ${cefr.acquiring_known}` : ""} / {threshold} to {cefr.next_level}
              </Text>
            </View>
          );
        })()}
      </View>

      {/* Pace Card */}
      <View style={styles.paceCard}>
        <Text style={styles.sectionTitle}>Learning Pace</Text>
        <View style={styles.paceGrid}>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.words_per_day_7d}</Text>
            <Text style={styles.paceLabel}>words/day</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.reviews_per_day_7d}</Text>
            <Text style={styles.paceLabel}>reviews/day</Text>
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
                {(analytics.total_words_reviewed_alltime ?? 0).toLocaleString()}
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

      {/* Quick Stats Grid */}
      <View style={styles.grid}>
        {stats.acquiring > 0 && (
          <StatCard
            label="Acquiring"
            value={stats.acquiring}
            color={colors.stateAcquiring}
          />
        )}
        <StatCard
          label="Learning"
          value={stats.learning}
          color={colors.stateLearning}
        />
        <StatCard
          label="Total Reviews"
          value={stats.total_reviews}
          color={colors.good}
        />
        {stats.lapsed > 0 && (
          <StatCard
            label="Lapsed"
            value={stats.lapsed}
            color={colors.missed}
          />
        )}
        {stats.encountered > 0 && (
          <StatCard
            label="Encountered"
            value={stats.encountered}
            color={colors.stateEncountered}
          />
        )}
        {stats.new > 0 && (
          <StatCard
            label="New"
            value={stats.new}
            color={colors.stateNew}
          />
        )}
      </View>

      {/* Daily History Mini Chart */}
      {daily_history.length > 0 && (
        <View style={styles.historyCard}>
          <Text style={styles.sectionTitle}>Recent Activity</Text>
          <View style={styles.chartArea}>
            {daily_history.slice(-14).map((day) => {
              const maxReviews = Math.max(
                ...daily_history.slice(-14).map((d) => d.reviews)
              );
              const height = maxReviews > 0
                ? Math.max((day.reviews / maxReviews) * 80, 4)
                : 4;
              return (
                <View key={day.date} style={styles.barContainer}>
                  <View
                    style={[
                      styles.bar,
                      {
                        height,
                        backgroundColor:
                          day.accuracy && day.accuracy >= 80
                            ? colors.good
                            : colors.accent,
                      },
                    ]}
                  />
                  <Text style={styles.barLabel}>
                    {day.date.slice(8)}
                  </Text>
                </View>
              );
            })}
          </View>
          <View style={styles.historyLegend}>
            <Text style={styles.legendText}>
              Last {Math.min(daily_history.length, 14)} days
            </Text>
            <Text style={styles.legendText}>
              Avg accuracy:{" "}
              {(
                daily_history
                  .filter((d) => d.accuracy !== null)
                  .reduce((s, d) => s + (d.accuracy || 0), 0) /
                Math.max(
                  daily_history.filter((d) => d.accuracy !== null).length,
                  1
                )
              ).toFixed(1)}
              %
            </Text>
          </View>
        </View>
      )}
      {/* Deep Analytics */}
      {deepAnalytics && (
        <>
          <VocabularyHealthSection data={deepAnalytics} />
          <LearningVelocitySection data={deepAnalytics} />
          <ComprehensionSection data={deepAnalytics} />
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

// --- Deep Analytics Components ---

const STABILITY_COLORS: Record<string, string> = {
  "<1h": "#e74c3c",
  "1h-12h": "#e67e22",
  "12h-1d": "#f39c12",
  "1-3d": "#f1c40f",
  "3-7d": "#2ecc71",
  "7-30d": "#27ae60",
  "30d+": "#1abc9c",
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
            <View
              key={b.label}
              style={[
                styles.stabilitySegment,
                {
                  width: `${Math.max(pct, 2)}%`,
                  backgroundColor: STABILITY_COLORS[b.label] || colors.border,
                },
              ]}
            />
          );
        })}
      </View>
      <View style={styles.stabilityLegend}>
        {solid > 0 && (
          <Text style={[styles.stabilityLabel, { color: "#27ae60" }]}>
            {solid} solid
          </Text>
        )}
        {growing > 0 && (
          <Text style={[styles.stabilityLabel, { color: "#f1c40f" }]}>
            {growing} growing
          </Text>
        )}
        {fragile > 0 && (
          <Text style={[styles.stabilityLabel, { color: "#e74c3c" }]}>
            {fragile} fragile
          </Text>
        )}
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

function LearningVelocitySection({ data }: { data: DeepAnalytics }) {
  const t = data.transitions_today;
  const t7 = data.transitions_7d;
  const hasToday = t.new_to_learning + t.learning_to_known + t.known_to_lapsed > 0;
  const has7d = t7.new_to_learning + t7.learning_to_known + t7.known_to_lapsed > 0;

  if (!hasToday && !has7d) return null;

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Learning Velocity</Text>
      {hasToday && (
        <View style={styles.velocityRow}>
          <Text style={styles.velocityPeriod}>Today</Text>
          <View style={styles.velocityItems}>
            {t.learning_to_known > 0 && (
              <Text style={[styles.velocityItem, { color: colors.good }]}>
                +{t.learning_to_known} known
              </Text>
            )}
            {t.new_to_learning > 0 && (
              <Text style={[styles.velocityItem, { color: colors.accent }]}>
                +{t.new_to_learning} learning
              </Text>
            )}
            {t.known_to_lapsed > 0 && (
              <Text style={[styles.velocityItem, { color: colors.missed }]}>
                {t.known_to_lapsed} lapsed
              </Text>
            )}
          </View>
        </View>
      )}
      {has7d && (
        <View style={styles.velocityRow}>
          <Text style={styles.velocityPeriod}>7 days</Text>
          <View style={styles.velocityItems}>
            {t7.learning_to_known > 0 && (
              <Text style={[styles.velocityItem, { color: colors.good }]}>
                +{t7.learning_to_known} known
              </Text>
            )}
            {t7.new_to_learning > 0 && (
              <Text style={[styles.velocityItem, { color: colors.accent }]}>
                +{t7.new_to_learning} learning
              </Text>
            )}
            {t7.known_to_lapsed > 0 && (
              <Text style={[styles.velocityItem, { color: colors.missed }]}>
                {t7.known_to_lapsed} lapsed
              </Text>
            )}
          </View>
        </View>
      )}
      {data.retention_7d.retention_pct !== null && (
        <View style={styles.retentionRow}>
          <Text style={styles.retentionLabel}>7-day retention</Text>
          <Text style={[styles.retentionValue, {
            color: (data.retention_7d.retention_pct ?? 0) >= 80 ? colors.good
              : (data.retention_7d.retention_pct ?? 0) >= 60 ? colors.accent
              : colors.missed,
          }]}>
            {data.retention_7d.retention_pct}%
          </Text>
          <Text style={styles.retentionDetail}>
            ({data.retention_7d.correct_reviews}/{data.retention_7d.total_reviews} correct)
          </Text>
        </View>
      )}
    </View>
  );
}

function ComprehensionSection({ data }: { data: DeepAnalytics }) {
  const c = data.comprehension_7d;
  if (c.total === 0) return null;

  const pctU = Math.round((c.understood / c.total) * 100);
  const pctP = Math.round((c.partial / c.total) * 100);
  const pctN = Math.round((c.no_idea / c.total) * 100);

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Comprehension (7d)</Text>
      <View style={styles.compBar}>
        {pctU > 0 && (
          <View style={[styles.compSegment, { width: `${pctU}%`, backgroundColor: colors.good }]} />
        )}
        {pctP > 0 && (
          <View style={[styles.compSegment, { width: `${pctP}%`, backgroundColor: colors.accent }]} />
        )}
        {pctN > 0 && (
          <View style={[styles.compSegment, { width: `${pctN}%`, backgroundColor: colors.missed }]} />
        )}
      </View>
      <View style={styles.compLegend}>
        <Text style={[styles.compLabel, { color: colors.good }]}>{pctU}% understood</Text>
        <Text style={[styles.compLabel, { color: colors.accent }]}>{pctP}% partial</Text>
        {pctN > 0 && (
          <Text style={[styles.compLabel, { color: colors.missed }]}>{pctN}% no idea</Text>
        )}
      </View>
      <Text style={styles.compTotal}>{c.total} sentence reviews</Text>
    </View>
  );
}

function StrugglingWordsSection({ words }: { words: DeepAnalytics["struggling_words"] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? words : words.slice(0, 5);

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Needs Re-introduction</Text>
      <Text style={styles.strugglingHint}>
        {words.length} words, 3+ attempts, no success
      </Text>
      {shown.map((w) => (
        <View key={w.lemma_id} style={styles.strugglingRow}>
          <Text style={styles.strugglingAr}>{w.lemma_ar}</Text>
          <Text style={styles.strugglingEn}>{w.gloss_en}</Text>
          <Text style={styles.strugglingSeen}>{w.times_seen}x</Text>
        </View>
      ))}
      {words.length > 5 && (
        <Pressable onPress={() => setExpanded(!expanded)}>
          <Text style={styles.showMoreText}>
            {expanded ? "Show less" : `Show all ${words.length}`}
          </Text>
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
        <Text style={styles.rootProgressCount}>
          {data.roots_with_known}/{data.total_roots} roots
        </Text>
        <Text style={styles.rootProgressPct}>{pct}%</Text>
      </View>
      <View style={styles.rootProgressTrack}>
        <View style={[styles.rootProgressFill, { width: `${pct}%` }]} />
      </View>
      {data.roots_fully_mastered > 0 && (
        <Text style={styles.rootProgressDetail}>
          {data.roots_fully_mastered} fully mastered
        </Text>
      )}
      {data.top_partial_roots.length > 0 && (
        <View style={styles.partialRoots}>
          <Text style={styles.partialRootsTitle}>Growing roots</Text>
          {data.top_partial_roots.slice(0, 3).map((r, i) => (
            <View key={i} style={styles.partialRootRow}>
              <Text style={styles.partialRootAr}>{r.root}</Text>
              <Text style={styles.partialRootMeaning}>{r.root_meaning}</Text>
              <Text style={styles.partialRootCount}>
                {r.known}/{r.total}
              </Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

// --- New Progression Components ---

function TodayHeroCard({
  comprehension,
  graduated,
  introduced,
  calibration,
  reviewsToday,
  dueToday,
  streak,
}: {
  comprehension?: ComprehensionBreakdown;
  graduated?: GraduatedWord[];
  introduced?: IntroducedBySource[];
  calibration?: string;
  reviewsToday: number;
  dueToday: number;
  streak: number;
}) {
  if (reviewsToday === 0) return null;

  const total = comprehension?.total || 0;
  const understood = comprehension?.understood || 0;
  const partial = comprehension?.partial || 0;
  const noIdea = comprehension?.no_idea || 0;

  const calibLabel = {
    well_calibrated: "Well calibrated",
    too_easy: "Sentences may be too easy",
    too_hard: "Sentences may be too hard",
    not_enough_data: "Keep going...",
  }[calibration || "not_enough_data"] || "Keep going...";

  const calibColor = {
    well_calibrated: colors.good,
    too_easy: colors.accent,
    too_hard: colors.missed,
    not_enough_data: colors.textSecondary,
  }[calibration || "not_enough_data"] || colors.textSecondary;

  return (
    <View style={styles.heroCard}>
      <View style={styles.heroTop}>
        <View>
          <Text style={styles.heroSentenceCount}>{total} sentences</Text>
          {streak >= 2 && (
            <Text style={styles.heroStreak}>{streak}d streak</Text>
          )}
        </View>
        <Text style={[styles.heroCalibrLabel, { color: calibColor }]}>
          {calibLabel}
        </Text>
      </View>

      {/* Due cards status */}
      {(dueToday > 0 || reviewsToday > 0) && (
        <View style={styles.heroStatus}>
          {dueToday === 0 ? (
            <Text style={[styles.heroStatusText, { color: colors.good }]}>
              All caught up
            </Text>
          ) : reviewsToday >= dueToday ? (
            <Text style={[styles.heroStatusText, { color: colors.good }]}>
              {reviewsToday} reviews · caught up
            </Text>
          ) : (
            <Text style={[styles.heroStatusText, { color: colors.accent }]}>
              {reviewsToday} reviews · {dueToday - reviewsToday} still waiting
            </Text>
          )}
        </View>
      )}

      {total > 0 && (
        <>
          <View style={styles.heroCompBar}>
            {understood > 0 && (
              <View style={[styles.heroCompSeg, { flex: understood, backgroundColor: colors.good }]} />
            )}
            {partial > 0 && (
              <View style={[styles.heroCompSeg, { flex: partial, backgroundColor: colors.accent }]} />
            )}
            {noIdea > 0 && (
              <View style={[styles.heroCompSeg, { flex: noIdea, backgroundColor: colors.missed }]} />
            )}
          </View>
          <View style={styles.heroCompLegend}>
            <Text style={[styles.heroCompLabel, { color: colors.good }]}>
              {understood} understood
            </Text>
            <Text style={[styles.heroCompLabel, { color: colors.accent }]}>
              {partial} partial
            </Text>
            {noIdea > 0 && (
              <Text style={[styles.heroCompLabel, { color: colors.missed }]}>
                {noIdea} no idea
              </Text>
            )}
          </View>
        </>
      )}

      {graduated && graduated.length > 0 && (
        <View style={styles.heroGrads}>
          <Text style={styles.heroGradsLabel}>Graduated today:</Text>
          <View style={styles.heroGradPills}>
            {graduated.map((w) => (
              <View key={w.lemma_id} style={styles.heroGradPill}>
                <Text style={styles.heroGradAr}>{w.lemma_ar}</Text>
              </View>
            ))}
          </View>
        </View>
      )}

      {introduced && introduced.length > 0 && (() => {
        const total = introduced.reduce((s, i) => s + i.count, 0);
        return (
          <View style={styles.heroGrads}>
            <Text style={styles.heroGradsLabel}>
              {total} new {total === 1 ? "word" : "words"} started:
            </Text>
            <View style={styles.heroGradPills}>
              {introduced.map((i) => (
                <View key={i.source} style={styles.heroIntroPill}>
                  <Text style={styles.heroIntroText}>
                    {i.source} {i.count}
                  </Text>
                </View>
              ))}
            </View>
          </View>
        );
      })()}
    </View>
  );
}

function AcquisitionPipelineCard({ pipeline }: { pipeline: AcquisitionPipeline }) {
  const total = pipeline.box_1_count + pipeline.box_2_count + pipeline.box_3_count;
  const [expanded, setExpanded] = useState(false);

  if (total === 0 && pipeline.recent_graduations.length === 0) return null;

  const renderBox = (
    label: string,
    words: typeof pipeline.box_1,
    count: number,
    due: number,
  ) => (
    <View style={styles.pipeBox}>
      <View style={styles.pipeBoxHeader}>
        <Text style={styles.pipeBoxLabel}>{label}</Text>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          {due > 0 && (
            <View style={styles.pipeDueBadge}>
              <Text style={styles.pipeDueText}>{due}</Text>
            </View>
          )}
          <Text style={styles.pipeBoxCount}>{count}</Text>
        </View>
      </View>
      {(expanded ? words : words.slice(0, 3)).map((w) => {
        const acc = w.times_seen > 0 ? Math.round(w.times_correct / w.times_seen * 100) : 0;
        return (
          <View key={w.lemma_id} style={styles.pipeWord}>
            <Text style={styles.pipeWordAr} numberOfLines={1}>{w.lemma_ar}</Text>
            <Text style={styles.pipeWordAcc}>{acc}%</Text>
          </View>
        );
      })}
      {!expanded && words.length > 3 && (
        <Text style={styles.pipeMore}>+{words.length - 3}</Text>
      )}
    </View>
  );

  // Flow chart
  const hasFlow = pipeline.flow_history && pipeline.flow_history.some(d => d.entered > 0 || d.graduated > 0);
  const maxFlow = hasFlow
    ? Math.max(...pipeline.flow_history.map(d => Math.max(d.entered, d.graduated)), 1)
    : 1;

  return (
    <View style={styles.deepCard}>
      <Pressable
        style={styles.pipeTitleRow}
        onPress={() => setExpanded(!expanded)}
      >
        <Text style={styles.sectionTitle}>Acquisition Pipeline</Text>
        <Text style={styles.pipeTotal}>{total} words</Text>
      </Pressable>
      <View style={styles.pipeBoxes}>
        {renderBox("Box 1\n4h", pipeline.box_1, pipeline.box_1_count, pipeline.box_1_due ?? 0)}
        <Text style={styles.pipeArrow}>{"\u2192"}</Text>
        {renderBox("Box 2\n1d", pipeline.box_2, pipeline.box_2_count, pipeline.box_2_due ?? 0)}
        <Text style={styles.pipeArrow}>{"\u2192"}</Text>
        {renderBox("Box 3\n3d", pipeline.box_3, pipeline.box_3_count, pipeline.box_3_due ?? 0)}
      </View>

      {hasFlow && (
        <View style={styles.pipeFlowChart}>
          <Text style={styles.pipeFlowTitle}>7-day flow</Text>
          <View style={styles.pipeFlowBars}>
            {pipeline.flow_history.map((d, i) => {
              const enteredH = (d.entered / maxFlow) * 48;
              const gradH = (d.graduated / maxFlow) * 48;
              return (
                <View key={i} style={styles.pipeFlowCol}>
                  <View style={styles.pipeFlowBarGroup}>
                    {d.entered > 0 && (
                      <View style={[styles.pipeFlowBar, { height: Math.max(enteredH, 3), backgroundColor: colors.accent }]} />
                    )}
                    {d.graduated > 0 && (
                      <View style={[styles.pipeFlowBar, { height: Math.max(gradH, 3), backgroundColor: colors.good }]} />
                    )}
                    {d.entered === 0 && d.graduated === 0 && (
                      <View style={[styles.pipeFlowBar, { height: 3, backgroundColor: colors.border }]} />
                    )}
                  </View>
                  <Text style={styles.pipeFlowLabel}>{d.date}</Text>
                </View>
              );
            })}
          </View>
          <View style={styles.pipeFlowLegend}>
            <View style={[styles.pipeFlowDot, { backgroundColor: colors.accent }]} />
            <Text style={styles.pipeFlowLegendText}>entered</Text>
            <View style={[styles.pipeFlowDot, { backgroundColor: colors.good }]} />
            <Text style={styles.pipeFlowLegendText}>graduated</Text>
          </View>
        </View>
      )}

      {pipeline.recent_graduations.length > 0 && (
        <View style={styles.pipeGrads}>
          <Text style={styles.pipeGradsTitle}>Recently graduated</Text>
          {pipeline.recent_graduations.slice(0, expanded ? 15 : 5).map((g) => (
            <View key={g.lemma_id} style={styles.pipeGradRow}>
              <Text style={styles.pipeGradAr}>{g.lemma_ar}</Text>
              <Text style={styles.pipeGradEn} numberOfLines={1}>{g.gloss_en}</Text>
              <Text style={styles.pipeGradTime}>{_relTime(g.graduated_at)}</Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

function SessionHistoryCard({ sessions }: { sessions: DeepAnalytics["recent_sessions"] }) {
  const shown = sessions.slice(0, 7);

  return (
    <View style={styles.deepCard}>
      <Text style={styles.sectionTitle}>Recent Sessions</Text>
      {shown.map((s) => {
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
                {understood > 0 && (
                  <View style={[styles.sessMiniSeg, { flex: understood, backgroundColor: colors.good }]} />
                )}
                {partial > 0 && (
                  <View style={[styles.sessMiniSeg, { flex: partial, backgroundColor: colors.accent }]} />
                )}
                {noIdea > 0 && (
                  <View style={[styles.sessMiniSeg, { flex: noIdea, backgroundColor: colors.missed }]} />
                )}
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

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <View style={styles.statCard}>
      <Text style={[styles.statValue, { color }]}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  centered: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  content: {
    padding: 20,
    alignItems: "center",
  },
  title: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "700",
    marginBottom: 20,
  },
  heroCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 20,
    width: "100%",
    maxWidth: 500,
    marginBottom: 16,
  },
  heroTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 12,
  },
  heroSentenceCount: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "700",
  },
  heroStreak: {
    fontSize: 13,
    color: colors.accent,
    fontWeight: "600",
    marginTop: 2,
  },
  heroCalibrLabel: {
    fontSize: 12,
    fontWeight: "600",
  },
  heroCompBar: {
    flexDirection: "row",
    height: 20,
    borderRadius: 10,
    overflow: "hidden",
    backgroundColor: colors.surfaceLight,
    marginBottom: 8,
  },
  heroCompSeg: {
    height: "100%",
  },
  heroCompLegend: {
    flexDirection: "row",
    justifyContent: "center",
    gap: 12,
    marginBottom: 4,
  },
  heroCompLabel: {
    fontSize: 12,
    fontWeight: "600",
  },
  heroGrads: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  heroGradsLabel: {
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 6,
  },
  heroGradPills: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  heroGradPill: {
    backgroundColor: colors.good + "20",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  heroGradAr: {
    fontSize: 14,
    color: colors.good,
    fontWeight: "600",
  },
  heroIntroPill: {
    backgroundColor: colors.accent + "20",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  heroIntroText: {
    fontSize: 12,
    color: colors.accent,
    fontWeight: "600",
  },
  pipeTitleRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  pipeTotal: {
    fontSize: 13,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  pipeBoxes: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 4,
    marginTop: 8,
  },
  pipeBox: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    padding: 10,
  },
  pipeBoxHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  pipeBoxLabel: {
    fontSize: 11,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  pipeBoxCount: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "700",
  },
  pipeWord: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 3,
  },
  pipeWordAr: {
    fontSize: 14,
    color: colors.text,
    flex: 1,
  },
  pipeWordAcc: {
    fontSize: 11,
    color: colors.textSecondary,
    marginLeft: 4,
  },
  pipeMore: {
    fontSize: 11,
    color: colors.accent,
    marginTop: 4,
    textAlign: "center",
  },
  pipeArrow: {
    fontSize: 16,
    color: colors.textSecondary,
    marginTop: 30,
  },
  pipeGrads: {
    marginTop: 12,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  pipeGradsTitle: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: "600",
    marginBottom: 6,
  },
  pipeGradRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 4,
  },
  pipeGradAr: {
    fontSize: 14,
    color: colors.text,
    fontWeight: "600",
    width: 70,
    textAlign: "right",
  },
  pipeGradEn: {
    fontSize: 12,
    color: colors.textSecondary,
    flex: 1,
  },
  pipeGradTime: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  heroStatus: {
    marginTop: 6,
    marginBottom: 4,
  },
  heroStatusText: {
    fontSize: 13,
    fontFamily: fontFamily.mono,
  },
  pipeDueBadge: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: 5,
    paddingVertical: 1,
    minWidth: 18,
    alignItems: "center",
  },
  pipeDueText: {
    color: colors.bg,
    fontSize: 10,
    fontFamily: fontFamily.mono,
    fontWeight: "700",
  },
  pipeFlowChart: {
    marginTop: 12,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  pipeFlowTitle: {
    fontSize: 11,
    color: colors.textSecondary,
    fontFamily: fontFamily.mono,
    marginBottom: 6,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  pipeFlowBars: {
    flexDirection: "row",
    alignItems: "flex-end",
    height: 56,
    gap: 3,
  },
  pipeFlowCol: {
    flex: 1,
    alignItems: "center",
    justifyContent: "flex-end",
    height: 56,
  },
  pipeFlowBarGroup: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 2,
    justifyContent: "center",
  },
  pipeFlowBar: {
    width: 5,
    borderRadius: 2,
  },
  pipeFlowLabel: {
    fontSize: 9,
    color: colors.textSecondary,
    fontFamily: fontFamily.mono,
    marginTop: 2,
  },
  pipeFlowLegend: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: 6,
  },
  pipeFlowDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  pipeFlowLegendText: {
    fontSize: 11,
    color: colors.textSecondary,
    fontFamily: fontFamily.mono,
    marginRight: 8,
  },
  sessRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  sessTime: {
    fontSize: 12,
    color: colors.textSecondary,
    width: 70,
  },
  sessCount: {
    fontSize: 13,
    color: colors.text,
    fontWeight: "600",
    width: 24,
    textAlign: "center",
  },
  sessMiniBar: {
    flex: 1,
    flexDirection: "row",
    height: 8,
    borderRadius: 4,
    overflow: "hidden",
    backgroundColor: colors.surfaceLight,
  },
  sessMiniSeg: {
    height: "100%",
  },
  sessAvg: {
    fontSize: 11,
    color: colors.textSecondary,
    width: 28,
    textAlign: "right",
  },
  sectionTitle: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 14,
  },
  cefrCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 28,
    width: "100%",
    maxWidth: 500,
    alignItems: "center",
    marginBottom: 16,
  },
  cefrLevel: {
    fontSize: 42,
    color: colors.accent,
    fontWeight: "800",
  },
  cefrLabel: {
    fontSize: 14,
    color: colors.textSecondary,
    marginTop: 2,
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  cefrMeta: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    columnGap: 16,
    marginTop: 12,
  },
  cefrDetail: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  cefrPrediction: {
    fontSize: 12,
    color: colors.accent,
    flexBasis: "100%",
  },
  coverageBar: {
    width: "100%",
    marginTop: 16,
  },
  coverageTrack: {
    height: 8,
    backgroundColor: colors.surfaceLight,
    borderRadius: 4,
    overflow: "hidden",
    flexDirection: "row",
  },
  coverageFill: {
    height: "100%",
    backgroundColor: colors.accent,
  },
  coverageFillAcquiring: {
    height: "100%",
    backgroundColor: colors.stateAcquiring,
    opacity: 0.5,
  },
  coverageLabel: {
    fontSize: 12,
    color: colors.textSecondary,
    marginTop: 6,
    textAlign: "center",
  },
  paceCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 20,
    width: "100%",
    maxWidth: 500,
    marginBottom: 16,
  },
  paceGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  paceItem: {
    flexBasis: "47%",
    flexGrow: 1,
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    padding: 14,
    alignItems: "center",
  },
  paceValue: {
    fontSize: 24,
    color: colors.text,
    fontWeight: "700",
  },
  paceLabel: {
    fontSize: 12,
    color: colors.textSecondary,
    marginTop: 2,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    width: "100%",
    maxWidth: 500,
    marginBottom: 16,
  },
  statCard: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 18,
    alignItems: "center",
    flexBasis: "47%",
    flexGrow: 1,
  },
  statValue: {
    fontSize: 28,
    fontWeight: "700",
  },
  statLabel: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginTop: 4,
  },
  historyCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 20,
    width: "100%",
    maxWidth: 500,
  },
  chartArea: {
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-around",
    height: 100,
    gap: 4,
  },
  barContainer: {
    flex: 1,
    alignItems: "center",
    justifyContent: "flex-end",
  },
  bar: {
    width: "80%",
    borderRadius: 3,
    minHeight: 4,
  },
  barLabel: {
    fontSize: 9,
    color: colors.textSecondary,
    marginTop: 4,
  },
  historyLegend: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 10,
  },
  legendText: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 18,
    marginBottom: 16,
  },
  retryButton: {
    backgroundColor: colors.accent,
    paddingVertical: 10,
    paddingHorizontal: 24,
    borderRadius: 10,
  },
  retryText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "600",
  },
  grammarCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 20,
    width: "100%",
    maxWidth: 500,
    marginTop: 16,
  },
  grammarSummary: {
    fontSize: 13,
    color: colors.textSecondary,
    marginBottom: 8,
  },
  grammarLegend: {
    flexDirection: "row",
    gap: 12,
    marginBottom: 14,
  },
  grammarLegendItem: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  grammarCategory: {
    marginBottom: 12,
  },
  grammarCatTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textSecondary,
    textTransform: "capitalize",
    marginBottom: 6,
    opacity: 0.8,
  },
  grammarFeatureRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  grammarFeatureLabel: {
    fontSize: 12,
    color: colors.text,
    width: 120,
  },
  grammarBarTrack: {
    flex: 1,
    height: 6,
    backgroundColor: colors.surfaceLight,
    borderRadius: 3,
    overflow: "hidden",
  },
  grammarBarFill: {
    height: "100%",
    borderRadius: 3,
  },
  grammarFeatureCount: {
    fontSize: 11,
    color: colors.textSecondary,
    width: 24,
    textAlign: "right",
  },
  // Deep analytics styles
  deepCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 20,
    width: "100%",
    maxWidth: 500,
    marginTop: 16,
  },
  stabilityBar: {
    flexDirection: "row",
    height: 20,
    borderRadius: 10,
    overflow: "hidden",
    backgroundColor: colors.surfaceLight,
    marginBottom: 8,
  },
  stabilitySegment: {
    height: "100%",
  },
  stabilityLegend: {
    flexDirection: "row",
    justifyContent: "center",
    gap: 16,
    marginBottom: 10,
  },
  stabilityLabel: {
    fontSize: 13,
    fontWeight: "600",
  },
  stabilityDetail: {
    gap: 4,
  },
  stabilityDetailRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  stabilityDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  stabilityDetailLabel: {
    fontSize: 12,
    color: colors.textSecondary,
    flex: 1,
  },
  stabilityDetailCount: {
    fontSize: 12,
    color: colors.text,
    fontWeight: "600",
  },
  velocityRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 8,
    gap: 12,
  },
  velocityPeriod: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: "600",
    width: 50,
  },
  velocityItems: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    flex: 1,
  },
  velocityItem: {
    fontSize: 13,
    fontWeight: "600",
  },
  retentionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 6,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  retentionLabel: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  retentionValue: {
    fontSize: 18,
    fontWeight: "700",
  },
  retentionDetail: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  compBar: {
    flexDirection: "row",
    height: 16,
    borderRadius: 8,
    overflow: "hidden",
    backgroundColor: colors.surfaceLight,
    marginBottom: 8,
  },
  compSegment: {
    height: "100%",
  },
  compLegend: {
    flexDirection: "row",
    justifyContent: "center",
    gap: 12,
    marginBottom: 4,
  },
  compLabel: {
    fontSize: 12,
    fontWeight: "600",
  },
  compTotal: {
    fontSize: 12,
    color: colors.textSecondary,
    textAlign: "center",
    marginTop: 4,
  },
  strugglingHint: {
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 10,
  },
  strugglingRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    gap: 8,
  },
  strugglingAr: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "600",
    width: 80,
    textAlign: "right",
  },
  strugglingEn: {
    fontSize: 13,
    color: colors.textSecondary,
    flex: 1,
  },
  strugglingSeen: {
    fontSize: 12,
    color: colors.missed,
    fontWeight: "600",
  },
  showMoreText: {
    fontSize: 13,
    color: colors.accent,
    fontWeight: "600",
    textAlign: "center",
    marginTop: 8,
  },
  rootProgressHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
    marginBottom: 8,
  },
  rootProgressCount: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  rootProgressPct: {
    fontSize: 18,
    color: colors.accent,
    fontWeight: "700",
  },
  rootProgressTrack: {
    height: 8,
    backgroundColor: colors.surfaceLight,
    borderRadius: 4,
    overflow: "hidden",
    marginBottom: 8,
  },
  rootProgressFill: {
    height: "100%",
    backgroundColor: colors.accent,
    borderRadius: 4,
  },
  rootProgressDetail: {
    fontSize: 12,
    color: colors.good,
    marginBottom: 8,
  },
  partialRoots: {
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  partialRootsTitle: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: "600",
    marginBottom: 6,
  },
  partialRootRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  partialRootAr: {
    fontSize: 14,
    color: colors.text,
    fontWeight: "600",
    width: 50,
    textAlign: "right",
  },
  partialRootMeaning: {
    fontSize: 12,
    color: colors.textSecondary,
    flex: 1,
  },
  partialRootCount: {
    fontSize: 12,
    color: colors.accent,
    fontWeight: "600",
  },
});
