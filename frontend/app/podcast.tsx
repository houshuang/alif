import { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  RefreshControl,
  Image,
  Dimensions,
} from "react-native";
import { Audio } from "expo-av";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily } from "../lib/theme";
import { BASE_URL } from "../lib/api";

const SCREEN_WIDTH = Dimensions.get("window").width;
const CARD_GAP = 12;
const CARD_WIDTH = (SCREEN_WIDTH - 16 * 2 - CARD_GAP) / 2;

interface KeyWord { arabic: string; gloss: string; }
interface PodSentence { arabic: string; english: string; }
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
  image_url: string | null;
}
interface PodDetail extends Podcast { sentences: PodSentence[]; }

export default function PodcastScreen() {
  const [podcasts, setPodcasts] = useState<Podcast[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<PodDetail | null>(null);
  const [status, setStatus] = useState<{ position: number; duration: number; isPlaying: boolean } | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);

  const fetchPodcasts = useCallback(async () => {
    try {
      const res = await fetch(`${BASE_URL}/api/podcasts`);
      const data = await res.json();
      setPodcasts((data.podcasts || []).reverse());
    } catch { setPodcasts([]); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);

  useEffect(() => { fetchPodcasts(); return () => { soundRef.current?.unloadAsync(); }; }, [fetchPodcasts]);

  const fetchDetail = async (fn: string) => {
    try {
      const res = await fetch(`${BASE_URL}/api/podcasts/detail/${fn}`);
      if (res.ok) setDetail(await res.json());
    } catch {}
  };

  const reportProgress = async (fn: string, prog: number, done: boolean) => {
    try {
      await fetch(`${BASE_URL}/api/podcasts/progress/${fn}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ progress: prog, completed: done }),
      });
    } catch {}
  };

  const playPodcast = async (fn: string) => {
    if (soundRef.current) { await soundRef.current.unloadAsync(); soundRef.current = null; }
    if (playing === fn) { setPlaying(null); setStatus(null); return; }
    await Audio.setAudioModeAsync({ playsInSilentModeIOS: true, staysActiveInBackground: true });
    const { sound } = await Audio.Sound.createAsync(
      { uri: `${BASE_URL}/api/podcasts/audio/${fn}` },
      { shouldPlay: true },
      (ps) => {
        if (ps.isLoaded) {
          setStatus({ position: ps.positionMillis, duration: ps.durationMillis || 0, isPlaying: ps.isPlaying });
          if (ps.didJustFinish) { reportProgress(fn, 1.0, true); setPlaying(null); setStatus(null); fetchPodcasts(); }
        }
      }
    );
    soundRef.current = sound;
    setPlaying(fn);
  };

  const togglePlayPause = async () => {
    if (!soundRef.current) return;
    if (status?.isPlaying) {
      await soundRef.current.pauseAsync();
      if (playing && status) reportProgress(playing, status.duration > 0 ? status.position / status.duration : 0, false);
    } else { await soundRef.current.playAsync(); }
  };

  const seekRelative = async (d: number) => {
    if (!soundRef.current || !status) return;
    await soundRef.current.setPositionAsync(Math.max(0, Math.min(status.position + d, status.duration)));
  };

  const fmt = (ms: number) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`; };
  const fmtDur = (sec: number) => sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)} min`;
  const title = (p: Podcast) => p.title_en || p.filename.replace(".mp3", "").replace(/^story-/, "").replace(/-\d{8}-\d{4}$/, "").replace(/-/g, " ");
  const progress = status && status.duration > 0 ? status.position / status.duration : 0;

  // Detail view
  if (selected) {
    const p = podcasts.find(x => x.filename === selected);
    if (!p) { setSelected(null); return null; }
    const isPlaying = playing === p.filename;
    return (
      <View style={st.container}>
        <ScrollView contentContainerStyle={{ paddingBottom: 120 }}>
          {/* Hero image */}
          {p.image_url ? (
            <Image source={{ uri: `${BASE_URL}${p.image_url}` }} style={st.heroImage} />
          ) : (
            <View style={[st.heroImage, { backgroundColor: colors.surfaceLight }]} />
          )}

          {/* Back button */}
          <Pressable style={st.backBtn} onPress={() => { setSelected(null); setDetail(null); }}>
            <Ionicons name="arrow-back" size={22} color="#fff" />
          </Pressable>

          <View style={st.detailContent}>
            {p.title_ar ? <Text style={st.detailTitleAr}>{p.title_ar}</Text> : null}
            <Text style={st.detailTitle}>{title(p)}</Text>
            <Text style={st.detailMeta}>
              {fmtDur(p.duration_seconds)} · {p.sentence_count} sentences
              {p.listened_at ? " · Listened" : ""}
            </Text>

            {/* Play button */}
            <Pressable
              style={st.detailPlayBtn}
              onPress={() => playPodcast(p.filename)}
            >
              <Ionicons name={isPlaying ? "pause" : "play"} size={20} color="#fff" />
              <Text style={st.detailPlayText}>{isPlaying ? "Pause" : "Play Episode"}</Text>
            </Pressable>

            {/* Summary */}
            {(detail?.summary || p.summary) ? (
              <Text style={st.detailSummary}>{detail?.summary || p.summary}</Text>
            ) : null}

            {/* Key words */}
            {(detail?.key_words?.length || p.key_words?.length) ? (
              <View style={st.section}>
                <Text style={st.sectionLabel}>Key vocabulary</Text>
                <View style={st.chips}>
                  {(detail?.key_words || p.key_words || []).slice(0, 10).map((w, i) => (
                    <View key={i} style={st.chip}>
                      <Text style={st.chipAr}>{w.arabic}</Text>
                      <Text style={st.chipEn}>{w.gloss}</Text>
                    </View>
                  ))}
                </View>
              </View>
            ) : null}

            {/* Sentences */}
            {detail?.sentences?.length ? (
              <View style={st.section}>
                <Text style={st.sectionLabel}>Story</Text>
                {detail.sentences.map((s, i) => (
                  <View key={i} style={st.sentRow}>
                    <Text style={st.sentNum}>{i + 1}</Text>
                    <View style={{ flex: 1 }}>
                      <Text style={st.sentAr}>{s.arabic}</Text>
                      <Text style={st.sentEn}>{s.english}</Text>
                    </View>
                  </View>
                ))}
              </View>
            ) : null}
          </View>
        </ScrollView>

        {/* Sticky player */}
        {playing && status && (
          <View style={st.stickyPlayer}>
            <View style={st.progressRow}>
              <Text style={st.time}>{fmt(status.position)}</Text>
              <View style={st.bar}><View style={[st.barFill, { width: `${progress * 100}%` }]} /></View>
              <Text style={st.time}>{fmt(status.duration)}</Text>
            </View>
            <View style={st.controls}>
              <Pressable onPress={() => seekRelative(-15000)}><Ionicons name="play-back" size={20} color={colors.text} /></Pressable>
              <Pressable onPress={togglePlayPause} style={st.miniPlayBtn}>
                <Ionicons name={status.isPlaying ? "pause" : "play"} size={24} color={colors.bg} />
              </Pressable>
              <Pressable onPress={() => seekRelative(30000)}><Ionicons name="play-forward" size={20} color={colors.text} /></Pressable>
            </View>
          </View>
        )}
      </View>
    );
  }

  // Grid view
  return (
    <View style={st.container}>
      <View style={st.header}>
        <Text style={st.headerTitle}>Podcast</Text>
      </View>

      {/* Mini player bar when playing */}
      {playing && status && (
        <Pressable style={st.miniBar} onPress={() => { const p = podcasts.find(x => x.filename === playing); if (p) { setSelected(playing); fetchDetail(playing); } }}>
          <Pressable onPress={togglePlayPause} style={st.miniBarPlay}>
            <Ionicons name={status.isPlaying ? "pause" : "play"} size={18} color={colors.bg} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={st.miniBarTitle} numberOfLines={1}>{title(podcasts.find(x => x.filename === playing)!)}</Text>
            <View style={st.miniBarProgress}>
              <View style={[st.miniBarFill, { width: `${progress * 100}%` }]} />
            </View>
          </View>
          <Text style={st.miniBarTime}>{fmt(status.position)}</Text>
        </Pressable>
      )}

      <ScrollView
        contentContainerStyle={st.grid}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchPodcasts(); }} tintColor={colors.accent} />}
      >
        {loading ? (
          <ActivityIndicator size="large" color={colors.accent} style={{ marginTop: 40 }} />
        ) : podcasts.length === 0 ? (
          <View style={st.empty}>
            <Ionicons name="mic-outline" size={48} color={colors.textSecondary} />
            <Text style={st.emptyText}>No episodes yet</Text>
          </View>
        ) : (
          <View style={st.gridRow}>
            {podcasts.map((p) => {
              const isPlaying = playing === p.filename;
              return (
                <Pressable
                  key={p.filename}
                  style={[st.gridCard, isPlaying && st.gridCardActive]}
                  onPress={() => { setSelected(p.filename); fetchDetail(p.filename); }}
                >
                  {p.image_url ? (
                    <Image source={{ uri: `${BASE_URL}${p.image_url}` }} style={st.gridImage} />
                  ) : (
                    <View style={[st.gridImage, st.gridImagePlaceholder]}>
                      <Ionicons name="mic" size={32} color={colors.textSecondary} />
                    </View>
                  )}

                  {/* Listened badge */}
                  {p.listened_at && (
                    <View style={st.listenedBadge}>
                      <Ionicons name="checkmark-circle" size={16} color={colors.gotIt} />
                    </View>
                  )}

                  {/* Progress overlay */}
                  {p.listen_progress > 0 && p.listen_progress < 1 && (
                    <View style={st.gridProgress}>
                      <View style={[st.gridProgressFill, { width: `${p.listen_progress * 100}%` }]} />
                    </View>
                  )}

                  <View style={st.gridInfo}>
                    {p.title_ar ? (
                      <Text style={st.gridTitleAr} numberOfLines={1}>{p.title_ar}</Text>
                    ) : null}
                    <Text style={st.gridTitle} numberOfLines={2}>{title(p)}</Text>
                    <Text style={st.gridMeta}>{fmtDur(p.duration_seconds)}</Text>
                  </View>
                </Pressable>
              );
            })}
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const st = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },

  // Header
  header: { paddingTop: 60, paddingHorizontal: 20, paddingBottom: 8 },
  headerTitle: { fontSize: 28, fontWeight: "700", color: colors.text },

  // Mini player bar
  miniBar: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: colors.surface, marginHorizontal: 16, borderRadius: 10,
    padding: 10, marginBottom: 8,
  },
  miniBarPlay: {
    width: 32, height: 32, borderRadius: 16, backgroundColor: colors.accent,
    justifyContent: "center", alignItems: "center",
  },
  miniBarTitle: { fontSize: 13, fontWeight: "600", color: colors.text },
  miniBarProgress: { height: 2, backgroundColor: colors.surfaceLight, borderRadius: 1, marginTop: 4 },
  miniBarFill: { height: "100%", backgroundColor: colors.accent, borderRadius: 1 },
  miniBarTime: { fontSize: 11, color: colors.textSecondary, fontVariant: ["tabular-nums"] },

  // Grid
  grid: { paddingHorizontal: 16, paddingBottom: 40 },
  gridRow: { flexDirection: "row", flexWrap: "wrap", gap: CARD_GAP },
  gridCard: {
    width: CARD_WIDTH, backgroundColor: colors.surface, borderRadius: 12,
    overflow: "hidden",
  },
  gridCardActive: { borderColor: colors.accent, borderWidth: 2 },
  gridImage: { width: "100%", aspectRatio: 1, backgroundColor: colors.surfaceLight },
  gridImagePlaceholder: { justifyContent: "center", alignItems: "center" },
  listenedBadge: {
    position: "absolute", top: 8, right: 8,
    backgroundColor: "rgba(0,0,0,0.5)", borderRadius: 10, padding: 2,
  },
  gridProgress: {
    height: 3, backgroundColor: colors.surfaceLight,
  },
  gridProgressFill: { height: "100%", backgroundColor: colors.accent },
  gridInfo: { padding: 10 },
  gridTitleAr: {
    fontSize: 16, color: colors.arabic, fontFamily: fontFamily.arabic,
    textAlign: "right", marginBottom: 2,
  },
  gridTitle: { fontSize: 13, fontWeight: "600", color: colors.text, textTransform: "capitalize" },
  gridMeta: { fontSize: 11, color: colors.textSecondary, marginTop: 3 },

  empty: { alignItems: "center", paddingTop: 60, gap: 12, width: "100%" },
  emptyText: { fontSize: 18, fontWeight: "600", color: colors.textSecondary },

  // Detail view
  heroImage: { width: "100%", aspectRatio: 1 },
  backBtn: {
    position: "absolute", top: 50, left: 16,
    width: 36, height: 36, borderRadius: 18,
    backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "center", alignItems: "center",
  },
  detailContent: { padding: 20 },
  detailTitleAr: {
    fontSize: 26, color: colors.arabic, fontFamily: fontFamily.arabic,
    textAlign: "right", marginBottom: 4,
  },
  detailTitle: { fontSize: 22, fontWeight: "700", color: colors.text, textTransform: "capitalize" },
  detailMeta: { fontSize: 13, color: colors.textSecondary, marginTop: 4 },
  detailPlayBtn: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: colors.accent, alignSelf: "flex-start",
    paddingHorizontal: 20, paddingVertical: 10, borderRadius: 24,
    marginTop: 16,
  },
  detailPlayText: { fontSize: 15, fontWeight: "600", color: "#fff" },
  detailSummary: {
    fontSize: 14, color: colors.textSecondary, lineHeight: 21,
    marginTop: 20, fontStyle: "italic",
  },
  section: { marginTop: 20 },
  sectionLabel: {
    fontSize: 11, fontWeight: "600", color: colors.textSecondary,
    textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8,
  },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: {
    backgroundColor: colors.surfaceLight, paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: 8, flexDirection: "row", alignItems: "center", gap: 6,
  },
  chipAr: { fontSize: 16, color: colors.arabic, fontFamily: fontFamily.arabic },
  chipEn: { fontSize: 11, color: colors.textSecondary },
  sentRow: {
    flexDirection: "row", gap: 8, paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
  },
  sentNum: { fontSize: 11, color: colors.textSecondary, width: 18, textAlign: "right", paddingTop: 4 },
  sentAr: { fontSize: 18, color: colors.arabic, fontFamily: fontFamily.arabic, textAlign: "right", lineHeight: 28 },
  sentEn: { fontSize: 13, color: colors.textSecondary, marginTop: 2 },

  // Sticky player
  stickyPlayer: {
    position: "absolute", bottom: 0, left: 0, right: 0,
    backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border,
    paddingHorizontal: 16, paddingTop: 10, paddingBottom: 30,
  },
  progressRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  bar: { flex: 1, height: 4, backgroundColor: colors.surfaceLight, borderRadius: 2, overflow: "hidden" },
  barFill: { height: "100%", backgroundColor: colors.accent, borderRadius: 2 },
  time: { fontSize: 11, color: colors.textSecondary, fontVariant: ["tabular-nums"], width: 36, textAlign: "center" },
  controls: { flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 28 },
  miniPlayBtn: {
    width: 48, height: 48, borderRadius: 24, backgroundColor: colors.accent,
    justifyContent: "center", alignItems: "center",
  },
});
