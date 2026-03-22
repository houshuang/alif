import { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  RefreshControl,
} from "react-native";
import { Audio } from "expo-av";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily } from "../lib/theme";
import { BASE_URL } from "../lib/api";

interface KeyWord {
  arabic: string;
  gloss: string;
  stability_days?: number;
}

interface PodcastSentence {
  arabic: string;
  english: string;
}

interface Podcast {
  filename: string;
  size_mb: number;
  duration_seconds: number;
  created_at: string;
  title_en: string;
  title_ar: string;
  summary: string;
  key_words: KeyWord[];
  sentence_count: number;
  listened_at: string | null;
  listen_progress: number;
  format_type: string;
}

interface PodcastDetail extends Podcast {
  sentences: PodcastSentence[];
}

export default function PodcastScreen() {
  const [podcasts, setPodcasts] = useState<Podcast[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<PodcastDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [status, setStatus] = useState<{
    position: number;
    duration: number;
    isPlaying: boolean;
  } | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);

  const fetchPodcasts = useCallback(async () => {
    try {
      const res = await fetch(`${BASE_URL}/api/podcasts`);
      const data = await res.json();
      setPodcasts((data.podcasts || []).reverse());
    } catch {
      setPodcasts([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchPodcasts();
    return () => {
      soundRef.current?.unloadAsync();
    };
  }, [fetchPodcasts]);

  const fetchDetail = async (filename: string) => {
    setLoadingDetail(true);
    try {
      const res = await fetch(`${BASE_URL}/api/podcasts/detail/${filename}`);
      if (res.ok) {
        setDetail(await res.json());
      }
    } catch { /* ignore */ }
    setLoadingDetail(false);
  };

  const toggleExpand = (filename: string) => {
    if (expanded === filename) {
      setExpanded(null);
      setDetail(null);
    } else {
      setExpanded(filename);
      fetchDetail(filename);
    }
  };

  const reportProgress = async (filename: string, progress: number, completed: boolean) => {
    try {
      await fetch(`${BASE_URL}/api/podcasts/progress/${filename}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ progress, completed }),
      });
    } catch { /* ignore */ }
  };

  const playPodcast = async (filename: string) => {
    if (soundRef.current) {
      await soundRef.current.unloadAsync();
      soundRef.current = null;
    }

    if (playing === filename) {
      setPlaying(null);
      setStatus(null);
      return;
    }

    await Audio.setAudioModeAsync({
      playsInSilentModeIOS: true,
      staysActiveInBackground: true,
    });

    const { sound } = await Audio.Sound.createAsync(
      { uri: `${BASE_URL}/api/podcasts/audio/${filename}` },
      { shouldPlay: true },
      (ps) => {
        if (ps.isLoaded) {
          setStatus({
            position: ps.positionMillis,
            duration: ps.durationMillis || 0,
            isPlaying: ps.isPlaying,
          });
          if (ps.didJustFinish) {
            reportProgress(filename, 1.0, true);
            setPlaying(null);
            setStatus(null);
            fetchPodcasts();
          }
        }
      }
    );

    soundRef.current = sound;
    setPlaying(filename);
  };

  const togglePlayPause = async () => {
    if (!soundRef.current) return;
    if (status?.isPlaying) {
      await soundRef.current.pauseAsync();
      if (playing && status) {
        const prog = status.duration > 0 ? status.position / status.duration : 0;
        reportProgress(playing, prog, false);
      }
    } else {
      await soundRef.current.playAsync();
    }
  };

  const seekRelative = async (deltaMs: number) => {
    if (!soundRef.current || !status) return;
    const newPos = Math.max(0, Math.min(status.position + deltaMs, status.duration));
    await soundRef.current.setPositionAsync(newPos);
  };

  const fmt = (ms: number) => {
    const s = Math.floor(ms / 1000);
    return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
  };

  const fmtDuration = (sec: number) => {
    if (sec < 60) return `${sec}s`;
    return `${Math.floor(sec / 60)} min`;
  };

  const fmtDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };

  const displayTitle = (p: Podcast) =>
    p.title_en || p.filename.replace(".mp3", "").replace(/^story-/, "").replace(/-\d{8}-\d{4}$/, "").replace(/-/g, " ");

  const progress = status && status.duration > 0 ? status.position / status.duration : 0;

  return (
    <View style={s.container}>
      <View style={s.header}>
        <Text style={s.title}>Podcast</Text>
        <Text style={s.subtitle}>
          {podcasts.length} episode{podcasts.length !== 1 ? "s" : ""}
        </Text>
      </View>

      {/* Player */}
      {playing && status && (
        <View style={s.player}>
          <View style={s.progressRow}>
            <Text style={s.time}>{fmt(status.position)}</Text>
            <View style={s.bar}><View style={[s.barFill, { width: `${progress * 100}%` }]} /></View>
            <Text style={s.time}>{fmt(status.duration)}</Text>
          </View>
          <View style={s.controls}>
            <Pressable onPress={() => seekRelative(-15000)} style={s.seekBtn}>
              <Ionicons name="play-back" size={22} color={colors.text} />
              <Text style={s.seekLabel}>15s</Text>
            </Pressable>
            <Pressable onPress={togglePlayPause} style={s.playBtn}>
              <Ionicons name={status.isPlaying ? "pause" : "play"} size={32} color={colors.bg} />
            </Pressable>
            <Pressable onPress={() => seekRelative(30000)} style={s.seekBtn}>
              <Ionicons name="play-forward" size={22} color={colors.text} />
              <Text style={s.seekLabel}>30s</Text>
            </Pressable>
          </View>
        </View>
      )}

      {/* List */}
      <ScrollView
        style={s.list}
        contentContainerStyle={{ paddingBottom: 40 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); fetchPodcasts(); }}
            tintColor={colors.accent}
          />
        }
      >
        {loading ? (
          <ActivityIndicator size="large" color={colors.accent} style={{ marginTop: 40 }} />
        ) : podcasts.length === 0 ? (
          <View style={s.empty}>
            <Ionicons name="mic-outline" size={48} color={colors.textSecondary} />
            <Text style={s.emptyText}>No episodes yet</Text>
          </View>
        ) : (
          podcasts.map((p) => {
            const isPlaying = playing === p.filename;
            const isExpanded = expanded === p.filename;
            const listened = !!p.listened_at;

            return (
              <View key={p.filename} style={[s.card, isPlaying && s.cardActive]}>
                {/* Main row */}
                <Pressable style={s.cardRow} onPress={() => toggleExpand(p.filename)}>
                  <Pressable
                    style={[s.playIcon, listened && s.playIconListened]}
                    onPress={(e) => { e.stopPropagation(); playPodcast(p.filename); }}
                  >
                    <Ionicons
                      name={isPlaying && status?.isPlaying ? "pause" : "play"}
                      size={22}
                      color={isPlaying ? colors.accent : listened ? colors.gotIt : colors.text}
                    />
                  </Pressable>

                  <View style={s.cardInfo}>
                    {p.title_ar ? (
                      <Text style={s.cardTitleAr} numberOfLines={1}>{p.title_ar}</Text>
                    ) : null}
                    <Text style={s.cardTitle} numberOfLines={1}>{displayTitle(p)}</Text>
                    <View style={s.cardMeta}>
                      <Text style={s.metaText}>{fmtDuration(p.duration_seconds)}</Text>
                      <Text style={s.metaDot}>·</Text>
                      <Text style={s.metaText}>{p.sentence_count} sentences</Text>
                      <Text style={s.metaDot}>·</Text>
                      <Text style={s.metaText}>{fmtDate(p.created_at)}</Text>
                      {listened && (
                        <>
                          <Text style={s.metaDot}>·</Text>
                          <Ionicons name="checkmark-circle" size={14} color={colors.gotIt} />
                        </>
                      )}
                    </View>
                  </View>

                  <Ionicons
                    name={isExpanded ? "chevron-up" : "chevron-down"}
                    size={18}
                    color={colors.textSecondary}
                  />
                </Pressable>

                {/* Listen progress bar */}
                {p.listen_progress > 0 && p.listen_progress < 1 && (
                  <View style={s.listenBar}>
                    <View style={[s.listenFill, { width: `${p.listen_progress * 100}%` }]} />
                  </View>
                )}

                {/* Expanded detail */}
                {isExpanded && (
                  <View style={s.detail}>
                    {loadingDetail ? (
                      <ActivityIndicator size="small" color={colors.accent} style={{ marginVertical: 12 }} />
                    ) : (
                      <>
                        {/* Summary */}
                        {(detail?.summary || p.summary) ? (
                          <Text style={s.summary}>{detail?.summary || p.summary}</Text>
                        ) : null}

                        {/* Key words */}
                        {(detail?.key_words?.length || p.key_words?.length) ? (
                          <View style={s.wordsSection}>
                            <Text style={s.sectionLabel}>Key vocabulary</Text>
                            <View style={s.wordChips}>
                              {(detail?.key_words || p.key_words || []).slice(0, 10).map((w, i) => (
                                <View key={i} style={s.chip}>
                                  <Text style={s.chipAr}>{w.arabic}</Text>
                                  <Text style={s.chipEn}>{w.gloss}</Text>
                                </View>
                              ))}
                            </View>
                          </View>
                        ) : null}

                        {/* Sentences */}
                        {detail?.sentences?.length ? (
                          <View style={s.sentencesSection}>
                            <Text style={s.sectionLabel}>Sentences</Text>
                            {detail.sentences.map((sent, i) => (
                              <View key={i} style={s.sentRow}>
                                <Text style={s.sentNum}>{i + 1}</Text>
                                <View style={s.sentText}>
                                  <Text style={s.sentAr}>{sent.arabic}</Text>
                                  <Text style={s.sentEn}>{sent.english}</Text>
                                </View>
                              </View>
                            ))}
                          </View>
                        ) : null}
                      </>
                    )}
                  </View>
                )}
              </View>
            );
          })
        )}
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  header: {
    paddingTop: 60,
    paddingHorizontal: 20,
    paddingBottom: 12,
  },
  title: { fontSize: 28, fontWeight: "700", color: colors.text },
  subtitle: { fontSize: 13, color: colors.textSecondary, marginTop: 2 },

  // Player
  player: {
    backgroundColor: colors.surface,
    marginHorizontal: 16,
    borderRadius: 14,
    padding: 16,
    marginBottom: 8,
  },
  progressRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 },
  bar: { flex: 1, height: 4, backgroundColor: colors.surfaceLight, borderRadius: 2, overflow: "hidden" },
  barFill: { height: "100%", backgroundColor: colors.accent, borderRadius: 2 },
  time: { fontSize: 11, color: colors.textSecondary, fontVariant: ["tabular-nums"], width: 36, textAlign: "center" },
  controls: { flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 28 },
  seekBtn: { alignItems: "center", gap: 1 },
  seekLabel: { fontSize: 9, color: colors.textSecondary },
  playBtn: {
    width: 56, height: 56, borderRadius: 28,
    backgroundColor: colors.accent,
    justifyContent: "center", alignItems: "center",
  },

  // List
  list: { flex: 1, paddingHorizontal: 16 },
  empty: { alignItems: "center", paddingTop: 60, gap: 12 },
  emptyText: { fontSize: 18, fontWeight: "600", color: colors.textSecondary },

  // Card
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    marginBottom: 10,
    overflow: "hidden",
  },
  cardActive: { borderColor: colors.accent, borderWidth: 1 },
  cardRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: 14,
    gap: 12,
  },
  playIcon: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: colors.surfaceLight,
    justifyContent: "center", alignItems: "center",
  },
  playIconListened: { backgroundColor: "rgba(46, 204, 113, 0.15)" },
  cardInfo: { flex: 1 },
  cardTitleAr: {
    fontSize: 18,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    marginBottom: 2,
  },
  cardTitle: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.text,
    textTransform: "capitalize",
  },
  cardMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: 3,
  },
  metaText: { fontSize: 12, color: colors.textSecondary },
  metaDot: { fontSize: 12, color: colors.textSecondary },

  // Listen progress
  listenBar: {
    height: 2,
    backgroundColor: colors.surfaceLight,
    marginHorizontal: 14,
    borderRadius: 1,
  },
  listenFill: {
    height: "100%",
    backgroundColor: colors.accent,
    borderRadius: 1,
  },

  // Detail
  detail: {
    paddingHorizontal: 14,
    paddingBottom: 16,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    marginTop: 4,
  },
  summary: {
    fontSize: 13,
    color: colors.textSecondary,
    lineHeight: 19,
    marginTop: 12,
    fontStyle: "italic",
  },
  wordsSection: { marginTop: 14 },
  sectionLabel: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.textSecondary,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  wordChips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: {
    backgroundColor: colors.surfaceLight,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 8,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  chipAr: {
    fontSize: 16,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
  },
  chipEn: { fontSize: 11, color: colors.textSecondary },

  // Sentences
  sentencesSection: { marginTop: 14 },
  sentRow: {
    flexDirection: "row",
    gap: 8,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  sentNum: {
    fontSize: 11,
    color: colors.textSecondary,
    width: 18,
    textAlign: "right",
    paddingTop: 4,
  },
  sentText: { flex: 1 },
  sentAr: {
    fontSize: 18,
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    textAlign: "right",
    lineHeight: 28,
  },
  sentEn: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 2,
  },
});
