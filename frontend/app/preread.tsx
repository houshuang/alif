import { useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  ActivityIndicator,
  Image,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import { useCameraPermissions } from "expo-camera";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useRouter } from "expo-router";

import MultiPageCamera from "../components/MultiPageCamera";
import { colors, fontFamily, fonts } from "../lib/theme";
import {
  createPreread,
  getPreread,
  listPrereads,
  startPreread,
  uploadPrereadPage,
} from "../lib/api";
import type { PrereadDetail, PrereadSummary, PrereadWord } from "../lib/types";

// The batch being prepared, so a reload resumes watching it.
const CURRENT_KEY = "alif.preread.current.v1";
const POLL_MS = 3000;

type Phase = "collect" | "uploading" | "working" | "result";

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) +
    " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function progressText(d: PrereadDetail | null): string {
  if (!d) return "Starting…";
  switch (d.status) {
    case "reading":
      return `Reading pages ${d.pages_read}/${d.page_count}`;
    case "analyzing":
      return "Choosing words";
    case "applying":
      return "Adding words";
    default:
      return "Starting…";
  }
}

export default function PrereadScreen() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("collect");
  const [photos, setPhotos] = useState<string[]>([]);
  const [uploaded, setUploaded] = useState(0);
  const [detail, setDetail] = useState<PrereadDetail | null>(null);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<PrereadSummary[]>([]);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function refreshRecent() {
    try {
      setRecent(await listPrereads());
    } catch {
      // Offline: keep the list we have.
    }
  }

  function stopPolling() {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }

  async function poll(id: number) {
    try {
      const d = await getPreread(id);
      setDetail(d);
      if (d.status === "done") {
        stopPolling();
        setPhase("result");
        refreshRecent();
      } else if (d.status === "failed") {
        stopPolling();
        setError(d.error || "Something went wrong.");
      }
    } catch {
      // Transient network error: the next tick retries.
    }
  }

  function watch(id: number) {
    stopPolling();
    setCurrentId(id);
    setPhase("working");
    setError(null);
    poll(id);
    pollRef.current = setInterval(() => poll(id), POLL_MS);
  }

  useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(CURRENT_KEY);
        const id = raw ? Number(raw) : null;
        if (id) {
          const d = await getPreread(id);
          setDetail(d);
          setCurrentId(id);
          if (d.status === "done") setPhase("result");
          else if (d.status !== "uploading") watch(id);
        }
      } catch {
        // Nothing to resume.
      }
      refreshRecent();
    })();
    return stopPolling;
  }, []);

  async function takePhoto() {
    if (Platform.OS === "web") {
      const res = await ImagePicker.launchCameraAsync({ quality: 0.7 });
      if (!res.canceled && res.assets.length > 0) setPhotos((p) => [...p, res.assets[0].uri]);
      return;
    }
    if (!cameraPermission?.granted) {
      const res = await requestCameraPermission();
      if (!res.granted) {
        setError("Camera permission is needed.");
        return;
      }
    }
    setCameraOpen(true);
  }

  async function pickPhotos() {
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.7,
      allowsMultipleSelection: true,
      orderedSelection: true,
      selectionLimit: 40,
    });
    if (res.canceled || res.assets.length === 0) return;
    setPhotos((p) => [...p, ...res.assets.map((a) => a.uri)]);
  }

  async function prepare() {
    if (photos.length === 0) return;
    setError(null);
    setPhase("uploading");
    setUploaded(0);
    try {
      const batch = await createPreread();
      await AsyncStorage.setItem(CURRENT_KEY, String(batch.preread_id)).catch(() => {});
      for (let i = 0; i < photos.length; i++) {
        try {
          // eslint-disable-next-line no-await-in-loop
          await uploadPrereadPage(batch.preread_id, photos[i]);
        } catch {
          // One retry for a dropped connection.
          // eslint-disable-next-line no-await-in-loop
          await uploadPrereadPage(batch.preread_id, photos[i]);
        }
        setUploaded(i + 1);
      }
      await startPreread(batch.preread_id);
      setPhotos([]);
      watch(batch.preread_id);
    } catch {
      setPhase("collect");
      setError("Upload failed. Check your connection and try again.");
    }
  }

  async function retry() {
    if (!currentId) return;
    try {
      await startPreread(currentId);
      watch(currentId);
    } catch {
      setError("Couldn't reach the server.");
    }
  }

  async function openRecent(item: PrereadSummary) {
    await AsyncStorage.setItem(CURRENT_KEY, String(item.preread_id)).catch(() => {});
    try {
      const d = await getPreread(item.preread_id);
      setDetail(d);
      setCurrentId(item.preread_id);
      if (d.status === "done") setPhase("result");
      else watch(item.preread_id);
    } catch {
      setError("Couldn't open that batch.");
    }
  }

  function newBatch() {
    stopPolling();
    AsyncStorage.removeItem(CURRENT_KEY).catch(() => {});
    setDetail(null);
    setCurrentId(null);
    setError(null);
    setPhase("collect");
    refreshRecent();
  }

  function openPage(snapId: number) {
    router.push({ pathname: "/snap", params: { snap: String(snapId) } });
  }

  const result = detail?.result;

  if (cameraOpen) {
    return (
      <MultiPageCamera
        onDone={(uris) => {
          setPhotos((p) => [...p, ...uris]);
          setCameraOpen(false);
        }}
        onCancel={() => setCameraOpen(false)}
      />
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {phase === "collect" && (
        <View>
          <Text style={styles.title}>Pre-read</Text>
          <View style={styles.row}>
            <Pressable style={styles.secondaryBtn} onPress={takePhoto}>
              <Ionicons name="camera" size={20} color={colors.accent} />
              <Text style={styles.secondaryBtnText}>Camera</Text>
            </Pressable>
            <Pressable style={styles.secondaryBtn} onPress={pickPhotos}>
              <Ionicons name="images-outline" size={20} color={colors.accent} />
              <Text style={styles.secondaryBtnText}>Choose photos</Text>
            </Pressable>
          </View>
          {photos.length > 0 && (
            <>
              <ScrollView horizontal style={styles.thumbs} contentContainerStyle={styles.thumbsInner}>
                {photos.map((uri, i) => (
                  <Pressable
                    key={`${uri}-${i}`}
                    onLongPress={() => setPhotos((p) => p.filter((_, j) => j !== i))}
                  >
                    <Image source={{ uri }} style={styles.thumb} />
                    <Text style={styles.thumbNo}>{i + 1}</Text>
                  </Pressable>
                ))}
              </ScrollView>
              <Pressable style={styles.primaryBtn} onPress={prepare}>
                <Ionicons name="sparkles" size={20} color="#fff" />
                <Text style={styles.primaryBtnText}>
                  Prepare {photos.length} {photos.length === 1 ? "page" : "pages"}
                </Text>
              </Pressable>
            </>
          )}
          {error && <Text style={styles.error}>{error}</Text>}
          {recent.length > 0 && (
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Recent</Text>
              {recent.map((item) => (
                <Pressable key={item.preread_id} style={styles.card} onPress={() => openRecent(item)}>
                  <Text style={styles.muted}>{formatWhen(item.created_at)}</Text>
                  <Text style={styles.cardText}>
                    {item.page_count} pages
                    {item.status === "done"
                      ? ` · ${item.learn_count ?? 0} words · ${item.shaky_count ?? 0} refreshed`
                      : item.status === "failed" ? " · failed" : " · in progress"}
                  </Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      )}

      {phase === "uploading" && (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.accent} />
          <Text style={styles.progress}>Uploading {uploaded}/{photos.length}</Text>
        </View>
      )}

      {phase === "working" && (
        <View style={styles.center}>
          {!error && <ActivityIndicator size="large" color={colors.accent} />}
          <Text style={styles.progress}>{error ? "Failed" : progressText(detail)}</Text>
          {error && <Text style={styles.error}>{error}</Text>}
          {error && (
            <Pressable style={styles.secondaryBtn} onPress={retry}>
              <Ionicons name="refresh" size={20} color={colors.accent} />
              <Text style={styles.secondaryBtnText}>Try again</Text>
            </Pressable>
          )}
          <Pressable style={styles.linkBtn} onPress={newBatch}>
            <Text style={styles.link}>Back</Text>
          </Pressable>
        </View>
      )}

      {phase === "result" && result && (
        <View>
          <View style={styles.headerRow}>
            <Text style={styles.title}>Ready to read</Text>
            <Pressable onPress={newBatch} hitSlop={8}>
              <Text style={styles.link}>New batch</Text>
            </Pressable>
          </View>

          <WordSection title="Learning now" words={result.learn} />
          <WordSection title="Refreshing" words={result.shaky} />
          <WordSection title="Names" words={result.names} names />

          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Pages · {detail.pages.length}</Text>
            <View style={styles.pageGrid}>
              {detail.pages.map((p) => (
                <Pressable
                  key={p.page}
                  style={[styles.pageChip, !p.snap_id && styles.pageChipFailed]}
                  disabled={!p.snap_id}
                  onPress={() => p.snap_id && openPage(p.snap_id)}
                >
                  <Text style={styles.pageChipText}>{p.page}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        </View>
      )}
    </ScrollView>
  );
}

function WordSection({ title, words, names }: { title: string; words: PrereadWord[]; names?: boolean }) {
  if (!words?.length) return null;
  return (
    <View style={styles.section}>
      <Text style={styles.sectionLabel}>{title} · {words.length}</Text>
      {words.map((w) => (
        <View key={`${title}-${w.lemma_ar_bare}`} style={styles.wordRow}>
          <View style={styles.wordMain}>
            <Text style={styles.wordArabic}>{w.lemma_ar}</Text>
            <Text style={styles.wordGloss}>
              {w.gloss_en}
              {names && w.transliteration ? `  ·  ${w.transliteration}` : ""}
            </Text>
          </View>
          <View style={styles.wordMeta}>
            {w.first_page ? <Text style={styles.muted}>p.{w.first_page}</Text> : null}
            {w.count_in_text && w.count_in_text > 1 ? (
              <Text style={styles.muted}>×{w.count_in_text}</Text>
            ) : null}
            {w.added === false && w.error ? (
              <Ionicons name="alert-circle-outline" size={16} color={colors.missed} />
            ) : null}
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, paddingBottom: 48 },
  title: { color: colors.text, fontSize: 22, fontWeight: "700", marginBottom: 16 },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" },
  row: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
  center: { alignItems: "center", gap: 14, paddingVertical: 64 },
  progress: { color: colors.text, fontSize: fonts.body, fontWeight: "600" },
  primaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: colors.accent,
    paddingVertical: 14,
    borderRadius: 12,
    marginTop: 16,
  },
  primaryBtnText: { color: "#fff", fontSize: fonts.body, fontWeight: "600" },
  secondaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
  },
  secondaryBtnText: { color: colors.accent, fontSize: fonts.body, fontWeight: "600" },
  linkBtn: { marginTop: 8 },
  link: { color: colors.accent, fontSize: fonts.small, fontWeight: "600" },
  error: { color: colors.missed, fontSize: fonts.small, textAlign: "center", marginTop: 12 },
  thumbs: { marginTop: 16 },
  thumbsInner: { gap: 8 },
  thumb: { width: 64, height: 88, borderRadius: 6, backgroundColor: colors.surfaceLight },
  thumbNo: { color: colors.textSecondary, fontSize: fonts.caption, textAlign: "center", marginTop: 2 },
  section: { marginTop: 28 },
  sectionLabel: {
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
  cardText: { color: colors.text, fontSize: fonts.body, marginTop: 2 },
  muted: { color: colors.textSecondary, fontSize: fonts.caption },
  wordRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
    paddingVertical: 8,
    paddingHorizontal: 12,
    marginBottom: 6,
  },
  wordMain: { flex: 1 },
  wordArabic: {
    color: colors.arabic,
    fontFamily: fontFamily.arabic,
    fontSize: fonts.arabicList,
    textAlign: "left",
  },
  wordGloss: { color: colors.text, fontSize: fonts.small, marginTop: 2 },
  wordMeta: { alignItems: "flex-end", gap: 2, marginLeft: 8 },
  pageGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  pageChip: {
    width: 44,
    height: 44,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  pageChipFailed: { opacity: 0.4 },
  pageChipText: { color: colors.accent, fontSize: fonts.body, fontWeight: "600" },
});
