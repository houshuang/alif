import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";

import { getStories } from "../lib/api";
import { StoryListItem } from "../lib/types";
import { colors, fontFamily, fonts } from "../lib/theme";

export default function BooksScreen() {
  const [books, setBooks] = useState<StoryListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const router = useRouter();

  const loadBooks = useCallback(async (refresh = false) => {
    refresh ? setRefreshing(true) : setLoading(true);
    try {
      const stories = await getStories();
      setBooks(stories.filter((story) => story.source === "book_ocr"));
      setLoadError(false);
    } catch (error) {
      console.error("Failed to load book library", error);
      setLoadError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { loadBooks(); }, [loadBooks]));

  function openBook(book: StoryListItem) {
    const nextPage = Math.min(book.page_count || 1, book.next_unread_page || 1);
    router.push(`/book-page?storyId=${book.id}&page=${Math.max(1, nextPage)}`);
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => loadBooks(true)} tintColor={colors.accent} />}
    >
      <View style={styles.hero}>
        <View style={styles.heroIcon}>
          <Ionicons name="library-outline" size={26} color={colors.accent} />
        </View>
        <View style={styles.heroCopy}>
          <Text style={styles.eyebrow}>SLOW READING</Text>
          <Text style={styles.heading}>Your Arabic library</Text>
          <Text style={styles.subtitle}>
            Books stay separate from study until you read them. Tap unfamiliar words; everything else in the passage is recorded as understood only when you continue.
          </Text>
        </View>
      </View>

      <View style={styles.actions}>
        <Pressable style={styles.importButton} onPress={() => router.push("/book-import")}>
          <Ionicons name="camera-outline" size={18} color={colors.text} />
          <Text style={styles.importText}>Import photographed book</Text>
        </Pressable>
      </View>

      {loadError && (
        <View style={styles.errorCard}>
          <Ionicons name="cloud-offline-outline" size={22} color={colors.stateLearning} />
          <View style={styles.errorCopy}>
            <Text style={styles.errorTitle}>Couldn&apos;t load your library</Text>
            <Text style={styles.errorText}>
              Check your connection. Any books already shown here are kept on screen.
            </Text>
          </View>
          <Pressable style={styles.retryButton} onPress={() => loadBooks()}>
            <Text style={styles.retryText}>Retry</Text>
          </Pressable>
        </View>
      )}

      {loading ? (
        <ActivityIndicator size="large" color={colors.accent} style={styles.loader} />
      ) : loadError && books.length === 0 ? (
        null
      ) : books.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No books yet</Text>
          <Text style={styles.emptyText}>Imported books will appear here without changing your learning queue.</Text>
        </View>
      ) : (
        <View style={styles.grid}>
          {books.map((book) => {
            const totalPages = book.page_count || 1;
            const pagesRead = book.pages_read || 0;
            const progress = Math.min(100, Math.round((pagesRead / totalPages) * 100));
            return (
              <Pressable key={book.id} style={styles.bookCard} onPress={() => openBook(book)}>
                <View style={styles.bookTop}>
                  <View style={styles.spine} />
                  <View style={styles.bookTitles}>
                    <Text style={styles.titleAr} numberOfLines={2}>{book.title_ar || "كتاب عربي"}</Text>
                    <Text style={styles.titleEn} numberOfLines={2}>{book.title_en || "Untitled book"}</Text>
                  </View>
                  <Ionicons name="chevron-forward" size={18} color={colors.textSecondary} />
                </View>
                <View style={styles.progressTrack}>
                  <View style={[styles.progressFill, { width: `${progress}%` }]} />
                </View>
                <View style={styles.bookFooter}>
                  <Text style={styles.progressText}>{pagesRead} of {totalPages} pages read</Text>
                  <Text style={styles.continueText}>{pagesRead > 0 ? "Continue" : "Begin"}</Text>
                </View>
              </Pressable>
            );
          })}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 18, paddingBottom: 42, maxWidth: 900, width: "100%", alignSelf: "center" },
  hero: { flexDirection: "row", gap: 14, marginBottom: 22, alignItems: "flex-start" },
  heroIcon: { width: 48, height: 48, borderRadius: 14, backgroundColor: colors.accent + "18", alignItems: "center", justifyContent: "center" },
  heroCopy: { flex: 1 },
  eyebrow: { color: colors.accent, fontSize: 10, fontWeight: "700", letterSpacing: 1.3, marginBottom: 3 },
  heading: { color: colors.text, fontSize: 25, fontWeight: "700" },
  subtitle: { color: colors.textSecondary, fontSize: fonts.small, lineHeight: 20, marginTop: 6, maxWidth: 640 },
  actions: { flexDirection: "row", marginBottom: 18 },
  importButton: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: 10, paddingHorizontal: 13, paddingVertical: 10 },
  importText: { color: colors.text, fontSize: fonts.small, fontWeight: "600" },
  errorCard: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.stateLearning + "66", borderRadius: 12, padding: 14, marginBottom: 16 },
  errorCopy: { flex: 1 },
  errorTitle: { color: colors.text, fontSize: fonts.small, fontWeight: "700" },
  errorText: { color: colors.textSecondary, fontSize: fonts.caption, lineHeight: 17, marginTop: 2 },
  retryButton: { minHeight: 36, justifyContent: "center", paddingHorizontal: 12, borderRadius: 8, backgroundColor: colors.accent + "18" },
  retryText: { color: colors.accent, fontSize: fonts.small, fontWeight: "700" },
  loader: { marginTop: 60 },
  empty: { backgroundColor: colors.surface, borderRadius: 14, padding: 28, alignItems: "center", borderWidth: 1, borderColor: colors.border },
  emptyTitle: { color: colors.text, fontSize: 18, fontWeight: "700" },
  emptyText: { color: colors.textSecondary, textAlign: "center", marginTop: 6, fontSize: fonts.small },
  grid: { gap: 12 },
  bookCard: { backgroundColor: colors.surface, borderRadius: 14, borderWidth: 1, borderColor: colors.border, padding: 16 },
  bookTop: { flexDirection: "row", alignItems: "center", gap: 13 },
  spine: { width: 5, alignSelf: "stretch", borderRadius: 3, backgroundColor: colors.accent },
  bookTitles: { flex: 1 },
  titleAr: { color: colors.arabic, fontFamily: fontFamily.arabic, fontSize: 22, textAlign: "right", writingDirection: "rtl" },
  titleEn: { color: colors.text, fontSize: fonts.body, fontWeight: "600", marginTop: 2 },
  progressTrack: { height: 3, backgroundColor: colors.surfaceLight, borderRadius: 3, marginTop: 15, overflow: "hidden" },
  progressFill: { height: "100%", backgroundColor: colors.gotIt },
  bookFooter: { flexDirection: "row", justifyContent: "space-between", marginTop: 9 },
  progressText: { color: colors.textSecondary, fontSize: fonts.caption },
  continueText: { color: colors.accent, fontSize: fonts.caption, fontWeight: "700" },
});
