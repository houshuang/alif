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
  listStories, getPage, markWord,
  getLemmaDetail,
  type StorySummary, type PageView, type TokenView,
  type LemmaDetail,
} from "../lib/polyglot-api";
import { enqueuePageReview, flushPolyglotQueue } from "../lib/polyglot-sync-queue";
import { generateClientReviewId } from "../lib/polyglot-review-helpers";
import { netStatus } from "../lib/net-status";
import { syncEvents } from "../lib/sync-events";
import { renderTokens } from "../lib/polyglot-render-helpers";
import PolyglotLookupCard from "../lib/polyglot-lookup-card";
import { POLYGLOT_COLORS } from "../lib/polyglot-design-colors";
import { POLYGLOT_FONTS } from "../lib/polyglot-design-tokens";
import { useLanguage } from "../lib/language-context";

// Polyglot surface display names (Arabic never routes to these screens).
const POLYGLOT_LANGUAGE_NAMES: Record<string, string> = {
  el: "Modern Greek",
  la: "Latin",
};

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

// Reading-cursor persistence. The lemma-detail screen is a hidden sibling tab,
// not a stacked route, so navigating into it tears this reader down; on return
// React rebuilds it fresh and the mount effect would otherwise reload the
// story's first content page — losing your page, scroll, and open lookup card.
// We stash that cursor and restore it on remount. Recency-gated (CURSOR_TTL_MS)
// so a genuine cold start long afterwards still opens on the story list rather
// than hijacking straight into the last page read.
const CURSOR_STORAGE_KEY = "@polyglot:readingCursor";
const CURSOR_TTL_MS = 15 * 60 * 1000;

type ReadingCursor = {
  storyId: number;
  pageNumber: number;
  scrollY: number;
  selectedPosition: number | null; // token position of the open lookup-card word
  savedAt: number;
};

// Reading palette. We treat the page as a book, not a flashcard surface:
// every word in body text renders in the same warm ink color, function
// words included. The only in-body color signal is the user's tap-cycle
// state on this page — red (don't know) or yellow (recognize after seeing
// English). Marks persist per (story, page) in AsyncStorage so navigating
// away and back preserves the exact taps the user made on that page,
// independent of how the backend's ULK state has evolved since.
//
// 2026-05-21: palette aligned with the polyglot Modern Editorial design
// (POLYGLOT_COLORS in lib/polyglot-design-colors.ts). The reader shares the
// EB Garamond Greek body the sentence-review and lemma-detail screens use, but
// trades the dark warm-slate surface for a light editorial one. Same visual
// language across reader, lookup card, intro card, and lemma detail page.
const C = {
  bg: POLYGLOT_COLORS.bg,
  surface: POLYGLOT_COLORS.surface,
  border: POLYGLOT_COLORS.border,
  ink: POLYGLOT_COLORS.text,
  inkMuted: POLYGLOT_COLORS.textSecondary,
  accent: POLYGLOT_COLORS.accent,
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
  // Lazy-fetched lemma detail (with enrichment) for the currently-tapped
  // word. The lookup card renders the head row immediately from `selected`
  // (already loaded as part of the page); enrichment streams in here when
  // available. Keyed by lemma_id so re-tapping the same word reuses the cache.
  const [lemmaDetailCache, setLemmaDetailCache] = useState<Record<number, LemmaDetail>>({});
  const detailRequestRef = useRef(0);
  const [bulkMarking, setBulkMarking] = useState(false);
  const scrollRef = useRef<ScrollView>(null);
  // Reading-cursor machinery (see CURSOR_STORAGE_KEY). scrollYRef tracks the
  // live scroll offset; pendingScrollYRef carries a to-be-restored offset until
  // the ScrollView has laid out; pendingRestoreRef/overridePageRef drive the
  // restore path; restoredRef makes restoration fire at most once.
  const scrollYRef = useRef(0);
  const pendingScrollYRef = useRef<number | null>(null);
  const pendingRestoreRef = useRef<ReadingCursor | null>(null);
  const overridePageRef = useRef<number | null>(null);
  const restoredRef = useRef(false);
  const insets = useSafeAreaInsets();
  const { language } = useLanguage();
  const languageCode = language === "la" ? "la" : "el";
  const languageName = POLYGLOT_LANGUAGE_NAMES[languageCode] ?? "Reading";

  useEffect(() => {
    listStories(languageCode).then(setStories).catch(() => setStories([]));
  }, [languageCode]);

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
      // If this load is restoring a saved cursor for this exact page, re-apply
      // the scroll offset (deferred to onContentSizeChange, once laid out) and
      // re-open the lookup card; otherwise start the page at the top.
      const restore = pendingRestoreRef.current;
      if (restore && restore.storyId === sid && restore.pageNumber === data.page_number) {
        pendingRestoreRef.current = null;
        pendingScrollYRef.current = restore.scrollY > 0 ? restore.scrollY : null;
        if (restore.selectedPosition != null) {
          const tok = data.tokens.find((t) => t.position === restore.selectedPosition);
          if (tok) setSelected(tok);
        }
      } else {
        pendingScrollYRef.current = null;
        scrollRef.current?.scrollTo({ y: 0, animated: false });
      }
      setPageData(data);
      setPageNumber(data.page_number);
    } catch (e) {
      console.warn("Page load failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (storyId == null) return;
    // overridePageRef is set when restoring a saved cursor — load that exact
    // page rather than jumping to the first content page. Otherwise land on the
    // first "real content" page (skip copyright / TOC / title pages), falling
    // back to 1 if the heuristic didn't surface one.
    const override = overridePageRef.current;
    overridePageRef.current = null;
    const story = stories?.find((s) => s.id === storyId);
    const page = override ?? story?.first_content_page_number ?? 1;
    loadPage(storyId, page);
  }, [storyId, loadPage, stories]);

  // Restore the last reading position after a remount. Only auto-resumes when
  // the cursor is fresh (the detail round-trip is seconds) and we're still on
  // the story list — a stale cursor from a previous day leaves you on the list.
  useEffect(() => {
    if (restoredRef.current) return;
    if (stories == null) return;   // wait for the story list to resolve
    if (storyId != null) return;   // already reading — nothing to restore
    restoredRef.current = true;
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(CURSOR_STORAGE_KEY);
        if (!raw) return;
        const cur: ReadingCursor = JSON.parse(raw);
        if (cur?.storyId == null) return;
        if (Date.now() - (cur.savedAt ?? 0) > CURSOR_TTL_MS) return;
        if (!stories.some((s) => s.id === cur.storyId)) return;
        pendingRestoreRef.current = cur;
        overridePageRef.current = cur.pageNumber;
        setStoryId(cur.storyId);
      } catch {
        // ignore — fall back to the story list
      }
    })();
  }, [stories, storyId]);

  // Persist the reading cursor so the restore effect above can land you back on
  // the same page, scroll offset, and open lookup card. Fires on any cursor
  // change; skipped mid-restore so it can't clobber a not-yet-applied offset.
  const persistCursor = useCallback(() => {
    if (storyId == null) return;
    if (pendingScrollYRef.current != null) return; // restore in flight
    const cursor: ReadingCursor = {
      storyId,
      pageNumber,
      scrollY: scrollYRef.current,
      selectedPosition: selected?.position ?? null,
      savedAt: Date.now(),
    };
    AsyncStorage.setItem(CURSOR_STORAGE_KEY, JSON.stringify(cursor)).catch(() => {});
  }, [storyId, pageNumber, selected]);

  useEffect(() => { persistCursor(); }, [persistCursor]);

  // Lazy-fetch full lemma detail (with enrichment) when a word is tapped.
  // The lookup card renders immediately from `selected`; enrichment fills in
  // as soon as the network round-trip completes. detailRequestRef guards
  // against stale responses if the user taps another word mid-flight.
  useEffect(() => {
    if (selected?.lemma_id == null) return;
    const lemmaId = selected.lemma_id;
    if (lemmaDetailCache[lemmaId]) return;  // already fetched
    const reqId = ++detailRequestRef.current;
    getLemmaDetail(lemmaId)
      .then((detail) => {
        if (detailRequestRef.current !== reqId) return;
        setLemmaDetailCache((prev) => ({ ...prev, [lemmaId]: detail }));
      })
      .catch(() => {
        // Best-effort: lookup card still renders without enrichment.
      });
  }, [selected, lemmaDetailCache]);

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
      // Advancing the page is a green comprehension review over every content
      // word you didn't tap. Reds (0) and yellows (1) carry their own signal and
      // are excluded from the green sweep — but we send them inline so the page
      // submit is self-contained: it applies the taps itself (the per-tap
      // markWord calls are best-effort and may never have reached the server
      // offline) and is idempotent on client_review_id. The whole page outcome
      // is queued first, so it survives even if we're offline right now and
      // auto-sends on reconnect.
      const unknownLemmaIds = Object.entries(cyclePositions)
        .filter(([, pos]) => pos === 0)
        .map(([id]) => Number(id));
      const encounteredLemmaIds = Object.entries(cyclePositions)
        .filter(([, pos]) => pos === 1)
        .map(([id]) => Number(id));
      await enqueuePageReview(
        {
          story_id: storyId,
          page_number: pageNumber,
          unknown_lemma_ids: unknownLemmaIds,
          encountered_lemma_ids: encounteredLemmaIds,
        },
        generateClientReviewId(),
      );
      if (netStatus.isOnline) {
        await flushPolyglotQueue();
        // Loading the next page needs server-side tokenization, so it only
        // happens online. Offline, the outcome is safely queued and we stay on
        // the current page (preserving the visible red/yellow marks) until the
        // queue auto-sends on reconnect.
        await loadPage(storyId, pageNumber + 1);
      }
    } finally {
      setBulkMarking(false);
    }
  }, [pageData, storyId, pageNumber, loadPage, cyclePositions]);

  // Auto-send any queued page reviews: once on mount (covers a previous offline
  // session) and again whenever the network comes back. Mirrors Alif's
  // flush-on-reconnect wiring (net-status emits "online" via syncEvents).
  useEffect(() => {
    flushPolyglotQueue().catch(() => {});
    const off = syncEvents.on("online", () => {
      flushPolyglotQueue().catch(() => {});
    });
    return off;
  }, []);

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
        <Text style={styles.h1}>{languageName}</Text>
        <Text style={styles.sub}>Reading — tap unknowns; next-page presumes the rest known.</Text>
        {stories == null ? (
          <ActivityIndicator color={C.accent} style={{ marginTop: 40 }} />
        ) : stories.length === 0 ? (
          <Text style={styles.empty}>
            No texts yet. Import a PDF or paste {languageName} text via the API.
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
        <Pressable onPress={() => {
          // Explicitly leaving for the library is a deliberate "done reading
          // here" — drop the cursor so a later remount doesn't auto-resume
          // back into this book.
          AsyncStorage.removeItem(CURSOR_STORAGE_KEY).catch(() => {});
          setStoryId(null); setPageData(null); setSelected(null);
        }}>
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
        <ScrollView
          ref={scrollRef}
          contentContainerStyle={styles.pageBody}
          scrollEventThrottle={100}
          onScroll={(e) => { scrollYRef.current = e.nativeEvent.contentOffset.y; }}
          onContentSizeChange={() => {
            const y = pendingScrollYRef.current;
            pendingScrollYRef.current = null;
            if (y != null && y > 0) {
              scrollRef.current?.scrollTo({ y, animated: false });
              scrollYRef.current = y;
            }
          }}
        >
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

      {/* Lookup card — Modern Editorial. Renders the basic head row from the
          already-loaded `selected` token, then streams in enrichment lazily.
          The "View full philology ›" link routes to /polyglot-lemma/{id}. */}
      {selected && selected.lemma_id != null && (() => {
        const lemmaId = selected.lemma_id;
        const pos = cyclePositions[lemmaId];
        const dotColor =
          pos === 0 ? C.cycleRed
          : pos === 1 ? C.cycleYellow
          : null;
        const gloss = glossByLemma[lemmaId] ?? selected.gloss_en;
        const detail = lemmaDetailCache[lemmaId];
        return (
          <View style={styles.lookupSlot}>
            <PolyglotLookupCard
              lemmaForm={selected.lemma_form ?? selected.surface}
              glossEn={gloss}
              pos={selected.pos}
              ancientForm={detail?.cognate_lemma_form ?? detail?.enrichment?.etymology?.ancient_form ?? null}
              enrichment={detail?.enrichment ?? null}
              frequencyRank={detail?.frequency_rank ?? null}
              cycleColor={dotColor}
              surfaceForm={selected.surface}
              onViewDetails={() => { persistCursor(); router.push(`/polyglot-lemma/${lemmaId}`); }}
              onClose={() => setSelected(null)}
            />
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

// Book-reader typography. Body prose is EB Garamond (greekBody / 400) — the
// same Greek face the sentence-review and lemma-detail screens use, loaded via
// @expo-google-fonts in app/_layout.tsx. Headings use greekDisplay (SemiBold).
// EB Garamond's x-height runs a touch smaller than Georgia's, so the body is
// nudged to 21px; line-height ~1.6× is the Bringhurst sweet spot.
const SERIF = POLYGLOT_FONTS.greekBody;
const BODY_FONT_SIZE = 21;
const BODY_LINE_HEIGHT = 34;
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

  h1: { fontSize: 28, color: C.ink, marginBottom: 4,
        paddingHorizontal: 24, fontFamily: POLYGLOT_FONTS.greekDisplay },
  sub: { fontSize: 13, color: C.inkMuted, marginBottom: 20, paddingHorizontal: 24 },
  empty: { color: C.inkMuted, marginTop: 24, paddingHorizontal: 24,
           fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }), fontSize: 12 },

  storyRow: { flexDirection: "row", alignItems: "center",
              marginHorizontal: 24, paddingVertical: 16, paddingHorizontal: 16,
              backgroundColor: C.surface, borderRadius: 10, marginBottom: 10,
              borderWidth: 1, borderColor: C.border },
  storyTitle: { color: C.ink, fontSize: 17, fontFamily: POLYGLOT_FONTS.greekDisplay },
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

  lookupSlot: { paddingHorizontal: 12, paddingTop: 8, paddingBottom: 4,
                borderTopWidth: 1, borderColor: C.border, backgroundColor: C.bg },

  pageNav: { paddingHorizontal: 20, paddingVertical: 14,
             borderTopWidth: 1, borderColor: C.border, backgroundColor: C.surface },
  navBtnPrimary: { paddingVertical: 12, paddingHorizontal: 18, borderRadius: 8,
                   backgroundColor: C.accent, alignItems: "center" },
  navBtnDisabled: { opacity: 0.4 },
  navBtnPrimaryText: { color: "#14121a", fontSize: 15, fontWeight: "700", letterSpacing: 0.4 },
});
