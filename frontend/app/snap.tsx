import { useMemo, useState } from "react";
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

import { colors, fontFamily, fonts } from "../lib/theme";
import { snapDiscover, addDiscoveredWord, lookupSnapWord } from "../lib/api";
import type { DiscoverWord, SnapResult } from "../lib/types";
import {
  alignSentences,
  firstArabicToken,
  linkForArabic,
  linkForEnglish,
  paragraphsOf,
  type AlignedSentence,
  type SnapToken,
} from "../lib/snap-alignment";

type AddState = "idle" | "adding" | "added" | "known" | "error";

type Token = { text: string; key: string; sentence: string; tappable: boolean };
type Focus = { sentence: number; link: number | null; en?: number };
type Lookup = { status: "loading" } | { status: "done"; word: DiscoverWord | null } | { status: "error" };

const ARABIC_LETTER = /[\u0621-\u064A]/;
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SnapResult | null>(null);
  // lemma_ar_bare -> add state
  const [adds, setAdds] = useState<Record<string, AddState>>({});
  const [selected, setSelected] = useState<Token | null>(null);
  // token text -> lookup result, so re-tapping a word is instant
  const [lookups, setLookups] = useState<Record<string, Lookup>>({});
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
  const [showEnglish, setShowEnglish] = useState(true);

  function selectToken(t: Token) {
    if (selected?.key === t.key) {
      setSelected(null);
      return;
    }
    openToken(t);
  }

  function openToken(t: Token) {
    setSelected(t);
    const cached = lookups[t.text];
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

  function tapEnglish(s: AlignedSentence, i: number) {
    const link = linkForEnglish(s, i);
    setFocus({ sentence: s.index, link, en: i });
    const source = link === null ? null : firstArabicToken(s, link);
    if (source) openToken(asToken(s, source));
    else setSelected(null);
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

  async function fetchLookup(t: Token) {
    setLookups((p) => ({ ...p, [t.text]: { status: "loading" } }));
    try {
      const word = await lookupSnapWord(t.text, t.sentence);
      setLookups((p) => ({ ...p, [t.text]: { status: "done", word } }));
    } catch {
      setLookups((p) => ({ ...p, [t.text]: { status: "error" } }));
    }
  }

  async function runSnap(imageUri: string) {
    setBusy(true);
    setError(null);
    setResult(null);
    setAdds({});
    setSelected(null);
    setLookups({});
    setFocus(null);
    try {
      const r = await snapDiscover(imageUri);
      setResult(r);
    } catch (e: any) {
      const msg = String(e?.message || e);
      setError(
        msg.includes("422")
          ? "No Arabic text found in that photo — try again with the page filling the frame."
          : "Couldn't read that page. Check your connection and try again."
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

  async function addWord(w: DiscoverWord) {
    const key = w.lemma_ar_bare;
    if (adds[key] === "adding" || adds[key] === "added" || adds[key] === "known") return;
    setAdds((p) => ({ ...p, [key]: "adding" }));
    try {
      const r = await addDiscoveredWord(w);
      setAdds((p) => ({ ...p, [key]: r.already_known ? "known" : "added" }));
    } catch {
      setAdds((p) => ({ ...p, [key]: "error" }));
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

  const selectedLookup = selected ? lookups[selected.text] : undefined;

  return (
    <View style={styles.screen}>
    <ScrollView
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
          {error && <Text style={styles.error}>{error}</Text>}
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
            <Pressable onPress={() => setShowEnglish((v) => !v)} hitSlop={8}>
              <Text style={styles.addAllText}>{showEnglish ? "Hide English" : "Show English"}</Text>
            </Pressable>
          </View>
          <View style={styles.arabicCard}>
            {aligned.map((paragraph, pi) => (
              <View key={pi} style={pi > 0 ? styles.arabicParagraph : undefined}>
                {paragraph.map((s) => (
                  <View key={s.index} style={styles.pair}>
                    <Text style={styles.arabicText}>
                      {s.arTokens.map((t) =>
                        t.isWord ? (
                          <Text
                            key={t.key}
                            onPress={() => tapArabic(s, t)}
                            style={arHighlighted(s, t) ? styles.tokenSelected : undefined}
                            accessibilityRole="button"
                          >
                            {t.text}
                          </Text>
                        ) : (
                          t.text
                        )
                      )}
                    </Text>
                    {showEnglish && (
                      <Text style={styles.pairEnglish}>
                        {s.enTokens.map((t, i) =>
                          t.isWord ? (
                            <Text
                              key={t.key}
                              onPress={() => tapEnglish(s, i)}
                              style={enHighlighted(s, i) ? styles.tokenSelected : undefined}
                            >
                              {t.text}
                            </Text>
                          ) : (
                            t.text
                          )
                        )}
                      </Text>
                    )}
                  </View>
                ))}
              </View>
            ))}
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
              Top words ({result.words.length})
            </Text>
            {result.words.length > 0 && (
              <Pressable onPress={addAll} hitSlop={8}>
                <Text style={styles.addAllText}>
                  {addedCount >= result.words.length ? "All added" : "Add all"}
                </Text>
              </Pressable>
            )}
          </View>

          {result.words.length === 0 && (
            <Text style={styles.introBody}>
              No new words here — looks like you already know this page's vocabulary.
            </Text>
          )}

          {result.words.map((w) => (
            <WordChip
              key={w.lemma_ar_bare}
              word={w}
              state={adds[w.lemma_ar_bare] || "idle"}
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
        surface={selected.text}
        lookup={selectedLookup}
        addState={(w) => adds[w.lemma_ar_bare] || "idle"}
        onAdd={addWord}
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
  lookup,
  addState,
  onAdd,
  onRetry,
  onClose,
}: {
  surface: string;
  lookup: Lookup | undefined;
  addState: (w: DiscoverWord) => AddState;
  onAdd: (w: DiscoverWord) => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  const word = lookup?.status === "done" ? lookup.word : null;
  const studied = word?.knowledge_state && STUDIED_STATES.has(word.knowledge_state);
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
      {word && (
        <View style={styles.panelRow}>
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
            </View>
          </View>
          {!studied && <AddButton state={addState(word)} onAdd={() => onAdd(word)} />}
        </View>
      )}
    </View>
  );
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
  pairEnglish: { color: colors.textSecondary, fontSize: fonts.body, lineHeight: 22, marginTop: 4 },
  arabicLabel: { marginTop: 24 },
  arabicParagraph: { marginTop: 12 },
  tokenSelected: { backgroundColor: colors.accent + "33", color: colors.text },
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
  arabicCard: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    padding: 16,
  },
  arabicText: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: fonts.arabicMedium,
    lineHeight: 44,
    writingDirection: "rtl",
    textAlign: "right",
  },

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
