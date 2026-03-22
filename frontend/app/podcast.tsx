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
import { colors } from "../lib/theme";
import { BASE_URL } from "../lib/api";

interface Podcast {
  filename: string;
  size_mb: number;
  created_at: string;
}

export default function PodcastScreen() {
  const [podcasts, setPodcasts] = useState<Podcast[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const [status, setStatus] = useState<{
    position: number;
    duration: number;
    isPlaying: boolean;
  } | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);
  const seekBarRef = useRef<View>(null);

  const fetchPodcasts = useCallback(async () => {
    try {
      const res = await fetch(`${BASE_URL}/api/podcasts`);
      const data = await res.json();
      setPodcasts(data.podcasts || []);
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

  const playPodcast = async (filename: string) => {
    // Stop current if playing
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
      (playbackStatus) => {
        if (playbackStatus.isLoaded) {
          setStatus({
            position: playbackStatus.positionMillis,
            duration: playbackStatus.durationMillis || 0,
            isPlaying: playbackStatus.isPlaying,
          });
          if (playbackStatus.didJustFinish) {
            setPlaying(null);
            setStatus(null);
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
    } else {
      await soundRef.current.playAsync();
    }
  };

  const seekRelative = async (deltaMs: number) => {
    if (!soundRef.current || !status) return;
    const newPos = Math.max(0, Math.min(status.position + deltaMs, status.duration));
    await soundRef.current.setPositionAsync(newPos);
  };

  const formatTime = (ms: number) => {
    const totalSec = Math.floor(ms / 1000);
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    return `${min}:${sec.toString().padStart(2, "0")}`;
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  };

  const progress = status && status.duration > 0 ? status.position / status.duration : 0;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Podcast</Text>
        <Text style={styles.subtitle}>Listening practice on the go</Text>
      </View>

      {/* Now Playing */}
      {playing && status && (
        <View style={styles.player}>
          <Text style={styles.nowPlaying} numberOfLines={1}>
            {playing.replace(".mp3", "").replace(/-/g, " ")}
          </Text>

          {/* Progress bar */}
          <View style={styles.progressContainer}>
            <Text style={styles.timeText}>{formatTime(status.position)}</Text>
            <View style={styles.progressBar}>
              <View style={[styles.progressFill, { width: `${progress * 100}%` }]} />
            </View>
            <Text style={styles.timeText}>{formatTime(status.duration)}</Text>
          </View>

          {/* Controls */}
          <View style={styles.controls}>
            <Pressable onPress={() => seekRelative(-15000)} style={styles.controlBtn}>
              <Ionicons name="play-back" size={24} color={colors.text} />
              <Text style={styles.controlLabel}>15s</Text>
            </Pressable>

            <Pressable onPress={togglePlayPause} style={styles.playBtn}>
              <Ionicons
                name={status.isPlaying ? "pause" : "play"}
                size={36}
                color={colors.bg}
              />
            </Pressable>

            <Pressable onPress={() => seekRelative(30000)} style={styles.controlBtn}>
              <Ionicons name="play-forward" size={24} color={colors.text} />
              <Text style={styles.controlLabel}>30s</Text>
            </Pressable>
          </View>
        </View>
      )}

      {/* Podcast list */}
      <ScrollView
        style={styles.list}
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
          <View style={styles.empty}>
            <Ionicons name="mic-outline" size={48} color={colors.textSecondary} />
            <Text style={styles.emptyText}>No podcasts yet</Text>
            <Text style={styles.emptySubtext}>
              Generate your first episode from the server
            </Text>
          </View>
        ) : (
          podcasts.map((p) => (
            <Pressable
              key={p.filename}
              style={[
                styles.podcastItem,
                playing === p.filename && styles.podcastItemActive,
              ]}
              onPress={() => playPodcast(p.filename)}
            >
              <View style={styles.podcastIcon}>
                <Ionicons
                  name={playing === p.filename && status?.isPlaying ? "pause-circle" : "play-circle"}
                  size={40}
                  color={playing === p.filename ? colors.accent : colors.textSecondary}
                />
              </View>
              <View style={styles.podcastInfo}>
                <Text style={styles.podcastTitle}>
                  {p.filename.replace(".mp3", "").replace(/-/g, " ")}
                </Text>
                <Text style={styles.podcastMeta}>
                  {p.size_mb} MB · {formatDate(p.created_at)}
                </Text>
              </View>
            </Pressable>
          ))
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  header: {
    paddingTop: 60,
    paddingHorizontal: 20,
    paddingBottom: 16,
  },
  title: {
    fontSize: 28,
    fontWeight: "700",
    color: colors.text,
  },
  subtitle: {
    fontSize: 14,
    color: colors.textSecondary,
    marginTop: 4,
  },
  player: {
    backgroundColor: colors.surface,
    marginHorizontal: 16,
    borderRadius: 16,
    padding: 20,
    marginBottom: 12,
  },
  nowPlaying: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.text,
    textAlign: "center",
    marginBottom: 16,
    textTransform: "capitalize",
  },
  progressContainer: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 16,
  },
  progressBar: {
    flex: 1,
    height: 4,
    backgroundColor: colors.surfaceLight,
    borderRadius: 2,
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    backgroundColor: colors.accent,
    borderRadius: 2,
  },
  timeText: {
    fontSize: 12,
    color: colors.textSecondary,
    fontVariant: ["tabular-nums"],
    width: 40,
    textAlign: "center",
  },
  controls: {
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    gap: 32,
  },
  controlBtn: {
    alignItems: "center",
    gap: 2,
  },
  controlLabel: {
    fontSize: 10,
    color: colors.textSecondary,
  },
  playBtn: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.accent,
    justifyContent: "center",
    alignItems: "center",
  },
  list: {
    flex: 1,
    paddingHorizontal: 16,
  },
  empty: {
    alignItems: "center",
    paddingTop: 60,
    gap: 12,
  },
  emptyText: {
    fontSize: 18,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  emptySubtext: {
    fontSize: 14,
    color: colors.textSecondary,
    textAlign: "center",
    paddingHorizontal: 40,
  },
  podcastItem: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    gap: 14,
  },
  podcastItemActive: {
    borderColor: colors.accent,
    borderWidth: 1,
  },
  podcastIcon: {},
  podcastInfo: {
    flex: 1,
  },
  podcastTitle: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.text,
    textTransform: "capitalize",
  },
  podcastMeta: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 2,
  },
});
