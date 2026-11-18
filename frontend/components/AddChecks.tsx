import { useEffect, useState } from "react";
import { View, Text, StyleSheet, Pressable, ActivityIndicator } from "react-native";

import { colors, fontFamily, fonts } from "../lib/theme";
import { getAddChecks, resolveAddCheck } from "../lib/api";
import type { AddCheck } from "../lib/types";

// Added words whose saved sentence turned out to use a different word than the
// entry that was enrolled. Switch enrolls the word the sentence uses; Keep
// leaves things as they are.
export default function AddChecks() {
  const [items, setItems] = useState<AddCheck[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [failed, setFailed] = useState<number | null>(null);

  useEffect(() => {
    getAddChecks()
      .then(setItems)
      .catch(() => {
        // Offline: nothing to show.
      });
  }, []);

  async function resolve(item: AddCheck, action: "swap" | "keep") {
    setBusy(item.id);
    setFailed(null);
    try {
      await resolveAddCheck(item.id, action);
      setItems((prev) => prev.filter((i) => i.id !== item.id));
    } catch {
      setFailed(item.id);
    } finally {
      setBusy(null);
    }
  }

  if (items.length === 0) return null;
  return (
    <View style={styles.section}>
      <Text style={styles.label}>Check added words · {items.length}</Text>
      {items.map((item) => (
        <View key={item.id} style={styles.card}>
          <Text style={styles.sentence}>{highlight(item.sentence_ar, item.surface)}</Text>
          <View style={styles.row}>
            <Text style={styles.tag}>Added</Text>
            <Text style={[styles.arabic, styles.dim]}>{item.added.lemma_ar}</Text>
            <Text style={[styles.gloss, styles.dim]} numberOfLines={2}>
              {item.added.gloss_en || "—"}
            </Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.tag}>Here</Text>
            <Text style={styles.arabic}>{item.proposed.lemma_ar}</Text>
            <Text style={styles.gloss} numberOfLines={2}>
              {item.proposed.gloss_en || "—"}
            </Text>
          </View>
          {busy === item.id ? (
            <ActivityIndicator color={colors.accent} style={styles.spinner} />
          ) : (
            <View style={styles.actions}>
              <Pressable style={styles.primary} onPress={() => resolve(item, "swap")}>
                <Text style={styles.primaryText}>Switch</Text>
              </Pressable>
              <Pressable style={styles.secondary} onPress={() => resolve(item, "keep")}>
                <Text style={styles.secondaryText}>Keep</Text>
              </Pressable>
            </View>
          )}
          {failed === item.id && <Text style={styles.error}>Couldn't save. Try again.</Text>}
        </View>
      ))}
    </View>
  );
}

function highlight(sentence: string, surface: string) {
  const at = surface ? sentence.indexOf(surface) : -1;
  if (at < 0) return sentence;
  return (
    <>
      {sentence.slice(0, at)}
      <Text style={styles.mark}>{surface}</Text>
      {sentence.slice(at + surface.length)}
    </>
  );
}

const styles = StyleSheet.create({
  section: { alignSelf: "stretch", marginTop: 32 },
  label: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 8,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 12,
    marginBottom: 8,
  },
  sentence: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: fonts.arabicList,
    writingDirection: "rtl",
    textAlign: "right",
    marginBottom: 8,
  },
  mark: { color: colors.accent },
  row: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 4 },
  tag: { color: colors.textSecondary, fontSize: fonts.caption, width: 44 },
  arabic: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: fonts.arabicList,
    writingDirection: "rtl",
  },
  gloss: { color: colors.text, fontSize: fonts.body, flex: 1 },
  dim: { opacity: 0.55 },
  actions: { flexDirection: "row", gap: 10, marginTop: 12 },
  primary: {
    backgroundColor: colors.accent,
    borderRadius: 10,
    paddingVertical: 8,
    paddingHorizontal: 18,
  },
  primaryText: { color: "#fff", fontSize: fonts.body, fontWeight: "600" },
  secondary: {
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
    paddingVertical: 8,
    paddingHorizontal: 18,
  },
  secondaryText: { color: colors.accent, fontSize: fonts.body, fontWeight: "600" },
  spinner: { marginTop: 12, alignSelf: "flex-start" },
  error: { color: colors.missed, fontSize: fonts.small, marginTop: 6 },
});
