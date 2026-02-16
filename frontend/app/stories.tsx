import { useState, useCallback } from "react";
import {
  View,
  Text,
  SectionList,
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
import { getStories, generateStory, importStory, deleteStory, suspendStory, prefetchStoryDetails, extractTextFromImage } from "../lib/api";
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

  const [completedExpanded, setCompletedExpanded] = useState(false);

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

  async function handleSuspend(item: StoryListItem) {
    try {
      const result = await suspendStory(item.id);
      setStories((prev) =>
        prev.map((s) =>
          s.id === item.id ? { ...s, status: result.status as StoryListItem["status"] } : s
        )
      );
    } catch (e) {
      console.error("Failed to toggle story suspension:", e);
    }
  }

  async function handleSuspendAll() {
    const toSuspend = stories.filter((s) => s.status !== "suspended");
    if (toSuspend.length === 0) return;
    const confirmed = Platform.OS === "web"
      ? window.confirm(`Suspend all ${toSuspend.length} stories?`)
      : await new Promise<boolean>((resolve) =>
          Alert.alert("Suspend All", `Suspend all ${toSuspend.length} stories?`, [
            { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
            { text: "Suspend All", onPress: () => resolve(true) },
          ])
        );
    if (!confirmed) return;
    for (const s of toSuspend) {
      try {
        await suspendStory(s.id);
      } catch (e) {
        console.error("Failed to suspend story:", s.id, e);
      }
    }
    setStories((prev) => prev.map((s) => ({ ...s, status: "suspended" as const })));
  }

  function readinessColor(item: StoryListItem): string {
    if (item.status === "completed") return colors.gotIt;
    if (item.readiness_pct >= 90 || item.unknown_count <= 10)
      return colors.gotIt;
    if (item.readiness_pct >= 70) return colors.stateLearning;
    return colors.missed;
  }

  type SectionKey = "active" | "suspended" | "completed";

  function buildSections(): { key: SectionKey; title: string; data: StoryListItem[] }[] {
    const active: StoryListItem[] = [];
    const suspended: StoryListItem[] = [];
    const completed: StoryListItem[] = [];
    for (const s of stories) {
      if (s.status === "completed") completed.push(s);
      else if (s.status === "suspended") suspended.push(s);
      else active.push(s);
    }
    const sections: { key: SectionKey; title: string; data: StoryListItem[] }[] = [];
    if (active.length > 0) sections.push({ key: "active", title: "Active", data: active });
    if (suspended.length > 0) sections.push({ key: "suspended", title: "Suspended", data: suspended });
    if (completed.length > 0) sections.push({ key: "completed", title: "Completed", data: completedExpanded ? completed : [] });
    return sections;
  }

  function renderStory({ item }: { item: StoryListItem }) {
    const title = item.title_en || item.title_ar || "Untitled Story";
    const ready = readinessColor(item);
    const isComplete = item.status === "completed";
    const readyText = isComplete
      ? "Completed"
      : item.unknown_count <= 3
        ? "Ready to read!"
        : item.new_learning && item.new_total
          ? `${item.new_learning}/${item.new_total} new words learning`
          : `${item.unknown_count} unknown`;

    const pctWidth = Math.min(100, Math.max(4, item.readiness_pct));
    const isSuspended = item.status === "suspended";

    return (
      <Pressable
        style={[styles.storyCard, isSuspended && { opacity: 0.55 }]}
        onPress={() => router.push(`/story/${item.id}`)}
      >
        <View style={styles.cardHeader}>
          <View style={styles.cardTitleArea}>
            <Text style={styles.storyTitle} numberOfLines={1}>
              {title}
            </Text>
            {item.title_ar && (
              <Text style={styles.storyTitleAr} numberOfLines={1}>
                {item.title_ar}
              </Text>
            )}
          </View>
          <View style={{ flexDirection: "row", gap: 6 }}>
            <Pressable
              onPress={(e) => { e.stopPropagation(); handleSuspend(item); }}
              hitSlop={8}
              style={styles.iconBtn}
            >
              <Ionicons
                name={isSuspended ? "play" : "pause"}
                size={14}
                color={isSuspended ? colors.gotIt : colors.textSecondary}
              />
            </Pressable>
            <Pressable
              onPress={(e) => { e.stopPropagation(); handleDelete(item); }}
              hitSlop={8}
              style={styles.iconBtn}
            >
              <Ionicons name="close" size={16} color={colors.textSecondary} />
            </Pressable>
          </View>
        </View>

        {item.page_readiness && item.page_readiness.length > 0 ? (
          <View style={styles.pageRow}>
            {item.page_readiness.map((p) => {
              return (
                <Pressable
                  key={p.page}
                  onPress={(e) => {
                    e.stopPropagation();
                    router.push(`/book-page?storyId=${item.id}&page=${p.page}`);
                  }}
                  style={[
                    styles.pagePill,
                    {
                      backgroundColor: p.unlocked
                        ? colors.gotIt + "25"
                        : p.learned_words > 0
                          ? colors.stateLearning + "25"
                          : colors.surfaceLight,
                    },
                  ]}
                >
                  <Text
                    style={[
                      styles.pagePillText,
                      {
                        color: p.unlocked
                          ? colors.gotIt
                          : p.learned_words > 0
                            ? colors.stateLearning
                            : colors.textSecondary,
                      },
                    ]}
                  >
                    {p.unlocked
                      ? `p${p.page} ✓`
                      : `p${p.page} ${p.learned_words}/${p.new_words}`}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        ) : (
          <View style={styles.progressBar}>
            <View
              style={[
                styles.progressFill,
                { width: `${pctWidth}%`, backgroundColor: ready },
              ]}
            />
          </View>
        )}

        <View style={styles.cardFooter}>
          <View style={{ flex: 1 }}>
            <Text style={styles.cardStats}>
              {item.source === "book_ocr" && item.sentences_seen != null && item.sentence_count ? (
                <>
                  <Text>{item.sentences_seen}/{item.sentence_count} sentences seen</Text>
                  <Text>{" · "}</Text>
                  <Text style={{ color: ready }}>{readyText}</Text>
                </>
              ) : (
                <>
                  <Text>{item.total_words} words</Text>
                  <Text>{" · "}</Text>
                  <Text style={{ color: ready }}>{readyText}</Text>
                </>
              )}
            </Text>
            {item.estimated_days_to_ready != null && item.estimated_days_to_ready > 0 &&
             item.status === "active" && item.unknown_count > 3 && (
              <Text style={styles.predictionText}>
                ~{item.estimated_days_to_ready > 60
                  ? `${Math.round(item.estimated_days_to_ready / 7)}w`
                  : `${item.estimated_days_to_ready}d`
                } until ready
              </Text>
            )}
          </View>
          <View
            style={[
              styles.sourceBadge,
              {
                backgroundColor:
                  item.source === "generated"
                    ? colors.accent + "18"
                    : colors.listening + "18",
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
                      : item.source === "book_ocr"
                        ? colors.gotIt
                        : colors.listening,
                },
              ]}
            >
              {item.source === "generated"
                ? "Gen"
                : item.source === "book_ocr"
                  ? "Book"
                  : "Imp"}
            </Text>
          </View>
        </View>
      </Pressable>
    );
  }

  function renderSectionHeader({ section }: { section: { key: SectionKey; title: string; data: StoryListItem[] } }) {
    const isCompleted = section.key === "completed";
    const completedCount = isCompleted ? stories.filter((s) => s.status === "completed").length : 0;

    return (
      <Pressable
        style={styles.sectionHeader}
        onPress={isCompleted ? () => setCompletedExpanded((v) => !v) : undefined}
        disabled={!isCompleted}
      >
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          {isCompleted && (
            <Ionicons
              name={completedExpanded ? "chevron-down" : "chevron-forward"}
              size={14}
              color={colors.textSecondary}
            />
          )}
          <Text style={styles.sectionTitle}>
            {section.title}
            <Text style={styles.sectionCount}> ({isCompleted ? completedCount : section.data.length})</Text>
          </Text>
        </View>
        {section.key === "active" && stories.filter((s) => s.status === "active").length > 1 && (
          <Pressable onPress={handleSuspendAll} hitSlop={8}>
            <Text style={styles.suspendAllText}>Suspend All</Text>
          </Pressable>
        )}
      </Pressable>
    );
  }

  const sections = buildSections();

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
        <Pressable
          style={[styles.actionBtn, styles.bookBtn]}
          onPress={() => router.push("/book-import")}
        >
          <Ionicons name="book-outline" size={18} color="#fff" />
          <Text style={styles.actionBtnText}>Book</Text>
        </Pressable>
      </View>

      <SectionList
        sections={sections}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderStory}
        renderSectionHeader={renderSectionHeader}
        contentContainerStyle={styles.list}
        stickySectionHeadersEnabled={false}
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
  bookBtn: {
    backgroundColor: colors.gotIt,
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
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 8,
    marginBottom: 10,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textSecondary,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  sectionCount: {
    fontWeight: "400",
  },
  suspendAllText: {
    fontSize: 13,
    color: colors.missed,
    fontWeight: "600",
  },
  pageRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 10,
  },
  pagePill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 8,
  },
  pagePillText: {
    fontSize: 11,
    fontWeight: "600",
  },
  storyCard: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: 16,
    marginBottom: 8,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 10,
  },
  cardTitleArea: {
    flex: 1,
    marginRight: 12,
  },
  iconBtn: {
    padding: 4,
    borderRadius: 12,
    backgroundColor: colors.surfaceLight,
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
  },
  storyTitle: {
    fontSize: 16,
    color: colors.text,
    fontWeight: "600",
    lineHeight: 20,
  },
  storyTitleAr: {
    fontSize: fonts.arabicMedium,
    color: colors.arabic,
    writingDirection: "rtl",
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    marginTop: 4,
    lineHeight: 30,
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
  cardFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  cardStats: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  predictionText: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
    opacity: 0.7,
  },
  sourceBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
  },
  badgeText: {
    fontSize: 10,
    fontWeight: "600",
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
