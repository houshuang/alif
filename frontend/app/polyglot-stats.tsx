/**
 * Polyglot stats — per-language overview of lemma states + story progress.
 * Minimal compared to Alif's stats; no FSRS retention curves yet because
 * polyglot doesn't have scheduling yet.
 */
import { useEffect, useState } from "react";
import { View, Text, ScrollView, StyleSheet, ActivityIndicator, Pressable } from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { getLanguageStats, type LanguageStats } from "../lib/polyglot-api";

const C = {
  bg: "#0f0f1a", surface: "#1a1a2e", border: "#2a2a40",
  text: "#e0e0f0", textDim: "#9090a8", accent: "#7aa2f7",
  known: "#3a8a52", acquiring: "#d4a06b", encountered: "#506a8e",
  unknown: "#c95f6f", ignored: "#3a3a3a", new: "#5a5a70",
};

export default function PolyglotStats() {
  const router = useRouter();
  const [stats, setStats] = useState<LanguageStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getLanguageStats("el").then(setStats).catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <View style={s.screen}>
        <BackHeader onBack={() => router.back()} />
        <Text style={s.error}>Failed to load stats: {error}</Text>
      </View>
    );
  }

  if (!stats) {
    return (
      <View style={s.screen}>
        <BackHeader onBack={() => router.back()} />
        <ActivityIndicator color={C.accent} style={{ marginTop: 40 }} />
      </View>
    );
  }

  const totalMarked =
    stats.by_state.known + stats.by_state.acquiring + stats.by_state.encountered +
    stats.by_state.unknown + stats.by_state.ignored;
  const knownPct = totalMarked > 0
    ? Math.round((stats.by_state.known / totalMarked) * 100)
    : 0;

  return (
    <View style={s.screen}>
      <BackHeader onBack={() => router.back()} />
      <ScrollView contentContainerStyle={s.body}>
        <Text style={s.h1}>Modern Greek</Text>
        <Text style={s.h2}>
          {totalMarked} marked · {knownPct}% known · {stats.total_lemmas} words in your texts
        </Text>

        <View style={s.card}>
          <Text style={s.cardLabel}>Knowledge breakdown</Text>
          <StateRow label="Known" value={stats.by_state.known} total={stats.total_lemmas} color={C.known} />
          <StateRow label="Acquiring" value={stats.by_state.acquiring} total={stats.total_lemmas} color={C.acquiring} />
          <StateRow label="Encountered" value={stats.by_state.encountered} total={stats.total_lemmas} color={C.encountered} />
          <StateRow label="Marked unknown" value={stats.by_state.unknown} total={stats.total_lemmas} color={C.unknown} />
          <StateRow label="Ignored" value={stats.by_state.ignored} total={stats.total_lemmas} color={C.ignored} />
          <StateRow label="Unseen" value={stats.new} total={stats.total_lemmas} color={C.new} />
        </View>

        <View style={s.card}>
          <Text style={s.cardLabel}>Texts</Text>
          {stats.stories.length === 0 ? (
            <Text style={s.dim}>No texts yet.</Text>
          ) : (
            stats.stories.map((st) => (
              <View key={st.id} style={s.storyLine}>
                <Text style={s.storyTitle} numberOfLines={1}>{st.title || `#${st.id}`}</Text>
                <Text style={s.storyMeta}>
                  {st.processed_pages}/{st.page_count ?? "?"} pages processed
                </Text>
              </View>
            ))
          )}
        </View>

        <Text style={s.footer}>
          Lemma counts are populated as you read — pages tokenize lazily on first view.
        </Text>
      </ScrollView>
    </View>
  );
}

function BackHeader({ onBack }: { onBack: () => void }) {
  return (
    <View style={s.header}>
      <Pressable onPress={onBack} style={s.backBtn}>
        <Ionicons name="chevron-back" size={22} color={C.accent} />
        <Text style={s.backText}>Back</Text>
      </Pressable>
    </View>
  );
}

function StateRow({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <View style={s.stateRow}>
      <View style={s.stateRowHeader}>
        <Text style={s.stateLabel}>{label}</Text>
        <Text style={s.stateValue}>{value}</Text>
      </View>
      <View style={s.barTrack}>
        <View style={[s.barFill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg, paddingTop: 40 },
  header: { flexDirection: "row", paddingHorizontal: 16, marginBottom: 8 },
  backBtn: { flexDirection: "row", alignItems: "center" },
  backText: { color: C.accent, fontSize: 16 },

  body: { paddingHorizontal: 16, paddingBottom: 60 },
  h1: { fontSize: 26, fontWeight: "700", color: C.text, marginTop: 8 },
  h2: { fontSize: 14, color: C.textDim, marginTop: 4, marginBottom: 20 },

  card: { backgroundColor: C.surface, borderRadius: 10, padding: 14, marginBottom: 14,
          borderWidth: 1, borderColor: C.border },
  cardLabel: { color: C.textDim, fontSize: 12, textTransform: "uppercase", marginBottom: 8 },

  stateRow: { marginBottom: 10 },
  stateRowHeader: { flexDirection: "row", justifyContent: "space-between", marginBottom: 4 },
  stateLabel: { color: C.text, fontSize: 14 },
  stateValue: { color: C.textDim, fontSize: 14 },
  barTrack: { height: 6, backgroundColor: C.bg, borderRadius: 3, overflow: "hidden" },
  barFill: { height: 6, borderRadius: 3 },

  storyLine: { paddingVertical: 6 },
  storyTitle: { color: C.text, fontSize: 15 },
  storyMeta: { color: C.textDim, fontSize: 12, marginTop: 2 },

  dim: { color: C.textDim, fontStyle: "italic" },
  error: { color: "#c95f6f", padding: 20 },
  footer: { color: C.textDim, fontSize: 11, marginTop: 8, fontStyle: "italic" },
});
