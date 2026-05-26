/**
 * Polyglot reading screen — Modern Greek (primary), Ancient Greek, Latin.
 * Same screen for every polyglot language; the active one is passed as
 * `language_code`.
 *
 * UX model (mirror of sentence-review, 2026-05-26):
 *   1. **Story list** — pick a text.
 *   2. **Reading view** — book typography, every word the same warm ink.
 *      Tap a content word to cycle red ("no idea") → yellow ("recognize") →
 *      clear; the gloss / lookup card appears for the tapped word. Each tap
 *      updates server state live (best-effort online).
 *   3. **Reveal** — one-way; drops the per-sentence English directly under each
 *      foreign sentence (interleaved EB Garamond italic). Source rows are the
 *      harvested `Sentence` rows for the page; NULL `translation_en` is lazy-
 *      filled in one batched LLM call. Prefetched on page load so the reveal
 *      is instant. Legacy / un-harvested pages fall back to a single whole-
 *      page LLM call shown as one trailing block. No `Hide` — pick Reveal or
 *      Know all, not both.
 *   4. **Prev · [middle] · Reveal** (pre-reveal) → **Prev · Next · [spacer]**
 *      (post-reveal). The middle button is "Know all" on fresh pages (commits
 *      the green sweep), "Next →" on already-advanced pages with no mark
 *      edits (pure navigation), and "Update" on already-advanced pages whose
 *      marks have been edited during this visit (per-tap markWord already
 *      pushed the changes; the label signals acknowledgment). Last page's
 *      middle button is "Finish ✓". The empty post-reveal right slot is
 *      intentional — a double-tap of Reveal can't accidentally land on Next,
 *      because Next sits in the middle, not where Reveal was. Sweep is
 *      idempotent on `pr:<story>:<page>` so re-advancing never double-counts;
 *      revisit + unchanged marks skip the queue write entirely.
 *   5. **Lazy** — pages are tokenized + LLM-verified on first view; the reader
 *      prefetches the next page so that work happens while you read.
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
import {
  listStories, getPage, getPageTranslation, markWord,
  getLemmaDetail,
  type StorySummary, type PageView, type TokenView,
  type LemmaDetail,
} from "../lib/polyglot-api";
import { enqueuePageReview, flushPolyglotQueue } from "../lib/polyglot-sync-queue";
import { pageReviewClientId } from "../lib/polyglot-review-helpers";
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

// Per-story max-advanced-page tracker. Lets the reader distinguish a fresh
// page (never advanced past) from a revisit (Prev'd back to it, or re-opened
// the book at an earlier page). Pre-reveal button label depends on it:
//   - fresh:    "Know all"  → asks for the green-sweep commit
//   - revisit:  "Next →" / "Update" → page already swept; mark edits go via
//               per-tap markWord, advance is navigation.
// Cheap to maintain (single int per story) and works retroactively because we
// bump on every advance; existing pre-feature data starts surfacing the right
// labels as soon as the user next advances.
const MAX_ADVANCED_STORAGE_PREFIX = "@polyglot:maxAdvanced";
const maxAdvancedStorageKey = (sid: number) =>
  `${MAX_ADVANCED_STORAGE_PREFIX}:${sid}`;

async function loadMaxAdvanced(sid: number): Promise<number> {
  try {
    const raw = await AsyncStorage.getItem(maxAdvancedStorageKey(sid));
    if (!raw) return 0;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : 0;
  } catch {
    return 0;
  }
}

async function bumpMaxAdvanced(sid: number, pn: number): Promise<void> {
  try {
    const current = await loadMaxAdvanced(sid);
    if (pn > current) {
      await AsyncStorage.setItem(maxAdvancedStorageKey(sid), String(pn));
    }
  } catch {
    // ignore — non-critical persistence
  }
}

// Two mark sets are equal when the (lemma_id → position) maps agree, treating
// missing-or-2 ("clear") as identical. Used to detect whether the user edited
// any mark during this visit to a revisited page.
function marksEqual(
  a: Record<number, number>,
  b: Record<number, number>,
): boolean {
  const keep = (v: number) => v === 0 || v === 1;
  const aFilt = Object.entries(a).filter(([, v]) => keep(v));
  const bFilt = Object.entries(b).filter(([, v]) => keep(v));
  if (aFilt.length !== bFilt.length) return false;
  const aMap = new Map(aFilt.map(([k, v]) => [k, v]));
  for (const [k, v] of bFilt) {
    if (aMap.get(k) !== v) return false;
  }
  return true;
}

// Reading-cursor persistence. Two roles: (a) survive a lemma-detail round-trip
// — that screen is a hidden sibling tab, not a stacked route, so navigating into
// it tears the reader down and React rebuilds it fresh on return; (b) serve as
// a bookmark across app reloads, so reopening the app lands you back on the
// page you were reading. Cleared on an explicit "back to library" tap
// (see goToLibrary) — that's the only way to genuinely reset to the story list.
const CURSOR_STORAGE_KEY = "@polyglot:readingCursor";

type ReadingCursor = {
  storyId: number;
  pageNumber: number;
  scrollY: number;
  selectedPosition: number | null; // token position of the open lookup-card word
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
  // English translations keyed by page_number → sentence_index_in_page → english.
  // Sentence-aligned so Reveal can interleave each English under its foreign
  // sentence (mirrors the sentence-review card's two-stage reveal but scaled to
  // a page). Fetched lazily + cached server-side; the reader prefetches on page
  // load so Reveal is instant. The page-level fallback string (for legacy pages
  // with no harvested sentence rows) lives in pageFallbackEn.
  const [sentenceTranslations, setSentenceTranslations] = useState<Record<number, Record<number, string | null>>>({});
  const [pageFallbackEn, setPageFallbackEn] = useState<Record<number, string>>({});
  // One-way reveal — mirrors polyglot-review.tsx's one-way "Show Translation".
  // Forcing a deliberate choice between Know all and Reveal pre-commit (then
  // Next post-reveal) prevents the double-tap-and-skip-the-English foot-gun.
  const [revealed, setRevealed] = useState(false);
  // Lazy-fetched lemma detail (with enrichment) for the currently-tapped
  // word. The lookup card renders the head row immediately from `selected`
  // (already loaded as part of the page); enrichment streams in here when
  // available. Keyed by lemma_id so re-tapping the same word reuses the cache.
  const [lemmaDetailCache, setLemmaDetailCache] = useState<Record<number, LemmaDetail>>({});
  const detailRequestRef = useRef(0);
  const scrollRef = useRef<ScrollView>(null);
  // In-memory page cache for the current story, keyed by page_number. Populated
  // on load and by background prefetch of the next page (so advancing is
  // instant and going back is free). Cleared when the user opens a story.
  const pageCacheRef = useRef<Record<number, PageView>>({});
  // Page numbers whose translation has been fetched or is in flight — dedups
  // prefetch + on-demand reveal without re-requesting. Cleared per story.
  const translationReqRef = useRef<Set<number>>(new Set());
  // Latest cycle positions, mirrored to a ref so the page-advance submit always
  // reads the freshest marks regardless of closure staleness.
  const cyclePositionsRef = useRef(cyclePositions);
  useEffect(() => { cyclePositionsRef.current = cyclePositions; }, [cyclePositions]);
  // Per-page mark baseline (positions loaded from AsyncStorage when the page
  // mounts). Used to detect whether the user has *edited* marks during this
  // visit — drives the "Update" button label on revisited pages.
  const initialPositionsRef = useRef<Record<number, number>>({});
  // Per-story highest page number the user has ever advanced past. Drives the
  // fresh-vs-revisited button-label split: pages with `pageNumber <= maxAdvanced`
  // have already been swept, so the middle button is "Next →" or "Update", not
  // "Know all". Loaded on story open and bumped on each successful advance.
  const [maxAdvanced, setMaxAdvanced] = useState(0);
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

  // Tracks the language whose story list is loaded, so we only RESET reading
  // state on an actual switch (not on first mount, where the cursor-restore
  // effect below should still be able to resume the last book).
  const loadedLangRef = useRef(languageCode);
  useEffect(() => {
    listStories(languageCode).then(setStories).catch(() => setStories([]));
    if (loadedLangRef.current !== languageCode) {
      loadedLangRef.current = languageCode;
      // Switching languages can't continue a book in the other language — drop
      // back to the (freshly reloaded) story list AND clear every per-story /
      // per-page cache. pageCacheRef and translations are keyed by integer page
      // number, which collides across languages (Greek page 5 vs Latin page 5);
      // without this clear, advancing in the new language could surface the
      // previous language's prefetched tokens or English translation.
      setStoryId(null);
      setPageData(null);
      setSelected(null);
      setRevealed(false);
      setSentenceTranslations({});
      setPageFallbackEn({});
      pageCacheRef.current = {};
      translationReqRef.current = new Set();
    }
  }, [languageCode]);

  // Background-prefetch a page into the cache (no spinner, best-effort). Used
  // for the next page so advancing is instant; the server-side tokenization
  // happens while the user reads the current page.
  const prefetchPage = useCallback((sid: number, p: number) => {
    if (p < 1) return;
    if (pageCacheRef.current[p]) return;
    getPage(sid, p)
      .then((d) => { pageCacheRef.current[p] = d; })
      .catch(() => { /* best-effort */ });
  }, []);

  // Fetch + cache a page's English translation. Idempotent via translationReqRef
  // (page numbers fetched-or-in-flight); a failed call (null) is removed from
  // the set so a later reveal retries. Populates both the per-sentence map
  // (modern path — interleaved Reveal) and the page-level fallback (legacy /
  // un-harvested pages).
  const fetchTranslation = useCallback((sid: number, p: number) => {
    if (translationReqRef.current.has(p)) return;
    translationReqRef.current.add(p);
    getPageTranslation(sid, p)
      .then((t) => {
        const gotSomething =
          (t.sentences && t.sentences.length > 0) ||
          (t.translation_en != null && t.translation_en !== "");
        if (!gotSomething && t.translation_en == null) {
          // null translation_en + empty sentences ⇒ LLM failed; retry later.
          translationReqRef.current.delete(p);
          return;
        }
        if (t.sentences && t.sentences.length > 0) {
          const byIdx: Record<number, string | null> = {};
          for (const s of t.sentences) byIdx[s.sentence_index_in_page] = s.translation_en;
          setSentenceTranslations((prev) => ({ ...prev, [p]: byIdx }));
        }
        if (t.translation_en != null) {
          setPageFallbackEn((prev) => ({ ...prev, [p]: t.translation_en as string }));
        }
      })
      .catch(() => { translationReqRef.current.delete(p); });
  }, []);

  const loadPage = useCallback(async (sid: number, p: number) => {
    setSelected(null);
    setGlossByLemma({});
    setRevealed(false);
    setCyclePositions({});
    initialPositionsRef.current = {};
    loadCyclePositions(sid, p).then((loaded) => {
      // Snapshot the baseline BEFORE setting React state so the comparison key
      // for "user edited marks during this visit" is exact, not racy.
      initialPositionsRef.current = loaded;
      setCyclePositions(loaded);
    });

    const applyData = (data: PageView) => {
      pageCacheRef.current[data.page_number] = data;
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
      // Warm the next page (so Next is instant) and this page's translation
      // (so "Show English" is instant).
      if (data.page_number < data.total_pages) prefetchPage(sid, data.page_number + 1);
      fetchTranslation(sid, data.page_number);
    };

    // Cache hit → swap in instantly, no spinner (covers back-nav + prefetched
    // next page). Cache miss → fetch with a spinner.
    const cached = pageCacheRef.current[p];
    if (cached) {
      applyData(cached);
      return;
    }
    setLoading(true);
    try {
      applyData(await getPage(sid, p));
    } catch (e) {
      console.warn("Page load failed:", e);
    } finally {
      setLoading(false);
    }
  }, [prefetchPage, fetchTranslation]);

  // Open a story fresh — drop the previous story's page + translation caches so
  // page numbers from a different book can't leak in.
  const openStory = useCallback((sid: number) => {
    pageCacheRef.current = {};
    translationReqRef.current = new Set();
    setSentenceTranslations({});
    setPageFallbackEn({});
    setStoryId(sid);
    // Surface the persisted max-advanced page so the very first render of any
    // page can show the correct fresh-vs-revisit label. 0 (no record yet) means
    // every page in this story is fresh until proven otherwise.
    loadMaxAdvanced(sid).then(setMaxAdvanced);
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

  // Restore the last reading position after a remount. Serves both a
  // lemma-detail round-trip and a full app reload — the cursor is only cleared
  // when the user explicitly returns to the library (goToLibrary). The
  // stories.some() guard prevents a cursor from one language hijacking when
  // the user has switched language since.
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
  // deletes the ULK (see reading_intake.mark_lemma). This is also how editing
  // marks on an already-read page updates the server live — independent of the
  // one-time page-advance green sweep.
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
  }, [cyclePositions, storyId, pageNumber]);

  // Reveal the per-sentence English. One-way until Next/Prev moves the page —
  // mirrors polyglot-review.tsx's one-way "Show Translation" reveal. Kicks the
  // lazy fetch on first reveal (idempotent — usually already prefetched).
  const handleReveal = useCallback(() => {
    setRevealed(true);
    if (storyId != null) fetchTranslation(storyId, pageNumber);
  }, [storyId, pageNumber, fetchTranslation]);

  // Submit a page's green comprehension sweep — the page-scale analogue of a
  // sentence-review submit. Self-contained + idempotent on a deterministic
  // per-(story,page) id, so re-advancing across a page never double-counts;
  // mark edits flow live via handleTap. Queued first (offline-safe), flushed in
  // the background — never awaited, so advancing is instant.
  const submitPageReview = useCallback((sid: number, pn: number) => {
    const positions = cyclePositionsRef.current;
    const unknownLemmaIds = Object.entries(positions)
      .filter(([, pos]) => pos === 0)
      .map(([id]) => Number(id));
    const encounteredLemmaIds = Object.entries(positions)
      .filter(([, pos]) => pos === 1)
      .map(([id]) => Number(id));
    enqueuePageReview(
      {
        story_id: sid,
        page_number: pn,
        unknown_lemma_ids: unknownLemmaIds,
        encountered_lemma_ids: encounteredLemmaIds,
      },
      pageReviewClientId(sid, pn),
    ).then(() => {
      if (netStatus.isOnline) flushPolyglotQueue().catch(() => {});
    });
  }, []);

  const goToLibrary = useCallback(() => {
    // Explicitly leaving for the library is a deliberate "done reading here" —
    // drop the cursor so a later remount doesn't auto-resume into this book.
    AsyncStorage.removeItem(CURSOR_STORAGE_KEY).catch(() => {});
    setStoryId(null);
    setPageData(null);
    setSelected(null);
  }, []);

  // Advance ("submit"). Runs the current page's green sweep, then moves to the
  // next page — instantly if prefetched, otherwise with a brief load. The last
  // page's button finishes the book and returns to the library. Offline with an
  // un-prefetched next page: the review is safely queued and we stay put (page
  // rendering needs server tokenization), preserving the visible marks until
  // reconnect.
  const handleAdvance = useCallback(() => {
    if (!pageData || !storyId || loading) return;
    const pn = pageData.page_number;
    // Bump max-advanced BEFORE submit so a re-render mid-flush already labels
    // this page as "revisited" — relevant if the user immediately Prev's back.
    if (pn > maxAdvanced) {
      setMaxAdvanced(pn);
      bumpMaxAdvanced(storyId, pn).catch(() => {});
    }
    // Revisits where marks haven't changed skip the queue write entirely — the
    // server has already applied this page (deterministic `pr:<sid>:<pn>` id,
    // dedupes server-side), and mark edits on revisits flow live via handleTap.
    // Skipping the no-op enqueue avoids cluttering the offline queue. Fresh
    // pages and revisits with edited marks still submit (server dedupes the
    // second-case anyway, but consistency over cleverness).
    const isRevisit = pn <= maxAdvanced;
    const marksUnchanged = isRevisit && marksEqual(
      cyclePositionsRef.current,
      initialPositionsRef.current,
    );
    if (!marksUnchanged) {
      submitPageReview(storyId, pn);
    }
    if (pn >= pageData.total_pages) {
      goToLibrary();
      return;
    }
    const next = pn + 1;
    if (pageCacheRef.current[next] || netStatus.isOnline) {
      loadPage(storyId, next);
    }
  }, [pageData, storyId, loading, maxAdvanced, submitPageReview, goToLibrary, loadPage]);

  const handlePrevPage = useCallback(() => {
    if (!pageData || !storyId || loading) return;
    const prev = pageData.page_number - 1;
    if (prev < 1) return;
    // No green sweep on the way back — going back is navigation, not a submit.
    // Marks for the previous page are restored by loadPage; editing them there
    // updates the server live via handleTap.
    if (pageCacheRef.current[prev] || netStatus.isOnline) {
      loadPage(storyId, prev);
    }
  }, [pageData, storyId, loading, loadPage]);

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
        <Text style={styles.sub}>Reading — tap unknowns, flip for English, next-page presumes the rest known.</Text>
        {stories == null ? (
          <ActivityIndicator color={C.accent} style={{ marginTop: 40 }} />
        ) : stories.length === 0 ? (
          <Text style={styles.empty}>
            No texts yet. Import a PDF or paste {languageName} text via the API.
          </Text>
        ) : (
          <ScrollView>
            {stories.map((s) => (
              <Pressable key={s.id} style={styles.storyRow} onPress={() => openStory(s.id)}>
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
  const atFirstPage = pageData ? pageData.page_number <= 1 : true;
  const atLastPage = pageData ? pageData.page_number >= pageData.total_pages : false;
  // Revisit detection: pages at or below the persisted max already had their
  // green sweep applied. Mark-change detection compares current React state to
  // the on-mount baseline (initialPositionsRef). Drives the middle-button label
  // pre-reveal so "Know all" only shows on fresh pages.
  const isRevisit = pageData ? pageData.page_number <= maxAdvanced : false;
  const marksChanged = isRevisit && !marksEqual(cyclePositions, initialPositionsRef.current);
  // Any red (cycle pos 0) or yellow (cycle pos 1) tap means the learner has
  // already expressed partial knowledge. Pre-reveal on a fresh page, the
  // middle button should soften from the "I know everything" claim of
  // "Know all" to plain navigation. Position 2 (clear) and undefined both
  // mean "no tap" and don't count.
  const hasTaps = Object.values(cyclePositions).some((p) => p === 0 || p === 1);
  const pn = pageData?.page_number;
  const sentenceEn = pn != null ? sentenceTranslations[pn] : undefined;
  const fallbackEn = pn != null ? pageFallbackEn[pn] : undefined;

  // Group renderable tokens by sentence_index so we can render each sentence
  // as its own <Text> block and interleave the English under it when revealed.
  // Headings are filtered first (same rule as before — meta-text, not prose).
  const sentenceGroups: { sentenceIdx: number; tokens: TokenView[] }[] = (() => {
    if (!pageData) return [];
    const out: { sentenceIdx: number; tokens: TokenView[] }[] = [];
    for (const t of pageData.tokens) {
      if (t.is_heading) continue;
      const last = out[out.length - 1];
      if (last && last.sentenceIdx === t.sentence_index) last.tokens.push(t);
      else out.push({ sentenceIdx: t.sentence_index, tokens: [t] });
    }
    return out;
  })();

  return (
    <View style={[styles.screen, { paddingTop: insets.top + 8 }]}>
      <View style={styles.headerRow}>
        <Pressable onPress={goToLibrary}>
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
            {/* One <Text> block per sentence — lets us drop the English under
                each one when revealed (interleaved sentence pairs, the design
                we landed on after the design-review mock 2026-05-26). Soft-
                hyphens never cross sentence boundaries, so per-sentence
                renderTokens is safe. */}
            {sentenceGroups.map((group) => {
              const spans = renderTokens(group.tokens);
              const en = sentenceEn ? sentenceEn[group.sentenceIdx] : undefined;
              return (
                <View key={group.sentenceIdx} style={styles.sentenceBlock}>
                  <Text style={styles.greekText} selectable={false}>
                    {spans.map((span, i) => {
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
                  {revealed && en && (
                    <Text style={styles.sentenceEnText}>{en}</Text>
                  )}
                </View>
              );
            })}

            {/* Page-level fallback shown only when no per-sentence English came
                back (legacy / un-harvested pages) — same layout as before the
                redesign so old corpora still get a usable reveal. */}
            {revealed && !sentenceEn && (
              <View style={styles.translationBlock}>
                <View style={styles.translationDivider} />
                {fallbackEn === undefined ? (
                  <ActivityIndicator color={C.accent} style={{ alignSelf: "flex-start" }} />
                ) : fallbackEn === "" ? (
                  <Text style={styles.translationEmpty}>No translatable text on this page.</Text>
                ) : (
                  <Text style={styles.translationText}>{fallbackEn}</Text>
                )}
              </View>
            )}
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

      {/* Page nav. Pre-reveal: [Prev] [Know all] [Reveal] — the reader either
          commits to knowing the page (Know all sweeps marks + advances) or
          asks for help (Reveal shows interleaved English). Post-reveal: the
          right slot is intentionally an empty spacer so a double-tap on Reveal
          can't accidentally tap Next where Reveal used to be — Next sits in
          the middle instead. Mirrors polyglot-review.tsx's spacer pattern. */}
      <View style={styles.pageNav}>
        <Pressable
          style={[styles.navBtnGhost, (atFirstPage || loading) && styles.navBtnDisabled]}
          onPress={handlePrevPage}
          disabled={atFirstPage || loading}
        >
          <Text style={styles.navBtnGhostText}>‹ Prev</Text>
        </Pressable>
        <Pressable
          style={[styles.navBtnPrimary, (!pageData || loading) && styles.navBtnDisabled]}
          onPress={handleAdvance}
          disabled={!pageData || loading}
        >
          <Text style={styles.navBtnPrimaryText}>
            {atLastPage
              ? "Finish ✓"
              : revealed
                ? "Next →"
                // Pre-reveal middle-button label depends on three signals.
                // Revisit + edited marks: "Update" — per-tap markWord already
                // pushed; this is the acknowledgment.
                // Revisit + unchanged marks: "Next →" — sweep already ran; just
                // navigation.
                // Fresh + any red/yellow tap: "Next →" — the no-help "Know all"
                // claim no longer applies once the learner has flagged unknown
                // words; the button is plain navigation that still submits the
                // page sweep.
                // Fresh + no taps: "Know all" — the deliberate no-help commit.
                : isRevisit
                  ? (marksChanged ? "Update" : "Next →")
                  : hasTaps
                    ? "Next →"
                    : "Know all"}
          </Text>
        </Pressable>
        {revealed ? (
          <View style={styles.navBtnSpacer} />
        ) : (
          <Pressable
            style={[styles.navBtnPrimary, !pageData && styles.navBtnDisabled]}
            onPress={handleReveal}
            disabled={!pageData}
          >
            <Text style={styles.navBtnPrimaryText}>Reveal</Text>
          </Pressable>
        )}
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

  // One block per sentence, so Reveal can drop the English under each
  // foreign sentence. Vertical spacing is part of the prose rhythm — too
  // little and the English merges with the next foreign sentence; too much
  // and the page reads as a list. 6px above the English, 14px between
  // sentence blocks lands right.
  sentenceBlock: { marginBottom: 14 },
  sentenceEnText: {
    fontSize: BODY_FONT_SIZE - 5,
    lineHeight: BODY_LINE_HEIGHT - 6,
    color: C.inkMuted,
    fontFamily: SERIF,
    fontStyle: "italic",
    marginTop: 6,
    paddingLeft: 14,
    borderLeftWidth: 2,
    borderLeftColor: C.border,
  },

  // Legacy page-level fallback (un-harvested pages) — muted ink below a
  // short hairline rule. Same as the pre-redesign behaviour so old corpora
  // still render usably.
  translationBlock: { marginTop: 28 },
  translationDivider: { height: 1, width: 56, backgroundColor: C.border, marginBottom: 18 },
  translationText: {
    fontSize: BODY_FONT_SIZE - 1,
    lineHeight: BODY_LINE_HEIGHT - 2,
    color: C.inkMuted,
    fontFamily: SERIF,
  },
  translationEmpty: { fontSize: 13, color: C.inkMuted, fontStyle: "italic" },

  lookupSlot: { paddingHorizontal: 12, paddingTop: 8, paddingBottom: 4,
                borderTopWidth: 1, borderColor: C.border, backgroundColor: C.bg },

  pageNav: { flexDirection: "row", gap: 10, alignItems: "stretch",
             paddingHorizontal: 20, paddingVertical: 14,
             borderTopWidth: 1, borderColor: C.border, backgroundColor: C.surface },
  navBtnGhost: { paddingVertical: 12, paddingHorizontal: 14, borderRadius: 8,
                 borderWidth: 1, borderColor: C.border, backgroundColor: C.bg,
                 alignItems: "center", justifyContent: "center" },
  navBtnGhostText: { color: C.accent, fontSize: 14, fontWeight: "600", letterSpacing: 0.2 },
  navBtnWide: { flex: 1 },
  navBtnPrimary: { flex: 1, paddingVertical: 12, paddingHorizontal: 18, borderRadius: 8,
                   backgroundColor: C.accent, alignItems: "center", justifyContent: "center" },
  // Empty right slot post-reveal — same width Reveal occupied, intentionally
  // unpressable so a double-tap of Reveal can't land on Next.
  navBtnSpacer: { flex: 1 },
  navBtnDisabled: { opacity: 0.4 },
  navBtnPrimaryText: { color: "#14121a", fontSize: 15, fontWeight: "700", letterSpacing: 0.4 },
});
