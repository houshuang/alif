import { useState, useCallback } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Alert,
} from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts } from "../lib/theme";
import { getActivity, ActivityEntry } from "../lib/api";
import { invalidateSessions } from "../lib/offline-store";

function relativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

const EVENT_ICONS: Record<string, { name: keyof typeof Ionicons.glyphMap; color: string }> = {
  flag_resolved: { name: "flag", color: colors.stateKnown },
  sentences_generated: { name: "document-text-outline", color: colors.accent },
  material_updated: { name: "sync-outline", color: colors.accent },
  audio_generated: { name: "volume-high-outline", color: colors.accent },
  sentences_retired: { name: "trash-outline", color: colors.textSecondary },
  frequency_backfill_completed: { name: "trending-up-outline", color: colors.stateLearning },
  grammar_backfill_completed: { name: "school-outline", color: colors.stateLearning },
  examples_backfill_completed: { name: "bulb-outline", color: colors.stateLearning },
  variant_cleanup_completed: { name: "git-merge-outline", color: colors.stateLearning },
  manual_action: { name: "construct-outline", color: colors.stateLearning },
  word_suspended: { name: "pause-circle-outline", color: colors.textSecondary },
  word_unsuspended: { name: "play-circle-outline", color: colors.stateKnown },
};

export default function MoreScreen() {
  const router = useRouter();
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [loadingActivity, setLoadingActivity] = useState(false);

  useFocusEffect(
    useCallback(() => {
      loadActivity();
    }, []),
  );

  async function loadActivity() {
    setLoadingActivity(true);
    try {
      const data = await getActivity(20);
      setActivity(data);
    } catch (e) {
      console.warn("Failed to load activity:", e);
    } finally {
      setLoadingActivity(false);
    }
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.sectionHeader}>Tools</Text>
      <NavRow
        icon="scan-outline"
        label="Textbook Scanner"
        onPress={() => router.push("/scanner")}
      />
      <NavRow
        icon="chatbubbles-outline"
        label="Chat History"
        onPress={() => router.push("/chats")}
      />

      <NavRow
        icon="trash-outline"
        label="Clear Cache"
        onPress={() => {
          invalidateSessions().then(() => {
            Alert.alert("Cache cleared", "Session and word data cache cleared. Pull to refresh.");
          });
        }}
      />

      <Text style={styles.sectionHeader}>Progress</Text>
      <NavRow
        icon="bar-chart-outline"
        label="Statistics"
        onPress={() => router.push("/stats")}
      />

      <Text style={styles.sectionHeader}>Activity</Text>
      {loadingActivity ? (
        <ActivityIndicator
          size="small"
          color={colors.accent}
          style={styles.activityLoader}
        />
      ) : activity.length === 0 ? (
        <Text style={styles.emptyText}>No recent activity</Text>
      ) : (
        activity.map((entry) => {
          const iconInfo = EVENT_ICONS[entry.event_type] ?? {
            name: "information-circle-outline" as keyof typeof Ionicons.glyphMap,
            color: colors.textSecondary,
          };
          return (
            <View key={entry.id} style={styles.activityRow}>
              <Ionicons
                name={iconInfo.name}
                size={18}
                color={iconInfo.color}
                style={styles.activityIcon}
              />
              <View style={styles.activityContent}>
                <Text style={styles.activitySummary} numberOfLines={2}>
                  {entry.summary}
                </Text>
                <Text style={styles.activityTime}>
                  {relativeTime(entry.created_at)}
                </Text>
              </View>
            </View>
          );
        })
      )}
    </ScrollView>
  );
}

function NavRow({
  icon,
  label,
  onPress,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable style={styles.navRow} onPress={onPress}>
      <View style={styles.navRowLeft}>
        <Ionicons name={icon} size={22} color={colors.text} />
        <Text style={styles.navRowLabel}>{label}</Text>
      </View>
      <Ionicons name="chevron-forward" size={18} color={colors.textSecondary} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  content: {
    paddingBottom: 40,
  },
  sectionHeader: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 1,
    paddingHorizontal: 20,
    paddingTop: 24,
    paddingBottom: 8,
  },
  navRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 14,
    paddingHorizontal: 20,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  navRowLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
  },
  navRowLabel: {
    color: colors.text,
    fontSize: fonts.body,
  },
  activityLoader: {
    marginTop: 20,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    textAlign: "center",
    marginTop: 20,
    fontStyle: "italic",
  },
  activityRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    paddingVertical: 12,
    paddingHorizontal: 20,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  activityIcon: {
    marginTop: 2,
    marginRight: 12,
  },
  activityContent: {
    flex: 1,
  },
  activitySummary: {
    color: colors.text,
    fontSize: fonts.small,
    lineHeight: 20,
  },
  activityTime: {
    color: colors.textSecondary,
    fontSize: 12,
    marginTop: 2,
  },
});
