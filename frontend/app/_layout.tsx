import { Tabs } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { Ionicons } from "@expo/vector-icons";
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
          name="index"
          options={{
            title: "Reading",
            tabBarLabel: "Reading",
            tabBarIcon: ({ color, size }) => <Ionicons name="book-outline" size={size} color={color} />,
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
          name="words"
          options={{
            title: "Words",
            tabBarLabel: "Words",
            tabBarIcon: ({ color, size }) => <Ionicons name="text-outline" size={size} color={color} />,
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
          name="learn"
          options={{ href: null, title: "Learn" }}
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
