import { useRef, useState } from "react";
import { View, Text, Pressable, StyleSheet, Image, ScrollView, Animated } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { CameraView } from "expo-camera";
import { colors, fonts } from "../lib/theme";

// Full-screen camera that stays open between shots, so a run of book pages can
// be captured in one go. The caller must have camera permission already.
export default function MultiPageCamera({
  onDone,
  onCancel,
}: {
  onDone: (uris: string[]) => void;
  onCancel: () => void;
}) {
  const [captures, setCaptures] = useState<string[]>([]);
  const [capturing, setCapturing] = useState(false);
  const cameraRef = useRef<CameraView>(null);
  const stripRef = useRef<ScrollView>(null);
  const flashOpacity = useRef(new Animated.Value(0)).current;

  async function capture() {
    if (capturing || !cameraRef.current) return;
    setCapturing(true);
    Animated.sequence([
      Animated.timing(flashOpacity, { toValue: 0.85, duration: 60, useNativeDriver: true }),
      Animated.timing(flashOpacity, { toValue: 0, duration: 180, useNativeDriver: true }),
    ]).start();
    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.8,
        skipProcessing: true,
      });
      if (photo?.uri) {
        setCaptures((prev) => [...prev, photo.uri]);
        requestAnimationFrame(() => {
          stripRef.current?.scrollToEnd({ animated: true });
        });
      }
    } catch (e) {
      console.error("Capture failed:", e);
    } finally {
      setCapturing(false);
    }
  }

  return (
    <View style={styles.container}>
      <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" />
      <Animated.View pointerEvents="none" style={[styles.flash, { opacity: flashOpacity }]} />
      <View style={styles.topBar}>
        <Pressable style={styles.topBtn} onPress={onCancel}>
          <Ionicons name="close" size={26} color="#fff" />
        </Pressable>
        <Pressable
          style={[styles.doneBtn, captures.length === 0 && styles.doneBtnDim]}
          onPress={() => onDone(captures)}
          disabled={captures.length === 0}
        >
          <Text style={styles.doneText}>
            Done{captures.length > 0 ? ` (${captures.length})` : ""}
          </Text>
        </Pressable>
      </View>

      <View style={styles.bottomBar}>
        {captures.length > 0 && (
          <ScrollView
            ref={stripRef}
            horizontal
            showsHorizontalScrollIndicator={false}
            style={styles.strip}
            contentContainerStyle={styles.stripContent}
          >
            {captures.map((uri, idx) => (
              <View key={`${uri}-${idx}`} style={styles.thumbItem}>
                <Image source={{ uri }} style={styles.thumb} />
                <Pressable
                  style={styles.thumbRemove}
                  onPress={() => setCaptures((prev) => prev.filter((_, i) => i !== idx))}
                >
                  <Ionicons name="close-circle" size={20} color="#fff" />
                </Pressable>
              </View>
            ))}
          </ScrollView>
        )}
        <View style={styles.shutterRow}>
          <Pressable
            style={[styles.shutter, capturing && styles.shutterDim]}
            onPress={capture}
            disabled={capturing}
          >
            <View style={styles.shutterInner} />
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#000" },
  flash: { ...StyleSheet.absoluteFillObject, backgroundColor: "#fff" },
  topBar: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    paddingTop: 50,
    paddingHorizontal: 16,
    paddingBottom: 12,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.4)",
  },
  topBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: "rgba(0,0,0,0.5)",
    alignItems: "center",
    justifyContent: "center",
  },
  doneBtn: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    backgroundColor: colors.gotIt,
  },
  doneBtnDim: { backgroundColor: "rgba(255,255,255,0.15)" },
  doneText: { color: "#fff", fontSize: fonts.body, fontWeight: "700" },
  bottomBar: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    paddingBottom: 30,
    paddingTop: 12,
    backgroundColor: "rgba(0,0,0,0.4)",
  },
  strip: { maxHeight: 80, marginBottom: 12 },
  stripContent: { paddingHorizontal: 16, gap: 8 },
  thumbItem: { position: "relative", marginRight: 8 },
  thumb: {
    width: 60,
    height: 70,
    borderRadius: 8,
    backgroundColor: "#333",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.3)",
  },
  thumbRemove: {
    position: "absolute",
    top: -6,
    right: -6,
    backgroundColor: "#000",
    borderRadius: 10,
  },
  shutterRow: { alignItems: "center", justifyContent: "center" },
  shutter: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: "rgba(255,255,255,0.25)",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 3,
    borderColor: "#fff",
  },
  shutterDim: { opacity: 0.5 },
  shutterInner: { width: 56, height: 56, borderRadius: 28, backgroundColor: "#fff" },
});
