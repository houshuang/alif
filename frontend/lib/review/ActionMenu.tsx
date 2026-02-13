import { useState, useRef, useCallback } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  Modal,
  Animated,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts } from "../theme";
import { suspendWord, flagContent } from "../api";
import AskAI from "../AskAI";

export interface ExtraAction {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
}

interface ActionMenuProps {
  focusedLemmaId: number | null;
  focusedLemmaAr: string | null;
  sentenceId: number | null;
  askAIContextBuilder: () => string;
  askAIScreen: string;
  askAIExplainPrompt?: () => string | null;
  askAIExplainSentencePrompt?: () => string | null;
  onWordSuspended?: (lemmaId: number) => void;
  onBack?: (() => void) | null;
  extraActions?: ExtraAction[];
}

type ToastState = { message: string; key: number } | null;

export default function ActionMenu({
  focusedLemmaId,
  focusedLemmaAr,
  sentenceId,
  askAIContextBuilder,
  askAIScreen,
  askAIExplainPrompt,
  askAIExplainSentencePrompt,
  onWordSuspended,
  onBack,
  extraActions,
}: ActionMenuProps) {
  const [menuVisible, setMenuVisible] = useState(false);
  const [askAIVisible, setAskAIVisible] = useState(false);
  const [sentenceFlagExpanded, setSentenceFlagExpanded] = useState(false);
  const [toast, setToast] = useState<ToastState>(null);
  const toastOpacity = useRef(new Animated.Value(0)).current;

  const showToast = useCallback(
    (message: string) => {
      const key = Date.now();
      setToast({ message, key });
      toastOpacity.setValue(1);
      Animated.timing(toastOpacity, {
        toValue: 0,
        duration: 500,
        delay: 1800,
        useNativeDriver: true,
      }).start(() => setToast(null));
    },
    [toastOpacity],
  );

  function handleOpenMenu() {
    setSentenceFlagExpanded(false);
    setMenuVisible(true);
  }

  function handleAskAI() {
    setMenuVisible(false);
    setAskAIVisible(true);
  }

  async function handleSuspend() {
    if (!focusedLemmaId) return;
    setMenuVisible(false);
    try {
      await suspendWord(focusedLemmaId);
      showToast("Word suspended");
      onWordSuspended?.(focusedLemmaId);
    } catch (e) {
      console.warn("suspend failed:", e);
      showToast("Failed to suspend");
    }
  }

  async function handleFlagWord() {
    if (!focusedLemmaId) return;
    setMenuVisible(false);
    try {
      await flagContent({ content_type: "word_gloss", lemma_id: focusedLemmaId });
      showToast("Flagged for review");
    } catch (e) {
      console.warn("flag failed:", e);
      showToast("Failed to flag");
    }
  }

  async function handleFlagSentence(type: string) {
    if (!sentenceId) return;
    setMenuVisible(false);
    try {
      await flagContent({ content_type: type, sentence_id: sentenceId });
      showToast("Flagged for review");
    } catch (e) {
      console.warn("flag failed:", e);
      showToast("Failed to flag");
    }
  }

  function handleBack() {
    setMenuVisible(false);
    onBack?.();
  }

  return (
    <View style={styles.trigger}>
      <Pressable onPress={handleOpenMenu} hitSlop={8} style={styles.triggerInner}>
        <Ionicons name="ellipsis-horizontal" size={18} color={colors.textSecondary} />
      </Pressable>

      <Modal
        visible={menuVisible}
        transparent
        animationType="fade"
        onRequestClose={() => setMenuVisible(false)}
      >
        <Pressable style={styles.backdrop} onPress={() => setMenuVisible(false)}>
          <View style={styles.sheet} onStartShouldSetResponder={() => true}>
            <View style={styles.handle} />

            {onBack && (
              <MenuItem
                icon="arrow-back-outline"
                label="Back"
                onPress={handleBack}
              />
            )}

            <MenuItem
              icon="chatbubble-ellipses-outline"
              label="Ask AI"
              onPress={handleAskAI}
            />

            {focusedLemmaId && (
              <>
                <MenuItem
                  icon="pause-circle-outline"
                  label={`Suspend "${focusedLemmaAr || "word"}"`}
                  onPress={handleSuspend}
                />
                <MenuItem
                  icon="flag-outline"
                  label="Flag translation"
                  onPress={handleFlagWord}
                />
              </>
            )}

            {sentenceId && (
              <>
                {!sentenceFlagExpanded ? (
                  <MenuItem
                    icon="flag-outline"
                    label="Flag sentence..."
                    onPress={() => setSentenceFlagExpanded(true)}
                  />
                ) : (
                  <View style={styles.subMenu}>
                    <MenuItem
                      icon="flag-outline"
                      label="Flag Arabic text"
                      onPress={() => handleFlagSentence("sentence_arabic")}
                      indent
                    />
                    <MenuItem
                      icon="flag-outline"
                      label="Flag English translation"
                      onPress={() => handleFlagSentence("sentence_english")}
                      indent
                    />
                    <MenuItem
                      icon="flag-outline"
                      label="Flag transliteration"
                      onPress={() => handleFlagSentence("sentence_transliteration")}
                      indent
                    />
                  </View>
                )}
              </>
            )}

            {extraActions?.map((action, i) => (
              <MenuItem
                key={i}
                icon={action.icon}
                label={action.label}
                onPress={() => { setMenuVisible(false); action.onPress(); }}
              />
            ))}
          </View>
        </Pressable>
      </Modal>

      {askAIVisible && (
        <AskAIModal
          contextBuilder={askAIContextBuilder}
          screen={askAIScreen}
          buildExplainPrompt={askAIExplainPrompt}
          buildExplainSentencePrompt={askAIExplainSentencePrompt}
          onClose={() => setAskAIVisible(false)}
        />
      )}

      {toast && (
        <Animated.View style={[styles.toast, { opacity: toastOpacity }]}>
          <Text style={styles.toastText}>{toast.message}</Text>
        </Animated.View>
      )}
    </View>
  );
}

function MenuItem({
  icon,
  label,
  onPress,
  indent,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
  indent?: boolean;
}) {
  return (
    <Pressable
      style={[styles.menuItem, indent && styles.menuItemIndent]}
      onPress={onPress}
    >
      <Ionicons name={icon} size={20} color={colors.text} />
      <Text style={styles.menuLabel}>{label}</Text>
    </Pressable>
  );
}

/**
 * Wrapper that renders AskAI's modal directly (without the FAB trigger).
 * We open it programmatically from the action menu.
 */
function AskAIModal({
  contextBuilder,
  screen,
  buildExplainPrompt,
  buildExplainSentencePrompt,
  onClose,
}: {
  contextBuilder: () => string;
  screen: string;
  buildExplainPrompt?: () => string | null;
  buildExplainSentencePrompt?: () => string | null;
  onClose: () => void;
}) {
  return (
    <AskAI
      contextBuilder={contextBuilder}
      screen={screen}
      buildExplainPrompt={buildExplainPrompt}
      buildExplainSentencePrompt={buildExplainSentencePrompt}
      autoOpen
      onClose={onClose}
    />
  );
}

const styles = StyleSheet.create({
  trigger: {
    width: 28,
    height: 28,
  },
  triggerInner: {
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
  },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingBottom: 30,
    paddingTop: 8,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.textSecondary,
    alignSelf: "center",
    marginBottom: 12,
    opacity: 0.5,
  },
  menuItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingVertical: 14,
    paddingHorizontal: 20,
  },
  menuItemIndent: {
    paddingLeft: 40,
  },
  menuLabel: {
    color: colors.text,
    fontSize: fonts.body,
  },
  subMenu: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    marginVertical: 2,
  },
  toast: {
    position: "absolute",
    bottom: 100,
    left: 20,
    right: 20,
    backgroundColor: colors.surfaceLight,
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  toastText: {
    color: colors.text,
    fontSize: fonts.small,
    fontWeight: "500",
  },
});
