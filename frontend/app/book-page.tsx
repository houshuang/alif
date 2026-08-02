import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useNavigation, useRouter } from "expo-router";

import { completeBookPage, getBookPageDetail, lookupReviewWord } from "../lib/api";
import {
  bookLookupDraftKey,
  groupBookTokens,
  parseBookLookupDraft,
  pendingBookLookupIds,
} from "../lib/book-reader";
import { BookPageDetail, BookPageToken, WordLookupResult } from "../lib/types";
import { colors, fontFamily, fonts } from "../lib/theme";

export default function BookPageScreen() {
  const { storyId, page } = useLocalSearchParams<{ storyId: string; page: string }>();
  const storyIdNumber = Number(storyId);
  const pageNumber = Number(page || 1);
  const [data, setData] = useState<BookPageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [showTranslation, setShowTranslation] = useState(false);
  const [lookedUp, setLookedUp] = useState<Set<number>>(new Set());
  const [selectedToken, setSelectedToken] = useState<BookPageToken | null>(null);
  const [lookup, setLookup] = useState<WordLookupResult | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [draftReady, setDraftReady] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [completionError, setCompletionError] = useState(false);
  const [lookupError, setLookupError] = useState(false);
  const requestRef = useRef(0);
  const pageStartedAt = useRef(Date.now());
  const router = useRouter();
  const navigation = useNavigation();

  useLayoutEffect(() => {
    navigation.setOptions({
      headerLeft: () => (
        <Pressable onPress={() => router.replace("/books")} style={styles.headerBack}>
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </Pressable>
      ),
      title: data?.story_title_en || data?.story_title_ar || "Reader",
      headerStyle: { backgroundColor: colors.bg },
      headerTintColor: colors.text,
    });
  }, [data?.story_title_ar, data?.story_title_en, navigation, router]);

  const loadPage = useCallback(async () => {
    if (!Number.isFinite(storyIdNumber) || !Number.isFinite(pageNumber)) {
      setData(null);
      setLoadError(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    setSubmitting(false);
    setShowTranslation(false);
    setSelectedToken(null);
    setLookup(null);
    setLoadError(false);
    setCompletionError(false);
    setDraftReady(false);
    pageStartedAt.current = Date.now();
    try {
      const draftKey = bookLookupDraftKey(storyIdNumber, pageNumber);
      const [next, rawDraft] = await Promise.all([
        getBookPageDetail(storyIdNumber, pageNumber),
        AsyncStorage.getItem(draftKey).catch(() => null),
      ]);
      const schedulableIds = new Set(
        next.tokens
          .filter((token) => token.is_schedulable && token.lemma_id != null)
          .map((token) => token.lemma_id!),
      );
      const restoredDraft = parseBookLookupDraft(rawDraft).filter((lemmaId) =>
        schedulableIds.has(lemmaId),
      );
      setData(next);
      setLookedUp(new Set([...next.looked_up_lemma_ids, ...restoredDraft]));
      setDraftReady(true);
    } catch (error) {
      console.error("Failed to load book page", error);
      setData(null);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [pageNumber, storyIdNumber]);

  useEffect(() => {
    loadPage();
  }, [loadPage]);

  useEffect(() => {
    if (!draftReady || !data) return;
    const key = bookLookupDraftKey(data.story_id, data.page_number);
    const pending = pendingBookLookupIds(lookedUp, data.looked_up_lemma_ids);
    const operation = pending.length > 0
      ? AsyncStorage.setItem(key, JSON.stringify(pending))
      : AsyncStorage.removeItem(key);
    operation.catch((error) => console.warn("Failed to persist book lookup draft", error));
  }, [data, draftReady, lookedUp]);

  async function handleTokenPress(token: BookPageToken) {
    setSelectedToken(token);
    setLookup(null);
    setLookupError(false);
    if (token.is_schedulable && token.lemma_id != null) {
      setLookedUp((current) => new Set(current).add(token.lemma_id!));
    }
    if (token.lemma_id == null) return;

    await loadTokenLookup(token);
  }

  async function loadTokenLookup(token: BookPageToken) {
    if (token.lemma_id == null) return;
    setLookupError(false);
    const requestId = ++requestRef.current;
    setLookupLoading(true);
    try {
      const result = await lookupReviewWord(token.lemma_id);
      if (requestRef.current === requestId) setLookup(result);
    } catch (error) {
      console.warn("Book lookup failed", error);
      if (requestRef.current === requestId) setLookupError(true);
    } finally {
      if (requestRef.current === requestId) setLookupLoading(false);
    }
  }

  function toggleUnknownMark(token: BookPageToken) {
    if (!token.is_schedulable || token.lemma_id == null) return;
    const isRecorded = data?.looked_up_lemma_ids.includes(token.lemma_id) ?? false;
    if (isRecorded) return;
    setLookedUp((current) => {
      const next = new Set(current);
      next.has(token.lemma_id!) ? next.delete(token.lemma_id!) : next.add(token.lemma_id!);
      return next;
    });
  }

  function navigateTo(targetPage: number) {
    router.replace(`/book-page?storyId=${storyIdNumber}&page=${targetPage}`);
  }

  async function handleAdvance() {
    if (!data || submitting) return;
    setSubmitting(true);
    setCompletionError(false);
    try {
      await completeBookPage(
        data.story_id,
        data.page_number,
        Array.from(lookedUp),
        Date.now() - pageStartedAt.current,
      );
      AsyncStorage.removeItem(bookLookupDraftKey(data.story_id, data.page_number)).catch((error) =>
        console.warn("Failed to clear saved book lookup draft", error),
      );
      if (data.page_number < data.page_count) {
        navigateTo(data.page_number + 1);
      } else {
        router.replace("/books");
      }
    } catch (error) {
      console.error("Failed to complete book page", error);
      setCompletionError(true);
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  if (!data) {
    return (
      <View style={styles.center}>
        <Ionicons
          name={loadError ? "cloud-offline-outline" : "document-outline"}
          size={34}
          color={colors.textSecondary}
        />
        <Text style={styles.errorStateTitle}>
          {loadError ? "Couldn&apos;t load this page" : "Page not found"}
        </Text>
        <Text style={styles.errorStateText}>
          {loadError
            ? "Check your connection and try again. Your saved reading progress is safe."
            : "This page may no longer be part of the imported book."}
        </Text>
        {loadError && (
          <Pressable style={styles.retryPrimary} onPress={loadPage}>
            <Text style={styles.retryPrimaryText}>Try again</Text>
          </Pressable>
        )}
        <Pressable style={styles.backLink} onPress={() => router.replace("/books")}>
          <Text style={styles.backLinkText}>Back to library</Text>
        </Pressable>
      </View>
    );
  }

  const tokenGroups = groupBookTokens(data.tokens);
  const pendingLookupCount = pendingBookLookupIds(lookedUp, data.looked_up_lemma_ids).length;
  const selectedLemmaId = selectedToken?.lemma_id ?? null;
  const selectedIsMarked = selectedLemmaId != null && lookedUp.has(selectedLemmaId);
  const selectedIsRecorded = selectedLemmaId != null && data.looked_up_lemma_ids.includes(selectedLemmaId);

  return (
    <View style={styles.container}>
      <View style={styles.progressHeader}>
        <Text style={styles.pageLabel}>
          {data.source_page_number != null
            ? `PRINTED PAGE ${data.source_page_number} · ${data.page_number} OF ${data.page_count}`
            : `PAGE ${data.page_number} OF ${data.page_count}`}
        </Text>
        <Text style={styles.readerHint}>
          {pendingLookupCount > 0
            ? `${pendingLookupCount} new unknown word${pendingLookupCount === 1 ? "" : "s"} to save`
            : data.completed
            ? "This page is already recorded"
            : lookedUp.size > 0
              ? `${lookedUp.size} word${lookedUp.size === 1 ? "" : "s"} looked up`
              : "Tap only the words you need"}
        </Text>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        <View style={styles.paper}>
          <View style={styles.arabicFlow}>
            {tokenGroups.map((group, groupIndex) => (
              <View style={styles.sentenceFlow} key={`${group[0]?.sentence_index ?? "none"}-${groupIndex}`}>
                {group.map((token) => {
                  const isLookedUp = token.lemma_id != null && lookedUp.has(token.lemma_id);
                  const isSelected = selectedToken?.position === token.position;
                  return (
                    <Pressable
                      key={token.position}
                      accessibilityRole="button"
                      accessibilityLabel={`${token.surface_form}${token.gloss_en ? `, ${token.gloss_en}` : ""}`}
                      onPress={() => handleTokenPress(token)}
                      style={[
                        styles.wordPress,
                        isLookedUp && styles.lookedUpWordPress,
                        isSelected && styles.selectedWordPress,
                      ]}
                    >
                      <Text style={[styles.arabicWord, isLookedUp && styles.lookedUpWord]}>
                        {token.surface_form}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            ))}
          </View>

          {data.tokens.length === 0 && (
            <Text style={styles.muted}>No readable tokens on this page.</Text>
          )}

          {showTranslation && (
            <View style={styles.translationBlock}>
              <View style={styles.translationHeader}>
                <Text style={styles.translationEyebrow}>FULL TRANSLATION</Text>
                <Pressable
                  onPress={() => setShowTranslation(false)}
                  hitSlop={8}
                  accessibilityRole="button"
                  accessibilityLabel="Hide English translation"
                >
                  <Text style={styles.hideTranslation}>Hide</Text>
                </Pressable>
              </View>
              <Text style={styles.translationText}>
                {data.english_translation || "No English translation is available for this page."}
              </Text>
            </View>
          )}
        </View>

        {!showTranslation && (
          <Pressable
            style={styles.revealButton}
            onPress={() => setShowTranslation(true)}
            accessibilityRole="button"
            accessibilityLabel="Show full English translation"
          >
            <Ionicons name="language-outline" size={18} color={colors.accent} />
            <Text style={styles.revealText}>Show full English translation</Text>
          </Pressable>
        )}
      </ScrollView>

      {selectedToken && (
        <View style={styles.lookupPanel}>
          <Pressable
            style={styles.lookupClose}
            hitSlop={10}
            accessibilityRole="button"
            accessibilityLabel="Close word lookup"
            onPress={() => {
              requestRef.current += 1;
              setSelectedToken(null);
            }}
          >
            <Ionicons name="close" size={18} color={colors.textSecondary} />
          </Pressable>
          <View style={styles.lookupHead}>
            <Text style={styles.lookupArabic}>{lookup?.lemma_ar || selectedToken.surface_form}</Text>
            {lookupLoading && <ActivityIndicator size="small" color={colors.accent} />}
          </View>
          <Text style={styles.lookupGloss}>
            {lookup?.gloss_en || selectedToken.gloss_en || "No gloss available"}
          </Text>
          {lookupError && (
            <Pressable style={styles.lookupErrorRow} onPress={() => loadTokenLookup(selectedToken)}>
              <Ionicons name="alert-circle-outline" size={15} color={colors.stateLearning} />
              <Text style={styles.lookupErrorText}>Definition unavailable · tap to retry</Text>
            </Pressable>
          )}
          {(lookup?.transliteration || lookup?.root) && (
            <Text style={styles.lookupMeta}>
              {[lookup?.transliteration, lookup?.root ? `root ${lookup.root}` : null]
                .filter(Boolean)
                .join("  ·  ")}
            </Text>
          )}
          {selectedToken.is_schedulable ? (
            selectedIsRecorded ? (
              <View style={styles.evidenceRow}>
                <Ionicons name="checkmark-circle-outline" size={16} color={colors.stateLearning} />
                <Text style={styles.scheduleNote}>Already recorded as unknown on this page</Text>
              </View>
            ) : (
              <Pressable
                style={[styles.scheduleToggle, selectedIsMarked && styles.scheduleToggleMarked]}
                onPress={() => toggleUnknownMark(selectedToken)}
                accessibilityRole="button"
                accessibilityLabel={selectedIsMarked ? "Do not schedule this word" : "Schedule this word"}
              >
                <Ionicons
                  name={selectedIsMarked ? "close-circle-outline" : "add-circle-outline"}
                  size={17}
                  color={selectedIsMarked ? colors.stateLearning : colors.accent}
                />
                <View style={styles.scheduleToggleCopy}>
                  <Text style={[styles.scheduleToggleTitle, selectedIsMarked && styles.scheduleToggleTitleMarked]}>
                    {selectedIsMarked ? "Don't schedule this word" : "Schedule this word"}
                  </Text>
                  <Text style={styles.scheduleToggleHint}>
                    {selectedIsMarked
                      ? "It is currently marked unknown for this page."
                      : "It will otherwise count as understood when you finish."}
                  </Text>
                </View>
              </Pressable>
            )
          ) : (
            <Text style={styles.referenceNote}>Reference only · never added to your learning queue</Text>
          )}
          {selectedToken.lemma_id != null && (
            <Pressable onPress={() => router.push(`/word/${selectedToken.lemma_id}`)}>
              <Text style={styles.detailLink}>Open full word detail →</Text>
            </Pressable>
          )}
        </View>
      )}

      {completionError && (
        <View style={styles.completionError} accessibilityRole="alert">
          <Ionicons name="cloud-offline-outline" size={18} color={colors.stateLearning} />
          <Text style={styles.completionErrorText}>
            Couldn&apos;t save this page. Your lookups are kept — tap the button to retry.
          </Text>
        </View>
      )}

      <View style={styles.footer}>
        <Pressable
          style={[styles.footerButton, data.page_number === 1 && styles.footerButtonDisabled]}
          disabled={data.page_number === 1 || submitting}
          accessibilityRole="button"
          accessibilityLabel="Previous page"
          onPress={() => navigateTo(data.page_number - 1)}
        >
          <Text style={styles.footerSecondary}>← Previous</Text>
        </Pressable>
        <Pressable
          style={[styles.footerButton, styles.nextButton]}
          onPress={handleAdvance}
          disabled={submitting}
          accessibilityRole="button"
          accessibilityLabel={
            data.page_number === data.page_count
              ? pendingLookupCount > 0 ? "Save page and finish book" : "Finish book"
              : pendingLookupCount > 0 ? "Save page and continue" : "Continue to next page"
          }
        >
          {submitting ? (
            <ActivityIndicator size="small" color="#fff" />
          ) : (
            <Text style={styles.nextText}>
              {data.page_number === data.page_count
                ? pendingLookupCount > 0 ? "Save · Finish ✓" : "Finish book ✓"
                : pendingLookupCount > 0 ? "Save · Next →" : data.completed ? "Next page →" : "Done · Next →"}
            </Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  headerBack: { paddingLeft: 12, paddingVertical: 6 },
  progressHeader: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surface,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10,
  },
  pageLabel: { color: colors.accent, fontSize: 11, fontWeight: "700", letterSpacing: 1.1 },
  readerHint: { color: colors.textSecondary, fontSize: fonts.caption, flexShrink: 1, textAlign: "right" },
  scroll: { flex: 1 },
  content: { padding: 16, paddingBottom: 36, alignItems: "center" },
  paper: {
    width: "100%",
    maxWidth: 760,
    backgroundColor: colors.surface,
    borderRadius: 14,
    paddingHorizontal: 22,
    paddingVertical: 28,
    borderWidth: 1,
    borderColor: colors.border,
  },
  arabicFlow: {
    width: "100%",
    gap: 13,
  },
  sentenceFlow: {
    flexDirection: "row-reverse",
    flexWrap: "wrap",
    alignItems: "flex-start",
    justifyContent: "flex-start",
    gap: 3,
    rowGap: 5,
  },
  wordPress: { borderRadius: 5, paddingHorizontal: 3, paddingVertical: 1 },
  lookedUpWordPress: { backgroundColor: colors.stateLearning + "25" },
  selectedWordPress: { backgroundColor: colors.stateLearning + "45" },
  arabicWord: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 28,
    lineHeight: 44,
    writingDirection: "rtl",
  },
  lookedUpWord: { color: colors.stateLearning },
  translationBlock: { borderTopWidth: 1, borderTopColor: colors.border, marginTop: 28, paddingTop: 22 },
  translationHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 },
  translationEyebrow: { color: colors.textSecondary, fontSize: 10, fontWeight: "700", letterSpacing: 1.2 },
  hideTranslation: { color: colors.accent, fontSize: fonts.caption, fontWeight: "700" },
  translationText: { color: colors.text, fontSize: 17, lineHeight: 28 },
  revealButton: { flexDirection: "row", alignItems: "center", gap: 8, padding: 16 },
  revealText: { color: colors.accent, fontSize: fonts.small, fontWeight: "600" },
  muted: { color: colors.textSecondary, fontSize: fonts.body },
  errorStateTitle: { color: colors.text, fontSize: 19, fontWeight: "700", marginTop: 14 },
  errorStateText: { color: colors.textSecondary, fontSize: fonts.small, lineHeight: 20, textAlign: "center", maxWidth: 360, marginTop: 6, paddingHorizontal: 20 },
  retryPrimary: { minHeight: 44, justifyContent: "center", backgroundColor: colors.accent, borderRadius: 10, paddingHorizontal: 22, marginTop: 18 },
  retryPrimaryText: { color: "#fff", fontSize: fonts.small, fontWeight: "700" },
  backLink: { minHeight: 40, justifyContent: "center", paddingHorizontal: 12, marginTop: 4 },
  backLinkText: { color: colors.textSecondary, fontSize: fonts.small, fontWeight: "600" },
  lookupPanel: {
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 12,
  },
  lookupClose: { position: "absolute", right: 16, top: 14, zIndex: 2 },
  lookupHead: { flexDirection: "row", alignItems: "center", gap: 12, paddingRight: 30 },
  lookupArabic: { color: colors.arabic, fontFamily: fontFamily.arabic, fontSize: 25 },
  lookupGloss: { color: colors.text, fontSize: fonts.body, marginTop: 2 },
  lookupMeta: { color: colors.textSecondary, fontSize: fonts.small, marginTop: 4 },
  lookupErrorRow: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 6, alignSelf: "flex-start" },
  lookupErrorText: { color: colors.stateLearning, fontSize: fonts.caption, fontWeight: "600" },
  evidenceRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 9 },
  scheduleNote: { color: colors.stateLearning, fontSize: fonts.caption, fontWeight: "600" },
  scheduleToggle: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 1, borderColor: colors.accent + "55", backgroundColor: colors.accent + "10", borderRadius: 9, paddingHorizontal: 10, paddingVertical: 8, marginTop: 9, alignSelf: "flex-start", maxWidth: 420 },
  scheduleToggleMarked: { borderColor: colors.stateLearning + "66", backgroundColor: colors.stateLearning + "12" },
  scheduleToggleCopy: { flexShrink: 1 },
  scheduleToggleTitle: { color: colors.accent, fontSize: fonts.caption, fontWeight: "700" },
  scheduleToggleTitleMarked: { color: colors.stateLearning },
  scheduleToggleHint: { color: colors.textSecondary, fontSize: 10, lineHeight: 14, marginTop: 1 },
  referenceNote: { color: colors.textSecondary, fontSize: fonts.caption, marginTop: 8, fontStyle: "italic" },
  detailLink: { color: colors.accent, fontSize: fonts.caption, marginTop: 7 },
  completionError: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.stateLearning + "14", borderTopWidth: 1, borderTopColor: colors.stateLearning + "55", paddingHorizontal: 16, paddingVertical: 9 },
  completionErrorText: { flex: 1, color: colors.text, fontSize: fonts.caption, lineHeight: 17 },
  footer: {
    flexDirection: "row",
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  footerButton: { minHeight: 44, flex: 1, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  footerButtonDisabled: { opacity: 0.3 },
  footerSecondary: { color: colors.textSecondary, fontWeight: "600" },
  nextButton: { backgroundColor: colors.accent },
  nextText: { color: "#fff", fontWeight: "700" },
});
