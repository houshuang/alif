import { useEffect, useState } from "react";
import { View, Text, StyleSheet, AppState } from "react-native";
import { Tabs } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../lib/theme";
import { netStatus, useNetStatus } from "../lib/net-status";
import { flushQueue, pendingCount } from "../lib/sync-queue";
import { syncEvents } from "../lib/sync-events";

export default function Layout() {
  const online = useNetStatus();
  const [pending, setPending] = useState(0);

  useEffect(() => {
    netStatus.start();

    const unsub = syncEvents.on("online", () => {
      flushQueue().catch(() => {});
    });

    const unsubSynced = syncEvents.on("synced", () => {
      pendingCount().then(setPending).catch(() => {});
    });

    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        flushQueue().catch(() => {});
        pendingCount().then(setPending).catch(() => {});
      }
    });

    const interval = setInterval(() => {
      pendingCount().then(setPending).catch(() => {});
    }, 5000);

    return () => {
      netStatus.stop();
      unsub();
      unsubSynced();
      sub.remove();
      clearInterval(interval);
    };
  }, []);

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
            tabBarLabel: "Reading",
            tabBarIcon: ({ color, size }) => <Ionicons name="book-outline" size={size} color={color} />,
            tabBarBadge: pending > 0 ? pending : undefined,
          }}
        />
        <Tabs.Screen
          name="listening"
          options={{
            title: "Listening",
            tabBarLabel: "Listening",
            tabBarIcon: ({ color, size }) => <Ionicons name="headset-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="learn"
          options={{
            title: "Learn",
            tabBarLabel: "Learn",
            tabBarIcon: ({ color, size }) => <Ionicons name="add-circle-outline" size={size} color={color} />,
          }}
        />
        <Tabs.Screen
          name="words"
          options={{
            title: "Words",
            tabBarLabel: "Words",
            tabBarIcon: ({ color, size }) => <Ionicons name="text-outline" size={size} color={color} />,
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
          name="stats"
          options={{
            title: "Stats",
            tabBarLabel: "Stats",
            tabBarIcon: ({ color, size }) => <Ionicons name="bar-chart-outline" size={size} color={color} />,
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
      </Tabs>
    </>
  );
}

const styles = StyleSheet.create({
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
