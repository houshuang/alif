import { useState } from "react";
import {
  View,
  Text,
  FlatList,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Image,
  TextInput,
  Platform,
  Alert,
} from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import { colors, fonts } from "../lib/theme";
import { importBook } from "../lib/api";

type ImportPhase =
  | "idle"
  | "reading_cover"
  | "scanning"
  | "cleaning"
  | "translating"
  | "done"
  | "error";

export default function BookImportScreen() {
  const router = useRouter();
  const [coverUri, setCoverUri] = useState<string | null>(null);
  const [pageUris, setPageUris] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [phase, setPhase] = useState<ImportPhase>("idle");
  const [progressText, setProgressText] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function pickCover() {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== "granted" && Platform.OS !== "web") return;

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      allowsMultipleSelection: false,
      quality: 0.8,
    });

    if (!result.canceled && result.assets.length > 0) {
      setCoverUri(result.assets[0].uri);
    }
  }

  async function takeCoverPhoto() {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== "granted") return;

    const result = await ImagePicker.launchCameraAsync({ quality: 0.8 });
    if (!result.canceled && result.assets.length > 0) {
      setCoverUri(result.assets[0].uri);
    }
  }

  async function pickPages() {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== "granted" && Platform.OS !== "web") return;

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      allowsMultipleSelection: true,
      quality: 0.8,
    });

    if (!result.canceled && result.assets.length > 0) {
      setPageUris((prev) => [...prev, ...result.assets.map((a) => a.uri)]);
    }
  }

  async function takePagePhoto() {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== "granted") return;

    const result = await ImagePicker.launchCameraAsync({ quality: 0.8 });
    if (!result.canceled && result.assets.length > 0) {
      setPageUris((prev) => [...prev, result.assets[0].uri]);
    }
  }

  function removePage(index: number) {
    setPageUris((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleImport() {
    if (!coverUri || pageUris.length === 0) return;

    setPhase("reading_cover");
    setProgressText("Reading cover...");
    setError(null);

    try {
      const allImages = [coverUri, ...pageUris];

      setPhase("scanning");
      setProgressText(`Scanning ${pageUris.length} pages...`);

      const storyDetail = await importBook(
        allImages,
        title.trim() || undefined
      );

      setPhase("done");
      setProgressText(
        `Done! ${storyDetail.total_words} words, ` +
          `${Math.round(storyDetail.readiness_pct)}% ready`
      );

      // Navigate to story reader after brief delay
      setTimeout(() => {
        router.push(`/story/${storyDetail.id}`);
      }, 1500);
    } catch (e: any) {
      setPhase("error");
      setError(e.message || "Import failed");
      setProgressText("");
    }
  }

  const canImport =
    coverUri && pageUris.length > 0 && phase === "idle";

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Import Book</Text>
      <Text style={styles.subtitle}>
        Photo the cover + all content pages of a children's book
      </Text>

      {/* Cover section */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Cover / Title Page</Text>
        {coverUri ? (
          <View style={styles.coverPreview}>
            <Image source={{ uri: coverUri }} style={styles.coverImage} />
            <Pressable
              style={styles.removeBtn}
              onPress={() => setCoverUri(null)}
            >
              <Ionicons name="close-circle" size={24} color={colors.missed} />
            </Pressable>
          </View>
        ) : (
          <View style={styles.buttonRow}>
            <Pressable style={styles.addBtn} onPress={takeCoverPhoto}>
              <Ionicons name="camera" size={20} color={colors.text} />
              <Text style={styles.addBtnText}>Camera</Text>
            </Pressable>
            <Pressable style={styles.addBtn} onPress={pickCover}>
              <Ionicons name="images" size={20} color={colors.text} />
              <Text style={styles.addBtnText}>Gallery</Text>
            </Pressable>
          </View>
        )}
      </View>

      {/* Title override */}
      <TextInput
        style={styles.titleInput}
        placeholder="Title (auto-detected from cover)"
        placeholderTextColor={colors.textSecondary}
        value={title}
        onChangeText={setTitle}
      />

      {/* Pages section */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>
          Content Pages ({pageUris.length})
        </Text>
        <View style={styles.buttonRow}>
          <Pressable style={styles.addBtn} onPress={takePagePhoto}>
            <Ionicons name="camera" size={20} color={colors.text} />
            <Text style={styles.addBtnText}>Camera</Text>
          </Pressable>
          <Pressable style={styles.addBtn} onPress={pickPages}>
            <Ionicons name="images" size={20} color={colors.text} />
            <Text style={styles.addBtnText}>Gallery</Text>
          </Pressable>
        </View>

        {pageUris.length > 0 && (
          <FlatList
            data={pageUris}
            horizontal
            keyExtractor={(_, i) => `page-${i}`}
            renderItem={({ item, index }) => (
              <View style={styles.pageThumb}>
                <Image source={{ uri: item }} style={styles.thumbImage} />
                <Text style={styles.pageNum}>{index + 1}</Text>
                <Pressable
                  style={styles.thumbRemove}
                  onPress={() => removePage(index)}
                >
                  <Ionicons
                    name="close-circle"
                    size={18}
                    color={colors.missed}
                  />
                </Pressable>
              </View>
            )}
            style={styles.thumbStrip}
            showsHorizontalScrollIndicator={false}
          />
        )}
      </View>

      {/* Import button / status */}
      {phase === "idle" ? (
        <Pressable
          style={[styles.importBtn, !canImport && styles.importBtnDisabled]}
          onPress={handleImport}
          disabled={!canImport}
        >
          <Ionicons name="book" size={20} color="#fff" />
          <Text style={styles.importBtnText}>Import Book</Text>
        </Pressable>
      ) : (
        <View style={styles.statusBox}>
          {phase !== "done" && phase !== "error" && (
            <ActivityIndicator color={colors.accent} size="small" />
          )}
          {phase === "done" && (
            <Ionicons
              name="checkmark-circle"
              size={24}
              color={colors.gotIt}
            />
          )}
          {phase === "error" && (
            <Ionicons name="alert-circle" size={24} color={colors.missed} />
          )}
          <Text
            style={[
              styles.statusText,
              phase === "error" && { color: colors.missed },
            ]}
          >
            {progressText}
          </Text>
          {error && <Text style={styles.errorDetail}>{error}</Text>}
          {phase === "error" && (
            <Pressable
              style={styles.retryBtn}
              onPress={() => {
                setPhase("idle");
                setError(null);
              }}
            >
              <Text style={styles.retryText}>Try Again</Text>
            </Pressable>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    padding: 16,
  },
  heading: {
    fontSize: 22,
    fontWeight: "700",
    color: colors.text,
    marginBottom: 4,
  },
  subtitle: {
    fontSize: fonts.small,
    color: colors.textSecondary,
    marginBottom: 20,
  },
  section: {
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: fonts.body,
    fontWeight: "600",
    color: colors.text,
    marginBottom: 8,
  },
  buttonRow: {
    flexDirection: "row",
    gap: 12,
  },
  addBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.surface,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
  },
  addBtnText: {
    fontSize: fonts.small,
    color: colors.text,
  },
  coverPreview: {
    position: "relative",
    width: 120,
    height: 160,
  },
  coverImage: {
    width: 120,
    height: 160,
    borderRadius: 8,
  },
  removeBtn: {
    position: "absolute",
    top: -8,
    right: -8,
  },
  titleInput: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    padding: 12,
    fontSize: fonts.body,
    color: colors.text,
    marginBottom: 16,
  },
  thumbStrip: {
    marginTop: 8,
  },
  pageThumb: {
    position: "relative",
    marginRight: 8,
  },
  thumbImage: {
    width: 60,
    height: 80,
    borderRadius: 4,
  },
  pageNum: {
    position: "absolute",
    bottom: 2,
    left: 2,
    backgroundColor: "rgba(0,0,0,0.6)",
    color: "#fff",
    fontSize: 10,
    paddingHorizontal: 4,
    borderRadius: 2,
  },
  thumbRemove: {
    position: "absolute",
    top: -6,
    right: -6,
  },
  importBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: colors.accent,
    paddingVertical: 14,
    borderRadius: 10,
    marginTop: 8,
  },
  importBtnDisabled: {
    opacity: 0.4,
  },
  importBtnText: {
    fontSize: fonts.body,
    fontWeight: "600",
    color: "#fff",
  },
  statusBox: {
    alignItems: "center",
    gap: 8,
    paddingVertical: 20,
  },
  statusText: {
    fontSize: fonts.body,
    color: colors.text,
  },
  errorDetail: {
    fontSize: fonts.small,
    color: colors.missed,
    textAlign: "center",
  },
  retryBtn: {
    marginTop: 8,
    paddingHorizontal: 20,
    paddingVertical: 8,
    backgroundColor: colors.surface,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
  },
  retryText: {
    fontSize: fonts.small,
    color: colors.text,
  },
});
