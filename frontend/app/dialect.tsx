import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  PanResponder,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Audio, AVPlaybackStatus } from "expo-av";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { dialectAudioUrl, getDialect, recordDialectEvent } from "../lib/api";
import {
  DIALECT_SPEEDS,
  expressionQueue,
  expressionsAt,
  lineSegments,
  linesByExpression,
  replaySpeed,
  segmentStep,
  sourceLines,
  tokenAt,
} from "../lib/dialect";
import { DialectCatalogue, DialectEvent, DialectLine } from "../lib/types";
import { colors, fontFamily } from "../lib/theme";

const PREFS_KEY = "@alif/dialect-prefs";
const HARD_KEY = "hard";
type Prefs = { speed: number; pos: Record<string, number> };
const prefs: Prefs = { speed: 0.75, pos: {} };
const savePrefs = () => AsyncStorage.setItem(PREFS_KEY, JSON.stringify(prefs)).catch(() => {});

type Queue = { key: string; title: string; ids: string[] };

export default function DialectScreen() {
  const [catalogue, setCatalogue] = useState<DialectCatalogue | null>(null);
  const [error, setError] = useState(false);
  const [queue, setQueue] = useState<Queue | null>(null);

  useEffect(() => {
    AsyncStorage.getItem(PREFS_KEY)
      .then((raw) => {
        if (raw) Object.assign(prefs, JSON.parse(raw));
      })
      .catch(() => {})
      .finally(() => {
        getDialect().then(setCatalogue).catch(() => setError(true));
      });
  }, []);

  const setHard = useCallback((lineId: string, hard: boolean) => {
    setCatalogue((prev) =>
      prev && { ...prev, lines: prev.lines.map((l) => (l.id === lineId ? { ...l, hard } : l)) },
    );
  }, []);

  if (catalogue && queue) {
    return (
      <Player
        key={queue.key}
        catalogue={catalogue}
        queue={queue}
        onExit={() => setQueue(null)}
        onHard={setHard}
      />
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={["top"]}>
      {!catalogue && !error && <ActivityIndicator style={{ marginTop: 40 }} color={colors.accent} />}
      {error && <Text style={styles.error}>Dialect lines could not load</Text>}
      {catalogue && <SourceList catalogue={catalogue} onOpen={setQueue} />}
    </SafeAreaView>
  );
}

function SourceList({ catalogue, onOpen }: { catalogue: DialectCatalogue; onOpen: (q: Queue) => void }) {
  const rows = useMemo(() => {
    const groups = new Map<string, { title: string; sources: string[]; single: boolean }>();
    for (const s of catalogue.sources) {
      const g = groups.get(s.group);
      if (g) {
        g.sources.push(s.id);
        g.single = false;
      } else {
        groups.set(s.group, { title: s.title, sources: [s.id], single: true });
      }
    }
    return [...groups.entries()].map(([group, g]) => {
      const ids = sourceLines(catalogue, g.sources);
      const byId = new Map(catalogue.lines.map((l) => [l.id, l]));
      return {
        key: g.single ? `src:${g.sources[0]}` : `group:${group}`,
        title: g.single ? g.title : group,
        ids,
        hard: ids.filter((id) => byId.get(id)?.hard).length,
      };
    });
  }, [catalogue]);
  const hardIds = catalogue.lines.filter((l) => l.hard).map((l) => l.id);

  return (
    <ScrollView contentContainerStyle={styles.listContent}>
      <Text style={styles.h1}>Dialect Lines</Text>
      {hardIds.length > 0 && (
        <Pressable
          style={styles.row}
          onPress={() => onOpen({ key: HARD_KEY, title: "Hard", ids: hardIds })}
        >
          <Text style={styles.rowTitle}>Hard</Text>
          <Text style={[styles.pill, styles.pillHard]}>{hardIds.length}</Text>
        </Pressable>
      )}
      {rows.map((r) => (
        <Pressable key={r.key} style={styles.row} onPress={() => onOpen(r)}>
          <Text style={styles.rowTitle}>{r.title}</Text>
          {r.hard > 0 && <Text style={[styles.pill, styles.pillHard]}>{r.hard}</Text>}
          <Text style={styles.pill}>{r.ids.length}</Text>
        </Pressable>
      ))}
    </ScrollView>
  );
}

type Stack = { exprId: string; ids: string[]; index: number };

function Player({
  catalogue,
  queue,
  onExit,
  onHard,
}: {
  catalogue: DialectCatalogue;
  queue: Queue;
  onExit: () => void;
  onHard: (lineId: string, hard: boolean) => void;
}) {
  const byId = useMemo(() => new Map(catalogue.lines.map((l) => [l.id, l])), [catalogue]);
  const byExpr = useMemo(() => linesByExpression(catalogue), [catalogue]);
  const exprs = useMemo(() => new Map(catalogue.expressions.map((x) => [x.id, x])), [catalogue]);
  const sources = useMemo(() => new Map(catalogue.sources.map((s) => [s.id, s])), [catalogue]);

  const [index, setIndex] = useState(() =>
    Math.min(prefs.pos[queue.key] ?? 0, Math.max(queue.ids.length - 1, 0)),
  );
  const [stack, setStack] = useState<Stack | null>(null);
  const [layer, setLayer] = useState(0);
  const [replays, setReplays] = useState(0);
  const [speed, setSpeed] = useState(prefs.speed);
  const [token, setToken] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [sheet, setSheet] = useState<string[] | null>(null);

  const ids = stack ? stack.ids : queue.ids;
  const pos = stack ? stack.index : index;
  const line = byId.get(ids[pos]);

  const sound = useRef<{ src: string; s: Audio.Sound } | null>(null);
  const segment = useRef<{ line: DialectLine; segs: [number, number][]; index: number } | null>(null);
  const tokenRef = useRef(-1);
  const playId = useRef(0);

  const send = (lineId: string, event: DialectEvent) => {
    recordDialectEvent(lineId, event).catch(() => {});
  };

  const onStatus = useCallback((status: AVPlaybackStatus) => {
    if (!status.isLoaded) return;
    const seg = segment.current;
    if (!seg) return;
    const step = status.didJustFinish ? { kind: "end" as const } : segmentStep(seg.segs, seg.index, status.positionMillis);
    if (step.kind === "stale") return;
    const t = tokenAt(seg.line, status.positionMillis / 1000);
    if (t !== tokenRef.current) {
      tokenRef.current = t;
      setToken(t);
    }
    if (step.kind === "next") {
      seg.index = step.index;
      sound.current?.s
        .setPositionAsync(seg.segs[step.index][0] * 1000, { toleranceMillisBefore: 0, toleranceMillisAfter: 0 })
        .catch(() => {});
      return;
    }
    if (step.kind === "end") {
      segment.current = null;
      tokenRef.current = -1;
      setToken(-1);
      setPlaying(false);
      sound.current?.s.pauseAsync().catch(() => {});
    }
  }, []);

  const soundFor = useCallback(
    async (src: string) => {
      if (sound.current?.src === src) return sound.current.s;
      const old = sound.current;
      sound.current = null;
      old?.s.unloadAsync().catch(() => {});
      const { sound: s } = await Audio.Sound.createAsync(
        { uri: dialectAudioUrl(src) },
        { shouldPlay: false, progressUpdateIntervalMillis: 40 },
        onStatus,
      );
      sound.current = { src, s };
      return s;
    },
    [onStatus],
  );

  const play = useCallback(
    async (l: DialectLine, rate: number) => {
      const id = ++playId.current;
      segment.current = null;
      try {
        const s = await soundFor(l.src);
        if (id !== playId.current) return;
        await s.pauseAsync();
        await s.setRateAsync(rate, true);
        const segs = lineSegments(l);
        await s.setPositionAsync(segs[0][0] * 1000, { toleranceMillisBefore: 0, toleranceMillisAfter: 0 });
        if (id !== playId.current) return;
        segment.current = { line: l, segs, index: 0 };
        setPlaying(true);
        setLoadFailed(false);
        await s.playAsync();
        send(l.id, { event: "play" });
      } catch {
        if (id === playId.current) {
          setPlaying(false);
          setLoadFailed(true);
        }
      }
    },
    [soundFor],
  );

  const stop = useCallback(() => {
    playId.current += 1;
    segment.current = null;
    setPlaying(false);
    setToken(-1);
    sound.current?.s.pauseAsync().catch(() => {});
  }, []);

  useEffect(() => {
    Audio.setAudioModeAsync({ playsInSilentModeIOS: true }).catch(() => {});
    return () => {
      playId.current += 1;
      sound.current?.s.unloadAsync().catch(() => {});
      sound.current = null;
    };
  }, []);

  // Every line opens blind and plays once at the chosen speed.
  useEffect(() => {
    if (!line) return;
    setLayer(0);
    setReplays(0);
    play(line, replaySpeed(speed, 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line?.id]);

  const go = (delta: number) => {
    const next = pos + delta;
    if (next < 0 || next >= ids.length) return;
    if (stack) {
      setStack({ ...stack, index: next });
    } else {
      setIndex(next);
      prefs.pos[queue.key] = next;
      savePrefs();
    }
  };

  const replay = () => {
    if (!line) return;
    if (playing) {
      stop();
      return;
    }
    const n = replays + 1;
    setReplays(n);
    play(line, replaySpeed(speed, n));
  };

  const reveal = (delta: number) => {
    if (!line) return;
    const next = Math.max(0, Math.min(2, layer + delta));
    if (next > layer) send(line.id, { event: next === 1 ? "arabic" : "english" });
    setLayer(next);
  };

  const toggleHard = () => {
    if (!line) return;
    const hard = !line.hard;
    onHard(line.id, hard);
    recordDialectEvent(line.id, { event: "hard", hard }).catch(() => onHard(line.id, !hard));
  };

  const chooseSpeed = () => {
    const next = DIALECT_SPEEDS[(DIALECT_SPEEDS.indexOf(speed) + 1) % DIALECT_SPEEDS.length];
    setSpeed(next);
    prefs.speed = next;
    savePrefs();
  };

  const openExpression = (exprId: string) => {
    if (!line) return;
    const others = expressionQueue(catalogue, byExpr, exprId, line);
    setSheet(null);
    if (others.length === 0) return;
    stop();
    setStack({ exprId, ids: others, index: 0 });
  };

  const goRef = useRef(go);
  goRef.current = go;
  const revealRef = useRef(reveal);
  revealRef.current = reveal;
  const pan = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_, g) => Math.abs(g.dx) > 14 || Math.abs(g.dy) > 14,
      onPanResponderRelease: (_, g) => {
        if (Math.abs(g.dx) > Math.abs(g.dy)) {
          if (g.dx < -50) goRef.current(1);
          else if (g.dx > 50) goRef.current(-1);
        } else if (g.dy < -40) revealRef.current(1);
        else if (g.dy > 40) revealRef.current(-1);
      },
    }),
  ).current;

  const stackExpr = stack ? exprs.get(stack.exprId) : null;
  const header = stackExpr ? stackExpr.ar : line ? sources.get(line.src)?.title ?? queue.title : queue.title;

  return (
    <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
      <View style={styles.topBar}>
        <Pressable
          hitSlop={12}
          onPress={() => {
            if (stack) {
              stop();
              setStack(null);
            } else {
              stop();
              onExit();
            }
          }}
        >
          <Ionicons name={stack ? "close" : "chevron-back"} size={22} color={colors.textSecondary} />
        </Pressable>
        <Text style={[styles.topTitle, stack && styles.topTitleAr]} numberOfLines={1}>
          {header}
        </Text>
        <Text style={styles.meta}>
          {ids.length ? pos + 1 : 0}/{ids.length}
        </Text>
      </View>

      {!line ? (
        <Text style={styles.error}>No lines</Text>
      ) : (
        <View style={{ flex: 1 }} {...pan.panHandlers}>
          <Pressable style={styles.card} onPress={replay}>
            <Text style={styles.scene} numberOfLines={2}>
              {line.st}
            </Text>
            <Text style={styles.who}>{line.who}</Text>
            <View style={styles.textArea}>
              {layer >= 1 ? (
                <Text style={styles.arabic}>
                  {line.toks.map((t, i) => {
                    const ex = expressionsAt(line, i);
                    return (
                      <Text key={i}>
                        <Text
                          style={[ex.length > 0 && styles.expr, i === token && styles.current]}
                          onPress={ex.length > 0 ? () => setSheet(ex) : undefined}
                          suppressHighlighting
                        >
                          {t.t}
                        </Text>
                        {i < line.toks.length - 1 ? " " : ""}
                      </Text>
                    );
                  })}
                </Text>
              ) : (
                <Ionicons
                  name={playing ? "volume-high-outline" : "ear-outline"}
                  size={42}
                  color={playing ? colors.accent : colors.border}
                />
              )}
              {layer >= 2 && <Text style={styles.english}>{line.en}</Text>}
            </View>
            {loadFailed && <Text style={styles.error}>Audio could not load</Text>}
          </Pressable>

          <View style={styles.controls}>
            <Pressable style={[styles.chip, line.hard && styles.chipHard]} onPress={toggleHard}>
              <Ionicons name={line.hard ? "flag" : "flag-outline"} size={15} color={line.hard ? "#fff" : colors.text} />
              <Text style={[styles.chipText, line.hard && { color: "#fff" }]}>Hard</Text>
            </Pressable>
            <Pressable style={styles.chip} onPress={chooseSpeed}>
              <Text style={styles.chipText}>{speed}×</Text>
            </Pressable>
            {replays > 0 && <Text style={styles.meta}>{replaySpeed(speed, replays)}×</Text>}
          </View>
          <View style={styles.nav}>
            <NavBtn icon="chevron-back" disabled={pos === 0} onPress={() => go(-1)} />
            <Pressable style={styles.revealBtn} onPress={() => (layer < 2 ? reveal(1) : reveal(-2))}>
              <Text style={styles.revealText}>{layer === 0 ? "Arabic" : layer === 1 ? "English" : "Hide"}</Text>
            </Pressable>
            <NavBtn icon="chevron-forward" disabled={pos >= ids.length - 1} onPress={() => go(1)} />
          </View>
        </View>
      )}

      <Modal visible={!!sheet} transparent animationType="slide" onRequestClose={() => setSheet(null)}>
        <Pressable style={styles.backdrop} onPress={() => setSheet(null)}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            {line &&
              sheet?.map((id) => {
                const x = exprs.get(id);
                if (!x) return null;
                const others = expressionQueue(catalogue, byExpr, id, line).length;
                return (
                  <View key={id} style={styles.sheetItem}>
                    <Text style={styles.sheetAr}>{x.ar}</Text>
                    <Text style={styles.sheetEn}>{x.en}</Text>
                    {!!x.msa && <Text style={styles.sheetMsa}>{x.msa}</Text>}
                    {!!x.note && <Text style={styles.meta}>{x.note}</Text>}
                    <Pressable
                      style={[styles.playLines, others === 0 && { opacity: 0.35 }]}
                      disabled={others === 0}
                      onPress={() => openExpression(id)}
                    >
                      <Ionicons name="play" size={15} color="#fff" />
                      <Text style={styles.playLinesText}>{others}</Text>
                    </Pressable>
                  </View>
                );
              })}
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

function NavBtn({
  icon,
  disabled,
  onPress,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  disabled: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable style={[styles.navBtn, disabled && { opacity: 0.3 }]} disabled={disabled} onPress={onPress}>
      <Ionicons name={icon} size={26} color={colors.text} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  error: { color: colors.missed, textAlign: "center", marginTop: 16 },
  listContent: { padding: 16, paddingBottom: 60 },
  h1: { color: colors.text, fontSize: 21, fontWeight: "600", marginBottom: 12 },
  meta: { color: colors.textSecondary, fontSize: 13 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
  },
  rowTitle: { flex: 1, color: colors.text, fontSize: 17 },
  pill: {
    color: colors.textSecondary,
    fontSize: 12,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 2,
    overflow: "hidden",
  },
  pillHard: { color: colors.missed, borderColor: colors.missed },
  topBar: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 14, paddingVertical: 8 },
  topTitle: { flex: 1, color: colors.text, fontSize: 16, fontWeight: "600" },
  topTitleAr: { fontFamily: fontFamily.arabic, fontSize: 20, color: colors.arabic, fontWeight: "400" },
  card: {
    flex: 1,
    margin: 14,
    padding: 18,
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
  },
  scene: { color: colors.textSecondary, fontSize: 13 },
  who: { color: colors.text, fontSize: 15, fontWeight: "600", marginTop: 4 },
  textArea: { flex: 1, justifyContent: "center", alignItems: "center", gap: 18 },
  arabic: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: 28,
    lineHeight: 50,
    textAlign: "center",
    writingDirection: "rtl",
  },
  expr: { textDecorationLine: "underline", textDecorationStyle: "dotted", textDecorationColor: colors.accent },
  current: { color: colors.accent },
  english: { color: colors.text, fontSize: 17, lineHeight: 24, textAlign: "center" },
  controls: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  chipHard: { backgroundColor: colors.missed, borderColor: colors.missed },
  chipText: { color: colors.text, fontSize: 14 },
  nav: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16 },
  navBtn: {
    width: 56,
    height: 52,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  revealBtn: {
    flex: 1,
    height: 52,
    borderRadius: 14,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  revealText: { color: "#fff", fontSize: 17, fontWeight: "600" },
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    padding: 18,
    paddingBottom: 36,
    gap: 14,
  },
  sheetItem: { gap: 4 },
  sheetAr: { color: colors.arabic, fontFamily: fontFamily.arabic, fontSize: 30, lineHeight: 50, textAlign: "right" },
  sheetEn: { color: colors.text, fontSize: 17 },
  sheetMsa: { color: colors.textSecondary, fontFamily: fontFamily.arabic, fontSize: 19, textAlign: "right" },
  playLines: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    alignSelf: "flex-start",
    backgroundColor: colors.accent,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 7,
    marginTop: 6,
  },
  playLinesText: { color: "#fff", fontSize: 15, fontWeight: "600" },
});
