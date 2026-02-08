import { Tabs } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { colors } from "../lib/theme";

export default function Layout() {
  return (
    <>
      <StatusBar style="light" />
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
          name="learn"
          options={{
            title: "Learn",
            tabBarLabel: "Learn",
            tabBarIcon: () => null,
          }}
        />
        <Tabs.Screen
          name="index"
          options={{
            title: "Review",
            tabBarLabel: "Review",
            tabBarIcon: () => null,
          }}
        />
        <Tabs.Screen
          name="words"
          options={{
            title: "Words",
            tabBarLabel: "Words",
            tabBarIcon: () => null,
          }}
        />
        <Tabs.Screen
          name="stats"
          options={{
            title: "Stats",
            tabBarLabel: "Stats",
            tabBarIcon: () => null,
          }}
        />
        <Tabs.Screen
          name="word/[id]"
          options={{
            href: null,
            title: "Word Detail",
          }}
        />
      </Tabs>
    </>
  );
}
