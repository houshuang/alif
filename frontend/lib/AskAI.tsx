import { useState, useRef, useEffect } from "react";
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  Modal,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts } from "./theme";
import { askAI } from "./api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface AskAIProps {
  contextBuilder: () => string;
  screen: string;
}

export default function AskAI({ contextBuilder, screen }: AskAIProps) {
  const [visible, setVisible] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<ScrollView>(null);

  function handleOpen() {
    setMessages([]);
    setConversationId(undefined);
    setInput("");
    setLoading(false);
    setVisible(true);
  }

  function handleClose() {
    setVisible(false);
  }

  useEffect(() => {
    if (messages.length > 0) {
      setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [messages.length]);

  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setLoading(true);

    try {
      const context = conversationId ? "" : contextBuilder();
      const result = await askAI(question, context, screen, conversationId);
      setConversationId(result.conversation_id);
      setMessages((prev) => [...prev, { role: "assistant", content: result.answer }]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Sorry, I could not get a response. Please try again." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Pressable style={styles.fab} onPress={handleOpen}>
        <Ionicons name="chatbubble-ellipses-outline" size={24} color="#fff" />
      </Pressable>

      <Modal
        visible={visible}
        animationType="slide"
        transparent
        onRequestClose={handleClose}
      >
        <KeyboardAvoidingView
          style={styles.overlay}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <View style={styles.modalContent}>
            <View style={styles.header}>
              <Text style={styles.headerTitle}>Ask AI</Text>
              <Pressable onPress={handleClose} hitSlop={8}>
                <Ionicons name="close" size={24} color={colors.textSecondary} />
              </Pressable>
            </View>

            <ScrollView
              ref={scrollRef}
              style={styles.messagesArea}
              contentContainerStyle={styles.messagesContent}
            >
              {messages.length === 0 && !loading && (
                <Text style={styles.placeholder}>
                  Ask anything about what you see on screen.
                </Text>
              )}
              {messages.map((msg, i) => (
                <View
                  key={i}
                  style={[
                    styles.messageBubble,
                    msg.role === "user" ? styles.userBubble : styles.assistantBubble,
                  ]}
                >
                  <Text
                    style={[
                      styles.messageText,
                      msg.role === "user" ? styles.userText : styles.assistantText,
                    ]}
                  >
                    {msg.content}
                  </Text>
                </View>
              ))}
              {loading && (
                <View style={[styles.messageBubble, styles.assistantBubble]}>
                  <ActivityIndicator size="small" color={colors.accent} />
                </View>
              )}
            </ScrollView>

            <View style={styles.inputRow}>
              <TextInput
                style={styles.textInput}
                placeholder="Ask a question..."
                placeholderTextColor={colors.textSecondary}
                value={input}
                onChangeText={setInput}
                onSubmitEditing={handleSend}
                returnKeyType="send"
                editable={!loading}
                multiline
              />
              <Pressable
                style={[styles.sendButton, (!input.trim() || loading) && styles.sendDisabled]}
                onPress={handleSend}
                disabled={!input.trim() || loading}
              >
                <Ionicons name="send" size={20} color="#fff" />
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  fab: {
    position: "absolute",
    bottom: 20,
    right: 20,
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
    elevation: 5,
  },
  overlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    justifyContent: "flex-end",
  },
  modalContent: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    maxHeight: "85%",
    padding: 20,
    paddingBottom: Platform.OS === "ios" ? 30 : 20,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 12,
  },
  headerTitle: {
    fontSize: 18,
    color: colors.text,
    fontWeight: "600",
  },
  messagesArea: {
    maxHeight: 400,
    marginBottom: 12,
  },
  messagesContent: {
    gap: 8,
    paddingVertical: 4,
  },
  placeholder: {
    color: colors.textSecondary,
    fontSize: fonts.body,
    textAlign: "center",
    marginTop: 40,
    fontStyle: "italic",
  },
  messageBubble: {
    maxWidth: "85%",
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 12,
  },
  userBubble: {
    alignSelf: "flex-end",
    backgroundColor: colors.accent,
  },
  assistantBubble: {
    alignSelf: "flex-start",
    backgroundColor: colors.surfaceLight,
  },
  messageText: {
    fontSize: fonts.body,
    lineHeight: 22,
  },
  userText: {
    color: "#fff",
  },
  assistantText: {
    color: colors.text,
  },
  inputRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 8,
  },
  textInput: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
    color: colors.text,
    fontSize: fonts.body,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    maxHeight: 100,
  },
  sendButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  sendDisabled: {
    opacity: 0.4,
  },
});
