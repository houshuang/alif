import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Audio, AVPlaybackStatus, ResizeMode, Video } from "expo-av";
import { clipVideoUrl, getClips, recordClipEvent } from "../lib/api";
import { Clip, ClipCatalogue, ClipLine, ClipProgress } from "../lib/types";
import { colors, fontFamily } from "../lib/theme";

const SPEEDS = [0.75, 0.9, 1];
const STAGES = ["Listen", "Shadow", "Blind", "TikTok"];
const SHADOW_REPEATS = 3;

const stripVowels = (text: string) => text.replace(/[ً-ْ]/g, "");

function statusLabel(p: ClipProgress): { text: string; color: string } {
  if (p.retest_due) return { text: "re-test", color: colors.missed };
  if (p.pass_count > 0) return { text: `passed ${p.pass_count}×`, color: colors.gotIt };
  if (p.stage > 0) return { text: STAGES[p.stage - 1], color: colors.confused };
  return { text: "new", color: colors.textSecondary };
}

function sortClips(clips: Clip[]): Clip[] {
  const rank = (c: Clip) => (c.progress.retest_due ? 0 : c.progress.pass_count > 0 ? 2 : 1);
  return [...clips].sort((a, b) => rank(a) - rank(b));
}

export default function ClipsScreen() {
  const router = useRouter();
  const [catalogue, setCatalogue] = useState<ClipCatalogue | null>(null);
  const [error, setError] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    getClips().then(setCatalogue).catch(() => setError(true));
  }, []);

  const updateProgress = useCallback((clipId: string, progress: ClipProgress) => {
    setCatalogue((prev) =>
      prev && {
        ...prev,
        clips: prev.clips.map((c) => (c.id === clipId ? { ...c, progress } : c)),
      },
    );
  }, []);

  const active = catalogue?.clips.find((c) => c.id === activeId);
  if (active) {
    return (
      <ClipPlayer
        key={active.id}
        clip={active}
        onBack={() => setActiveId(null)}
        onProgress={(p) => updateProgress(active.id, p)}
      />
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={["top"]}>
      <Pressable style={styles.back} onPress={() => router.back()} hitSlop={12}>
        <Ionicons name="chevron-back" size={22} color={colors.textSecondary} />
      </Pressable>
      {!catalogue && !error && <ActivityIndicator style={{ marginTop: 40 }} color={colors.accent} />}
      {error && <Text style={styles.error}>Clips could not load</Text>}
      {catalogue && (
        <ScrollView contentContainerStyle={styles.listContent}>
          <Text style={styles.h1}>Egyptian Clips</Text>
          {sortClips(catalogue.clips).map((c) => {
            const status = statusLabel(c.progress);
            return (
              <Pressable key={c.id} style={styles.clipCard} onPress={() => setActiveId(c.id)}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.clipTitle}>{c.title}</Text>
                  <Text style={styles.meta}>
                    {c.en} · {c.sec}s
                  </Text>
                </View>
                <Text style={[styles.chip, { color: status.color, borderColor: status.color }]}>
                  {status.text}
                </Text>
              </Pressable>
            );
          })}
          <Text style={[styles.h1, { marginTop: 24 }]}>Core Egyptian</Text>
          {catalogue.core.map(([ar, translit, en]) => (
            <View key={ar} style={styles.coreRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.coreEn}>{en}</Text>
                {!!translit && <Text style={styles.meta}>{translit}</Text>}
              </View>
              <Text style={styles.coreAr}>{ar}</Text>
            </View>
          ))}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

function ClipPlayer({
  clip,
  onBack,
  onProgress,
}: {
  clip: Clip;
  onBack: () => void;
  onProgress: (p: ClipProgress) => void;
}) {
  const videoRef = useRef<Video>(null);
  const scrollRef = useRef<ScrollView>(null);
  const lineY = useRef<Record<number, number>>({});
  const segment = useRef<{ endMs: number; onEnd?: () => void } | null>(null);
  const runId = useRef(0);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const curRef = useRef(0);

  const [stage, setStage] = useState(Math.min(Math.max(clip.progress.stage, 1), 3));
  const [speed, setSpeed] = useState(1);
  const [cur, setCurState] = useState(0);
  const [vowels, setVowels] = useState(true);
  const [english, setEnglish] = useState(false);
  const [loopLine, setLoopLine] = useState(false);
  const [hideShadowText, setHideShadowText] = useState(false);
  const [showPicture, setShowPicture] = useState(false);
  const loopRef = useRef(loopLine);
  loopRef.current = loopLine;

  const setCur = useCallback((i: number) => {
    curRef.current = i;
    setCurState(i);
  }, []);

  const send = useCallback(
    (event: Parameters<typeof recordClipEvent>[1]) =>
      recordClipEvent(clip.id, event).then(onProgress),
    [clip.id, onProgress],
  );

  const cancelPlayback = useCallback(() => {
    runId.current += 1;
    segment.current = null;
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  useEffect(() => {
    Audio.setAudioModeAsync({ playsInSilentModeIOS: true }).catch(() => {});
    if (clip.progress.stage === 0) send({ event: "stage", stage: 1 }).catch(() => {});
    return cancelPlayback;
  }, [cancelPlayback, clip.progress.stage, send]);

  const later = (fn: () => void, ms: number) => {
    timers.current.push(setTimeout(fn, ms));
  };

  const playLine = useCallback(
    async (i: number, onEnd?: () => void) => {
      const video = videoRef.current;
      if (!video) return;
      segment.current = null;
      const line = clip.lines[i];
      setCur(i);
      await video.pauseAsync().catch(() => {});
      await video.setPositionAsync(Math.max(0, line.s * 1000 - 50), {
        toleranceMillisBefore: 0,
        toleranceMillisAfter: 0,
      });
      segment.current = { endMs: line.e * 1000 + 120, onEnd };
      await video.playAsync();
    },
    [clip.lines, setCur],
  );

  const onStatus = (status: AVPlaybackStatus) => {
    if (!status.isLoaded) return;
    const pos = status.positionMillis;
    const seg = segment.current;
    if (seg) {
      if (pos >= seg.endMs) {
        segment.current = null;
        videoRef.current?.pauseAsync().catch(() => {});
        seg.onEnd?.();
      }
      return;
    }
    if (status.isPlaying) {
      const i = clip.lines.findIndex((l) => pos / 1000 >= l.s && pos / 1000 < l.e + 0.3);
      if (i >= 0 && i !== curRef.current) {
        setCur(i);
        const y = lineY.current[i];
        if (y != null) scrollRef.current?.scrollTo({ y: Math.max(0, y - 60), animated: true });
      }
    }
  };

  const playAll = async () => {
    cancelPlayback();
    await videoRef.current?.setPositionAsync(0);
    await videoRef.current?.playAsync();
  };

  const pause = () => {
    cancelPlayback();
    videoRef.current?.pauseAsync().catch(() => {});
  };

  const tapLine = (i: number) => {
    cancelPlayback();
    const run = runId.current;
    const repeat = () => {
      if (run === runId.current && loopRef.current) later(() => playLine(i, repeat), 500);
    };
    playLine(i, repeat);
  };

  const shadow = () => {
    cancelPlayback();
    const run = runId.current;
    const i = curRef.current;
    const line = clip.lines[i];
    const gap = Math.max(1200, ((line.e - line.s) * 1000) / speed + 700);
    let plays = 0;
    const once = () => {
      if (run !== runId.current) return;
      playLine(i, () => {
        plays += 1;
        if (plays < SHADOW_REPEATS) later(once, gap);
      });
    };
    once();
  };

  const changeStage = (next: number) => {
    pause();
    setStage(next);
    send({ event: "stage", stage: next }).catch(() => {});
  };

  const stepLine = (delta: number) => {
    pause();
    setCur(Math.min(clip.lines.length - 1, Math.max(0, curRef.current + delta)));
  };

  const rateBlind = (rating: "bits" | "most" | "all") => {
    send({ event: "blind_rating", rating }).catch(() => {});
    changeStage(rating === "all" ? 4 : rating === "most" ? 2 : 1);
  };

  const pass = async () => {
    pause();
    try {
      await send({ event: "pass" });
      onBack();
    } catch {
      Alert.alert("Not saved", "Check the connection and try again.");
    }
  };

  const arabic = (l: ClipLine) => (vowels ? l.ar : stripVowels(l.ar));
  const line = clip.lines[cur];
  const coverPicture = stage === 3 && !showPicture;

  return (
    <SafeAreaView style={styles.screen} edges={["top"]}>
      <View style={styles.playerTop}>
        <Pressable style={styles.back} onPress={onBack} hitSlop={12}>
          <Ionicons name="chevron-back" size={22} color={colors.textSecondary} />
        </Pressable>
        <View style={styles.videoBox}>
          <Video
            ref={videoRef}
            source={{ uri: clipVideoUrl(clip.id) }}
            style={StyleSheet.absoluteFill}
            resizeMode={ResizeMode.CONTAIN}
            progressUpdateIntervalMillis={50}
            rate={speed}
            shouldCorrectPitch
            onPlaybackStatusUpdate={onStatus}
          />
          {coverPicture && <View style={styles.cover} />}
        </View>
        <View style={styles.bar}>
          {SPEEDS.map((x) => (
            <Btn key={x} label={`${x}×`} on={speed === x} onPress={() => setSpeed(x)} />
          ))}
          <View style={{ flex: 1 }} />
          <Btn icon="play" label="All" onPress={playAll} />
          <Btn icon="pause" onPress={pause} />
        </View>
        <View style={styles.bar}>
          {STAGES.map((name, k) => (
            <Btn
              key={name}
              label={`${k + 1} ${name}`}
              on={stage === k + 1}
              grow
              onPress={() => changeStage(k + 1)}
            />
          ))}
        </View>
      </View>

      <ScrollView ref={scrollRef} contentContainerStyle={styles.body}>
        {stage === 1 && (
          <>
            <View style={styles.bar}>
              <Btn label="Vowels" on={vowels} onPress={() => setVowels(!vowels)} />
              <Btn label="English" on={english} onPress={() => setEnglish(!english)} />
              <View style={{ flex: 1 }} />
              <Btn icon="repeat" label="Loop" on={loopLine} onPress={() => setLoopLine(!loopLine)} />
            </View>
            {clip.lines.map((l, i) => (
              <Pressable
                key={i}
                style={[styles.line, i === cur && styles.lineCur]}
                onLayout={(e) => {
                  lineY.current[i] = e.nativeEvent.layout.y;
                }}
                onPress={() => tapLine(i)}
              >
                <Text style={styles.ar}>{arabic(l)}</Text>
                {english && (
                  <>
                    <Text style={styles.en}>{l.en}</Text>
                    <Text style={styles.note}>
                      {l.n}
                      {l.u ? "  ·  uncertain" : ""}
                    </Text>
                  </>
                )}
              </Pressable>
            ))}
          </>
        )}

        {stage === 2 && (
          <View style={styles.shadowBox}>
            <Text style={styles.meta}>
              {cur + 1} / {clip.lines.length}
            </Text>
            <Pressable onPress={() => setHideShadowText(!hideShadowText)}>
              <Text style={[styles.arBig, hideShadowText && styles.invisible]}>{arabic(line)}</Text>
            </Pressable>
            <Pressable onPress={() => setEnglish(!english)}>
              <Text style={[styles.en, !english && styles.invisible]}>{line.en}</Text>
            </Pressable>
            <View style={styles.bar}>
              <Btn label="Prev" grow onPress={() => stepLine(-1)} />
              <Btn label={`Repeat ×${SHADOW_REPEATS}`} on grow onPress={shadow} />
              <Btn label="Next" grow onPress={() => stepLine(1)} />
            </View>
            <View style={styles.bar}>
              <Btn
                label="Once"
                grow
                onPress={() => {
                  cancelPlayback();
                  playLine(curRef.current);
                }}
              />
              <Btn
                label={hideShadowText ? "Show text" : "Hide text"}
                grow
                onPress={() => setHideShadowText(!hideShadowText)}
              />
            </View>
          </View>
        )}

        {stage === 3 && (
          <>
            <View style={styles.bar}>
              <Btn label="Play whole clip" on grow onPress={playAll} />
              <Btn
                label={showPicture ? "Hide picture" : "Show picture"}
                grow
                onPress={() => setShowPicture(!showPicture)}
              />
            </View>
            <Text style={styles.h2}>How much did you follow?</Text>
            <View style={styles.bar}>
              <Btn label="Bits" grow onPress={() => rateBlind("bits")} />
              <Btn label="Most" grow onPress={() => rateBlind("most")} />
              <Btn label="Everything" grow onPress={() => rateBlind("all")} />
            </View>
          </>
        )}

        {stage === 4 && (
          <>
            <Btn
              label="Open in TikTok"
              on
              onPress={() => {
                pause();
                Linking.openURL(`https://www.tiktok.com/@${clip.creator}/video/${clip.id}`);
              }}
            />
            <Text style={styles.h2}>Did you follow every word, at full speed, without text?</Text>
            <View style={styles.bar}>
              <Btn label="Not yet" grow onPress={() => changeStage(1)} />
              <Btn label="Yes" on grow onPress={pass} />
            </View>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function Btn({
  label,
  icon,
  on,
  grow,
  onPress,
}: {
  label?: string;
  icon?: keyof typeof Ionicons.glyphMap;
  on?: boolean;
  grow?: boolean;
  onPress: () => void;
}) {
  const color = on ? "#ffffff" : colors.text;
  return (
    <Pressable style={[styles.btn, on && styles.btnOn, grow && { flex: 1 }]} onPress={onPress}>
      {icon && <Ionicons name={icon} size={15} color={color} />}
      {!!label && (
        <Text style={[styles.btnText, { color }]} numberOfLines={1}>
          {label}
        </Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  back: { paddingHorizontal: 12, paddingVertical: 6, alignSelf: "flex-start" },
  error: { color: colors.missed, textAlign: "center", marginTop: 40 },
  listContent: { padding: 16, paddingBottom: 60 },
  h1: { color: colors.text, fontSize: 21, fontWeight: "600", marginBottom: 12 },
  h2: { color: colors.text, fontSize: 17, fontWeight: "600", marginTop: 20, marginBottom: 4 },
  meta: { color: colors.textSecondary, fontSize: 13 },
  clipCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
  },
  clipTitle: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 22,
    lineHeight: 36,
    textAlign: "left",
  },
  chip: {
    fontSize: 12,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 3,
    overflow: "hidden",
  },
  coreRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 8,
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
  },
  coreEn: { color: colors.text, fontSize: 15 },
  coreAr: { color: colors.arabic, fontFamily: fontFamily.arabic, fontSize: 21, lineHeight: 34 },
  playerTop: { paddingHorizontal: 16 },
  videoBox: { height: 230, backgroundColor: "#000", borderRadius: 12, overflow: "hidden" },
  cover: { ...StyleSheet.absoluteFillObject, backgroundColor: "#000" },
  bar: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 },
  btn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 9,
    paddingHorizontal: 10,
    paddingVertical: 9,
  },
  btnOn: { backgroundColor: colors.accent, borderColor: colors.accent },
  btnText: { fontSize: 13, fontWeight: "500" },
  body: { paddingHorizontal: 16, paddingBottom: 60 },
  line: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 8,
  },
  lineCur: { borderColor: colors.accent },
  ar: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 25,
    lineHeight: 46,
    textAlign: "right",
    writingDirection: "rtl",
  },
  arBig: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 30,
    lineHeight: 56,
    textAlign: "right",
    writingDirection: "rtl",
  },
  en: { color: colors.textSecondary, fontSize: 14, marginTop: 2 },
  note: { color: colors.confused, fontSize: 12.5, marginTop: 4 },
  shadowBox: { marginTop: 18, gap: 8 },
  invisible: { opacity: 0.04 },
});
