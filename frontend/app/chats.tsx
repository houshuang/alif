import { useState, useCallback } from "react";
import {
  View,
  Text,
  FlatList,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts } from "../lib/theme";
import { getChatConversations, getChatConversation } from "../lib/api";
import { ConversationSummary, ChatMessageItem } from "../lib/types";

const SCREEN_COLORS: Record<string, string> = {
  review: colors.accent,
  learn: colors.gotIt,
  word_detail: colors.stateLearning,
  story: colors.listening,
  words: colors.targetWord,
  stories: colors.listening,
  stats: colors.stateKnown,
};

function relativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

export default function ChatsScreen() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedMessages, setExpandedMessages] = useState<ChatMessageItem[]>([]);
  const [expandLoading, setExpandLoading] = useState(false);

  useFocusEffect(
    useCallback(() => {
      loadConversations();
    }, [])
  );

  async function loadConversations() {
    setLoading(true);
    try {
      const data = await getChatConversations();
      setConversations(data);
    } catch (e) {
      console.error("Failed to load conversations:", e);
    } finally {
      setLoading(false);
    }
  }

  async function toggleExpand(conversationId: string) {
    if (expandedId === conversationId) {
      setExpandedId(null);
      setExpandedMessages([]);
      return;
    }

    setExpandedId(conversationId);
    setExpandLoading(true);
    try {
      const detail = await getChatConversation(conversationId);
      setExpandedMessages(detail.messages);
    } catch (e) {
      console.error("Failed to load conversation:", e);
      setExpandedMessages([]);
    } finally {
      setExpandLoading(false);
    }
  }

  function renderConversation({ item }: { item: ConversationSummary }) {
    const badgeColor = SCREEN_COLORS[item.screen] || colors.textSecondary;
    const isExpanded = expandedId === item.conversation_id;

    return (
      <Pressable
        style={styles.conversationCard}
        onPress={() => toggleExpand(item.conversation_id)}
      >
        <View style={styles.cardHeader}>
          <View style={[styles.screenBadge, { backgroundColor: badgeColor + "25" }]}>
            <Text style={[styles.screenBadgeText, { color: badgeColor }]}>
              {item.screen.replace("_", " ")}
            </Text>
          </View>
          <Text style={styles.timestamp}>{relativeTime(item.created_at)}</Text>
        </View>
        <Text style={styles.preview} numberOfLines={isExpanded ? undefined : 2}>
          {item.preview}
        </Text>
        <View style={styles.cardFooter}>
          <Text style={styles.messageCount}>
            {item.message_count} message{item.message_count !== 1 ? "s" : ""}
          </Text>
          <Ionicons
            name={isExpanded ? "chevron-up" : "chevron-down"}
            size={16}
            color={colors.textSecondary}
          />
        </View>

        {isExpanded && (
          <View style={styles.expandedSection}>
            {expandLoading ? (
              <ActivityIndicator size="small" color={colors.accent} style={{ marginVertical: 12 }} />
            ) : (
              expandedMessages.map((msg, i) => (
                <View
                  key={i}
                  style={[
                    styles.messageBubble,
                    msg.role === "user" ? styles.userBubble : styles.assistantBubble,
                  ]}
                >
                  <Text style={styles.messageRole}>
                    {msg.role === "user" ? "You" : "AI"}
                  </Text>
                  <Text
                    style={[
                      styles.messageText,
                      msg.role === "user" ? styles.userText : styles.assistantText,
                    ]}
                  >
                    {msg.content}
                  </Text>
                </View>
              ))
            )}
          </View>
        )}
      </Pressable>
    );
  }

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={conversations}
        keyExtractor={(item) => item.conversation_id}
        renderItem={renderConversation}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Ionicons
              name="chatbubbles-outline"
              size={48}
              color={colors.textSecondary}
              style={{ marginBottom: 12, opacity: 0.5 }}
            />
            <Text style={styles.emptyText}>No conversations yet</Text>
            <Text style={styles.emptyHint}>
              Tap the chat button on any screen to ask AI a question
            </Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  centered: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  list: {
    padding: 12,
    paddingBottom: 20,
  },
  conversationCard: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  screenBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 8,
  },
  screenBadgeText: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "capitalize",
  },
  timestamp: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
  preview: {
    fontSize: fonts.body,
    color: colors.text,
    lineHeight: 22,
    marginBottom: 8,
  },
  cardFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  messageCount: {
    fontSize: fonts.caption,
    color: colors.textSecondary,
  },
  expandedSection: {
    marginTop: 12,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 12,
    gap: 8,
  },
  messageBubble: {
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 10,
    maxWidth: "90%",
  },
  userBubble: {
    alignSelf: "flex-end",
    backgroundColor: colors.accent + "20",
  },
  assistantBubble: {
    alignSelf: "flex-start",
    backgroundColor: colors.surfaceLight,
  },
  messageRole: {
    fontSize: 11,
    color: colors.textSecondary,
    fontWeight: "600",
    marginBottom: 2,
  },
  messageText: {
    fontSize: fonts.small,
    lineHeight: 20,
  },
  userText: {
    color: colors.accent,
  },
  assistantText: {
    color: colors.text,
  },
  emptyContainer: {
    alignItems: "center",
    marginTop: 60,
    paddingHorizontal: 40,
  },
  emptyText: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "600",
    marginBottom: 6,
  },
  emptyHint: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    textAlign: "center",
    lineHeight: 20,
  },
});
