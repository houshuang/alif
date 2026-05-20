/**
 * Polyglot reading screen — Modern Greek (primary), Ancient Greek, Latin.
 *
 * UX model:
 *   1. **Story list** — pick a text (currently only the Greek history textbook).
 *   2. **Reading view** — colored tokens by knowledge state. Tap a content
 *      word → see lemma + gloss in a single line at the bottom (doesn't
 *      break reading flow). Tap "Unknown" / "Known" / "Encountered" inline.
 *   3. **Next page** — every un-tapped content word is presumed known.
 *      This is the central UX accelerator for intermediate learners.
 *   4. **Lazy** — pages are tokenized + LLM-verified only when first viewed.
 *
 * Talks to the polyglot backend (separate from Alif). See polyglot-api.ts.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import {
  View, Text, Pressable, StyleSheet, ScrollView, ActivityIndicator,
  Platform,
} from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import {
  listStories, getPage, markWord, markRemainingKnown,
  type StorySummary, type PageView, type TokenView, type MarkState,
} from "../lib/polyglot-api";

// Reading palette. We treat the page as a book, not a flashcard surface:
// every word in body text renders in the same warm ink color, function
// words included. Knowledge state is exposed only when the user explicitly
// taps a word — see the lookup bar at the bottom and the `selectedHighlight`
// style for the in-flow tap accent.
const C = {
  bg: "#14121a",             // warm slate — softer than pure black for long reading
  surface: "#1d1a26",        // bottom-bar surface
  border: "#2f2a3a",
  ink: "#ede4cf",            // body text — warm off-white, "paper ink"
  inkMuted: "#a89c83",       // for chrome (page number, headers)
  accent: "#a98ef0",          // tap highlight + chevrons
  // State colors — used ONLY in the lookup bar and tap state, never in body
  known: "#3a3a52",
  acquiring: "#d4a06b",
  encountered: "#506a8e",
  unknown: "#c95f6f",
  ignored: "#3a3a3a",
};

// The lookup-bar uses these colors to indicate state. Body text never does.
function lookupStateColor(t: TokenView): string {
  if (t.is_known) return C.known;
  if (t.is_unknown) return C.unknown;
  if (t.is_acquiring) return C.acquiring;
  if (t.is_encountered) return C.encountered;
  if (t.is_ignored) return C.ignored;
  return C.accent;
}

export default function Polyglot() {
  const router = useRouter();
  const [stories, setStories] = useState<StorySummary[] | null>(null);
  const [storyId, setStoryId] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageData, setPageData] = useState<PageView | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<TokenView | null>(null);
  const [bulkMarking, setBulkMarking] = useState(false);
  const scrollRef = useRef<ScrollView>(null);

  useEffect(() => { listStories().then(setStories).catch(() => setStories([])); }, []);

  const loadPage = useCallback(async (sid: number, p: number) => {
    setLoading(true);
    setSelected(null);
    try {
      const data = await getPage(sid, p);
      setPageData(data);
      setPageNumber(data.page_number);
      scrollRef.current?.scrollTo({ y: 0, animated: false });
    } catch (e) {
      console.warn("Page load failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (storyId == null) return;
    // Land on the first "real content" page (skip copyright / TOC / title
    // pages) instead of always page 1. Falls back to 1 if the heuristic
    // didn't surface a content page.
    const story = stories?.find((s) => s.id === storyId);
    const startPage = story?.first_content_page_number ?? 1;
    loadPage(storyId, startPage);
  }, [storyId, loadPage, stories]);

  const handleMark = useCallback(async (state: MarkState) => {
    if (!selected?.lemma_id || !storyId) return;
    try {
      const res = await markWord(storyId, selected.lemma_id, state);
      // Optimistic update of the selected token so user sees feedback instantly
      setSelected({
        ...selected,
        is_known: state === "known",
        is_unknown: state === "unknown",
        is_encountered: state === "encountered",
        is_ignored: state === "ignore",
        is_new: false,
        gloss_en: res.gloss_en ?? selected.gloss_en,
      });
      // Re-fetch page for full coloring update — small page, so cheap
      const fresh = await getPage(storyId, pageNumber);
      setPageData(fresh);
    } catch (e) {
      console.warn("Mark failed:", e);
    }
  }, [selected, storyId, pageNumber]);

  const handleNextPage = useCallback(async () => {
    if (!pageData || !storyId) return;
    setBulkMarking(true);
    try {
      // Presume known: everything unmarked on this page → known.
      await markRemainingKnown(storyId, pageNumber);
      await loadPage(storyId, pageNumber + 1);
    } finally {
      setBulkMarking(false);
    }
  }, [pageData, storyId, pageNumber, loadPage]);

  const handlePrevPage = useCallback(() => {
    if (storyId && pageNumber > 1) loadPage(storyId, pageNumber - 1);
  }, [storyId, pageNumber, loadPage]);

  // ─── Story list ──────────────────────────────────────────────────────
  if (storyId == null) {
    return (
      <View style={styles.screen}>
        <View style={styles.headerRow}>
          <Pressable onPress={() => router.push("/languages")}><Text style={styles.headerLink}>‹ Languages</Text></Pressable>
          <Pressable onPress={() => router.push("/polyglot-stats")}>
            <Text style={styles.headerLink}>Stats ›</Text>
          </Pressable>
        </View>
        <Text style={styles.h1}>Modern Greek</Text>
        <Text style={styles.sub}>Reading — tap unknowns; next-page presumes the rest known.</Text>
        {stories == null ? (
          <ActivityIndicator color={C.accent} style={{ marginTop: 40 }} />
        ) : stories.length === 0 ? (
          <Text style={styles.empty}>
            No texts yet. Import a PDF or paste Greek text via the API.
          </Text>
        ) : (
          <ScrollView>
            {stories.map((s) => (
              <Pressable key={s.id} style={styles.storyRow} onPress={() => setStoryId(s.id)}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.storyTitle} numberOfLines={2}>{s.title || `Story #${s.id}`}</Text>
                  <Text style={styles.storyMeta}>
                    {s.language_code} · {s.page_count ?? "?"} pages · {s.processed_pages} processed
                  </Text>
                </View>
                <Text style={styles.chevron}>›</Text>
              </Pressable>
            ))}
          </ScrollView>
        )}
      </View>
    );
  }

  // ─── Page view ───────────────────────────────────────────────────────
  return (
    <View style={styles.screen}>
      <View style={styles.headerRow}>
        <Pressable onPress={() => { setStoryId(null); setPageData(null); setSelected(null); }}>
          <Text style={styles.headerLink}>‹ Library</Text>
        </Pressable>
        <Text style={styles.pageLabel}>
          {pageData ? `${pageData.page_number} / ${pageData.total_pages}` : "…"}
        </Text>
        <Pressable onPress={() => router.push("/polyglot-stats")}>
          <Text style={styles.headerLink}>Stats</Text>
        </Pressable>
      </View>

      {loading || !pageData ? (
        <ActivityIndicator color={C.accent} style={{ marginTop: 40 }} />
      ) : (
        <ScrollView ref={scrollRef} contentContainerStyle={styles.pageBody}>
          <View style={styles.column}>
            <Text style={styles.greekText} selectable={false}>
              {pageData.tokens
                // Headings (chapter/section titles, running page headers
                // injected by the PDF extractor) are meta-text; drop them
                // entirely from the prose flow. Punctuation that sits
                // inside a heading sentence is dropped too — it'd otherwise
                // leave orphan periods or numbers floating in the body.
                .filter((t) => !t.is_heading)
                .map((t, i) => {
                  const isSelected = selected != null && selected.position === t.position;
                  return (
                    <Text
                      key={i}
                      style={isSelected ? styles.tokenSelected : styles.token}
                      onPress={() => !t.is_punctuation && t.lemma_id && setSelected(t)}
                    >
                      {t.surface}
                      {!t.is_punctuation ? " " : ""}
                    </Text>
                  );
                })}
            </Text>
          </View>
        </ScrollView>
      )}

      {/* Single-line lookup bar — doesn't break reading flow */}
      {selected && (
        <View style={styles.lookupBar}>
          <View style={styles.lookupRow}>
            <Text style={styles.lookupSurface}>{selected.surface}</Text>
            {selected.lemma_form && selected.lemma_form !== selected.surface && (
              <Text style={styles.lookupLemma}> · {selected.lemma_form}</Text>
            )}
            {selected.pos && <Text style={styles.lookupPos}> · {selected.pos.toLowerCase()}</Text>}
            <Text style={styles.lookupGloss} numberOfLines={1}>
              {selected.gloss_en
                ? `  →  ${selected.gloss_en}`
                : "  →  (mark unknown to fetch gloss)"}
            </Text>
            <Pressable onPress={() => setSelected(null)} style={styles.lookupClose}>
              <Ionicons name="close" size={20} color={C.inkMuted} />
            </Pressable>
          </View>
          <View style={styles.lookupActions}>
            <Pressable style={[styles.actionBtn, { backgroundColor: C.known }]}
              onPress={() => handleMark("known")}>
              <Text style={styles.actionText}>Known</Text>
            </Pressable>
            <Pressable style={[styles.actionBtn, { backgroundColor: C.unknown }]}
              onPress={() => handleMark("unknown")}>
              <Text style={styles.actionText}>Unknown</Text>
            </Pressable>
            <Pressable style={[styles.actionBtn, { backgroundColor: C.encountered }]}
              onPress={() => handleMark("encountered")}>
              <Text style={styles.actionText}>Encountered</Text>
            </Pressable>
            <Pressable style={[styles.actionBtn, { backgroundColor: C.ignored }]}
              onPress={() => handleMark("ignore")}>
              <Text style={styles.actionText}>Ignore</Text>
            </Pressable>
          </View>
        </View>
      )}

      {/* Page nav */}
      <View style={styles.pageNav}>
        <Pressable
          style={[styles.navBtn, pageData?.page_number === 1 && styles.navBtnDisabled]}
          onPress={handlePrevPage}
          disabled={pageData?.page_number === 1}
        >
          <Text style={styles.navBtnText}>‹ Prev</Text>
        </Pressable>
        <Pressable
          style={[styles.navBtnPrimary, bulkMarking && styles.navBtnDisabled]}
          onPress={handleNextPage}
          disabled={!pageData || pageData.page_number >= pageData.total_pages || bulkMarking}
        >
          <Text style={styles.navBtnPrimaryText}>
            {bulkMarking ? "Marking…" : "Next → (presume rest known)"}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

// Book-reader typography. The body text uses a serif on iOS (Georgia is
// bundled, has solid Greek polytonic coverage) and falls back to the system
// serif elsewhere. Line-height ~1.65× font size is the Bringhurst sweet spot.
const SERIF = Platform.select({
  ios: "Georgia",
  android: "serif",
  default: "Georgia, 'Noto Serif', serif",
});
const BODY_FONT_SIZE = 20;
const BODY_LINE_HEIGHT = 33;
const READING_MAX_WIDTH = 680;          // measure cap for tablet/web

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg, paddingTop: 40 },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center",
              paddingHorizontal: 24, marginBottom: 12 },
  headerLink: { color: C.accent, fontSize: 13, letterSpacing: 0.2 },
  pageLabel: { color: C.inkMuted, fontSize: 12, letterSpacing: 0.8 },

  h1: { fontSize: 28, fontWeight: "700", color: C.ink, marginBottom: 4,
        paddingHorizontal: 24, fontFamily: SERIF },
  sub: { fontSize: 13, color: C.inkMuted, marginBottom: 20, paddingHorizontal: 24 },
  empty: { color: C.inkMuted, marginTop: 24, paddingHorizontal: 24,
           fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }), fontSize: 12 },

  storyRow: { flexDirection: "row", alignItems: "center",
              marginHorizontal: 24, paddingVertical: 16, paddingHorizontal: 16,
              backgroundColor: C.surface, borderRadius: 10, marginBottom: 10,
              borderWidth: 1, borderColor: C.border },
  storyTitle: { color: C.ink, fontSize: 17, fontWeight: "600", fontFamily: SERIF },
  storyMeta: { color: C.inkMuted, fontSize: 12, marginTop: 4 },
  chevron: { color: C.inkMuted, fontSize: 24 },

  // Page body: generous margins, max-width on wider screens to keep the
  // measure (line length) within a comfortable read.
  pageBody: { paddingHorizontal: 28, paddingTop: 8, paddingBottom: 220,
              alignItems: "center" },
  column: { maxWidth: READING_MAX_WIDTH, width: "100%" },
  greekText: {
    fontSize: BODY_FONT_SIZE,
    lineHeight: BODY_LINE_HEIGHT,
    color: C.ink,
    fontFamily: SERIF,
  },
  // The default token: same color as body — function words look identical to
  // content words. Knowledge state is invisible in body text by design.
  token: {
    fontSize: BODY_FONT_SIZE,
    lineHeight: BODY_LINE_HEIGHT,
    color: C.ink,
    fontFamily: SERIF,
  },
  tokenSelected: {
    fontSize: BODY_FONT_SIZE,
    lineHeight: BODY_LINE_HEIGHT,
    color: C.accent,
    fontFamily: SERIF,
    textDecorationLine: "underline",
    textDecorationColor: C.accent,
  },

  lookupBar: { backgroundColor: C.surface, borderTopWidth: 1, borderColor: C.border,
               paddingVertical: 12, paddingHorizontal: 20 },
  lookupRow: { flexDirection: "row", alignItems: "center", flexWrap: "nowrap" },
  lookupSurface: { color: C.ink, fontSize: 17, fontWeight: "600", fontFamily: SERIF },
  lookupLemma: { color: C.inkMuted, fontSize: 15, fontFamily: SERIF },
  lookupPos: { color: C.accent, fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6 },
  lookupGloss: { color: C.ink, fontSize: 14, flex: 1, fontFamily: SERIF },
  lookupClose: { padding: 4 },
  lookupActions: { flexDirection: "row", marginTop: 10, gap: 6 },
  actionBtn: { flex: 1, paddingVertical: 9, borderRadius: 8, alignItems: "center" },
  actionText: { color: C.ink, fontSize: 12, fontWeight: "600", letterSpacing: 0.3 },

  pageNav: { flexDirection: "row", justifyContent: "space-between",
             paddingHorizontal: 20, paddingVertical: 14, gap: 8,
             borderTopWidth: 1, borderColor: C.border, backgroundColor: C.surface },
  navBtn: { paddingVertical: 12, paddingHorizontal: 18, borderRadius: 8,
            backgroundColor: C.bg, borderWidth: 1, borderColor: C.border },
  navBtnPrimary: { flex: 1, paddingVertical: 12, paddingHorizontal: 18, borderRadius: 8,
                   backgroundColor: C.accent, alignItems: "center" },
  navBtnDisabled: { opacity: 0.4 },
  navBtnText: { color: C.ink, fontSize: 14 },
  navBtnPrimaryText: { color: "#14121a", fontSize: 14, fontWeight: "700", letterSpacing: 0.4 },
});
