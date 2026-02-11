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
  Image,
  KeyboardAvoidingView,
  ScrollView,
} from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import { colors, fonts, fontFamily } from "../lib/theme";
import { getStories, generateStory, importStory, deleteStory, prefetchStoryDetails, extractTextFromImage } from "../lib/api";
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
  const [importImageUri, setImportImageUri] = useState<string | null>(null);
  const [extractingText, setExtractingText] = useState(false);

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
      setImportImageUri(null);
      router.push(`/story/${story.id}`);
    } catch (e) {
      console.error("Failed to import story:", e);
    } finally {
      setImporting(false);
    }
  }

  async function handleImportImage() {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.8,
    });
    if (result.canceled || result.assets.length === 0) return;

    const uri = result.assets[0].uri;
    setImportImageUri(uri);
    setExtractingText(true);

    try {
      const text = await extractTextFromImage(uri);
      setImportText(text);
    } catch (e) {
      console.error("OCR extraction failed:", e);
      setImportImageUri(null);
    } finally {
      setExtractingText(false);
    }
  }

  async function handleImportCamera() {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== "granted") return;

    const result = await ImagePicker.launchCameraAsync({ quality: 0.8 });
    if (result.canceled || result.assets.length === 0) return;

    const uri = result.assets[0].uri;
    setImportImageUri(uri);
    setExtractingText(true);

    try {
      const text = await extractTextFromImage(uri);
      setImportText(text);
    } catch (e) {
      console.error("OCR extraction failed:", e);
      setImportImageUri(null);
    } finally {
      setExtractingText(false);
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

    const statusBadgeColor = isComplete
      ? colors.gotIt
      : item.status === "too_difficult"
        ? colors.missed
        : item.status === "skipped"
          ? colors.textSecondary
          : colors.accent;

    return (
      <Pressable
        style={styles.storyCard}
        onPress={() => router.push(`/story/${item.id}`)}
      >
        <View style={styles.cardHeader}>
          <View style={styles.cardTitleArea}>
            <Text style={styles.storyTitle} numberOfLines={2}>
              {title}
            </Text>
            {item.title_ar && (
              <Text style={styles.storyTitleAr} numberOfLines={1}>
                {item.title_ar}
              </Text>
            )}
          </View>
          <Pressable
            onPress={() => handleDelete(item)}
            hitSlop={12}
            style={styles.deleteBtn}
          >
            <Ionicons name="close" size={16} color={colors.textSecondary} />
          </Pressable>
        </View>

        <View style={styles.progressBar}>
          <View
            style={[
              styles.progressFill,
              { width: `${pctWidth}%`, backgroundColor: ready },
            ]}
          />
        </View>

        <View style={styles.cardFooter}>
          <View style={styles.cardBadges}>
            <View
              style={[
                styles.badge,
                {
                  backgroundColor:
                    item.source === "generated"
                      ? colors.accent + "18"
                      : colors.listening + "18",
                },
              ]}
            >
              <Ionicons
                name={item.source === "generated" ? "sparkles" : "clipboard"}
                size={10}
                color={item.source === "generated" ? colors.accent : colors.listening}
                style={{ marginRight: 4 }}
              />
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
            {item.status !== "active" && (
              <View style={[styles.badge, { backgroundColor: statusBadgeColor + "18" }]}>
                <Text style={[styles.badgeText, { color: statusBadgeColor }]}>
                  {statusLabel(item.status)}
                </Text>
              </View>
            )}
            <Text style={styles.wordCount}>
              {item.total_words} words
            </Text>
          </View>
          <Text style={[styles.readinessLabel, { color: ready }]}>
            {readyText}
          </Text>
        </View>
      </Pressable>
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
        <Ionicons
          name="sparkles"
          size={32}
          color={colors.accent}
          style={{ marginBottom: 16, opacity: 0.8 }}
        />
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
              size={56}
              color={colors.textSecondary}
              style={{ marginBottom: 16, opacity: 0.4 }}
            />
            <Text style={styles.emptyText}>No stories yet</Text>
            <Text style={styles.emptyHint}>
              Generate a story from your vocabulary{"\n"}or import Arabic text
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
          <Pressable style={styles.modalOverlayTouch} onPress={() => setShowGenerate(false)} />
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Generate Story</Text>
              <Pressable onPress={() => setShowGenerate(false)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </Pressable>
            </View>

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
        onRequestClose={() => { setShowImport(false); setImportImageUri(null); }}
      >
        <KeyboardAvoidingView
          style={styles.modalOverlay}
          behavior={Platform.OS === "ios" ? "padding" : "height"}
        >
          <Pressable style={styles.modalOverlayTouch} onPress={() => { setShowImport(false); setImportImageUri(null); }} />
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Import Arabic Text</Text>
              <Pressable onPress={() => { setShowImport(false); setImportImageUri(null); }} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </Pressable>
            </View>

            <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
              <View style={styles.importSourceRow}>
                <Pressable
                  style={styles.importSourceBtn}
                  onPress={handleImportCamera}
                  disabled={extractingText}
                >
                  <Ionicons name="camera-outline" size={20} color={colors.accent} />
                  <Text style={styles.importSourceText}>Camera</Text>
                </Pressable>
                <Pressable
                  style={styles.importSourceBtn}
                  onPress={handleImportImage}
                  disabled={extractingText}
                >
                  <Ionicons name="image-outline" size={20} color={colors.listening} />
                  <Text style={styles.importSourceText}>Photo</Text>
                </Pressable>
              </View>

              {extractingText && (
                <View style={styles.extractingBanner}>
                  <ActivityIndicator size="small" color={colors.accent} />
                  <Text style={styles.extractingText}>Extracting Arabic text...</Text>
                </View>
              )}

              {importImageUri && !extractingText && (
                <View style={styles.importImagePreview}>
                  <Image source={{ uri: importImageUri }} style={styles.importPreviewImg} />
                  <Pressable
                    style={styles.importImageRemove}
                    onPress={() => { setImportImageUri(null); setImportText(""); }}
                  >
                    <Ionicons name="close-circle" size={20} color={colors.missed} />
                  </Pressable>
                </View>
              )}

              <TextInput
                style={styles.topicInput}
                placeholder="Title (optional)"
                placeholderTextColor={colors.textSecondary}
                value={importTitle}
                onChangeText={setImportTitle}
              />
              <TextInput
                style={styles.importTextInput}
                placeholder="Paste Arabic text here or use camera/photo above..."
                placeholderTextColor={colors.textSecondary}
                value={importText}
                onChangeText={setImportText}
                multiline
                textAlign="right"
              />
              <View style={styles.modalActions}>
                <Pressable
                  style={styles.modalCancel}
                  onPress={() => { setShowImport(false); setImportImageUri(null); }}
                >
                  <Text style={styles.modalCancelText}>Cancel</Text>
                </Pressable>
                <Pressable
                  style={[
                    styles.modalSubmit,
                    (!importText.trim() || importing || extractingText) && styles.modalSubmitDisabled,
                  ]}
                  onPress={handleImport}
                  disabled={!importText.trim() || importing || extractingText}
                >
                  {importing ? (
                    <ActivityIndicator size="small" color="#fff" />
                  ) : (
                    <Text style={styles.modalSubmitText}>Analyze</Text>
                  )}
                </Pressable>
              </View>
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
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
    padding: 20,
  },
  generatingText: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "600",
  },
  generatingHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginTop: 6,
  },
  actionBar: {
    flexDirection: "row",
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  actionBtn: {
    flex: 1,
    backgroundColor: colors.accent,
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "center",
    gap: 8,
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
    paddingHorizontal: 16,
    paddingBottom: 24,
  },
  storyCard: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: 18,
    marginBottom: 10,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 12,
  },
  cardTitleArea: {
    flex: 1,
    marginRight: 12,
  },
  deleteBtn: {
    padding: 4,
    borderRadius: 12,
    backgroundColor: colors.surfaceLight,
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
  },
  storyTitle: {
    fontSize: 17,
    color: colors.text,
    fontWeight: "600",
    lineHeight: 22,
  },
  storyTitleAr: {
    fontSize: fonts.arabicMedium,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    marginTop: 6,
    lineHeight: 34,
  },
  progressBar: {
    height: 5,
    backgroundColor: colors.surfaceLight,
    borderRadius: 3,
    overflow: "hidden",
    marginBottom: 12,
  },
  progressFill: {
    height: "100%",
    borderRadius: 3,
  },
  cardFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  cardBadges: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flex: 1,
  },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 3,
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
    marginLeft: 8,
  },
  emptyContainer: {
    alignItems: "center",
    marginTop: 80,
    paddingHorizontal: 40,
  },
  emptyText: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "600",
    marginBottom: 8,
  },
  emptyHint: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    textAlign: "center",
    lineHeight: 22,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    justifyContent: "flex-end",
  },
  modalOverlayTouch: {
    flex: 1,
  },
  modalContent: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 24,
    maxHeight: "85%",
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 20,
  },
  modalTitle: {
    fontSize: 20,
    color: colors.text,
    fontWeight: "700",
  },
  fieldLabel: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    fontWeight: "600",
    marginBottom: 8,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  lengthRow: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 20,
  },
  lengthOption: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 10,
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
    marginTop: 3,
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
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: 16,
  },
  importTextInput: {
    backgroundColor: colors.surfaceLight,
    color: colors.arabic,
    fontSize: fonts.arabicList,
    paddingHorizontal: 14,
    paddingVertical: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    minHeight: 150,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlignVertical: "top",
    marginBottom: 16,
    lineHeight: 30,
  },
  modalActions: {
    flexDirection: "row",
    gap: 12,
  },
  modalCancel: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 12,
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
    paddingVertical: 14,
    borderRadius: 12,
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
  importSourceRow: {
    flexDirection: "row",
    gap: 12,
    marginBottom: 16,
  },
  importSourceBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 12,
    borderRadius: 12,
    backgroundColor: colors.surfaceLight,
    borderWidth: 1,
    borderColor: colors.border,
  },
  importSourceText: {
    fontSize: fonts.small,
    color: colors.text,
    fontWeight: "600",
  },
  extractingBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.accent + "15",
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 14,
    marginBottom: 16,
  },
  extractingText: {
    fontSize: fonts.small,
    color: colors.accent,
    fontWeight: "500",
  },
  importImagePreview: {
    alignItems: "center",
    marginBottom: 16,
    position: "relative" as const,
  },
  importPreviewImg: {
    width: 80,
    height: 100,
    borderRadius: 10,
    backgroundColor: colors.surfaceLight,
  },
  importImageRemove: {
    position: "absolute" as const,
    top: -6,
    right: "35%" as any,
    backgroundColor: colors.bg,
    borderRadius: 10,
  },
});
