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

const C = {
  bg: "#0f0f1a",
  surface: "#1a1a2e",
  border: "#2a2a40",
  text: "#e0e0f0",
  textDim: "#9090a8",
  accent: "#7aa2f7",
  known: "#3a3a52",          // already known — fades into background
  acquiring: "#d4a06b",       // orange — actively learning
  encountered: "#506a8e",     // muted blue — seen, not claimed
  unknown: "#c95f6f",         // red — marked unknown
  newWord: "#e0e0f0",         // default — never seen
  oov: "#5a5a70",
  ignored: "#3a3a3a",
};

function tokenColor(t: TokenView): string {
  // Function words always faded — they're noise for an intermediate learner.
  if (t.is_function_word) return C.known;
  if (t.is_known) return C.known;
  if (t.is_unknown) return C.unknown;
  if (t.is_acquiring) return C.acquiring;
  if (t.is_encountered) return C.encountered;
  if (t.is_ignored) return C.ignored;
  if (t.is_oov) return C.oov;
  return C.newWord;
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

  useEffect(() => { if (storyId != null) loadPage(storyId, 1); }, [storyId, loadPage]);

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
            No texts yet. From the project shell, import a PDF:
            {"\n\n"}curl -X POST http://localhost:3001/api/texts/pdf \
            {"\n"}     -H 'Content-Type: application/json' \
            {"\n"}     -d {`'{"language_code":"el","pdf_path":"..."}'`}
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
          <Text style={styles.greekText}>
            {pageData.tokens.map((t, i) => (
              <Text
                key={i}
                style={[styles.token, { color: tokenColor(t) }]}
                onPress={() => !t.is_punctuation && t.lemma_id && setSelected(t)}
              >
                {t.surface}
                {!t.is_punctuation ? " " : ""}
              </Text>
            ))}
          </Text>
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
              <Ionicons name="close" size={20} color={C.textDim} />
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

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg, paddingTop: 40 },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center",
              paddingHorizontal: 16, marginBottom: 8 },
  headerLink: { color: C.accent, fontSize: 14 },
  pageLabel: { color: C.textDim, fontSize: 14 },

  h1: { fontSize: 28, fontWeight: "700", color: C.text, marginBottom: 4, paddingHorizontal: 16 },
  sub: { fontSize: 13, color: C.textDim, marginBottom: 16, paddingHorizontal: 16 },
  empty: { color: C.textDim, marginTop: 24, paddingHorizontal: 16,
           fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }), fontSize: 12 },

  storyRow: { flexDirection: "row", alignItems: "center",
              marginHorizontal: 16, paddingVertical: 14, paddingHorizontal: 12,
              backgroundColor: C.surface, borderRadius: 8, marginBottom: 8,
              borderWidth: 1, borderColor: C.border },
  storyTitle: { color: C.text, fontSize: 16, fontWeight: "600" },
  storyMeta: { color: C.textDim, fontSize: 12, marginTop: 4 },
  chevron: { color: C.textDim, fontSize: 24 },

  pageBody: { paddingHorizontal: 16, paddingBottom: 220 },
  greekText: { fontSize: 19, lineHeight: 32, color: C.text },
  token: { fontSize: 19, lineHeight: 32 },

  lookupBar: { backgroundColor: C.surface, borderTopWidth: 1, borderColor: C.border,
               paddingVertical: 10, paddingHorizontal: 16 },
  lookupRow: { flexDirection: "row", alignItems: "center", flexWrap: "nowrap" },
  lookupSurface: { color: C.text, fontSize: 17, fontWeight: "600" },
  lookupLemma: { color: C.textDim, fontSize: 15 },
  lookupPos: { color: C.accent, fontSize: 11, textTransform: "uppercase" },
  lookupGloss: { color: C.text, fontSize: 14, flex: 1 },
  lookupClose: { padding: 4 },
  lookupActions: { flexDirection: "row", marginTop: 8, gap: 6 },
  actionBtn: { flex: 1, paddingVertical: 8, borderRadius: 6, alignItems: "center" },
  actionText: { color: C.text, fontSize: 12, fontWeight: "600" },

  pageNav: { flexDirection: "row", justifyContent: "space-between",
             paddingHorizontal: 16, paddingVertical: 12, gap: 8,
             borderTopWidth: 1, borderColor: C.border, backgroundColor: C.surface },
  navBtn: { paddingVertical: 12, paddingHorizontal: 18, borderRadius: 6,
            backgroundColor: C.bg, borderWidth: 1, borderColor: C.border },
  navBtnPrimary: { flex: 1, paddingVertical: 12, paddingHorizontal: 18, borderRadius: 6,
                   backgroundColor: C.accent, alignItems: "center" },
  navBtnDisabled: { opacity: 0.4 },
  navBtnText: { color: C.text, fontSize: 14 },
  navBtnPrimaryText: { color: "#0f0f1a", fontSize: 14, fontWeight: "700" },
});
