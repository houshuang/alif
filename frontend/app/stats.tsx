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
import { colors, fonts } from "../lib/theme";
import { getAnalytics } from "../lib/api";
import { Analytics } from "../lib/types";

export default function StatsScreen() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);

  useFocusEffect(
    useCallback(() => {
      loadAnalytics();
    }, [])
  );

  async function loadAnalytics() {
    setLoading(true);
    try {
      const data = await getAnalytics();
      setAnalytics(data);
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
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Your Progress</Text>

      {/* CEFR Level Card */}
      <View style={styles.cefrCard}>
        <Text style={styles.cefrLevel}>{cefr.sublevel}</Text>
        <Text style={styles.cefrLabel}>Reading Level</Text>
        <View style={styles.cefrMeta}>
          <Text style={styles.cefrDetail}>
            {cefr.known_words} words known
          </Text>
          {cefr.next_level && (
            <Text style={styles.cefrDetail}>
              {cefr.words_to_next} words to {cefr.next_level}
            </Text>
          )}
        </View>
        <View style={styles.coverageBar}>
          <View style={styles.coverageTrack}>
            <View
              style={[
                styles.coverageFill,
                { width: `${Math.min(cefr.reading_coverage_pct, 100)}%` },
              ]}
            />
          </View>
          <Text style={styles.coverageLabel}>
            {cefr.reading_coverage_pct}% text coverage
          </Text>
        </View>
      </View>

      {/* Pace Card */}
      <View style={styles.paceCard}>
        <Text style={styles.sectionTitle}>Learning Pace</Text>
        <View style={styles.paceGrid}>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.words_per_day_7d}</Text>
            <Text style={styles.paceLabel}>words/day (7d)</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.reviews_per_day_7d}</Text>
            <Text style={styles.paceLabel}>reviews/day (7d)</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.current_streak}</Text>
            <Text style={styles.paceLabel}>day streak</Text>
          </View>
          <View style={styles.paceItem}>
            <Text style={styles.paceValue}>{pace.total_study_days}</Text>
            <Text style={styles.paceLabel}>study days</Text>
          </View>
        </View>
      </View>

      {/* Quick Stats Grid */}
      <View style={styles.grid}>
        <StatCard
          label="Due Today"
          value={stats.due_today}
          color={colors.accent}
        />
        <StatCard
          label="Reviewed Today"
          value={stats.reviews_today}
          color={colors.good}
        />
        <StatCard
          label="Learning"
          value={stats.learning}
          color={colors.stateLearning}
        />
        <StatCard
          label="New"
          value={stats.new}
          color={colors.stateNew}
        />
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
    </ScrollView>
  );
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
    gap: 16,
    marginTop: 12,
  },
  cefrDetail: {
    fontSize: 13,
    color: colors.textSecondary,
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
  },
  coverageFill: {
    height: "100%",
    backgroundColor: colors.accent,
    borderRadius: 4,
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
});
