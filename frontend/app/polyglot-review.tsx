/**
 * Polyglot review screen — bare-word card UX over the FSRS + acquisition engine.
 *
 * Transitional layout: today reviews are submitted by lemma_id only (no
 * sentence wrapper). When the sentence-review pipeline lands, this screen
 * upgrades to sentence cards. The button row + rating semantics stay.
 *
 * Talks to /api/reviews/{due,submit,stats} on the polyglot backend.
 */
import { useCallback, useEffect, useState } from "react";
import {
  View, Text, Pressable, StyleSheet, ActivityIndicator, ScrollView,
} from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useLanguage } from "../lib/language-context";
import {
  getDueLemmas, submitReview, getReviewStats,
  type DueLemma, type AcquisitionStats, type ReviewRating,
} from "../lib/polyglot-api";

const C = {
  bg: "#0f0f1a",
  surface: "#1a1a2e",
  border: "#2a2a40",
  text: "#e0e0f0",
  textDim: "#9090a8",
  accent: "#7aa2f7",
  again: "#c95f6f",
  hard: "#d4a06b",
  good: "#74c096",
  easy: "#7aa2f7",
};

const RATINGS: { value: ReviewRating; label: string; color: string; desc: string }[] = [
  { value: 1, label: "Again", color: C.again, desc: "Forgot — start over" },
  { value: 2, label: "Hard",  color: C.hard,  desc: "Recalled with effort" },
  { value: 3, label: "Good",  color: C.good,  desc: "Knew it" },
  { value: 4, label: "Easy",  color: C.easy,  desc: "Trivially" },
];

function clientReviewId(): string {
  // Cheap UUIDv4-ish, only needs to be unique within the offline queue.
  return "rv-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

export default function PolyglotReview() {
  const router = useRouter();
  const { language } = useLanguage();
  // The language switcher today tracks only 'ar' / 'el'. When polyglot adds
  // grc/la support to the switcher, extend this map and the underlying
  // AppLanguage union together.
  const languageCode = language === "el" ? "el" : "el";

  const [queue, setQueue] = useState<DueLemma[]>([]);
  const [stats, setStats] = useState<AcquisitionStats | null>(null);
  const [index, setIndex] = useState(0);
  const [showAnswer, setShowAnswer] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shownAt, setShownAt] = useState<number>(Date.now());

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [due, s] = await Promise.all([
        getDueLemmas(languageCode, 20),
        getReviewStats(),
      ]);
      setQueue(due);
      setStats(s);
      setIndex(0);
      setShowAnswer(false);
      setShownAt(Date.now());
    } catch (e: any) {
      setError(e?.message ?? "Failed to load review queue");
    } finally {
      setLoading(false);
    }
  }, [languageCode]);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  const current = queue[index];

  const rate = useCallback(async (rating: ReviewRating) => {
    if (!current || submitting) return;
    setSubmitting(true);
    try {
      const responseMs = Date.now() - shownAt;
      await submitReview(current.lemma_id, rating, {
        responseMs,
        clientReviewId: clientReviewId(),
      });
      // Advance. If we're at the end, refetch — there may be more due now
      // (or graduations / leech reactivations may have changed the queue).
      if (index + 1 >= queue.length) {
        await loadQueue();
      } else {
        setIndex(index + 1);
        setShowAnswer(false);
        setShownAt(Date.now());
      }
    } catch (e: any) {
      setError(e?.message ?? "Submit failed");
    } finally {
      setSubmitting(false);
    }
  }, [current, submitting, shownAt, queue.length, index, loadQueue]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={C.accent} />
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>{error}</Text>
        <Pressable style={styles.button} onPress={loadQueue}>
          <Text style={styles.buttonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  if (!current) {
    return (
      <View style={styles.center}>
        <Ionicons name="checkmark-circle-outline" size={64} color={C.good} />
        <Text style={styles.emptyTitle}>Nothing due right now</Text>
        <Text style={styles.emptySubtitle}>
          {stats
            ? `${stats.total_acquiring} word${stats.total_acquiring === 1 ? "" : "s"} in the acquisition pipeline`
            : ""}
        </Text>
        <View style={styles.statsRow}>
          {stats ? (
            <>
              <Stat label="Box 1" value={stats.box_1} />
              <Stat label="Box 2" value={stats.box_2} />
              <Stat label="Box 3" value={stats.box_3} />
            </>
          ) : null}
        </View>
        <Pressable style={styles.button} onPress={() => router.back()}>
          <Text style={styles.buttonText}>Back</Text>
        </Pressable>
        <Pressable style={[styles.button, styles.buttonGhost]} onPress={loadQueue}>
          <Text style={styles.buttonText}>Refresh</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.root}>
      <View style={styles.header}>
        <Text style={styles.progress}>
          {index + 1} / {queue.length}
        </Text>
        {current.acquisition_box != null && (
          <Text style={styles.boxBadge}>Box {current.acquisition_box}</Text>
        )}
        <Text style={styles.stateBadge}>{current.state}</Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.lemmaForm}>{current.lemma_form}</Text>
        <Text style={styles.lemmaBare}>{current.lemma_bare}</Text>
        {showAnswer ? (
          <View style={styles.answerArea}>
            <Text style={styles.gloss}>
              {current.gloss_en ?? "(no gloss yet)"}
            </Text>
          </View>
        ) : (
          <Pressable style={styles.revealButton} onPress={() => setShowAnswer(true)}>
            <Text style={styles.revealText}>Tap to reveal</Text>
          </Pressable>
        )}
      </View>

      {showAnswer && (
        <View style={styles.ratingsRow}>
          {RATINGS.map((r) => (
            <Pressable
              key={r.value}
              style={[styles.ratingButton, { borderColor: r.color }]}
              onPress={() => rate(r.value)}
              disabled={submitting}
            >
              <Text style={[styles.ratingLabel, { color: r.color }]}>{r.label}</Text>
              <Text style={styles.ratingDesc}>{r.desc}</Text>
            </Pressable>
          ))}
        </View>
      )}

      {submitting && (
        <ActivityIndicator color={C.accent} style={{ marginTop: 16 }} />
      )}
    </ScrollView>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: C.bg, flexGrow: 1, padding: 16, paddingTop: 24,
  },
  center: {
    flex: 1, alignItems: "center", justifyContent: "center",
    backgroundColor: C.bg, padding: 24,
  },
  header: {
    flexDirection: "row", alignItems: "center", marginBottom: 24, gap: 12,
  },
  progress: { color: C.textDim, fontSize: 14, flex: 1 },
  boxBadge: {
    color: C.accent, fontSize: 12, fontWeight: "600",
    backgroundColor: C.surface, paddingHorizontal: 8, paddingVertical: 4,
    borderRadius: 8, borderColor: C.border, borderWidth: 1,
  },
  stateBadge: {
    color: C.textDim, fontSize: 12,
    backgroundColor: C.surface, paddingHorizontal: 8, paddingVertical: 4,
    borderRadius: 8, borderColor: C.border, borderWidth: 1,
  },
  card: {
    backgroundColor: C.surface, padding: 32, borderRadius: 16,
    alignItems: "center", borderWidth: 1, borderColor: C.border,
  },
  lemmaForm: {
    color: C.text, fontSize: 36, fontWeight: "600", letterSpacing: 0.5,
  },
  lemmaBare: { color: C.textDim, fontSize: 14, marginTop: 4 },
  answerArea: { marginTop: 24, alignItems: "center" },
  gloss: { color: C.accent, fontSize: 22 },
  revealButton: {
    marginTop: 32, paddingHorizontal: 24, paddingVertical: 12,
    backgroundColor: C.bg, borderRadius: 12,
    borderWidth: 1, borderColor: C.border,
  },
  revealText: { color: C.textDim, fontSize: 16 },
  ratingsRow: {
    flexDirection: "row", marginTop: 24, gap: 8,
  },
  ratingButton: {
    flex: 1, padding: 12, borderRadius: 12, borderWidth: 1.5,
    alignItems: "center", backgroundColor: C.surface,
  },
  ratingLabel: { fontWeight: "600", fontSize: 14, marginBottom: 4 },
  ratingDesc: { color: C.textDim, fontSize: 10, textAlign: "center" },
  button: {
    backgroundColor: C.accent, paddingHorizontal: 24, paddingVertical: 12,
    borderRadius: 12, marginTop: 16,
  },
  buttonGhost: {
    backgroundColor: "transparent",
    borderWidth: 1, borderColor: C.border,
  },
  buttonText: { color: C.text, fontWeight: "600" },
  errorText: { color: C.again, marginBottom: 16 },
  emptyTitle: { color: C.text, fontSize: 20, marginTop: 16, fontWeight: "600" },
  emptySubtitle: { color: C.textDim, marginTop: 8 },
  statsRow: { flexDirection: "row", marginTop: 24, gap: 24 },
  stat: { alignItems: "center" },
  statValue: { color: C.text, fontSize: 24, fontWeight: "600" },
  statLabel: { color: C.textDim, fontSize: 12, marginTop: 4 },
});
