import { useEffect } from "react";
import { View, Text, Pressable, StyleSheet, AppState, ActivityIndicator } from "react-native";
import { Tabs, useRouter, usePathname } from "expo-router";
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
  homePathFor,
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
  // is always visible — that's where the user switches.
  const { language, ready } = useLanguage();
  const router = useRouter();
  const pathname = usePathname();
  const isArabic = language === "ar";
  const isGreek = language === "el";

  // href: null = hidden tab. Tab files still exist but don't show in the bar.
  const arHref = (path: string) => (isArabic ? undefined : (null as any));
  const elHref = (path: string) => (isGreek ? undefined : (null as any));

  // Keep the active route in sync with the active language. Without this, a
  // cold start with stored language "el" briefly resolves URL "/" to the
  // Arabic Reading screen (because AsyncStorage hadn't loaded yet when Expo
  // Router picked the initial route) — leaving Arabic content under a Greek
  // tab bar. Also catches web reloads on a route that belongs to the other
  // language.
  useEffect(() => {
    if (!ready) return;
    const r = routeLanguage(pathname);
    if (r === "shared") return;
    if (r !== language) {
      router.replace(homePathFor(language) as any);
    }
  }, [ready, language, pathname, router]);

  if (!ready) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

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
        <Tabs.Screen
          name="polyglot"
          options={{
            href: elHref("polyglot"),
            title: "Reading",
            headerShown: false,
            tabBarLabel: "Reading",
            tabBarIcon: ({ color, size }) => <Ionicons name="book-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="polyglot-review"
          options={{
            href: elHref("polyglot-review"),
            title: "Review",
            headerShown: false,
            tabBarLabel: "Review",
            tabBarIcon: ({ color, size }) => <Ionicons name="layers-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="polyglot-stats"
          options={{
            href: elHref("polyglot-stats"),
            title: "Modern Greek Stats",
            tabBarLabel: "Stats",
            tabBarIcon: ({ color, size }) => <Ionicons name="bar-chart-outline" size={size} color={color} />,
          }}
        />

        {/* ─── Globe (always visible) ──────────────────────────────── */}
        <Tabs.Screen
          name="languages"
          options={{
            title: "Languages",
            tabBarLabel: "Languages",
            tabBarIcon: ({ color, size }) => <Ionicons name="globe-outline" size={size} color={color} />,
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
    </>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
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
});
