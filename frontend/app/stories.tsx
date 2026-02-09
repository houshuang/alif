import { useState, useCallback } from "react";
import {
  View,
  Text,
  FlatList,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Modal,
  Alert,
  Platform,
} from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, fontFamily } from "../lib/theme";
import { getStories, generateStory, importStory, deleteStory, prefetchStoryDetails } from "../lib/api";
import { netStatus } from "../lib/net-status";
import { StoryListItem } from "../lib/types";

type StoryLength = "short" | "medium" | "long";

const LENGTH_LABELS: Record<StoryLength, { label: string; desc: string }> = {
  short: { label: "Short", desc: "2-4 sentences" },
  medium: { label: "Medium", desc: "4-7 sentences" },
  long: { label: "Long", desc: "7-12 sentences" },
};

export default function StoriesScreen() {
  const [stories, setStories] = useState<StoryListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  // Generate modal
  const [showGenerate, setShowGenerate] = useState(false);
  const [genLength, setGenLength] = useState<StoryLength>("medium");
  const [genTopic, setGenTopic] = useState("");

  // Import modal
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState("");
  const [importTitle, setImportTitle] = useState("");
  const [importing, setImporting] = useState(false);

  const router = useRouter();

  useFocusEffect(
    useCallback(() => {
      loadStories();
    }, [])
  );

  async function loadStories() {
    setLoading(true);
    try {
      const data = await getStories();
      setStories(data);
      if (netStatus.isOnline) {
        const active = data.filter((s) => s.status === "active");
        if (active.length > 0) {
          prefetchStoryDetails(active).catch(() => {});
        }
      }
    } catch (e) {
      console.error("Failed to load stories:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleGenerate() {
    setShowGenerate(false);
    setGenerating(true);
    try {
      const story = await generateStory({
        length: genLength,
        topic: genTopic.trim() || undefined,
      });
      setGenTopic("");
      setGenLength("medium");
      router.push(`/story/${story.id}`);
    } catch (e) {
      console.error("Failed to generate story:", e);
    } finally {
      setGenerating(false);
    }
  }

  async function handleImport() {
    if (!importText.trim()) return;
    setImporting(true);
    try {
      const story = await importStory(
        importText.trim(),
        importTitle.trim() || undefined
      );
      setShowImport(false);
      setImportText("");
      setImportTitle("");
      router.push(`/story/${story.id}`);
    } catch (e) {
      console.error("Failed to import story:", e);
    } finally {
      setImporting(false);
    }
  }

  async function handleDelete(item: StoryListItem) {
    const title = item.title_en || item.title_ar || "this story";
    const confirmed = Platform.OS === "web"
      ? window.confirm(`Delete "${title}"?`)
      : await new Promise<boolean>((resolve) =>
          Alert.alert("Delete Story", `Delete "${title}"?`, [
            { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
            { text: "Delete", style: "destructive", onPress: () => resolve(true) },
          ])
        );
    if (!confirmed) return;
    try {
      await deleteStory(item.id);
      setStories((prev) => prev.filter((s) => s.id !== item.id));
    } catch (e) {
      console.error("Failed to delete story:", e);
    }
  }

  function readinessColor(item: StoryListItem): string {
    if (item.status === "completed") return colors.gotIt;
    if (item.readiness_pct >= 90 || item.unknown_count <= 10)
      return colors.gotIt;
    if (item.readiness_pct >= 70) return colors.stateLearning;
    return colors.missed;
  }

  function statusIcon(status: StoryListItem["status"]): string {
    return {
      active: "ellipse-outline",
      completed: "checkmark-circle",
      too_difficult: "alert-circle-outline",
      skipped: "arrow-forward-circle-outline",
    }[status] as string;
  }

  function statusLabel(status: StoryListItem["status"]): string {
    return {
      active: "Active",
      completed: "Completed",
      too_difficult: "Too Difficult",
      skipped: "Skipped",
    }[status];
  }

  function renderStory({ item }: { item: StoryListItem }) {
    const title = item.title_en || item.title_ar || "Untitled Story";
    const ready = readinessColor(item);
    const isComplete = item.status === "completed";
    const readyText = isComplete
      ? "Completed"
      : item.unknown_count <= 3
        ? "Ready to read!"
        : `${item.unknown_count} unknown words`;

    const pctWidth = Math.min(100, Math.max(4, item.readiness_pct));

    return (
      <View style={styles.storyCard}>
        <Pressable onPress={() => router.push(`/story/${item.id}`)}>
          <View style={styles.cardTop}>
            <View style={styles.cardTitleRow}>
              <Ionicons
                name={statusIcon(item.status) as any}
                size={18}
                color={ready}
                style={{ marginRight: 8 }}
              />
              <Text style={styles.storyTitle} numberOfLines={1}>
                {title}
              </Text>
            </View>
            {item.title_ar && (
              <Text style={styles.storyTitleAr} numberOfLines={1}>
                {item.title_ar}
              </Text>
            )}
          </View>

          <View style={styles.progressBar}>
            <View
              style={[
                styles.progressFill,
                { width: `${pctWidth}%`, backgroundColor: ready },
              ]}
            />
          </View>

          <View style={styles.cardBottom}>
            <View style={styles.cardBadges}>
              <View
                style={[
                  styles.badge,
                  {
                    backgroundColor:
                      item.source === "generated"
                        ? colors.accent + "20"
                        : colors.listening + "20",
                  },
                ]}
              >
                <Text
                  style={[
                    styles.badgeText,
                    {
                      color:
                        item.source === "generated"
                          ? colors.accent
                          : colors.listening,
                    },
                  ]}
                >
                  {item.source === "generated" ? "Generated" : "Imported"}
                </Text>
              </View>
              <Text style={styles.wordCount}>
                {item.total_words} words
              </Text>
            </View>
            <Text style={[styles.readinessLabel, { color: ready }]}>
              {readyText}
            </Text>
          </View>
        </Pressable>

        <Pressable
          onPress={() => handleDelete(item)}
          hitSlop={8}
          style={styles.deleteBtn}
        >
          <Ionicons name="trash-outline" size={16} color={colors.textSecondary} />
        </Pressable>
      </View>
    );
  }

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (generating) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
        <Text style={styles.generatingText}>Generating story...</Text>
        <Text style={styles.generatingHint}>This may take a few seconds</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.actionBar}>
        <Pressable
          style={styles.actionBtn}
          onPress={() => setShowGenerate(true)}
        >
          <Ionicons name="sparkles-outline" size={18} color="#fff" />
          <Text style={styles.actionBtnText}>Generate</Text>
        </Pressable>
        <Pressable
          style={[styles.actionBtn, styles.importBtn]}
          onPress={() => setShowImport(true)}
        >
          <Ionicons name="clipboard-outline" size={18} color="#fff" />
          <Text style={styles.actionBtnText}>Import</Text>
        </Pressable>
      </View>

      <FlatList
        data={stories}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderStory}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Ionicons
              name="book-outline"
              size={48}
              color={colors.textSecondary}
              style={{ marginBottom: 12, opacity: 0.5 }}
            />
            <Text style={styles.emptyText}>No stories yet</Text>
            <Text style={styles.emptyHint}>
              Generate a story from your vocabulary or import Arabic text
            </Text>
          </View>
        }
      />

      {/* Generate Modal */}
      <Modal
        visible={showGenerate}
        animationType="slide"
        transparent
        onRequestClose={() => setShowGenerate(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Generate Story</Text>

            <Text style={styles.fieldLabel}>Length</Text>
            <View style={styles.lengthRow}>
              {(["short", "medium", "long"] as StoryLength[]).map((len) => (
                <Pressable
                  key={len}
                  style={[
                    styles.lengthOption,
                    genLength === len && styles.lengthOptionActive,
                  ]}
                  onPress={() => setGenLength(len)}
                >
                  <Text
                    style={[
                      styles.lengthLabel,
                      genLength === len && styles.lengthLabelActive,
                    ]}
                  >
                    {LENGTH_LABELS[len].label}
                  </Text>
                  <Text
                    style={[
                      styles.lengthDesc,
                      genLength === len && styles.lengthDescActive,
                    ]}
                  >
                    {LENGTH_LABELS[len].desc}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Text style={styles.fieldLabel}>Topic (optional)</Text>
            <TextInput
              style={styles.topicInput}
              placeholder="e.g. a trip to the market, a funny cat..."
              placeholderTextColor={colors.textSecondary}
              value={genTopic}
              onChangeText={setGenTopic}
            />

            <View style={styles.modalActions}>
              <Pressable
                style={styles.modalCancel}
                onPress={() => setShowGenerate(false)}
              >
                <Text style={styles.modalCancelText}>Cancel</Text>
              </Pressable>
              <Pressable style={styles.modalSubmit} onPress={handleGenerate}>
                <Ionicons name="sparkles" size={16} color="#fff" />
                <Text style={styles.modalSubmitText}>Generate</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* Import Modal */}
      <Modal
        visible={showImport}
        animationType="slide"
        transparent
        onRequestClose={() => setShowImport(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Import Arabic Text</Text>
            <TextInput
              style={styles.topicInput}
              placeholder="Title (optional)"
              placeholderTextColor={colors.textSecondary}
              value={importTitle}
              onChangeText={setImportTitle}
            />
            <TextInput
              style={styles.importTextInput}
              placeholder="Paste Arabic text here..."
              placeholderTextColor={colors.textSecondary}
              value={importText}
              onChangeText={setImportText}
              multiline
              textAlign="right"
            />
            <View style={styles.modalActions}>
              <Pressable
                style={styles.modalCancel}
                onPress={() => setShowImport(false)}
              >
                <Text style={styles.modalCancelText}>Cancel</Text>
              </Pressable>
              <Pressable
                style={[
                  styles.modalSubmit,
                  (!importText.trim() || importing) && styles.modalSubmitDisabled,
                ]}
                onPress={handleImport}
                disabled={!importText.trim() || importing}
              >
                {importing ? (
                  <ActivityIndicator size="small" color="#fff" />
                ) : (
                  <Text style={styles.modalSubmitText}>Analyze</Text>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

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
  generatingText: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "600",
    marginTop: 20,
  },
  generatingHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginTop: 6,
  },
  actionBar: {
    flexDirection: "row",
    gap: 10,
    padding: 12,
  },
  actionBtn: {
    flex: 1,
    backgroundColor: colors.accent,
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "center",
    gap: 6,
  },
  importBtn: {
    backgroundColor: colors.listening,
  },
  actionBtnText: {
    color: "#fff",
    fontSize: fonts.body,
    fontWeight: "600",
  },
  list: {
    paddingHorizontal: 12,
    paddingBottom: 20,
  },
  storyCard: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
    marginBottom: 10,
    position: "relative" as const,
  },
  deleteBtn: {
    position: "absolute" as const,
    top: 14,
    right: 14,
    padding: 6,
  },
  cardTop: {
    marginBottom: 10,
  },
  cardTitleRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  storyTitle: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "600",
    flex: 1,
  },
  storyTitleAr: {
    fontSize: fonts.arabicList,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    marginTop: 4,
    opacity: 0.85,
  },
  progressBar: {
    height: 4,
    backgroundColor: colors.surfaceLight,
    borderRadius: 2,
    overflow: "hidden",
    marginBottom: 10,
  },
  progressFill: {
    height: "100%",
    borderRadius: 2,
  },
  cardBottom: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  cardBadges: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: "600",
  },
  wordCount: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  readinessLabel: {
    fontSize: 13,
    fontWeight: "600",
  },
  emptyContainer: {
    alignItems: "center",
    marginTop: 60,
    paddingHorizontal: 40,
  },
  emptyText: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "600",
    marginBottom: 6,
  },
  emptyHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    textAlign: "center",
    lineHeight: 20,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    justifyContent: "center",
    padding: 20,
  },
  modalContent: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 20,
    maxHeight: "80%",
  },
  modalTitle: {
    fontSize: 20,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 16,
  },
  fieldLabel: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    fontWeight: "600",
    marginBottom: 8,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  lengthRow: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 16,
  },
  lengthOption: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 8,
    alignItems: "center",
    borderWidth: 2,
    borderColor: "transparent",
  },
  lengthOptionActive: {
    borderColor: colors.accent,
    backgroundColor: colors.accent + "15",
  },
  lengthLabel: {
    fontSize: fonts.body,
    color: colors.text,
    fontWeight: "600",
  },
  lengthLabelActive: {
    color: colors.accent,
  },
  lengthDesc: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
  },
  lengthDescActive: {
    color: colors.accent,
    opacity: 0.8,
  },
  topicInput: {
    backgroundColor: colors.surfaceLight,
    color: colors.text,
    fontSize: fonts.body,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: 16,
  },
  importTextInput: {
    backgroundColor: colors.surfaceLight,
    color: colors.arabic,
    fontSize: fonts.arabicList,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
    minHeight: 150,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlignVertical: "top",
    marginBottom: 16,
  },
  modalActions: {
    flexDirection: "row",
    gap: 12,
  },
  modalCancel: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: "center",
    backgroundColor: colors.surfaceLight,
  },
  modalCancelText: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    fontWeight: "600",
  },
  modalSubmit: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: "center",
    backgroundColor: colors.accent,
    flexDirection: "row",
    justifyContent: "center",
    gap: 6,
  },
  modalSubmitDisabled: {
    opacity: 0.5,
  },
  modalSubmitText: {
    color: "#fff",
    fontSize: fonts.body,
    fontWeight: "600",
  },
});
