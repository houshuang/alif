import { useState, useCallback } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Alert,
  Modal,
} from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts } from "../lib/theme";
import {
  getActivity,
  ActivityEntry,
  getTopicSettings,
  getAvailableTopics,
  setActiveTopic,
} from "../lib/api";
import { TopicSettings, TopicInfo } from "../lib/types";
import { TOPIC_LABELS } from "../lib/topic-labels";
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
  const [topicSettings, setTopicSettings] = useState<TopicSettings | null>(null);
  const [topicPickerVisible, setTopicPickerVisible] = useState(false);
  const [availableTopics, setAvailableTopics] = useState<TopicInfo[]>([]);
  const [loadingTopics, setLoadingTopics] = useState(false);

  useFocusEffect(
    useCallback(() => {
      loadActivity();
      loadTopicSettings();
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

  async function loadTopicSettings() {
    try {
      const data = await getTopicSettings();
      setTopicSettings(data);
    } catch (e) {
      console.warn("Failed to load topic settings:", e);
    }
  }

  async function openTopicPicker() {
    setTopicPickerVisible(true);
    setLoadingTopics(true);
    try {
      const data = await getAvailableTopics();
      setAvailableTopics(data);
    } catch (e) {
      console.warn("Failed to load topics:", e);
    } finally {
      setLoadingTopics(false);
    }
  }

  async function handleSetTopic(domain: string) {
    try {
      await setActiveTopic(domain);
      setTopicPickerVisible(false);
      await loadTopicSettings();
    } catch (e) {
      Alert.alert("Error", "Failed to change topic");
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

      <Text style={styles.sectionHeader}>Learning Topic</Text>
      <Pressable style={styles.topicRow} onPress={openTopicPicker}>
        <View style={styles.topicRowLeft}>
          <Ionicons name="bookmark-outline" size={22} color={colors.accent} />
          <View>
            <Text style={styles.topicName}>
              {topicSettings?.active_topic
                ? TOPIC_LABELS[topicSettings.active_topic] || topicSettings.active_topic
                : "No topic selected"}
            </Text>
            {topicSettings?.active_topic && (
              <Text style={styles.topicProgress}>
                {topicSettings.words_introduced_in_topic}/{topicSettings.max_topic_batch} words introduced
              </Text>
            )}
          </View>
        </View>
        <Ionicons name="chevron-forward" size={18} color={colors.textSecondary} />
      </Pressable>

      <Modal
        visible={topicPickerVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setTopicPickerVisible(false)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Choose Topic</Text>
            <Pressable onPress={() => setTopicPickerVisible(false)}>
              <Ionicons name="close" size={24} color={colors.text} />
            </Pressable>
          </View>
          {loadingTopics ? (
            <ActivityIndicator size="large" color={colors.accent} style={{ marginTop: 40 }} />
          ) : (
            <ScrollView style={styles.modalScroll}>
              {availableTopics.map((t) => {
                const isActive = topicSettings?.active_topic === t.domain;
                return (
                  <Pressable
                    key={t.domain}
                    style={[styles.topicItem, isActive && styles.topicItemActive]}
                    onPress={() => !isActive && t.eligible && handleSetTopic(t.domain)}
                    disabled={isActive || !t.eligible}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={[
                        styles.topicItemName,
                        !t.eligible && styles.topicItemDisabled,
                      ]}>
                        {t.label}
                        {isActive ? "  (current)" : ""}
                      </Text>
                      <Text style={styles.topicItemStats}>
                        {t.available_words} available, {t.learned_words} learned
                      </Text>
                    </View>
                    {!t.eligible && !isActive && (
                      <Text style={styles.topicItemIneligible}>too few</Text>
                    )}
                  </Pressable>
                );
              })}
            </ScrollView>
          )}
        </View>
      </Modal>

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
  topicRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 14,
    paddingHorizontal: 20,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  topicRowLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
  },
  topicName: {
    color: colors.text,
    fontSize: fonts.body,
    fontWeight: "600",
  },
  topicProgress: {
    color: colors.textSecondary,
    fontSize: 12,
    marginTop: 2,
  },
  modalContainer: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  modalTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "700",
  },
  modalScroll: {
    flex: 1,
  },
  topicItem: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 14,
    paddingHorizontal: 20,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  topicItemActive: {
    backgroundColor: colors.accent + "15",
  },
  topicItemName: {
    color: colors.text,
    fontSize: fonts.body,
    fontWeight: "500",
  },
  topicItemDisabled: {
    color: colors.textSecondary,
    opacity: 0.5,
  },
  topicItemStats: {
    color: colors.textSecondary,
    fontSize: 12,
    marginTop: 2,
  },
  topicItemIneligible: {
    color: colors.textSecondary,
    fontSize: 12,
    fontStyle: "italic",
  },
});
