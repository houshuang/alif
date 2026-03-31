import { useEffect } from "react";
import { View, Text, Pressable, StyleSheet, AppState, ActivityIndicator } from "react-native";
import { Tabs } from "expo-router";
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
import { colors } from "../lib/theme";
import { netStatus, useNetStatus } from "../lib/net-status";
import { flushQueue } from "../lib/sync-queue";
import { syncEvents } from "../lib/sync-events";

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
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
          tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
          tabBarActiveTintColor: colors.accent,
          tabBarInactiveTintColor: colors.textSecondary,
        }}
      >
        <Tabs.Screen
          name="index"
          options={{
            title: "Reading",
            headerShown: false,
            tabBarLabel: "Reading",
            tabBarIcon: ({ color, size }) => <Ionicons name="book-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="listening"
          options={{
            href: null,
            title: "Listening",
            headerShown: false,
          }}
        />
        <Tabs.Screen
          name="podcast"
          options={{
            title: "Podcast",
            headerShown: false,
            tabBarLabel: "Podcast",
            tabBarIcon: ({ color, size }) => <Ionicons name="mic-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="stats"
          options={{
            title: "Stats",
            tabBarLabel: "Stats",
            tabBarIcon: ({ color, size }) => <Ionicons name="bar-chart-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="explore"
          options={{
            title: "Explore",
            tabBarLabel: "Explore",
            tabBarIcon: ({ color, size }) => <Ionicons name="compass-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="stories"
          options={{
            title: "Stories",
            tabBarLabel: "Stories",
            tabBarIcon: ({ color, size }) => <Ionicons name="book" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="more"
          options={{
            title: "More",
            tabBarLabel: "More",
            tabBarIcon: ({ color, size }) => <Ionicons name="ellipsis-horizontal-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="scanner"
          options={{
            href: null,
            title: "Scanner",
          }}
        />
        <Tabs.Screen
          name="chats"
          options={{
            href: null,
            title: "Chats",
          }}
        />
        <Tabs.Screen
          name="learn"
          options={{
            href: null,
            title: "New Words",
          }}
        />
        <Tabs.Screen
          name="word/[id]"
          options={{
            href: null,
            title: "Word Detail",
          }}
        />
        <Tabs.Screen
          name="story/[id]"
          options={{
            href: null,
            title: "Story",
          }}
        />
        <Tabs.Screen
          name="book-import"
          options={{
            href: null,
            title: "Import Book",
          }}
        />
        <Tabs.Screen
          name="book-page"
          options={{
            href: null,
            title: "Book Page",
          }}
        />
        <Tabs.Screen
          name="review-lab"
          options={{
            href: null,
            title: "Review Lab",
          }}
        />
        <Tabs.Screen
          name="root/[id]"
          options={{
            href: null,
            title: "Root Detail",
          }}
        />
        <Tabs.Screen
          name="pattern/[id]"
          options={{
            href: null,
            title: "Pattern Detail",
          }}
        />
        <Tabs.Screen
          name="words"
          options={{
            href: null,
            title: "Words",
          }}
        />
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
