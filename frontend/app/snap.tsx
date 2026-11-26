import { useEffect, useMemo, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  ActivityIndicator,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useRouter } from "expo-router";

import { colors, fontFamily, fonts, arabicText } from "../lib/theme";
import {
  snapDiscover,
  addDiscoveredWord,
  lookupSnapWord,
  checkLookupSense,
  getSnapHistory,
  getSnapPage,
  getSnapPrematch,
  logCardShown,
  lookupReviewWord,
  lookupReviewWordFresh,
  recordLookupMiss,
} from "../lib/api";
import type {
  DiscoverWord, LookupSense, SnapHistoryItem, SnapPrematch, SnapResult, WordFailureCause,
  WordLookupResult,
} from "../lib/types";
import DockWordPanel from "../lib/review/DockWordPanel";
import { AssistedRecognitionCauses } from "../lib/review/AssistedRecognitionCauses";
import {
  alignSentences,
  firstArabicToken,
  linkForArabic,
  linkForEnglish,
  paragraphsOf,
  type AlignedSentence,
  type SnapToken,
} from "../lib/snap-alignment";
import AddChecks from "../components/AddChecks";

type AddState = "idle" | "adding" | "added" | "known" | "error";

// The open page, cached so a reload shows it at once (and offline).
const OPEN_PAGE_KEY = "alif.snap.openPage.v1";

async function rememberOpenPage(page: SnapResult | null) {
  try {
    if (page) await AsyncStorage.setItem(OPEN_PAGE_KEY, JSON.stringify(page));
    else await AsyncStorage.removeItem(OPEN_PAGE_KEY);
  } catch {
    // A cache miss only means the reader starts on the history list.
  }
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

type Token = { text: string; key: string; sentence: string; tappable: boolean };
type Focus = { sentence: number; link: number | null; en?: number };
// A word matched to a vocabulary entry is then checked against its sentence.
type SenseState = { status: "checking" } | { status: "done"; sense: LookupSense } | { status: "error" };
type Lookup =
  | { status: "loading" }
  | { status: "done"; word: DiscoverWord | null; sense?: SenseState }
  | { status: "error" };

// The sense check depends on the sentence, so a lookup is cached per token and sentence.
const lookupKey = (t: { text: string; sentence: string }) => `${t.text}\u0000${t.sentence}`;

const ARABIC_LETTER = /[\u0621-\u064A]/;
// The backend tokenizer (_ARABIC_TOKEN in routers/discover.py): the page pass
// keys its answers by these tokens.
const ARABIC_TOKEN = /[\u0621-\u064A\u064B-\u065F\u0670-\u06FF\u0750-\u077F]+/g;
// The page pass takes about 25 s after OCR; stop asking after three minutes.
const PREMATCH_POLL_MS = 3000;
const PREMATCH_POLL_TRIES = 60;
// Words the learner already studies show their state instead of an Add button.
const STUDIED_STATES = new Set(["acquiring", "learning", "known", "lapsed", "suspended"]);

// Paragraphs of whitespace-split tokens; each token remembers its sentence so a
// tapped word is glossed in context.
function tokenizePage(text: string): Token[][] {
  return text.split(/\n+/).filter((p) => p.trim()).map((para, pi) => {
    const sentences = para.match(/[^.!؟?:؛]+[.!؟?:؛]*/g) ?? [para];
    const out: Token[] = [];
    sentences.forEach((sentence, si) => {
      const context = sentence.trim();
      sentence.split(/(\s+)/).forEach((part, ti) => {
        if (!part) return;
        out.push({
          text: part,
          key: `${pi}-${si}-${ti}`,
          sentence: context,
          tappable: ARABIC_LETTER.test(part),
        });
      });
    });
    return out;
  });
}

// Snap-to-read: photograph an authentic Arabic page → faithful English translation
// + the top unknown words as add-to-Alif chips. One synchronous backend round trip
// (OCR/translation plus lexical lookup), so the reader gets help in the moment
// rather than batch-importing a book after the fact.
export default function SnapScreen() {
  const router = useRouter();
  // `?snap=<id>` opens a saved page directly (pre-read links to its pages).
  const { snap: snapParam } = useLocalSearchParams<{ snap?: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SnapResult | null>(null);
  // The last photo, so a failed page can be retried without retaking it.
  const [lastUri, setLastUri] = useState<string | null>(null);
  const [history, setHistory] = useState<SnapHistoryItem[]>([]);
  const scrollRef = useRef<ScrollView>(null);
  const scrollToTop = () => scrollRef.current?.scrollTo({ y: 0, animated: false });

  async function refreshHistory() {
    try {
      setHistory(await getSnapHistory());
    } catch {
      // Offline: keep whatever list we already have.
    }
  }

  function showPage(page: SnapResult) {
    setAdds({});
    setSelected(null);
    setLookups({});
    setFocus(null);
    setExpanded(new Set());
    setResult(page);
    rememberOpenPage(page);
    scrollToTop();
  }

  // Restore the page that was open before a reload, then refresh it from the
  // server so word states are current.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (Number(snapParam)) {
        // A linked page wins over the cached one.
        refreshHistory();
        return;
      }
      let cached: SnapResult | null = null;
      try {
        const raw = await AsyncStorage.getItem(OPEN_PAGE_KEY);
        cached = raw ? (JSON.parse(raw) as SnapResult) : null;
      } catch {
        cached = null;
      }
      if (cached && !cancelled) setResult(cached);
      if (cached?.snap_id) {
        try {
          const fresh = await getSnapPage(cached.snap_id);
          if (!cancelled) {
            setResult(fresh);
            rememberOpenPage(fresh);
          }
        } catch {
          // Keep the cached copy.
        }
      }
      refreshHistory();
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const id = Number(snapParam);
    if (!id) return;
    openFromHistory({ snap_id: id } as SnapHistoryItem);
    router.setParams({ snap: undefined });
  }, [snapParam]);

  async function openFromHistory(item: SnapHistoryItem) {
    setBusy(true);
    setError(null);
    try {
      showPage(await getSnapPage(item.snap_id));
    } catch {
      setError("Couldn't open that page. Check your connection.");
    } finally {
      setBusy(false);
    }
  }
  // lemma_ar_bare -> add state
  const [adds, setAdds] = useState<Record<string, AddState>>({});
  const [selected, setSelected] = useState<Token | null>(null);
  // lookupKey(token) -> lookup result, so re-tapping a word is instant
  const [lookups, setLookups] = useState<Record<string, Lookup>>({});
  // A new page comes back before its suggested words; fetch them when ready.
  useEffect(() => {
    const id = result?.snap_id;
    if (!id || !result?.words_pending) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const poll = async () => {
      try {
        const fresh = await getSnapPage(id);
        if (cancelled) return;
        if (!fresh.words_pending) {
          setResult((prev) => {
            if (!prev || prev.snap_id !== id) return prev;
            const next = { ...prev, words: fresh.words, count: fresh.count, words_pending: false };
            rememberOpenPage(next);
            return next;
          });
          return;
        }
      } catch {
        // Try again on the next tick.
      }
      if (!cancelled && ++tries < PREMATCH_POLL_TRIES) timer = setTimeout(poll, PREMATCH_POLL_MS);
    };
    timer = setTimeout(poll, PREMATCH_POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [result?.snap_id, result?.words_pending]);

  // The page's words matched and checked in the background; a tap on one of
  // them needs no model call. Until it arrives, taps use the per-tap lookup.
  const [prematch, setPrematch] = useState<NonNullable<SnapPrematch["words"]>>({});
  useEffect(() => {
    setPrematch({});
    const id = result?.snap_id;
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const poll = async () => {
      try {
        const r = await getSnapPrematch(id);
        if (cancelled) return;
        if (r.status === "ready") {
          setPrematch(r.words ?? {});
          return;
        }
        if (r.status === "failed") return;
      } catch {
        // Offline or a server error: taps keep using the per-tap lookup.
      }
      if (!cancelled && ++tries < PREMATCH_POLL_TRIES) timer = setTimeout(poll, PREMATCH_POLL_MS);
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [result?.snap_id]);
  const paragraphs = useMemo(
    () => (result ? tokenizePage(result.arabic_text) : []),
    [result]
  );
  // Sentence pairs with phrase links; when present they replace the separate
  // translation and Arabic blocks.
  const aligned = useMemo(
    () => (result?.sentences?.length ? paragraphsOf(alignSentences(result.sentences)) : []),
    [result]
  );
  const [focus, setFocus] = useState<Focus | null>(null);
  // Paragraph indexes whose Arabic is shown; collapsed by default.
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggleParagraph(pi: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(pi)) next.delete(pi);
      else next.add(pi);
      return next;
    });
  }

  function selectToken(t: Token) {
    if (selected?.key === t.key) {
      setSelected(null);
      return;
    }
    openToken(t);
  }

  function openToken(t: Token) {
    setSelected(t);
    const cached = lookups[lookupKey(t)];
    if (cached && cached.status !== "error") return;
    fetchLookup(t);
  }

  const asToken = (s: AlignedSentence, t: SnapToken): Token => ({
    text: t.text, key: t.key, sentence: s.ar, tappable: true,
  });

  function tapArabic(s: AlignedSentence, t: SnapToken) {
    if (selected?.key === t.key) {
      setSelected(null);
      setFocus(null);
      return;
    }
    setFocus({ sentence: s.index, link: linkForArabic(s, t) });
    openToken(asToken(s, t));
  }

  function tapEnglish(s: AlignedSentence, i: number, paragraph: number) {
    const link = linkForEnglish(s, i);
    setFocus({ sentence: s.index, link, en: i });
    const source = link === null ? null : firstArabicToken(s, link);
    if (source) {
      // Show the Arabic the word comes from.
      setExpanded((prev) => new Set(prev).add(paragraph));
      openToken(asToken(s, source));
    } else setSelected(null);
  }

  function arHighlighted(s: AlignedSentence, t: SnapToken): boolean {
    if (selected?.key === t.key) return true;
    if (!focus || focus.sentence !== s.index || focus.link === null) return false;
    return s.links[focus.link]?.ar.has(t.match) ?? false;
  }

  function enHighlighted(s: AlignedSentence, i: number): boolean {
    if (!focus || focus.sentence !== s.index) return false;
    if (focus.en === i) return true;
    return focus.link !== null && (s.links[focus.link]?.en.has(i) ?? false);
  }

  function logTap(t: Token, source: "prematch" | "live", started: number, extra: Record<string, unknown>) {
    logCardShown({
      card_type: "snap_tap",
      detail: {
        snap_id: result?.snap_id ?? null, surface: t.text, source,
        ms: Date.now() - started, ...extra,
      },
    });
  }

  async function fetchLookup(t: Token) {
    const key = lookupKey(t);
    const started = Date.now();
    const tokens = t.text.match(ARABIC_TOKEN) ?? [];
    const stored = tokens.length === 1 ? prematch[tokens[0]] : undefined;
    if (stored) {
      setLookups((p) => ({
        ...p,
        [key]: {
          status: "done",
          word: stored.word,
          sense: stored.sense ? { status: "done", sense: stored.sense } : undefined,
        },
      }));
      logTap(t, "prematch", started, {
        lemma_id: stored.word.lemma_id ?? null, verdict: stored.sense?.verdict ?? null,
      });
      return;
    }
    setLookups((p) => ({ ...p, [key]: { status: "loading" } }));
    let word: DiscoverWord | null;
    try {
      word = await lookupSnapWord(t.text, t.sentence);
    } catch {
      setLookups((p) => ({ ...p, [key]: { status: "error" } }));
      return;
    }
    // Entries from the vocabulary were matched without context; new words were
    // already glossed against the sentence.
    const lemmaId = word?.lemma_source === "database" && t.sentence ? word.lemma_id : null;
    setLookups((p) => ({
      ...p,
      [key]: { status: "done", word, sense: lemmaId ? { status: "checking" } : undefined },
    }));
    if (!lemmaId) {
      logTap(t, "live", started, { lemma_id: word?.lemma_id ?? null, verdict: null });
      return;
    }
    let sense: SenseState;
    try {
      sense = { status: "done", sense: await checkLookupSense(lemmaId, t.text, t.sentence) };
    } catch {
      sense = { status: "error" };
    }
    logTap(t, "live", started, {
      lemma_id: lemmaId, verdict: sense.status === "done" ? sense.sense.verdict : null,
    });
    setLookups((p) => {
      const cur = p[key];
      return cur?.status === "done" ? { ...p, [key]: { ...cur, sense } } : p;
    });
  }

  async function runSnap(imageUri: string) {
    setLastUri(imageUri);
    setBusy(true);
    setError(null);
    setResult(null);
    setAdds({});
    setSelected(null);
    setLookups({});
    setFocus(null);
    setExpanded(new Set());
    try {
      const r = await snapDiscover(imageUri);
      showPage(r);
      refreshHistory();
    } catch (e: any) {
      const msg = String(e?.message || e);
      setError(
        msg.includes("422")
          ? "No Arabic text found in that photo — try again with the page filling the frame."
          : msg.includes("API error")
            ? "The server couldn't process that page."
            : "Couldn't reach the server. Check your connection."
      );
    } finally {
      setBusy(false);
    }
  }

  async function takePhoto() {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== "granted" && Platform.OS !== "web") {
      setError("Camera permission is needed to snap a page.");
      return;
    }
    const res = await ImagePicker.launchCameraAsync({ quality: 0.8 });
    if (res.canceled || res.assets.length === 0) return;
    runSnap(res.assets[0].uri);
  }

  async function pickPhoto() {
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.8,
    });
    if (res.canceled || res.assets.length === 0) return;
    runSnap(res.assets[0].uri);
  }

  async function addWord(w: DiscoverWord, sentence?: string) {
    const key = w.lemma_ar_bare;
    if (adds[key] === "adding" || adds[key] === "added" || adds[key] === "known") return;
    setAdds((p) => ({ ...p, [key]: "adding" }));
    try {
      const r = await addDiscoveredWord(w, {
        surface: sentence ? selected?.text : w.surface,
        sentence: sentence ?? w.example_ar,
        snapId: result?.snap_id,
      });
      setAdds((p) => ({ ...p, [key]: r.already_known ? "known" : "added" }));
    } catch {
      setAdds((p) => ({ ...p, [key]: "error" }));
    }
  }

  async function enrichWord(w: DiscoverWord, sentence: string): Promise<number | null> {
    const key = w.lemma_ar_bare;
    try {
      const r = await addDiscoveredWord(w, {
        surface: selected?.text, sentence, snapId: result?.snap_id,
      }, true);
      setAdds((p) => ({ ...p, [key]: r.already_known ? "known" : "added" }));
      return r.lemma_id ?? null;
    } catch {
      setAdds((p) => ({ ...p, [key]: "error" }));
      return null;
    }
  }

  async function addAll() {
    if (!result) return;
    for (const w of result.words) {
      // eslint-disable-next-line no-await-in-loop
      await addWord(w);
    }
  }

  function reset() {
    scrollToTop();
    rememberOpenPage(null);
    refreshHistory();
    setResult(null);
    setError(null);
    setAdds({});
    setSelected(null);
    setLookups({});
    setFocus(null);
  }

  const addedCount = Object.values(adds).filter(
    (s) => s === "added" || s === "known"
  ).length;

  const selectedLookup = selected ? lookups[lookupKey(selected)] : undefined;

  return (
    <View style={styles.screen}>
    <ScrollView
      ref={scrollRef}
      style={styles.screen}
      contentContainerStyle={[styles.content, selected && styles.contentWithPanel]}
      keyboardShouldPersistTaps="handled"
    >
      {!result && !busy && (
        <View style={styles.intro}>
          <Ionicons name="camera-outline" size={48} color={colors.accent} />
          <Text style={styles.introTitle}>Snap a page to read</Text>
          <Text style={styles.introBody}>
            Photograph any Arabic page for an instant English translation plus the top
            new words — tap to add them to your learning.
          </Text>
          <Pressable style={styles.primaryBtn} onPress={takePhoto}>
            <Ionicons name="camera" size={20} color="#fff" />
            <Text style={styles.primaryBtnText}>Take photo</Text>
          </Pressable>
          <Pressable style={styles.secondaryBtn} onPress={pickPhoto}>
            <Ionicons name="images-outline" size={20} color={colors.accent} />
            <Text style={styles.secondaryBtnText}>Choose from library</Text>
          </Pressable>
          <Pressable style={styles.secondaryBtn} onPress={() => router.push("/preread")}>
            <Ionicons name="albums-outline" size={20} color={colors.accent} />
            <Text style={styles.secondaryBtnText}>Pre-read pages</Text>
          </Pressable>
          {error && <Text style={styles.error}>{error}</Text>}
          {error && lastUri && (
            <Pressable style={styles.secondaryBtn} onPress={() => runSnap(lastUri)}>
              <Ionicons name="refresh" size={20} color={colors.accent} />
              <Text style={styles.secondaryBtnText}>Try again</Text>
            </Pressable>
          )}
          <AddChecks />
          {history.length > 0 && (
            <View style={styles.history}>
              <Text style={styles.sectionLabel}>Recent pages</Text>
              {history.map((item) => (
                <Pressable
                  key={item.snap_id}
                  style={styles.historyRow}
                  onPress={() => openFromHistory(item)}
                  accessibilityRole="button"
                >
                  <Text style={styles.historyWhen}>{formatWhen(item.created_at)}</Text>
                  <Text style={styles.historyPreview} numberOfLines={2}>
                    {item.preview_en || item.preview_ar}
                  </Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      )}

      {busy && (
        <View style={styles.intro}>
          <ActivityIndicator size="large" color={colors.accent} />
          <Text style={styles.introBody}>Reading the page…</Text>
        </View>
      )}

      {result && aligned.length > 0 && (
        <View>
          <View style={styles.wordsHeaderFirst}>
            <Text style={styles.sectionLabel}>Page</Text>
            <Pressable
              onPress={() =>
                setExpanded(expanded.size === aligned.length ? new Set() : new Set(aligned.map((_, i) => i)))
              }
              hitSlop={8}
            >
              <Text style={styles.addAllText}>
                {expanded.size === aligned.length ? "Hide all Arabic" : "Show all Arabic"}
              </Text>
            </Pressable>
          </View>
          <View style={styles.arabicCard}>
            {aligned.map((paragraph, pi) => {
              const open = expanded.has(pi);
              const english = (s: AlignedSentence, style: object) => (
                <Text key={`en-${s.index}`} style={style}>
                  {s.enTokens.map((t, i) =>
                    t.isWord ? (
                      <Text
                        key={t.key}
                        onPress={() => tapEnglish(s, i, pi)}
                        style={enHighlighted(s, i) ? styles.tokenSelected : undefined}
                      >
                        {t.text}
                      </Text>
                    ) : (
                      t.text
                    )
                  )}
                  {" "}
                </Text>
              );
              return (
                <View key={pi} style={pi > 0 ? styles.arabicParagraph : undefined}>
                  <Pressable
                    onPress={() => toggleParagraph(pi)}
                    hitSlop={8}
                    style={styles.paraToggle}
                    accessibilityRole="button"
                    accessibilityLabel={open ? "Hide Arabic" : "Show Arabic"}
                  >
                    <Ionicons
                      name={open ? "chevron-down" : "chevron-forward"}
                      size={14}
                      color={colors.textSecondary}
                    />
                    <Text style={styles.paraToggleText}>عربي</Text>
                  </Pressable>
                  {open ? (
                    paragraph.map((s) => (
                      <View key={s.index} style={styles.pair}>
                        <Text style={styles.arabicText}>
                          {s.arTokens.map((t) =>
                            t.isWord ? (
                              <Text
                                key={t.key}
                                onPress={() => tapArabic(s, t)}
                                style={selected?.key === t.key ? styles.tokenSelected
                                  : arHighlighted(s, t) ? styles.tokenLinked : undefined}
                                accessibilityRole="button"
                              >
                                {t.text}
                              </Text>
                            ) : (
                              t.text
                            )
                          )}
                        </Text>
                        {english(s, styles.pairEnglish)}
                      </View>
                    ))
                  ) : (
                    <Text style={styles.paraEnglish}>
                      {paragraph.map((s) => english(s, {}))}
                    </Text>
                  )}
                </View>
              );
            })}
          </View>
        </View>
      )}

      {result && (
        <View>
          {aligned.length === 0 && (<>
          {/* English translation */}
          <Text style={styles.sectionLabel}>Translation</Text>
          <View style={styles.translationCard}>
            <Text style={styles.translationText} selectable>
              {result.translation_en || "(no translation returned)"}
            </Text>
          </View>

          {/* Original Arabic: every word is tappable */}
          <Text style={[styles.sectionLabel, styles.arabicLabel]}>Arabic</Text>
          <View style={styles.arabicCard}>
            {paragraphs.map((tokens, pi) => (
              <Text key={pi} style={[styles.arabicText, pi > 0 && styles.arabicParagraph]}>
                {tokens.map((t) =>
                  t.tappable ? (
                    <Text
                      key={t.key}
                      onPress={() => selectToken(t)}
                      style={selected?.key === t.key ? styles.tokenSelected : undefined}
                      accessibilityRole="button"
                    >
                      {t.text}
                    </Text>
                  ) : (
                    t.text
                  )
                )}
              </Text>
            ))}
          </View>
          </>)}

          {/* Top words */}
          <View style={styles.wordsHeader}>
            <Text style={styles.sectionLabel}>
              {result.words_pending ? "Top words" : `Top words (${result.words.length})`}
            </Text>
            {result.words.length > 0 && (
              <Pressable onPress={addAll} hitSlop={8}>
                <Text style={styles.addAllText}>
                  {addedCount >= result.words.length ? "All added" : "Add all"}
                </Text>
              </Pressable>
            )}
          </View>

          {result.words_pending && (
            <View style={styles.wordsPending}>
              <ActivityIndicator size="small" color={colors.textSecondary} />
              <Text style={styles.introBody}>Finding words…</Text>
            </View>
          )}

          {!result.words_pending && result.words.length === 0 && (
            <Text style={styles.introBody}>
              No new words here — looks like you already know this page's vocabulary.
            </Text>
          )}

          {result.words.map((w) => (
            <WordChip
              key={w.lemma_ar_bare}
              word={w}
              state={adds[w.lemma_ar_bare] || stateFromKnowledge(w.knowledge_state)}
              onAdd={() => addWord(w)}
            />
          ))}

          <Pressable style={styles.secondaryBtn} onPress={reset}>
            <Ionicons name="camera-outline" size={20} color={colors.accent} />
            <Text style={styles.secondaryBtnText}>New photo</Text>
          </Pressable>
        </View>
      )}
    </ScrollView>
    {selected && (
      <WordPanel
        key={lookupKey(selected)}
        surface={selected.text}
        sentence={selected.sentence}
        snapId={result?.snap_id ?? null}
        lookup={selectedLookup}
        addState={(w) => adds[w.lemma_ar_bare] || "idle"}
        onAdd={(w) => addWord(w, selected.sentence)}
        onEnrich={(w) => enrichWord(w, selected.sentence)}
        onRetry={() => fetchLookup(selected)}
        onClose={() => {
          setSelected(null);
          setFocus(null);
        }}
      />
    )}
    </View>
  );
}

function WordPanel({
  surface,
  sentence,
  snapId,
  lookup,
  addState,
  onAdd,
  onEnrich,
  onRetry,
  onClose,
}: {
  surface: string;
  sentence: string;
  snapId: number | null;
  lookup: Lookup | undefined;
  addState: (w: DiscoverWord) => AddState;
  onAdd: (w: DiscoverWord) => void;
  /** Add (if needed) and enrich now; resolves to the lemma id, or null on failure. */
  onEnrich: (w: DiscoverWord) => Promise<number | null>;
  onRetry: () => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const matched = lookup?.status === "done" ? lookup.word : null;
  const sense = lookup?.status === "done" ? lookup.sense : undefined;
  const checking = sense?.status === "checking";
  const verdict = sense?.status === "done" ? sense.sense : null;
  // A different word: show the one the sentence uses, and the wrong match below it.
  const replaced = verdict?.verdict === "different" && verdict.word ? matched : null;
  let word = replaced ? verdict!.word! : matched;
  if (word && verdict?.verdict === "broader" && verdict.gloss_en) {
    // A written widening is the stored gloss now; an unwritten one (the page
    // pass only proposes) is shown as the meaning here.
    word = verdict.written
      ? { ...word, gloss_en: verdict.gloss_en }
      : { ...word, gloss_here: word.gloss_here || verdict.gloss_en };
  }
  const isName = verdict?.verdict === "name" || !!word?.is_proper_noun
    || (word?.pos || "").toLowerCase().startsWith("proper");
  const studied = !!word?.knowledge_state && STUDIED_STATES.has(word.knowledge_state);

  // A word the learner studies, or one just enriched, shows the review word card.
  const [enrichedId, setEnrichedId] = useState<number | null>(null);
  const [enriching, setEnriching] = useState(false);
  const cardLemmaId = enrichedId ?? (studied && !isName ? word?.lemma_id ?? null : null);
  const [card, setCard] = useState<WordLookupResult | null>(null);
  const [cardError, setCardError] = useState(false);
  const [causes, setCauses] = useState<WordFailureCause[]>([]);
  useEffect(() => {
    setCard(null);
    setCardError(false);
    if (cardLemmaId == null) return;
    let live = true;
    (enrichedId != null ? lookupReviewWordFresh(cardLemmaId) : lookupReviewWord(cardLemmaId))
      .then((r) => { if (live) setCard(r); })
      .catch(() => { if (live) setCardError(true); });
    return () => { live = false; };
  }, [cardLemmaId, enrichedId]);

  function toggleCause(cause: WordFailureCause) {
    if (cardLemmaId == null) return;
    const next = causes.includes(cause) ? causes.filter((c) => c !== cause) : [...causes, cause];
    setCauses(next);
    recordLookupMiss({ lemmaId: cardLemmaId, surface, context: sentence, snapId, causes: next })
      .catch(() => setCauses(causes));
  }

  async function enrich(w: DiscoverWord) {
    setEnriching(true);
    const id = await onEnrich(w);
    setEnriching(false);
    if (id != null) setEnrichedId(id);
  }

  return (
    <View style={styles.panel}>
      <View style={styles.panelHeader}>
        <Text style={styles.panelSurface}>{surface}</Text>
        <Pressable onPress={onClose} hitSlop={12} accessibilityLabel="Close">
          <Ionicons name="close" size={22} color={colors.textSecondary} />
        </Pressable>
      </View>
      {(!lookup || lookup.status === "loading") && (
        <ActivityIndicator color={colors.accent} style={styles.panelSpinner} />
      )}
      {lookup?.status === "error" && (
        <Pressable onPress={onRetry} style={styles.panelRow}>
          <Ionicons name="refresh" size={18} color={colors.missed} />
          <Text style={styles.panelMuted}>Lookup failed. Tap to retry.</Text>
        </Pressable>
      )}
      {lookup?.status === "done" && !word && (
        <Text style={styles.panelMuted}>Function word or name</Text>
      )}
      {checking && (
        <View style={styles.panelRow}>
          <ActivityIndicator size="small" color={colors.textSecondary} />
          <Text style={styles.panelNote}>Checking the meaning here…</Text>
        </View>
      )}
      {replaced && (
        <Text style={styles.panelNote}>
          Not {replaced.lemma_ar} “{replaced.gloss_en || "—"}”
        </Text>
      )}
      {word && cardLemmaId != null && (
        <>
          <ScrollView style={styles.cardScroll} nestedScrollEnabled>
            {card ? (
              <DockWordPanel
                result={card}
                surfaceForm={surface}
                markState={causes.length ? "missed" : null}
                onNavigateToDetail={(id) => router.push(`/word/${id}`)}
                onNavigateToRoot={(id) => router.push(`/root/${id}`)}
              />
            ) : cardError ? (
              <WordRow word={word} name={isName} unchecked={false} addState="known" onAdd={() => {}} />
            ) : (
              <ActivityIndicator color={colors.accent} style={styles.panelSpinner} />
            )}
          </ScrollView>
          {!checking && (
            <AssistedRecognitionCauses
              compact
              otherWordSurface={null}
              rating={1}
              selected={causes}
              missingTashkeelApplicable
              onToggle={toggleCause}
            />
          )}
        </>
      )}
      {word && cardLemmaId == null && (
        <>
          <WordRow word={word} name={isName} unchecked={checking} addState={addState(word)} onAdd={() => onAdd(word!)} />
          {word.gloss_here ? (
            <Text style={styles.panelNote}>Here: {word.gloss_here}</Text>
          ) : null}
          {word.phrase ? (
            <Text style={styles.panelNote}>{word.phrase.ar} · {word.phrase.en}</Text>
          ) : null}
          {verdict?.verdict === "broader" && verdict.written && (
            <Text style={styles.panelNote}>Gloss widened (was: {verdict.gloss_from || "—"})</Text>
          )}
          {!isName && !checking && (
            <Pressable
              style={styles.enrichBtn}
              disabled={enriching}
              onPress={() => enrich(word!)}
              accessibilityLabel="Add and enrich this word now"
            >
              {enriching ? <ActivityIndicator size="small" color={colors.accent} />
                : <Ionicons name="sparkles-outline" size={18} color={colors.accent} />}
              <Text style={styles.enrichText}>{enriching ? "Enriching…" : "Enrich"}</Text>
            </Pressable>
          )}
        </>
      )}
    </View>
  );
}

function WordRow({
  word,
  name,
  unchecked,
  addState,
  onAdd,
}: {
  word: DiscoverWord;
  name: boolean;
  unchecked: boolean;
  addState: AddState;
  onAdd: () => void;
}) {
  const studied = word.knowledge_state && STUDIED_STATES.has(word.knowledge_state);
  // Names are not vocabulary; the backend refuses to add them.
  const isName =
    name || word.is_proper_noun || (word.pos || "").toLowerCase().startsWith("proper");
  return (
    <View style={[styles.panelRow, unchecked && styles.unchecked]}>
      <View style={styles.chipMain}>
        <Text style={styles.chipArabic}>{word.lemma_ar}</Text>
        <Text style={styles.chipGloss}>{word.gloss_en || "—"}</Text>
        <View style={styles.chipBadges}>
          {word.pos && <Badge text={word.pos} color={colors.textSecondary} />}
          {word.register && word.register !== "neutral" && (
            <Badge text={word.register} color={colors.confused} />
          )}
          {word.dialect && word.dialect !== "msa" && (
            <Badge text={word.dialect} color={colors.listening} />
          )}
          {studied && <Badge text={word.knowledge_state!} color={colors.gotIt} />}
          {isName && <Badge text="name" color={colors.stateEncountered} />}
        </View>
      </View>
      {!studied && !isName && !unchecked && <AddButton state={addState} onAdd={onAdd} />}
    </View>
  );
}

function stateFromKnowledge(state: string | null | undefined): AddState {
  if (state === "acquiring") return "added";
  if (state && STUDIED_STATES.has(state)) return "known";
  return "idle";
}

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <View style={[styles.badge, { borderColor: color + "55", backgroundColor: color + "1e" }]}>
      <Text style={[styles.badgeText, { color }]}>{text}</Text>
    </View>
  );
}

function WordChip({
  word,
  state,
  onAdd,
}: {
  word: DiscoverWord;
  state: AddState;
  onAdd: () => void;
}) {
  // Highlight non-neutral register/dialect — the signal that matters most for
  // authentic and classical texts (literary, colloquial, vulgar, archaic).
  const reg = word.register && word.register !== "neutral" ? word.register : null;
  const dia = word.dialect && word.dialect !== "msa" ? word.dialect : null;

  return (
    <View style={styles.chip}>
      <View style={styles.chipMain}>
        <Text style={styles.chipArabic}>{word.lemma_ar}</Text>
        <Text style={styles.chipGloss}>{word.gloss_en || "—"}</Text>
        {word.sense_from && (
          <Text style={styles.panelNote}>
            Not {word.sense_from.lemma_ar} “{word.sense_from.gloss_en || "—"}”
          </Text>
        )}
        <View style={styles.chipBadges}>
          {word.pos && <Badge text={word.pos} color={colors.textSecondary} />}
          {reg && <Badge text={reg} color={colors.confused} />}
          {dia && <Badge text={dia} color={colors.listening} />}
          {word.is_proper_noun && <Badge text="name" color={colors.stateEncountered} />}
        </View>
      </View>
      <AddButton state={state} onAdd={onAdd} />
    </View>
  );
}

function AddButton({ state, onAdd }: { state: AddState; onAdd: () => void }) {
  if (state === "adding") {
    return (
      <View style={styles.addBtn}>
        <ActivityIndicator size="small" color={colors.accent} />
      </View>
    );
  }
  if (state === "added") {
    return (
      <View style={[styles.addBtn, styles.addedBtn]}>
        <Ionicons name="checkmark" size={20} color={colors.gotIt} />
      </View>
    );
  }
  if (state === "known") {
    return (
      <View style={[styles.addBtn, styles.knownBtn]}>
        <Text style={styles.knownText}>Known</Text>
      </View>
    );
  }
  return (
    <Pressable style={styles.addBtn} onPress={onAdd} hitSlop={8}>
      <Ionicons
        name={state === "error" ? "refresh" : "add-circle-outline"}
        size={26}
        color={state === "error" ? colors.missed : colors.accent}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, paddingBottom: 48 },
  intro: { alignItems: "center", gap: 14, paddingVertical: 48 },
  introTitle: { color: colors.text, fontSize: 22, fontWeight: "700" },
  introBody: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    textAlign: "center",
    lineHeight: 22,
    paddingHorizontal: 12,
  },
  primaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.accent,
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 12,
    marginTop: 8,
  },
  primaryBtnText: { color: "#fff", fontSize: fonts.body, fontWeight: "600" },
  secondaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    marginTop: 16,
    alignSelf: "center",
  },
  secondaryBtnText: { color: colors.accent, fontSize: fonts.body, fontWeight: "600" },
  error: { color: colors.missed, fontSize: fonts.small, textAlign: "center", marginTop: 8 },

  sectionLabel: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 8,
  },
  translationCard: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: colors.border,
  },
  translationText: { color: colors.text, fontSize: 17, lineHeight: 26 },

  contentWithPanel: { paddingBottom: 220 },
  wordsHeaderFirst: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  pair: { marginBottom: 14 },
  history: { alignSelf: "stretch", marginTop: 32 },
  historyRow: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 12,
    marginBottom: 8,
  },
  historyWhen: { color: colors.textSecondary, fontSize: fonts.caption },
  historyPreview: { color: colors.text, fontSize: fonts.body, marginTop: 2, lineHeight: 21 },
  paraToggle: { flexDirection: "row", alignItems: "center", gap: 4, alignSelf: "flex-end", marginBottom: 4 },
  paraToggleText: { color: colors.textSecondary, fontFamily: fontFamily.arabic, fontSize: fonts.small },
  paraEnglish: { color: colors.text, fontSize: fonts.body, lineHeight: 24 },
  pairEnglish: { color: colors.textSecondary, fontSize: fonts.body, lineHeight: 22, marginTop: 4 },
  arabicLabel: { marginTop: 24 },
  arabicParagraph: { marginTop: 12 },
  tokenSelected: { backgroundColor: colors.accent + "33", color: colors.text },
  // The rest of the tapped word's translation phrase: marked, not selected.
  tokenLinked: { textDecorationLine: "underline", textDecorationColor: colors.accent + "88" },
  cardScroll: { maxHeight: 300 },
  enrichBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    height: 44, borderRadius: 12, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surfaceLight, marginTop: 10,
  },
  enrichText: { color: colors.accent, fontSize: 15, fontWeight: "600" },
  panel: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderColor: colors.border,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 28,
  },
  panelHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  panelSurface: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: fonts.arabicList,
  },
  panelSpinner: { marginVertical: 16 },
  panelRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  panelMuted: { color: colors.textSecondary, fontSize: fonts.body, marginVertical: 8 },
  panelNote: { color: colors.textSecondary, fontSize: fonts.small, marginTop: 6 },
  unchecked: { opacity: 0.55 },
  arabicCard: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    padding: 16,
  },
  arabicText: {
    ...arabicText(fonts.arabicMedium, 44),
    color: colors.arabic,
    writingDirection: "rtl",
    textAlign: "right",
  },

  wordsPending: { flexDirection: "row", alignItems: "center", gap: 8 },
  wordsHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 28,
  },
  addAllText: { color: colors.accent, fontSize: fonts.small, fontWeight: "600" },

  chip: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginBottom: 10,
  },
  chipMain: { flex: 1 },
  chipArabic: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: fonts.arabicList,
    writingDirection: "rtl",
    textAlign: "left",
  },
  chipGloss: { color: colors.text, fontSize: fonts.body, marginTop: 2 },
  chipBadges: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  badge: {
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  badgeText: { fontSize: 11, fontWeight: "600" },

  addBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: "center",
    justifyContent: "center",
    marginLeft: 10,
  },
  addedBtn: { backgroundColor: colors.gotIt + "22" },
  knownBtn: {
    backgroundColor: colors.stateEncountered + "22",
    width: "auto",
    paddingHorizontal: 12,
  },
  knownText: { color: colors.stateEncountered, fontSize: fonts.caption, fontWeight: "600" },
});
