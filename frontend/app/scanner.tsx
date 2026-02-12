import { useState, useCallback, useRef, useEffect } from "react";
import {
  View,
  Text,
  FlatList,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Image,
  ScrollView,
  Platform,
  Switch,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import { colors, fonts, fontFamily } from "../lib/theme";
import {
  scanTextbookPages,
  getBatchStatus,
  getUploadHistory,
} from "../lib/api";
import {
  BatchUploadResult,
  BatchSummary,
  PageUploadResult,
  ExtractedWord,
} from "../lib/types";

type ViewMode = "upload" | "history";

export default function ScannerScreen() {
  const [viewMode, setViewMode] = useState<ViewMode>("upload");
  const [selectedImages, setSelectedImages] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [currentBatch, setCurrentBatch] = useState<BatchUploadResult | null>(null);
  const [history, setHistory] = useState<BatchSummary[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [expandedBatch, setExpandedBatch] = useState<string | null>(null);
  const [startAcquiring, setStartAcquiring] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useFocusEffect(
    useCallback(() => {
      loadHistory();
      return () => {
        if (pollRef.current) clearInterval(pollRef.current);
      };
    }, [])
  );

  async function loadHistory() {
    setLoadingHistory(true);
    try {
      const data = await getUploadHistory();
      setHistory(data.batches);
    } catch (e) {
      console.error("Failed to load upload history:", e);
    } finally {
      setLoadingHistory(false);
    }
  }

  async function pickImages() {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== "granted") {
      if (Platform.OS === "web") {
        // On web, permissions aren't needed
      } else {
        return;
      }
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      allowsMultipleSelection: true,
      quality: 0.8,
    });

    if (!result.canceled && result.assets.length > 0) {
      setSelectedImages((prev) => [
        ...prev,
        ...result.assets.map((a) => a.uri),
      ]);
    }
  }

  async function takePhoto() {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== "granted") return;

    const result = await ImagePicker.launchCameraAsync({
      quality: 0.8,
    });

    if (!result.canceled && result.assets.length > 0) {
      setSelectedImages((prev) => [...prev, result.assets[0].uri]);
    }
  }

  function removeImage(index: number) {
    setSelectedImages((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleUpload() {
    if (selectedImages.length === 0) return;
    setUploading(true);

    try {
      const batch = await scanTextbookPages(selectedImages, startAcquiring);
      setCurrentBatch(batch);
      setSelectedImages([]);
      setViewMode("history");

      // Start polling for results
      startPolling(batch.batch_id);
    } catch (e) {
      console.error("Upload failed:", e);
    } finally {
      setUploading(false);
    }
  }

  function startPolling(batchId: string) {
    if (pollRef.current) clearInterval(pollRef.current);

    setExpandedBatch(batchId);

    pollRef.current = setInterval(async () => {
      try {
        const updated = await getBatchStatus(batchId);
        setCurrentBatch(updated);

        // Update in history too
        setHistory((prev) => {
          const exists = prev.find((b) => b.batch_id === batchId);
          if (exists) {
            return prev.map((b) =>
              b.batch_id === batchId
                ? {
                    ...b,
                    pages: updated.pages,
                    total_new: updated.total_new,
                    total_existing: updated.total_existing,
                    status: updated.pages.every(
                      (p) => p.status === "completed" || p.status === "failed"
                    )
                      ? updated.pages.some((p) => p.status === "failed")
                        ? "failed"
                        : "completed"
                      : "processing",
                  }
                : b
            );
          }
          return prev;
        });

        const allDone = updated.pages.every(
          (p) => p.status === "completed" || p.status === "failed"
        );
        if (allDone && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          loadHistory();
        }
      } catch (e) {
        console.error("Polling failed:", e);
      }
    }, 2000);
  }

  function renderUploadView() {
    return (
      <ScrollView style={styles.scrollContent}>
        <Text style={styles.sectionTitle}>Scan Textbook Pages</Text>
        <Text style={styles.sectionHint}>
          Take photos or select images of textbook pages. Words will be
          extracted and added to your vocabulary.
        </Text>

        <View style={styles.buttonRow}>
          <Pressable style={styles.pickBtn} onPress={takePhoto}>
            <Ionicons name="camera-outline" size={22} color="#fff" />
            <Text style={styles.pickBtnText}>Camera</Text>
          </Pressable>
          <Pressable
            style={[styles.pickBtn, styles.galleryBtn]}
            onPress={pickImages}
          >
            <Ionicons name="images-outline" size={22} color="#fff" />
            <Text style={styles.pickBtnText}>Gallery</Text>
          </Pressable>
        </View>

        {selectedImages.length > 0 && (
          <>
            <Text style={styles.previewLabel}>
              {selectedImages.length} page{selectedImages.length > 1 ? "s" : ""}{" "}
              selected
            </Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              style={styles.previewScroll}
            >
              {selectedImages.map((uri, idx) => (
                <View key={idx} style={styles.previewItem}>
                  <Image source={{ uri }} style={styles.previewImage} />
                  <Pressable
                    style={styles.removeBtn}
                    onPress={() => removeImage(idx)}
                  >
                    <Ionicons name="close-circle" size={22} color={colors.missed} />
                  </Pressable>
                  <Text style={styles.pageNumber}>Page {idx + 1}</Text>
                </View>
              ))}
            </ScrollView>

            <View style={styles.toggleRow}>
              <Text style={styles.toggleLabel}>Start learning immediately</Text>
              <Switch
                value={startAcquiring}
                onValueChange={setStartAcquiring}
                trackColor={{ false: "#555", true: colors.accent + "80" }}
                thumbColor={startAcquiring ? colors.accent : "#999"}
              />
            </View>
            <Text style={styles.toggleHint}>
              {startAcquiring
                ? "Words will be scheduled for review right away"
                : "Words will be saved for later introduction"}
            </Text>

            <Pressable
              style={[
                styles.uploadBtn,
                uploading && styles.uploadBtnDisabled,
              ]}
              onPress={handleUpload}
              disabled={uploading}
            >
              {uploading ? (
                <>
                  <ActivityIndicator size="small" color="#fff" />
                  <Text style={styles.uploadBtnText}>Uploading...</Text>
                </>
              ) : (
                <>
                  <Ionicons name="scan-outline" size={20} color="#fff" />
                  <Text style={styles.uploadBtnText}>
                    Scan {selectedImages.length} Page
                    {selectedImages.length > 1 ? "s" : ""}
                  </Text>
                </>
              )}
            </Pressable>
          </>
        )}
      </ScrollView>
    );
  }

  function renderWordItem(word: ExtractedWord) {
    const isNew = word.status === "new";
    const statusColor = isNew ? colors.gotIt : colors.accent;
    const statusLabel = isNew ? "New" : "Known";

    return (
      <View style={styles.wordItem} key={`${word.arabic_bare}-${word.lemma_id}`}>
        <View style={styles.wordLeft}>
          <Text style={styles.wordArabic}>{word.arabic}</Text>
          {word.english && (
            <Text style={styles.wordEnglish}>{word.english}</Text>
          )}
        </View>
        <View style={[styles.wordBadge, { backgroundColor: statusColor + "20" }]}>
          <Text style={[styles.wordBadgeText, { color: statusColor }]}>
            {statusLabel}
          </Text>
        </View>
      </View>
    );
  }

  function renderPageResult(page: PageUploadResult) {
    if (page.status === "pending" || page.status === "processing") {
      return (
        <View style={styles.pageCard} key={page.id}>
          <View style={styles.pageHeader}>
            <ActivityIndicator size="small" color={colors.accent} />
            <Text style={styles.pageFilename}>
              {page.filename || `Page ${page.id}`}
            </Text>
            <Text style={styles.pageStatus}>Processing...</Text>
          </View>
        </View>
      );
    }

    if (page.status === "failed") {
      return (
        <View style={styles.pageCard} key={page.id}>
          <View style={styles.pageHeader}>
            <Ionicons name="alert-circle" size={18} color={colors.missed} />
            <Text style={styles.pageFilename}>
              {page.filename || `Page ${page.id}`}
            </Text>
            <Text style={[styles.pageStatus, { color: colors.missed }]}>
              Failed
            </Text>
          </View>
          {page.error_message && (
            <Text style={styles.errorText}>{page.error_message}</Text>
          )}
        </View>
      );
    }

    const words = page.extracted_words || [];
    const newWords = words.filter((w) => w.status === "new");
    const existingWords = words.filter((w) => w.status !== "new");

    return (
      <View style={styles.pageCard} key={page.id}>
        <View style={styles.pageHeader}>
          <Ionicons name="checkmark-circle" size={18} color={colors.gotIt} />
          <Text style={styles.pageFilename}>
            {page.filename || `Page ${page.id}`}
          </Text>
          <View style={styles.pageCounts}>
            <Text style={[styles.countBadge, { color: colors.gotIt }]}>
              +{page.new_words} new
            </Text>
            <Text style={[styles.countBadge, { color: colors.accent }]}>
              {page.existing_words} known
            </Text>
          </View>
        </View>

        {words.length > 0 && (
          <View style={styles.wordList}>
            {newWords.length > 0 && (
              <>
                <Text style={styles.wordGroupLabel}>New Words</Text>
                {newWords.map(renderWordItem)}
              </>
            )}
            {existingWords.length > 0 && (
              <>
                <Text style={styles.wordGroupLabel}>Existing Words</Text>
                {existingWords.map(renderWordItem)}
              </>
            )}
          </View>
        )}
      </View>
    );
  }

  function renderBatchItem(batch: BatchSummary) {
    const isExpanded = expandedBatch === batch.batch_id;
    const isProcessing = batch.status === "processing";
    const date = new Date(batch.created_at);
    const dateStr = date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });

    return (
      <View style={styles.batchCard} key={batch.batch_id}>
        <Pressable
          onPress={() =>
            setExpandedBatch(isExpanded ? null : batch.batch_id)
          }
        >
          <View style={styles.batchHeader}>
            <View style={styles.batchInfo}>
              <View style={styles.batchTitleRow}>
                {isProcessing ? (
                  <ActivityIndicator
                    size="small"
                    color={colors.accent}
                    style={{ marginRight: 8 }}
                  />
                ) : batch.status === "failed" ? (
                  <Ionicons
                    name="alert-circle"
                    size={18}
                    color={colors.missed}
                    style={{ marginRight: 8 }}
                  />
                ) : (
                  <Ionicons
                    name="checkmark-circle"
                    size={18}
                    color={colors.gotIt}
                    style={{ marginRight: 8 }}
                  />
                )}
                <Text style={styles.batchTitle}>
                  {batch.page_count} page{batch.page_count > 1 ? "s" : ""}
                </Text>
              </View>
              <Text style={styles.batchDate}>{dateStr}</Text>
            </View>
            <View style={styles.batchStats}>
              {batch.total_new > 0 && (
                <Text style={[styles.batchStat, { color: colors.gotIt }]}>
                  +{batch.total_new} new
                </Text>
              )}
              <Text style={[styles.batchStat, { color: colors.accent }]}>
                {batch.total_existing} known
              </Text>
              <Ionicons
                name={isExpanded ? "chevron-up" : "chevron-down"}
                size={18}
                color={colors.textSecondary}
              />
            </View>
          </View>
        </Pressable>

        {isExpanded && (
          <View style={styles.batchPages}>
            {batch.pages.map(renderPageResult)}
          </View>
        )}
      </View>
    );
  }

  function renderHistoryView() {
    if (loadingHistory && history.length === 0) {
      return (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={colors.accent} />
        </View>
      );
    }

    if (history.length === 0) {
      return (
        <View style={styles.emptyContainer}>
          <Ionicons
            name="scan-outline"
            size={48}
            color={colors.textSecondary}
            style={{ marginBottom: 12, opacity: 0.5 }}
          />
          <Text style={styles.emptyText}>No scans yet</Text>
          <Text style={styles.emptyHint}>
            Upload textbook pages to extract vocabulary
          </Text>
        </View>
      );
    }

    return (
      <FlatList
        data={history}
        keyExtractor={(item) => item.batch_id}
        renderItem={({ item }) => renderBatchItem(item)}
        contentContainerStyle={styles.list}
        onRefresh={loadHistory}
        refreshing={loadingHistory}
      />
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.tabRow}>
        <Pressable
          style={[styles.tab, viewMode === "upload" && styles.tabActive]}
          onPress={() => setViewMode("upload")}
        >
          <Ionicons
            name="camera-outline"
            size={18}
            color={viewMode === "upload" ? colors.accent : colors.textSecondary}
          />
          <Text
            style={[
              styles.tabText,
              viewMode === "upload" && styles.tabTextActive,
            ]}
          >
            Upload
          </Text>
        </Pressable>
        <Pressable
          style={[styles.tab, viewMode === "history" && styles.tabActive]}
          onPress={() => {
            setViewMode("history");
            loadHistory();
          }}
        >
          <Ionicons
            name="list-outline"
            size={18}
            color={viewMode === "history" ? colors.accent : colors.textSecondary}
          />
          <Text
            style={[
              styles.tabText,
              viewMode === "history" && styles.tabTextActive,
            ]}
          >
            History
          </Text>
        </Pressable>
      </View>

      {viewMode === "upload" ? renderUploadView() : renderHistoryView()}
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
    alignItems: "center",
    justifyContent: "center",
  },
  scrollContent: {
    flex: 1,
    padding: 16,
  },
  tabRow: {
    flexDirection: "row",
    padding: 12,
    gap: 8,
  },
  tab: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: colors.surface,
  },
  tabActive: {
    backgroundColor: colors.accent + "20",
    borderWidth: 1,
    borderColor: colors.accent + "40",
  },
  tabText: {
    fontSize: fonts.body,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  tabTextActive: {
    color: colors.accent,
  },
  sectionTitle: {
    fontSize: 20,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 6,
  },
  sectionHint: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginBottom: 20,
    lineHeight: 20,
  },
  buttonRow: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 20,
  },
  pickBtn: {
    flex: 1,
    backgroundColor: colors.accent,
    paddingVertical: 14,
    borderRadius: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  galleryBtn: {
    backgroundColor: colors.listening,
  },
  pickBtnText: {
    color: "#fff",
    fontSize: fonts.body,
    fontWeight: "600",
  },
  previewLabel: {
    fontSize: fonts.body,
    color: colors.text,
    fontWeight: "600",
    marginBottom: 10,
  },
  previewScroll: {
    marginBottom: 16,
  },
  previewItem: {
    marginRight: 12,
    position: "relative" as const,
  },
  previewImage: {
    width: 120,
    height: 160,
    borderRadius: 10,
    backgroundColor: colors.surfaceLight,
  },
  removeBtn: {
    position: "absolute" as const,
    top: -6,
    right: -6,
    backgroundColor: colors.bg,
    borderRadius: 11,
  },
  pageNumber: {
    fontSize: 11,
    color: colors.textSecondary,
    textAlign: "center",
    marginTop: 4,
  },
  toggleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 12,
    paddingHorizontal: 4,
  },
  toggleLabel: {
    color: colors.textPrimary,
    fontSize: fonts.body,
    fontWeight: "600",
  },
  toggleHint: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    paddingHorizontal: 4,
    marginTop: 4,
    marginBottom: 8,
  },
  uploadBtn: {
    backgroundColor: colors.gotIt,
    paddingVertical: 14,
    borderRadius: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    marginTop: 4,
  },
  uploadBtnDisabled: {
    opacity: 0.6,
  },
  uploadBtnText: {
    color: "#fff",
    fontSize: fonts.body,
    fontWeight: "700",
  },
  list: {
    paddingHorizontal: 12,
    paddingBottom: 20,
  },
  batchCard: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    marginBottom: 10,
    overflow: "hidden",
  },
  batchHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 14,
  },
  batchInfo: {
    flex: 1,
  },
  batchTitleRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  batchTitle: {
    fontSize: fonts.body,
    color: colors.text,
    fontWeight: "600",
  },
  batchDate: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    marginTop: 2,
    marginLeft: 26,
  },
  batchStats: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  batchStat: {
    fontSize: fonts.small,
    fontWeight: "600",
  },
  batchPages: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    padding: 12,
  },
  pageCard: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
  },
  pageHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  pageFilename: {
    fontSize: fonts.small,
    color: colors.text,
    flex: 1,
  },
  pageStatus: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
  pageCounts: {
    flexDirection: "row",
    gap: 8,
  },
  countBadge: {
    fontSize: fonts.caption,
    fontWeight: "600",
  },
  errorText: {
    fontSize: fonts.caption,
    color: colors.missed,
    marginTop: 6,
  },
  wordList: {
    marginTop: 10,
  },
  wordGroupLabel: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 6,
    marginTop: 4,
  },
  wordItem: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 6,
    paddingHorizontal: 8,
    borderRadius: 6,
    marginBottom: 2,
  },
  wordLeft: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  wordArabic: {
    fontSize: fonts.arabicList,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    writingDirection: "rtl",
  },
  wordEnglish: {
    fontSize: fonts.small,
    color: colors.textSecondary,
  },
  wordBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  wordBadgeText: {
    fontSize: 11,
    fontWeight: "600",
  },
  emptyContainer: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
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
});
