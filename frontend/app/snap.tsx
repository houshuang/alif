import { useState } from "react";
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
import { snapDiscover, addDiscoveredWord } from "../lib/api";
import type { DiscoverWord, SnapResult } from "../lib/types";

type AddState = "idle" | "adding" | "added" | "known" | "error";

// Snap-to-read: photograph an authentic Arabic page → faithful English translation
// + the top unknown words as add-to-Alif chips. One synchronous backend round trip
// (Gemini OCR/translation + Haiku gloss), so the reader gets help in the moment
// rather than batch-importing a book after the fact.
export default function SnapScreen() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SnapResult | null>(null);
  const [showArabic, setShowArabic] = useState(false);
  // lemma_ar_bare -> add state
  const [adds, setAdds] = useState<Record<string, AddState>>({});

  async function runSnap(imageUri: string) {
    setBusy(true);
    setError(null);
    setResult(null);
    setAdds({});
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
  }

  const addedCount = Object.values(adds).filter(
    (s) => s === "added" || s === "known"
  ).length;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
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

      {result && (
        <View>
          {/* English translation */}
          <Text style={styles.sectionLabel}>Translation</Text>
          <View style={styles.translationCard}>
            <Text style={styles.translationText} selectable>
              {result.translation_en || "(no translation returned)"}
            </Text>
          </View>

          {/* Collapsible original Arabic */}
          <Pressable
            style={styles.arabicToggle}
            onPress={() => setShowArabic((s) => !s)}
          >
            <Ionicons
              name={showArabic ? "chevron-down" : "chevron-forward"}
              size={16}
              color={colors.textSecondary}
            />
            <Text style={styles.arabicToggleText}>
              {showArabic ? "Hide" : "Show"} Arabic text
            </Text>
          </Pressable>
          {showArabic && (
            <View style={styles.arabicCard}>
              <Text style={styles.arabicText} selectable>
                {result.arabic_text}
              </Text>
            </View>
          )}

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

  arabicToggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 14,
    marginBottom: 4,
  },
  arabicToggleText: { color: colors.textSecondary, fontSize: fonts.small },
  arabicCard: {
    backgroundColor: colors.surfaceLight,
    borderRadius: 12,
    padding: 16,
    marginTop: 6,
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
