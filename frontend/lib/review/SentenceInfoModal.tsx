import { useState, useEffect } from "react";
import {
  View,
  Text,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { colors, fonts } from "../theme";
import { getSentenceInfo, SentenceInfo } from "../api";

interface Props {
  sentenceId: number;
  visible: boolean;
  onClose: () => void;
}

function formatDate(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hours = String(d.getHours()).padStart(2, "0");
  const mins = String(d.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hours}:${mins}`;
}

function stabilityLabel(s: number | null): string {
  if (s == null) return "--";
  if (s < 1) return `${Math.round(s * 24)}h`;
  if (s < 30) return `${Math.round(s)}d`;
  return `${(s / 30).toFixed(1)}mo`;
}

function comprehensionIcon(c: string): string {
  if (c === "understood") return "+";
  if (c === "partial") return "~";
  return "-";
}

function stateColor(state: string | null): string {
  if (!state) return colors.textSecondary;
  switch (state) {
    case "known": return colors.stateKnown;
    case "learning": return colors.stateLearning;
    case "acquiring": return colors.stateAcquiring;
    case "encountered": return colors.stateEncountered;
    case "lapsed": return colors.missed;
    default: return colors.textSecondary;
  }
}

export default function SentenceInfoModal({ sentenceId, visible, onClose }: Props) {
  const [info, setInfo] = useState<SentenceInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    setError(null);
    getSentenceInfo(sentenceId)
      .then(setInfo)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [visible, sentenceId]);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <View style={styles.sheet} onStartShouldSetResponder={() => true}>
          <View style={styles.handle} />
          <ScrollView style={styles.scroll} showsVerticalScrollIndicator={false}>
            {loading ? (
              <ActivityIndicator color={colors.accent} style={{ marginTop: 40 }} />
            ) : error ? (
              <Text style={styles.error}>{error}</Text>
            ) : info ? (
              <>
                <Text style={styles.heading}>Sentence #{info.sentence_id}</Text>

                <View style={styles.metaRow}>
                  <MetaItem label="Source" value={info.source || "?"} />
                  <MetaItem label="Created" value={formatDate(info.created_at)} />
                  <MetaItem label="Shown" value={String(info.times_shown)} />
                  <MetaItem
                    label="Difficulty"
                    value={info.difficulty_score != null ? info.difficulty_score.toFixed(2) : "--"}
                  />
                </View>

                {(info.last_reading_shown_at || info.last_listening_shown_at) && (
                  <View style={styles.metaRow}>
                    {info.last_reading_shown_at && (
                      <MetaItem label="Last read" value={formatDate(info.last_reading_shown_at)} />
                    )}
                    {info.last_listening_shown_at && (
                      <MetaItem label="Last listen" value={formatDate(info.last_listening_shown_at)} />
                    )}
                  </View>
                )}

                <Text style={styles.sectionTitle}>Words</Text>
                <View style={styles.wordTable}>
                  <View style={styles.wordHeaderRow}>
                    <Text style={[styles.wordCell, styles.wordCellAr, styles.headerText]}>Word</Text>
                    <Text style={[styles.wordCell, styles.wordCellGloss, styles.headerText]}>English</Text>
                    <Text style={[styles.wordCell, styles.wordCellState, styles.headerText]}>State</Text>
                    <Text style={[styles.wordCell, styles.wordCellNum, styles.headerText]}>Diff</Text>
                    <Text style={[styles.wordCell, styles.wordCellNum, styles.headerText]}>Stab</Text>
                    <Text style={[styles.wordCell, styles.wordCellNum, styles.headerText]}>Acc</Text>
                  </View>
                  {info.words.map((w, i) => {
                    const acc = w.times_seen > 0
                      ? `${Math.round((w.times_correct / w.times_seen) * 100)}%`
                      : "--";
                    const stateLabel = w.knowledge_state
                      ? w.acquisition_box
                        ? `acq ${w.acquisition_box}`
                        : w.knowledge_state.slice(0, 4)
                      : "--";
                    return (
                      <View
                        key={i}
                        style={[styles.wordRow, w.is_target_word && styles.targetWordRow]}
                      >
                        <Text style={[styles.wordCell, styles.wordCellAr]} numberOfLines={1}>
                          {w.surface_form}
                        </Text>
                        <Text style={[styles.wordCell, styles.wordCellGloss]} numberOfLines={1}>
                          {w.gloss_en || ""}
                        </Text>
                        <Text
                          style={[
                            styles.wordCell,
                            styles.wordCellState,
                            { color: stateColor(w.knowledge_state) },
                          ]}
                        >
                          {stateLabel}
                        </Text>
                        <Text style={[styles.wordCell, styles.wordCellNum]}>
                          {w.fsrs_difficulty != null ? `${Math.round(w.fsrs_difficulty * 10)}%` : "--"}
                        </Text>
                        <Text style={[styles.wordCell, styles.wordCellNum]}>
                          {stabilityLabel(w.fsrs_stability)}
                        </Text>
                        <Text style={[styles.wordCell, styles.wordCellNum]}>{acc}</Text>
                      </View>
                    );
                  })}
                </View>

                {info.reviews.length > 0 && (
                  <>
                    <Text style={styles.sectionTitle}>
                      Review history ({info.reviews.length})
                    </Text>
                    {info.reviews.slice(0, 10).map((r, i) => (
                      <View key={i} style={styles.reviewRow}>
                        <Text style={styles.reviewComp}>
                          {comprehensionIcon(r.comprehension)}
                        </Text>
                        <Text style={styles.reviewText}>{r.comprehension}</Text>
                        <Text style={styles.reviewMode}>{r.review_mode || "?"}</Text>
                        <Text style={styles.reviewDate}>{formatDate(r.reviewed_at)}</Text>
                        {r.response_ms != null && (
                          <Text style={styles.reviewMs}>
                            {(r.response_ms / 1000).toFixed(1)}s
                          </Text>
                        )}
                      </View>
                    ))}
                    {info.reviews.length > 10 && (
                      <Text style={styles.moreText}>
                        +{info.reviews.length - 10} more
                      </Text>
                    )}
                  </>
                )}
              </>
            ) : null}
          </ScrollView>
        </View>
      </Pressable>
    </Modal>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metaItem}>
      <Text style={styles.metaLabel}>{label}</Text>
      <Text style={styles.metaValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    maxHeight: "75%",
    paddingTop: 8,
    paddingBottom: 30,
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
  scroll: {
    paddingHorizontal: 20,
  },
  heading: {
    color: colors.text,
    fontSize: fonts.body,
    fontWeight: "700",
    marginBottom: 12,
  },
  sectionTitle: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 20,
    marginBottom: 8,
  },
  metaRow: {
    flexDirection: "row",
    gap: 16,
    marginBottom: 4,
  },
  metaItem: {
    flexDirection: "column",
  },
  metaLabel: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
  },
  metaValue: {
    color: colors.text,
    fontSize: fonts.small,
    fontWeight: "500",
  },
  wordTable: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    borderRadius: 8,
    overflow: "hidden",
  },
  wordHeaderRow: {
    flexDirection: "row",
    backgroundColor: colors.surfaceLight,
    paddingVertical: 6,
    paddingHorizontal: 8,
  },
  headerText: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    fontWeight: "600",
  },
  wordRow: {
    flexDirection: "row",
    paddingVertical: 5,
    paddingHorizontal: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  targetWordRow: {
    backgroundColor: "rgba(74, 158, 255, 0.08)",
  },
  wordCell: {
    color: colors.text,
    fontSize: fonts.caption,
  },
  wordCellAr: {
    flex: 1.5,
  },
  wordCellGloss: {
    flex: 1.5,
    color: colors.textSecondary,
  },
  wordCellState: {
    flex: 1,
  },
  wordCellNum: {
    flex: 0.7,
    textAlign: "right",
  },
  reviewRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 4,
  },
  reviewComp: {
    color: colors.text,
    fontSize: fonts.body,
    fontWeight: "700",
    width: 16,
    textAlign: "center",
  },
  reviewText: {
    color: colors.text,
    fontSize: fonts.caption,
    flex: 1,
  },
  reviewMode: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
  },
  reviewDate: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
  },
  reviewMs: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    width: 40,
    textAlign: "right",
  },
  moreText: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    marginTop: 4,
  },
  error: {
    color: colors.missed,
    fontSize: fonts.body,
    textAlign: "center",
    marginTop: 40,
  },
});
