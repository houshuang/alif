import { useEffect, useState } from "react";
import { View, Text, Pressable, StyleSheet, AppState, ActivityIndicator } from "react-native";
import { Tabs, useRouter, usePathname } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { Ionicons } from "@expo/vector-icons";
import { useFonts } from "expo-font";
import {
  ScheherazadeNew_400Regular,
  ScheherazadeNew_700Bold,
} from "@expo-google-fonts/scheherazade-new";
import {
  Amiri_400Regular,
  Amiri_700Bold,
} from "@expo-google-fonts/amiri";
import {
  NotoNaskhArabic_400Regular,
  NotoNaskhArabic_700Bold,
} from "@expo-google-fonts/noto-naskh-arabic";
import {
  NotoSans_400Regular,
  NotoSans_400Regular_Italic,
} from "@expo-google-fonts/noto-sans";
// Greek display/body face for Polyglot. Loaded here (the single useFonts call
// for the whole app) so it's actually available at runtime — the prior
// Cormorant Garamond reference in polyglot-design-tokens was never registered,
// so Greek silently fell back to Georgia (web) / system sans (iOS).
import {
  EBGaramond_400Regular,
  EBGaramond_600SemiBold,
} from "@expo-google-fonts/eb-garamond";
import { colors } from "../lib/theme";
import { netStatus, useNetStatus } from "../lib/net-status";
import { flushQueue } from "../lib/sync-queue";
import { syncEvents } from "../lib/sync-events";
import {
  LanguageProvider,
  useLanguage,
  routeLanguage,
  routeMatchesLanguage,
  homePathFor,
  type AppLanguage,
} from "../lib/language-context";

export default function Layout() {
  const [fontsLoaded] = useFonts({
    ScheherazadeNew_400Regular,
    ScheherazadeNew_700Bold,
    Amiri_400Regular,
    Amiri_700Bold,
    NotoNaskhArabic_400Regular,
    NotoNaskhArabic_700Bold,
    NotoSans_400Regular,
    NotoSans_400Regular_Italic,
    EBGaramond_400Regular,
    EBGaramond_600SemiBold,
  });
  const online = useNetStatus();

  useEffect(() => {
    netStatus.start();

    const unsub = syncEvents.on("online", () => {
      flushQueue().catch(() => {});
    });

    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        flushQueue().catch(() => {});
      }
    });

    return () => {
      netStatus.stop();
      unsub();
      sub.remove();
    };
  }, []);

  if (!fontsLoaded) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  return (
    <LanguageProvider>
      <LayoutInner online={online} />
    </LanguageProvider>
  );
}

function LayoutInner({ online }: { online: boolean }) {
  // The active language drives which tabs are visible. Globe (`languages`)
  // is always visible — tapping it opens a small popover (see below) rather
  // than navigating to a full screen.
  const { language, setLanguage, ready } = useLanguage();
  const router = useRouter();
  const pathname = usePathname();
  const insets = useSafeAreaInsets();
  const [pickerOpen, setPickerOpen] = useState(false);
  const isArabic = language === "ar";
  // Greek and Latin share the Polyglot tab group + polyglot-* screens.
  const isPolyglot = language === "el" || language === "la";

  // href: null = hidden tab. Tab files still exist but don't show in the bar.
  const arHref = (path: string) => (isArabic ? undefined : (null as any));
  const polyHref = (path: string) => (isPolyglot ? undefined : (null as any));

  // Keep the active route in sync with the active language. Without this, a
  // cold start with a stored polyglot language briefly resolves URL "/" to the
  // Arabic Reading screen (because AsyncStorage hadn't loaded yet when Expo
  // Router picked the initial route) — leaving Arabic content under a Polyglot
  // tab bar. Also catches web reloads on a route that belongs to the other
  // surface. A polyglot route matches both el and la actives (routeMatchesLanguage).
  useEffect(() => {
    if (!ready) return;
    const r = routeLanguage(pathname);
    if (r === "shared") return;
    if (!routeMatchesLanguage(r, language)) {
      router.replace(homePathFor(language) as any);
    }
  }, [ready, language, pathname, router]);

  // NOTE: do NOT early-return a non-navigator (e.g. a bare spinner) while
  // `!ready`. The language-sync effect above calls router.replace(homePathFor)
  // the moment `ready` flips true for a stored "el" language. If the <Tabs>
  // navigator only mounts at that same moment, the replace races the mount:
  // getRootState() can't resolve the tab navigator yet, so expo-router emits a
  // raw REPLACE action (instead of JUMP_TO) that no navigator handles —
  // "The action 'REPLACE' with payload {name:'polyglot-review'} was not handled
  // by any navigator." Keep <Tabs> mounted from the first render and overlay
  // the spinner instead, so the redirect always targets a stable navigator.
  return (
    <>
      <StatusBar style="light" />
      {!online && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineBannerText}>
            Offline — reviews will sync when connected
          </Text>
        </View>
      )}
      <Tabs
        // Every screen — including detail pages like word/[id] and
        // polyglot-lemma/[id] — is a flat sibling tab here (no <Stack>). The
        // bottom-tab default backBehavior is "firstRoute", so router.back() from
        // any detail jumps to the FIRST tab (index = Arabic Reading), not the
        // tab you opened it from. Alif tolerated this by luck (its detail screens
        // open from index), but Polyglot opens the philology page from
        // polyglot-review, so back landed on Arabic index → the language-sync
        // effect then bounced to /polyglot (Greek Reading) — never Review.
        // "history" makes back return to the previously focused tab, so
        // philology → back → polyglot-review works (and reader → back → reader).
        backBehavior="history"
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
          tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
          tabBarActiveTintColor: colors.accent,
          tabBarInactiveTintColor: colors.textSecondary,
        }}
      >
        {/* ─── Arabic (Alif) tabs ──────────────────────────────────── */}
        <Tabs.Screen
          name="index"
          options={{
            href: arHref("index"),
            title: "Reading",
            headerShown: false,
            tabBarLabel: "Reading",
            tabBarIcon: ({ color, size }) => <Ionicons name="book-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="listening"
          options={{ href: null, title: "Listening", headerShown: false }}
        />
        <Tabs.Screen
          name="podcast"
          options={{
            href: arHref("podcast"),
            title: "Podcast",
            headerShown: false,
            tabBarLabel: "Podcast",
            tabBarIcon: ({ color, size }) => <Ionicons name="mic-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="stats"
          options={{
            href: arHref("stats"),
            title: "Stats",
            tabBarLabel: "Stats",
            tabBarIcon: ({ color, size }) => <Ionicons name="bar-chart-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="explore"
          options={{
            href: arHref("explore"),
            title: "Explore",
            tabBarLabel: "Explore",
            tabBarIcon: ({ color, size }) => <Ionicons name="compass-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="stories"
          options={{
            href: arHref("stories"),
            title: "Stories",
            tabBarLabel: "Stories",
            tabBarIcon: ({ color, size }) => <Ionicons name="book" size={size} color={color} />,
          }}
        />

        {/* ─── Modern Greek (Polyglot) tabs ────────────────────────── */}
        {/* Review is the first Greek tab — switching into Greek lands here
            (see homePathFor in language-routes.ts). Reading follows. */}
        <Tabs.Screen
          name="polyglot-review"
          options={{
            href: polyHref("polyglot-review"),
            title: "Review",
            headerShown: false,
            tabBarLabel: "Review",
            tabBarIcon: ({ color, size }) => <Ionicons name="layers-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="polyglot"
          options={{
            href: polyHref("polyglot"),
            title: "Reading",
            headerShown: false,
            tabBarLabel: "Reading",
            tabBarIcon: ({ color, size }) => <Ionicons name="book-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="polyglot-stats"
          options={{
            href: polyHref("polyglot-stats"),
            title: "Modern Greek Stats",
            tabBarLabel: "Stats",
            tabBarIcon: ({ color, size }) => <Ionicons name="bar-chart-outline" size={size} color={color} />,
          }}
        />

        {/* ─── Globe (always visible) ──────────────────────────────── */}
        {/* Tapping the globe opens a small popover anchored over it instead of
            navigating to the full `languages` screen (which stays as a route
            fallback). preventDefault stops the tab from switching. */}
        <Tabs.Screen
          name="languages"
          options={{
            title: "Languages",
            tabBarLabel: "Languages",
            tabBarIcon: ({ color, size }) => <Ionicons name="globe-outline" size={size} color={color} />,
          }}
          listeners={{
            tabPress: (e) => {
              e.preventDefault();
              setPickerOpen((open) => !open);
            },
          }}
        />

        {/* ─── More menu (visible in Arabic mode for now) ─────────── */}
        <Tabs.Screen
          name="more"
          options={{
            href: arHref("more"),
            title: "More",
            tabBarLabel: "More",
            tabBarIcon: ({ color, size }) => <Ionicons name="ellipsis-horizontal-outline" size={size} color={color} />,
          }}
        />

        {/* ─── Hidden screens (Arabic-side detail/nested routes) ──── */}
        <Tabs.Screen name="scanner" options={{ href: null, title: "Scanner" }} />
        <Tabs.Screen name="chats" options={{ href: null, title: "Chats" }} />
        <Tabs.Screen name="learn" options={{ href: null, title: "New Words" }} />
        <Tabs.Screen name="word/[id]" options={{ href: null, title: "Word Detail" }} />
        <Tabs.Screen name="story/[id]" options={{ href: null, title: "Story" }} />
        <Tabs.Screen name="book-import" options={{ href: null, title: "Import Book" }} />
        <Tabs.Screen name="book-page" options={{ href: null, title: "Book Page" }} />
        <Tabs.Screen name="review-lab" options={{ href: null, title: "Review Lab" }} />
        <Tabs.Screen name="root/[id]" options={{ href: null, title: "Root Detail" }} />
        <Tabs.Screen name="pattern/[id]" options={{ href: null, title: "Pattern Detail" }} />
        <Tabs.Screen name="words" options={{ href: null, title: "Words" }} />
        <Tabs.Screen name="polyglot-lemma/[id]" options={{ href: null, title: "Lemma Detail" }} />
      </Tabs>

      {pickerOpen && (
        <Pressable style={styles.pickerBackdrop} onPress={() => setPickerOpen(false)}>
          <Pressable
            style={[styles.pickerCard, { bottom: insets.bottom + 56 }]}
            onPress={() => {}}
          >
            {LANGUAGE_OPTIONS.map((opt) => {
              const active = opt.code === language;
              return (
                <Pressable
                  key={opt.code}
                  style={styles.pickerRow}
                  onPress={() => {
                    setPickerOpen(false);
                    if (opt.code !== language) {
                      setLanguage(opt.code);
                      router.replace(homePathFor(opt.code) as any);
                    }
                  }}
                >
                  <Text style={styles.pickerNative}>{opt.native}</Text>
                  <Text style={styles.pickerName}>{opt.name}</Text>
                  {active ? (
                    <Ionicons name="checkmark" size={18} color={colors.accent} />
                  ) : (
                    <View style={styles.pickerCheckSpacer} />
                  )}
                </Pressable>
              );
            })}
          </Pressable>
        </Pressable>
      )}

      {!ready && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" color={colors.accent} />
        </View>
      )}
    </>
  );
}

const LANGUAGE_OPTIONS: { code: AppLanguage; native: string; name: string }[] = [
  { code: "ar", native: "العربية", name: "Arabic" },
  { code: "el", native: "Ελληνικά", name: "Greek" },
  { code: "la", native: "Lingua Latina", name: "Latin" },
];

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  // Covers the (now always-mounted) tab navigator while AsyncStorage resolves
  // the stored language, so the wrong-language screen never flashes before the
  // language-sync redirect runs. See the note above the return in LayoutInner.
  loadingOverlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  offlineBanner: {
    backgroundColor: "#d4a017",
    paddingVertical: 6,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  offlineBannerText: {
    color: "#1a1a2e",
    fontSize: 13,
    fontWeight: "600",
  },
  // Language popover: a small card floating above the globe tab (bottom-right),
  // over a dim full-screen backdrop that closes on outside tap.
  pickerBackdrop: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0,0,0,0.25)",
  },
  pickerCard: {
    position: "absolute",
    right: 8,
    minWidth: 200,
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
    paddingVertical: 6,
    shadowColor: "#000",
    shadowOpacity: 0.35,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 6 },
    elevation: 10,
  },
  pickerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 11,
  },
  pickerNative: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "700",
    minWidth: 78,
  },
  pickerName: {
    color: colors.textSecondary,
    fontSize: 13,
    flex: 1,
  },
  pickerCheckSpacer: {
    width: 18,
  },
});
