import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  ScrollView,
  StyleProp,
  StyleSheet,
  Text,
  TextStyle,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Audio, AVPlaybackStatus, ResizeMode, Video } from "expo-av";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { clipVideoUrl, getClips, recordClipEvent } from "../lib/api";
import { Clip, ClipCatalogue, ClipPhrase, ClipProgress } from "../lib/types";
import { colors, fontFamily } from "../lib/theme";

const SPEEDS = [0.5, 0.6, 0.75, 0.9, 1];
const STAGES = ["Words", "Listen", "Blind", "TikTok"];
const SPEAK_REPEATS = 3;

const PREFS_KEY = "@alif/clip-prefs";
const textPrefs = { vowels: true, english: false, speed: 0.75 };

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
  const listRef = useRef<ScrollView>(null);
  const listY = useRef(0);

  useEffect(() => {
    getClips().then(setCatalogue).catch(() => setError(true));
    AsyncStorage.getItem(PREFS_KEY)
      .then((raw) => {
        if (raw) Object.assign(textPrefs, JSON.parse(raw));
      })
      .catch(() => {});
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
      <Pressable
        style={styles.back}
        onPress={() => (router.canGoBack() ? router.back() : router.replace("/more"))}
        hitSlop={12}
      >
        <Ionicons name="chevron-back" size={22} color={colors.textSecondary} />
      </Pressable>
      {!catalogue && !error && <ActivityIndicator style={{ marginTop: 40 }} color={colors.accent} />}
      {error && <Text style={styles.error}>Clips could not load</Text>}
      {catalogue && (
        <ScrollView
          ref={listRef}
          contentContainerStyle={styles.listContent}
          scrollEventThrottle={100}
          onScroll={(e) => {
            listY.current = e.nativeEvent.contentOffset.y;
          }}
          onContentSizeChange={() => listRef.current?.scrollTo({ y: listY.current, animated: false })}
        >
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

function PhraseText({
  phrase,
  vowels,
  activeWord,
  target,
  style,
}: {
  phrase: ClipPhrase;
  vowels: boolean;
  activeWord: number;
  target?: [number, number];
  style: StyleProp<TextStyle>;
}) {
  return (
    <Text style={style}>
      {phrase.words.map((w, i) => (
        <Text
          key={i}
          style={[
            target && i >= target[0] && i <= target[1] && { color: colors.confused },
            i === activeWord && { color: colors.targetWord },
          ]}
        >
          {vowels ? w.t : stripVowels(w.t)}
          {i < phrase.words.length - 1 ? " " : ""}
        </Text>
      ))}
    </Text>
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
  const curWordRef = useRef(-1);
  const phrases = useRef(
    clip.lines.flatMap((line, lineIndex) => line.phrases.map((p) => ({ ...p, line: lineIndex }))),
  ).current;

  const [stage, setStage] = useState(
    clip.progress.pass_count > 0 ? 3 : Math.max(clip.progress.stage, 1),
  );
  const [speed, setSpeed] = useState(textPrefs.speed);
  const [cur, setCurState] = useState(0);
  const [curWord, setCurWordState] = useState(-1);
  const [vowels, setVowels] = useState(textPrefs.vowels);
  const [english, setEnglish] = useState(textPrefs.english);
  const [notice, setNotice] = useState<string | null>(null);
  const [heardBlind, setHeardBlind] = useState(false);
  const [loopLine, setLoopLine] = useState(false);
  const [showPicture, setShowPicture] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [repeating, setRepeating] = useState<{ phrase: number; play: number } | null>(null);
  const loopRef = useRef(loopLine);
  loopRef.current = loopLine;

  const setCur = useCallback((i: number) => {
    curRef.current = i;
    setCurState(i);
  }, []);

  const setCurWord = useCallback((i: number) => {
    if (curWordRef.current === i) return;
    curWordRef.current = i;
    setCurWordState(i);
  }, []);

  const send = useCallback(
    (event: Parameters<typeof recordClipEvent>[1]) =>
      recordClipEvent(clip.id, event).then(onProgress),
    [clip.id, onProgress],
  );
  const sendRef = useRef(send);
  sendRef.current = send;
  const initialStage = useRef(clip.progress.stage);

  const cancelPlayback = useCallback(() => {
    runId.current += 1;
    segment.current = null;
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setRepeating(null);
    setCurWord(-1);
  }, [setCurWord]);

  useEffect(() => {
    videoRef.current?.setRateAsync(speed, true).catch(() => {});
  }, [speed]);

  useEffect(() => {
    Audio.setAudioModeAsync({ playsInSilentModeIOS: true }).catch(() => {});
    if (initialStage.current === 0) sendRef.current({ event: "stage", stage: 1 }).catch(() => {});
    return cancelPlayback;
  }, [cancelPlayback]);

  const later = (fn: () => void, ms: number) => {
    timers.current.push(setTimeout(fn, ms));
  };

  const playPhrase = useCallback(
    async (i: number, onEnd?: () => void) => {
      const video = videoRef.current;
      if (!video) return;
      segment.current = null;
      const phrase = phrases[i];
      setCur(i);
      try {
        await video.pauseAsync();
        await video.setPositionAsync(Math.max(0, phrase.s * 1000 - 50), {
          toleranceMillisBefore: 0,
          toleranceMillisAfter: 0,
        });
        segment.current = { endMs: phrase.e * 1000 + 120, onEnd };
        await video.playAsync();
      } catch {
        segment.current = null;
      }
    },
    [phrases, setCur],
  );

  const wordAt = (i: number, seconds: number) => {
    let found = -1;
    phrases[i].words.forEach((w, k) => {
      if (w.s <= seconds) found = k;
    });
    return found;
  };

  const onStatus = (status: AVPlaybackStatus) => {
    if (!status.isLoaded) return;
    setIsPlaying(status.isPlaying);
    const pos = status.positionMillis;
    const seg = segment.current;
    if (seg) {
      setCurWord(wordAt(curRef.current, pos / 1000));
      if (pos >= seg.endMs) {
        segment.current = null;
        setCurWord(-1);
        videoRef.current?.pauseAsync().catch(() => {});
        seg.onEnd?.();
      }
      return;
    }
    if (status.isPlaying) {
      const seconds = pos / 1000;
      const i = phrases.findIndex((p) => seconds >= p.s - 0.05 && seconds < p.e + 0.25);
      if (i < 0) {
        setCurWord(-1);
        return;
      }
      if (i !== curRef.current) {
        setCur(i);
        const y = lineY.current[phrases[i].line];
        if (stage === 2 && y != null) {
          scrollRef.current?.scrollTo({ y: Math.max(0, y - 60), animated: true });
        }
      }
      setCurWord(wordAt(i, seconds));
    }
  };

  const playAll = async () => {
    cancelPlayback();
    if (stage === 3) setHeardBlind(true);
    try {
      await videoRef.current?.setPositionAsync(0);
      await videoRef.current?.playAsync();
    } catch {
      setVideoFailed(true);
    }
  };

  const pause = () => {
    cancelPlayback();
    videoRef.current?.pauseAsync().catch(() => {});
  };

  const togglePlay = async () => {
    if (isPlaying || repeating) {
      pause();
      return;
    }
    cancelPlayback();
    if (stage === 3) setHeardBlind(true);
    const video = videoRef.current;
    if (!video) return;
    try {
      const status = await video.getStatusAsync();
      const atEnd =
        status.isLoaded && !!status.durationMillis && status.positionMillis >= status.durationMillis - 100;
      if (atEnd) await video.setPositionAsync(0);
      await video.playAsync();
    } catch {
      setVideoFailed(true);
    }
  };

  const tapPhrase = (i: number) => {
    cancelPlayback();
    const run = runId.current;
    const repeat = () => {
      if (run === runId.current && loopRef.current) later(() => playPhrase(i, repeat), 500);
    };
    playPhrase(i, repeat);
  };

  const toggleLoop = () => {
    const next = !loopLine;
    setLoopLine(next);
    loopRef.current = next;
    if (next && !isPlaying) tapPhrase(curRef.current);
  };

  const repeatToSpeak = (i: number) => {
    cancelPlayback();
    const run = runId.current;
    const phrase = phrases[i];
    const gap = Math.max(1200, ((phrase.e - phrase.s) * 1000) / speed + 700);
    let plays = 0;
    const once = () => {
      if (run !== runId.current) return;
      setRepeating({ phrase: i, play: plays + 1 });
      playPhrase(i, () => {
        plays += 1;
        if (plays < SPEAK_REPEATS) later(once, gap);
        else setRepeating(null);
      });
    };
    once();
  };

  const changeStage = (next: number, message: string | null = null) => {
    pause();
    if (next === 3) setHeardBlind(false);
    setNotice(message);
    setStage(next);
    scrollRef.current?.scrollTo({ y: 0, animated: false });
    send({ event: "stage", stage: next }).catch(() => {});
  };

  const rateBlind = (rating: "bits" | "most" | "all") => {
    send({ event: "blind_rating", rating }).catch(() => {});
    if (rating === "all") changeStage(4);
    else if (rating === "most") changeStage(2, "Most: repeat the phrases you lost");
    else changeStage(1, "Bits: back to the words");
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

  const updateTextPrefs = (next: Partial<typeof textPrefs>) => {
    Object.assign(textPrefs, next);
    AsyncStorage.setItem(PREFS_KEY, JSON.stringify(textPrefs)).catch(() => {});
    if (next.vowels !== undefined) setVowels(next.vowels);
    if (next.english !== undefined) setEnglish(next.english);
    if (next.speed !== undefined) setSpeed(next.speed);
  };

  const coverPicture = stage === 3 && !showPicture;
  let phraseIndex = -1;

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
            videoStyle={styles.videoFill}
            onLoad={() => videoRef.current?.setRateAsync(speed, true).catch(() => {})}
            onPlaybackStatusUpdate={onStatus}
            onError={() => setVideoFailed(true)}
          />
          {coverPicture && <View style={styles.cover} />}
          {videoFailed && <Text style={styles.videoError}>Video could not load</Text>}
        </View>
        <View style={styles.bar}>
          {stage !== 4 &&
            SPEEDS.map((x) => (
              <Btn
                key={x}
                compact
                label={`${x}×`}
                on={speed === x}
                onPress={() => updateTextPrefs({ speed: x })}
              />
            ))}
          <View style={{ flex: 1 }} />
          <Btn icon="play-skip-back" onPress={playAll} />
          <Btn icon={isPlaying || repeating ? "pause" : "play"} onPress={togglePlay} />
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
        {!!notice && <Text style={styles.notice}>{notice}</Text>}

        {stage === 1 &&
          clip.vocab.map((v, i) => (
            <Pressable
              key={i}
              style={[styles.vocabRow, cur === v.ph && curWord >= 0 && styles.lineCur]}
              onPress={() => tapPhrase(v.ph)}
            >
              <View style={styles.vocabHead}>
                <Text style={styles.vocabEn}>{v.en}</Text>
                <Text style={styles.vocabAr}>{v.ar}</Text>
              </View>
              <PhraseText
                phrase={phrases[v.ph]}
                vowels
                activeWord={cur === v.ph ? curWord : -1}
                target={v.w}
                style={styles.vocabContext}
              />
            </Pressable>
          ))}

        {stage === 2 && (
          <>
            <View style={styles.bar}>
              <Btn label="Vowels" on={vowels} onPress={() => updateTextPrefs({ vowels: !vowels })} />
              <Btn label="English" on={english} onPress={() => updateTextPrefs({ english: !english })} />
              <View style={{ flex: 1 }} />
              <Btn icon="repeat" label="Loop" on={loopLine} onPress={toggleLoop} />
            </View>
            {clip.lines.map((l, lineIndex) => (
              <View
                key={lineIndex}
                style={styles.line}
                onLayout={(e) => {
                  lineY.current[lineIndex] = e.nativeEvent.layout.y;
                }}
              >
                {l.phrases.map((p, k) => {
                  phraseIndex += 1;
                  const i = phraseIndex;
                  const active = i === cur;
                  return (
                    <View key={k} style={[styles.phraseRow, active && styles.phraseCur]}>
                      <Pressable style={styles.speakBtn} onPress={() => repeatToSpeak(i)} hitSlop={6}>
                        <Ionicons name="mic-outline" size={15} color={colors.textSecondary} />
                        <Text style={styles.speakText}>
                          {repeating?.phrase === i
                            ? `${repeating.play}/${SPEAK_REPEATS}`
                            : `×${SPEAK_REPEATS}`}
                        </Text>
                      </Pressable>
                      <Pressable style={{ flex: 1 }} onPress={() => tapPhrase(i)}>
                        <PhraseText
                          phrase={p}
                          vowels={vowels}
                          activeWord={active ? curWord : -1}
                          style={styles.ar}
                        />
                      </Pressable>
                    </View>
                  );
                })}
                {english && (
                  <>
                    <Text style={styles.en}>{l.en}</Text>
                    <Text style={styles.note}>
                      {l.n}
                      {l.u ? "  ·  uncertain" : ""}
                    </Text>
                  </>
                )}
              </View>
            ))}
          </>
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
              <Btn label="Bits" grow disabled={!heardBlind} onPress={() => rateBlind("bits")} />
              <Btn label="Most" grow disabled={!heardBlind} onPress={() => rateBlind("most")} />
              <Btn label="Everything" grow disabled={!heardBlind} onPress={() => rateBlind("all")} />
            </View>
          </>
        )}

        {stage === 4 && (
          <>
            <View style={{ height: 14 }} />
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
              <Btn label="Not yet" grow onPress={() => changeStage(2, "Not yet: back to the phrases")} />
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
  compact,
  disabled,
  onPress,
}: {
  label?: string;
  icon?: keyof typeof Ionicons.glyphMap;
  on?: boolean;
  grow?: boolean;
  compact?: boolean;
  disabled?: boolean;
  onPress: () => void;
}) {
  const color = on ? "#ffffff" : colors.text;
  return (
    <Pressable
      style={[
        styles.btn,
        compact && { paddingHorizontal: 6 },
        on && styles.btnOn,
        grow && { flex: 1 },
        disabled && { opacity: 0.35 },
      ]}
      disabled={disabled}
      onPress={onPress}
    >
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
  videoBox: { height: 200, backgroundColor: "#000", borderRadius: 12, overflow: "hidden" },
  cover: { ...StyleSheet.absoluteFillObject, backgroundColor: "#000" },
  videoFill: { width: "100%", height: "100%" },
  notice: { color: colors.confused, fontSize: 14, marginTop: 10 },
  videoError: { color: colors.missed, textAlign: "center", marginTop: 100 },
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
  en: { color: colors.textSecondary, fontSize: 14, marginTop: 2 },
  note: { color: colors.confused, fontSize: 12.5, marginTop: 4 },
  phraseRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderRadius: 8,
    paddingHorizontal: 4,
  },
  phraseCur: { backgroundColor: colors.surfaceLight },
  speakBtn: { flexDirection: "row", alignItems: "center", gap: 2, paddingVertical: 8, minWidth: 40 },
  speakText: { color: colors.textSecondary, fontSize: 12 },
  vocabRow: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 8,
  },
  vocabHead: { flexDirection: "row", alignItems: "center", gap: 12 },
  vocabEn: { flex: 1, color: colors.text, fontSize: 15 },
  vocabAr: { color: colors.arabic, fontFamily: fontFamily.arabic, fontSize: 25, lineHeight: 42 },
  vocabContext: {
    color: colors.textSecondary,
    fontFamily: fontFamily.arabic,
    fontSize: 19,
    lineHeight: 34,
    textAlign: "right",
    writingDirection: "rtl",
  },
});
