/**
 * Polyglot reading screen — Modern Greek (primary), Ancient Greek, Latin.
 *
 * UX model:
 *   1. **Story list** — pick a text (currently only the Greek history textbook).
 *   2. **Reading view** — book typography, every word the same warm ink.
 *      Tap a content word → it's marked unknown and the English gloss appears
 *      at the bottom. No buttons, no state pickers — the act of tapping IS
 *      the answer ("I don't know this one"). Misclicks get sorted in review.
 *   3. **Next page** — every un-tapped content word is presumed known. The
 *      only page-nav action; no Prev (forward reading only).
 *   4. **Lazy** — pages are tokenized + LLM-verified only when first viewed.
 *
 * Talks to the polyglot backend (separate from Alif). See polyglot-api.ts.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import {
  View, Text, Pressable, StyleSheet, ScrollView, ActivityIndicator,
  Platform,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import {
  listStories, getPage, markWord, markRemainingKnown,
  type StorySummary, type PageView, type TokenView,
} from "../lib/polyglot-api";
import { renderTokens } from "../lib/polyglot-render-helpers";

// Per-page tap-cycle persistence. Red (0) and yellow (1) marks survive
// page navigation and app reloads so the user keeps the visual record of
// what they've tapped on this specific page, regardless of how the
// backend's ULK state has since evolved (a red tap becomes 'acquiring',
// later may graduate to 'known' — but the user still sees their red).
// Cleared marks (cycle position 2) are dropped from storage.
const CYCLE_STORAGE_PREFIX = "@polyglot:cyclePos";
const cycleStorageKey = (sid: number, pn: number) =>
  `${CYCLE_STORAGE_PREFIX}:${sid}:${pn}`;

async function loadCyclePositions(
  sid: number, pn: number,
): Promise<Record<number, number>> {
  try {
    const raw = await AsyncStorage.getItem(cycleStorageKey(sid, pn));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed != null ? parsed : {};
  } catch {
    return {};
  }
}

async function saveCyclePositions(
  sid: number, pn: number, positions: Record<number, number>,
): Promise<void> {
  const filtered: Record<number, number> = {};
  for (const [k, v] of Object.entries(positions)) {
    if (v === 0 || v === 1) filtered[Number(k)] = v;
  }
  try {
    if (Object.keys(filtered).length === 0) {
      await AsyncStorage.removeItem(cycleStorageKey(sid, pn));
    } else {
      await AsyncStorage.setItem(cycleStorageKey(sid, pn), JSON.stringify(filtered));
    }
  } catch {
    // ignore — non-critical persistence
  }
}

// Reading palette. We treat the page as a book, not a flashcard surface:
// every word in body text renders in the same warm ink color, function
// words included. The only in-body color signal is the user's tap-cycle
// state on this page — red (don't know) or yellow (recognize after seeing
// English). Marks persist per (story, page) in AsyncStorage so navigating
// away and back preserves the exact taps the user made on that page,
// independent of how the backend's ULK state has evolved since.
const C = {
  bg: "#14121a",             // warm slate — softer than pure black for long reading
  surface: "#1d1a26",        // bottom-bar surface
  border: "#2f2a3a",
  ink: "#ede4cf",            // body text — warm off-white, "paper ink"
  inkMuted: "#a89c83",       // for chrome (page number, headers)
  accent: "#a98ef0",         // selection underline
  cycleRed: "#c95f6f",       // tap 1: "no idea" — full SRS enrollment
  cycleYellow: "#d4a06b",    // tap 2: "recognize after seeing English"
};

// Tap-cycle positions, in order. Tapping a word advances by one position
// (wrapping after the third). See handleTap. The third position is a true
// "unmark" — the backend deletes the ULK row and the body styling reverts.
//
// Why "no_idea" / "recognize" rather than the backend's "unknown" /
// "encountered" terms: the backend names describe SRS lifecycle stages;
// these names describe what the user is *expressing* with the tap.
const CYCLE: { state: "unknown" | "encountered" | "clear"; label: string }[] = [
  { state: "unknown",     label: "no idea" },
  { state: "encountered", label: "recognize" },
  { state: "clear",       label: "untapped" },
];

export default function Polyglot() {
  const router = useRouter();
  const [stories, setStories] = useState<StorySummary[] | null>(null);
  const [storyId, setStoryId] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageData, setPageData] = useState<PageView | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<TokenView | null>(null);
  // Per-page tap-cycle positions, keyed by lemma_id. 0 = unknown (red),
  // 1 = encountered (yellow), 2 = clear (no state). Tapping a word advances
  // by one (wrapping). Restored from AsyncStorage on page load so red/yellow
  // marks persist across navigation and app reloads. Same lemma appearing at
  // two positions on one page shares state — natural since both are the same
  // word.
  const [cyclePositions, setCyclePositions] = useState<Record<number, number>>({});
  const [glossByLemma, setGlossByLemma] = useState<Record<number, string | null>>({});
  const [bulkMarking, setBulkMarking] = useState(false);
  const scrollRef = useRef<ScrollView>(null);
  const insets = useSafeAreaInsets();

  useEffect(() => { listStories().then(setStories).catch(() => setStories([])); }, []);

  const loadPage = useCallback(async (sid: number, p: number) => {
    setLoading(true);
    setSelected(null);
    setCyclePositions({});
    setGlossByLemma({});
    try {
      const [data, savedPositions] = await Promise.all([
        getPage(sid, p),
        loadCyclePositions(sid, p),
      ]);
      setCyclePositions(savedPositions);
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

  // Tap-cycle handler. The act of tapping a word advances its position in
  // the three-state cycle:
  //   tap 1 → "no idea" (red, enrols in SRS)
  //   tap 2 → "recognize" (yellow, light tracking, no SRS)
  //   tap 3 → "untapped" (clear, ULK deleted, treated as if never tapped)
  //   tap 4 → back to "no idea"
  // Tapping a different word selects it fresh at position 0. The backend
  // ALWAYS receives the resulting state, including "clear" which truly
  // deletes the ULK (see reading_intake.mark_lemma).
  const handleTap = useCallback(async (t: TokenView) => {
    if (!t.lemma_id || !storyId) return;
    const lemmaId = t.lemma_id;
    // Advance from whatever this lemma's last cycle position was (even if
    // the focus has since moved to another word). Re-tapping a lemma always
    // means "advance one more position" — not "reset to red." Otherwise
    // glancing at a different word, then returning, would silently undo the
    // earlier cycle progress.
    const nextPos = ((cyclePositions[lemmaId] ?? -1) + 1) % CYCLE.length;
    const nextState = CYCLE[nextPos].state;

    setSelected(t);
    setCyclePositions((prev) => {
      const next = { ...prev, [lemmaId]: nextPos };
      saveCyclePositions(storyId, pageNumber, next);
      return next;
    });

    try {
      const res = await markWord(storyId, lemmaId, nextState);
      if (res.gloss_en != null) {
        setGlossByLemma((prev) => ({ ...prev, [lemmaId]: res.gloss_en }));
      }
    } catch (e) {
      console.warn(`Tap-cycle to ${nextState} failed:`, e);
    }
  }, [selected, cyclePositions, storyId, pageNumber]);

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

  // ─── Story list ──────────────────────────────────────────────────────
  if (storyId == null) {
    return (
      <View style={[styles.screen, { paddingTop: insets.top + 8 }]}>
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
    <View style={[styles.screen, { paddingTop: insets.top + 8 }]}>
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
              {renderTokens(
                // Headings (chapter/section titles, running page headers
                // injected by the PDF extractor) are meta-text; drop them
                // entirely before computing spacing so the body reads as
                // continuous prose.
                pageData.tokens.filter((t) => !t.is_heading),
              ).map((span, i) => {
                const lemmaId = span.token.lemma_id;
                const cyclePos = lemmaId != null ? cyclePositions[lemmaId] : undefined;
                const cycleStyle =
                  cyclePos === 0 ? styles.tokenCycleRed
                  : cyclePos === 1 ? styles.tokenCycleYellow
                  : undefined;
                const isSelected =
                  selected != null && selected.position === span.token.position;
                const tokenStyle = cycleStyle ?? (isSelected ? styles.tokenSelected : styles.token);
                return (
                  <Text key={i} style={tokenStyle}>
                    {span.leadingSpace}
                    <Text
                      onPress={
                        !span.isPunctuation && span.token.lemma_id
                          ? () => handleTap(span.token)
                          : undefined
                      }
                    >
                      {span.surface}
                    </Text>
                  </Text>
                );
              })}
            </Text>
          </View>
        </ScrollView>
      )}

      {/* Lookup bar — surface word + English gloss + close. No lemma,
          no POS, no action buttons. Cycle dot reflects current tap state. */}
      {selected && selected.lemma_id != null && (() => {
        const lemmaId = selected.lemma_id;
        const pos = cyclePositions[lemmaId];
        const dotColor =
          pos === 0 ? C.cycleRed
          : pos === 1 ? C.cycleYellow
          : C.inkMuted;
        const gloss = glossByLemma[lemmaId] ?? selected.gloss_en;
        return (
          <View style={styles.lookupBar}>
            <View style={styles.lookupRow}>
              <View style={[styles.lookupDot, { backgroundColor: dotColor }]} />
              <Text style={styles.lookupSurface}>{selected.surface}</Text>
              <Text style={styles.lookupGloss} numberOfLines={2}>
                {gloss ? `  ${gloss}` : "  …"}
              </Text>
              <Pressable onPress={() => setSelected(null)} style={styles.lookupClose}>
                <Ionicons name="close" size={20} color={C.inkMuted} />
              </Pressable>
            </View>
          </View>
        );
      })()}

      {/* Page nav — single "Next" button. No Prev (forward reading only). */}
      <View style={styles.pageNav}>
        <Pressable
          style={[styles.navBtnPrimary, bulkMarking && styles.navBtnDisabled]}
          onPress={handleNextPage}
          disabled={!pageData || pageData.page_number >= pageData.total_pages || bulkMarking}
        >
          <Text style={styles.navBtnPrimaryText}>
            {bulkMarking ? "Marking…" : "Next →"}
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
  // Top inset is applied dynamically via useSafeAreaInsets in the component
  // body so iOS notch / Android status-bar height get the right value
  // instead of the previous static 40.
  screen: { flex: 1, backgroundColor: C.bg },
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
  pageBody: { paddingHorizontal: 28, paddingTop: 20, paddingBottom: 220,
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
    color: C.ink,
    fontFamily: SERIF,
    textDecorationLine: "underline",
    textDecorationColor: C.accent,
  },
  // In-body cycle colors. Underline + tinted text so the cycle state is
  // visible without losing the prose's serif feel.
  tokenCycleRed: {
    fontSize: BODY_FONT_SIZE,
    lineHeight: BODY_LINE_HEIGHT,
    color: C.cycleRed,
    fontFamily: SERIF,
    textDecorationLine: "underline",
    textDecorationColor: C.cycleRed,
  },
  tokenCycleYellow: {
    fontSize: BODY_FONT_SIZE,
    lineHeight: BODY_LINE_HEIGHT,
    color: C.cycleYellow,
    fontFamily: SERIF,
    textDecorationLine: "underline",
    textDecorationColor: C.cycleYellow,
  },

  lookupBar: { backgroundColor: C.surface, borderTopWidth: 1, borderColor: C.border,
               paddingVertical: 14, paddingHorizontal: 20 },
  lookupRow: { flexDirection: "row", alignItems: "center", flexWrap: "nowrap" },
  lookupDot: { width: 10, height: 10, borderRadius: 5, marginRight: 10 },
  lookupSurface: { color: C.ink, fontSize: 17, fontWeight: "600", fontFamily: SERIF },
  lookupGloss: { color: C.ink, fontSize: 15, flex: 1, fontFamily: SERIF },
  lookupClose: { padding: 4, marginLeft: 8 },

  pageNav: { paddingHorizontal: 20, paddingVertical: 14,
             borderTopWidth: 1, borderColor: C.border, backgroundColor: C.surface },
  navBtnPrimary: { paddingVertical: 12, paddingHorizontal: 18, borderRadius: 8,
                   backgroundColor: C.accent, alignItems: "center" },
  navBtnDisabled: { opacity: 0.4 },
  navBtnPrimaryText: { color: "#14121a", fontSize: 15, fontWeight: "700", letterSpacing: 0.4 },
});
