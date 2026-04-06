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
import { askAI, flagContent } from "./api";
import MarkdownMessage from "./MarkdownMessage";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  hidden?: boolean;
  isError?: boolean;
}

interface AskAIProps {
  contextBuilder: () => string;
  screen: string;
  autoExplainPrompt?: string | null;
  autoOpen?: boolean;
  onClose?: () => void;
  sentenceId?: number | null;
  focusedLemmaId?: number | null;
}

export default function AskAI({
  contextBuilder,
  screen,
  autoExplainPrompt,
  autoOpen,
  onClose,
  sentenceId,
  focusedLemmaId,
}: AskAIProps) {
  const hasAutoPrompt = !!(autoOpen && autoExplainPrompt);
  const [visible, setVisible] = useState(!!autoOpen);
  const [messages, setMessages] = useState<ChatMessage[]>(
    hasAutoPrompt ? [{ role: "user", content: autoExplainPrompt!, hidden: true }] : []
  );
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(hasAutoPrompt);
  const [flagged, setFlagged] = useState(false);
  const scrollRef = useRef<ScrollView>(null);
  const hasSentAutoRef = useRef(false);
  const userSentRef = useRef(false);
  const [slowWarning, setSlowWarning] = useState(false);
  const [lastFailedQuestion, setLastFailedQuestion] = useState<string | null>(null);
  const loadingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function startLoadingTimers() {
    clearLoadingTimers();
    slowTimerRef.current = setTimeout(() => setSlowWarning(true), 8000);
    loadingTimerRef.current = setTimeout(() => {
      setLoading(false);
      setSlowWarning(false);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Request timed out — the server may be busy. Try again.", isError: true },
      ]);
    }, 35000);
  }

  function clearLoadingTimers() {
    if (slowTimerRef.current) clearTimeout(slowTimerRef.current);
    if (loadingTimerRef.current) clearTimeout(loadingTimerRef.current);
    slowTimerRef.current = null;
    loadingTimerRef.current = null;
    setSlowWarning(false);
  }

  function handleOpen() {
    setMessages([]);
    setConversationId(undefined);
    setInput("");
    setLoading(false);
    setFlagged(false);
    setLastFailedQuestion(null);
    clearLoadingTimers();
    setVisible(true);
  }

  function handleClose() {
    clearLoadingTimers();
    setVisible(false);
    onClose?.();
  }

  // Auto-send explain prompt on open — API call only (message already in initial state)
  useEffect(() => {
    if (hasAutoPrompt && !hasSentAutoRef.current) {
      hasSentAutoRef.current = true;
      startLoadingTimers();
      const doAutoSend = async () => {
        try {
          const context = contextBuilder();
          const result = await askAI(autoExplainPrompt!, context, screen, conversationId);
          setConversationId(result.conversation_id);
          setLastFailedQuestion(null);
          setMessages((prev) => [...prev, { role: "assistant", content: result.answer }]);
        } catch (e: any) {
          console.error("AskAI auto-explain error:", e);
          setLastFailedQuestion(autoExplainPrompt!);
          const detail = e?.message || String(e);
          const friendly = detail.includes("aborted") || detail.includes("timeout")
            ? "Request timed out — the server may be busy."
            : `Something went wrong: ${detail}`;
          setMessages((prev) => [...prev, { role: "assistant", content: friendly, isError: true }]);
        } finally {
          clearLoadingTimers();
          setLoading(false);
        }
      };
      doAutoSend();
    }
  }, [hasAutoPrompt]);

  // Cleanup timers on unmount
  useEffect(() => () => clearLoadingTimers(), []);

  // Only scroll to end on user-initiated messages
  useEffect(() => {
    if (messages.length > 0 && userSentRef.current) {
      setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 100);
      userSentRef.current = false;
    }
  }, [messages.length]);

  async function sendQuestion(question: string, options?: { hidden?: boolean }) {
    if (!question || loading) return;

    setMessages((prev) => [...prev, { role: "user", content: question, hidden: options?.hidden }]);
    setLoading(true);
    startLoadingTimers();

    try {
      const context = contextBuilder();
      const result = await askAI(question, context, screen, conversationId);
      setConversationId(result.conversation_id);
      setLastFailedQuestion(null);
      setMessages((prev) => [...prev, { role: "assistant", content: result.answer }]);
    } catch (e: any) {
      console.error("AskAI error:", e);
      setLastFailedQuestion(question);
      const detail = e?.message || String(e);
      const friendly = detail.includes("aborted") || detail.includes("timeout")
        ? "Request timed out — the server may be busy."
        : `Something went wrong: ${detail}`;
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: friendly, isError: true },
      ]);
    } finally {
      clearLoadingTimers();
      setLoading(false);
    }
  }

  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;
    setInput("");
    userSentRef.current = true;
    await sendQuestion(question);
  }

  async function handleFlag() {
    if (!sentenceId || flagged) return;
    try {
      // Include the AI conversation as context for the flag
      const chatSummary = messages
        .map((m) => `${m.role}: ${m.content}`)
        .join("\n\n");
      await flagContent({
        content_type: "word_mapping",
        sentence_id: sentenceId,
        ...(focusedLemmaId ? { lemma_id: focusedLemmaId } : {}),
      });
      setFlagged(true);
    } catch (e) {
      console.warn("flag failed:", e);
    }
  }

  const hasResponse = messages.some((m) => m.role === "assistant");

  return (
    <>
      {!autoOpen && (
        <Pressable style={styles.fab} onPress={handleOpen}>
          <Ionicons name="chatbubble-ellipses-outline" size={24} color="#fff" />
        </Pressable>
      )}

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
              {messages.map((msg, i) => {
                if (msg.hidden && msg.role === "user") {
                  return (
                    <Text key={i} style={styles.autoExplainLabel}>
                      Explaining this sentence...
                    </Text>
                  );
                }
                return (
                  <View
                    key={i}
                    style={[
                      styles.messageBubble,
                      msg.role === "user" ? styles.userBubble : styles.assistantBubble,
                    ]}
                  >
                    {msg.role === "user" ? (
                      <Text style={[styles.messageText, styles.userText]}>{msg.content}</Text>
                    ) : (
                      <View>
                        <MarkdownMessage content={msg.content} textColor={msg.isError ? colors.missed : colors.text} />
                        {msg.isError && lastFailedQuestion && i === messages.length - 1 && (
                          <Pressable
                            style={styles.retryButton}
                            onPress={() => {
                              const q = lastFailedQuestion;
                              setLastFailedQuestion(null);
                              setMessages((prev) => prev.filter((_, idx) => idx !== i));
                              sendQuestion(q, { hidden: true });
                            }}
                          >
                            <Ionicons name="refresh" size={14} color={colors.accent} />
                            <Text style={styles.retryText}>Retry</Text>
                          </Pressable>
                        )}
                      </View>
                    )}
                  </View>
                );
              })}
              {loading && (
                <View style={[styles.messageBubble, styles.assistantBubble]}>
                  <ActivityIndicator size="small" color={colors.accent} />
                  {slowWarning && (
                    <Text style={styles.slowWarningText}>Taking longer than expected...</Text>
                  )}
                </View>
              )}
            </ScrollView>

            {sentenceId && hasResponse && (
              <View style={styles.quickActions}>
                {!flagged ? (
                  <Pressable
                    style={styles.quickActionButton}
                    onPress={handleFlag}
                  >
                    <Ionicons name="flag-outline" size={16} color={colors.missed} />
                    <Text style={[styles.quickActionText, { color: colors.missed }]}>Flag sentence</Text>
                  </Pressable>
                ) : (
                  <View style={[styles.quickActionButton, { borderColor: colors.good }]}>
                    <Ionicons name="checkmark-circle-outline" size={16} color={colors.good} />
                    <Text style={[styles.quickActionText, { color: colors.good }]}>Flagged</Text>
                  </View>
                )}
              </View>
            )}

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
    top: 20,
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
    minHeight: 150,
    maxHeight: 500,
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
  autoExplainLabel: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    fontStyle: "italic",
    marginBottom: 4,
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
  quickActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 10,
  },
  quickActionButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 8,
    backgroundColor: colors.surfaceLight,
  },
  quickActionText: {
    color: colors.text,
    fontSize: fonts.small,
    fontWeight: "600",
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
  retryButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: 8,
    alignSelf: "flex-start",
  },
  retryText: {
    color: colors.accent,
    fontSize: fonts.small,
    fontWeight: "600",
  },
  slowWarningText: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginTop: 6,
  },
});
