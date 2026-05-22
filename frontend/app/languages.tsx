/**
 * Language picker — full-screen fallback.
 *
 * The live picker is now a small popover anchored over the globe tab (see
 * LANGUAGE_OPTIONS + the pickerBackdrop overlay in `app/_layout.tsx`); the
 * globe's tabPress is intercepted so this screen is no longer navigated to in
 * normal use. It's retained as a route fallback (e.g. a direct `/languages`
 * deep link on web) and to keep the globe tab registered.
 *
 * Acts like a menu: tapping a language flips the active language (persisted
 * via LanguageContext) and routes to that language's primary screen. The
 * tab-bar visibility updates automatically because _layout.tsx subscribes to
 * the language context.
 */
import { View, Text, Pressable, StyleSheet, ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useLanguage, type AppLanguage } from "../lib/language-context";

const C = {
  bg: "#0f0f1a", surface: "#1a1a2e", border: "#2a2a40",
  text: "#e0e0f0", textDim: "#9090a8", accent: "#7aa2f7",
};

type LanguageOption = {
  code: AppLanguage;
  name: string;
  nativeName: string;
  blurb: string;
  primaryPath: string;
};

const LANGUAGES: LanguageOption[] = [
  {
    code: "ar",
    name: "Arabic (Alif)",
    nativeName: "العربية",
    blurb: "Full Alif app: sentence review, listening, stories, Quran.",
    primaryPath: "/",
  },
  {
    code: "el",
    name: "Modern Greek (Polyglot)",
    nativeName: "Ελληνικά",
    blurb: "Reading-as-mapping. Tap unknowns; next-page presumes the rest known.",
    primaryPath: "/polyglot",
  },
];

export default function Languages() {
  const router = useRouter();
  const { language, setLanguage } = useLanguage();

  const pick = (opt: LanguageOption) => {
    setLanguage(opt.code);
    // Replace so the back stack doesn't bounce the user between languages
    router.replace(opt.primaryPath as any);
  };

  return (
    <View style={s.screen}>
      <Text style={s.h1}>Languages</Text>
      <Text style={s.sub}>
        Pick which language you're working on. The app shows different tabs for each.
      </Text>
      <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
        {LANGUAGES.map((L) => {
          const active = L.code === language;
          return (
            <Pressable
              key={L.code}
              onPress={() => pick(L)}
              style={[s.card, active && s.cardActive]}
            >
              <View style={s.row}>
                <View style={{ flex: 1 }}>
                  <Text style={s.native}>{L.nativeName}</Text>
                  <Text style={s.name}>{L.name}</Text>
                  <Text style={s.blurb}>{L.blurb}</Text>
                </View>
                {active && (
                  <Ionicons name="checkmark-circle" size={28} color={C.accent} />
                )}
              </View>
            </Pressable>
          );
        })}
      </ScrollView>
      <Text style={s.footer}>
        More languages (Ancient Greek, Latin, Icelandic, Portuguese) are on the roadmap.
      </Text>
    </View>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg, paddingTop: 40, paddingHorizontal: 16 },
  h1: { fontSize: 28, fontWeight: "700", color: C.text, marginBottom: 4 },
  sub: { fontSize: 13, color: C.textDim, marginBottom: 20 },
  card: { backgroundColor: C.surface, borderRadius: 10, padding: 16, marginBottom: 12,
          borderWidth: 1, borderColor: C.border },
  cardActive: { borderColor: C.accent, borderWidth: 2 },
  row: { flexDirection: "row", alignItems: "center" },
  native: { color: C.text, fontSize: 22, fontWeight: "700" },
  name: { color: C.textDim, fontSize: 14, marginTop: 2 },
  blurb: { color: C.textDim, fontSize: 12, marginTop: 8, lineHeight: 16 },
  footer: { color: C.textDim, fontSize: 11, fontStyle: "italic",
            textAlign: "center", marginBottom: 20 },
});
