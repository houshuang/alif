import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { colors, fonts } from "../theme";
import { getStoryInfoForSentence, StoryInfo } from "../api";

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

function accuracy(timesSeen: number, timesCorrect: number): string {
  if (!timesSeen) return "--";
  return `${Math.round((timesCorrect / timesSeen) * 100)}%`;
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

function storySourceLabel(info: StoryInfo): string {
  if (info.authentic_source === "hindawi") return "Hindawi";
  if (info.format_type === "maintenance_passage") return "Maintenance passage";
  return info.source || "Story";
}

export default function StoryInfoModal({ sentenceId, visible, onClose }: Props) {
  const [info, setInfo] = useState<StoryInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    setError(null);
    getStoryInfoForSentence(sentenceId)
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
                <Text style={styles.heading}>{info.title_en || "Story"} #{info.story_id}</Text>
                {info.title_ar && <Text style={styles.arabicTitle}>{info.title_ar}</Text>}

                <View style={styles.metaRow}>
                  <MetaItem label="Source" value={storySourceLabel(info)} />
                  <MetaItem label="Generated" value={formatDate(info.created_at)} />
                  <MetaItem label="Status" value={info.status || "--"} />
                </View>
                <View style={styles.metaRow}>
                  <MetaItem label="Sentences" value={`${info.active_sentence_count}/${info.sentence_count}`} />
                  <MetaItem label="Shown" value={String(info.times_shown_total)} />
                  <MetaItem
                    label="Readiness"
                    value={info.readiness_pct != null ? `${Math.round(info.readiness_pct)}%` : "--"}
                  />
                </View>
                {info.last_shown_at && (
                  <View style={styles.metaRow}>
                    <MetaItem label="Last shown" value={formatDate(info.last_shown_at)} />
                    <MetaItem label="Words" value={String(info.total_words)} />
                    <MetaItem label="Unknown" value={String(info.unknown_count)} />
                  </View>
                )}

                {(info.style_tag || info.authentic_source || info.hindawi) && (
                  <>
                    <Text style={styles.sectionTitle}>Provenance</Text>
                    <View style={styles.provenanceBox}>
                      {info.style_tag && <InfoLine label="Style" value={info.style_tag} />}
                      {info.authentic_source && <InfoLine label="Source" value={info.authentic_source} />}
                      {info.hindawi && (
                        <>
                          <InfoLine label="Title" value={String(info.hindawi.title || "")} />
                          <InfoLine label="Start" value={String(info.hindawi.start_sentence || "")} />
                        </>
                      )}
                    </View>
                  </>
                )}

                <Text style={styles.sectionTitle}>Targeted Words</Text>
                {info.target_lemmas.length ? (
                  <View style={styles.targetList}>
                    {info.target_lemmas.map((lemma) => (
                      <View key={lemma.lemma_id} style={styles.targetRow}>
                        <View style={styles.targetMain}>
                          <Text style={styles.targetArabic}>{lemma.lemma_ar}</Text>
                          <Text style={styles.targetGloss} numberOfLines={1}>
                            #{lemma.lemma_id} · {lemma.gloss_en || ""}
                          </Text>
                          {lemma.surface_forms.length > 0 && (
                            <Text style={styles.surfaceForms} numberOfLines={1}>
                              Forms: {lemma.surface_forms.join(", ")}
                            </Text>
                          )}
                        </View>
                        <View style={styles.targetStats}>
                          <Text style={[styles.targetState, { color: stateColor(lemma.knowledge_state) }]}>
                            {lemma.knowledge_state || "--"}
                          </Text>
                          <Text style={styles.targetSmall}>
                            {lemma.occurrence_count}x · {accuracy(lemma.times_seen, lemma.times_correct)}
                          </Text>
                          <Text style={styles.targetSmall}>
                            Due {formatDate(lemma.fsrs_due)}
                          </Text>
                        </View>
                      </View>
                    ))}
                  </View>
                ) : (
                  <Text style={styles.emptyText}>No targeted words recorded.</Text>
                )}

                <Text style={styles.sectionTitle}>Sentence Rows</Text>
                <View style={styles.sentenceList}>
                  {info.sentences.map((sent, idx) => (
                    <View key={sent.sentence_id} style={styles.sentenceRow}>
                      <Text style={styles.sentenceIndex}>{idx + 1}</Text>
                      <View style={styles.sentenceDetails}>
                        <Text style={styles.sentenceText}>
                          Sentence #{sent.sentence_id}
                          {sent.target_lemma_id ? ` · target #${sent.target_lemma_id}` : ""}
                        </Text>
                        <Text style={styles.sentenceMeta}>
                          {sent.is_active ? "active" : "inactive"} · shown {sent.times_shown || 0} · {formatDate(sent.created_at)}
                        </Text>
                      </View>
                    </View>
                  ))}
                </View>
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

function InfoLine({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <View style={styles.infoLine}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
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
  },
  arabicTitle: {
    color: colors.textSecondary,
    fontSize: fonts.small,
    marginTop: 2,
    marginBottom: 12,
    textAlign: "right",
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
    marginBottom: 6,
  },
  metaItem: {
    flex: 1,
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
  provenanceBox: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    borderRadius: 8,
    padding: 10,
    gap: 6,
  },
  infoLine: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  infoLabel: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
  },
  infoValue: {
    color: colors.text,
    fontSize: fonts.caption,
    flex: 1,
    textAlign: "right",
  },
  targetList: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    borderRadius: 8,
    overflow: "hidden",
  },
  targetRow: {
    flexDirection: "row",
    padding: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    backgroundColor: "rgba(74, 158, 255, 0.06)",
  },
  targetMain: {
    flex: 1,
    paddingRight: 10,
  },
  targetArabic: {
    color: colors.text,
    fontSize: fonts.body,
    fontWeight: "700",
  },
  targetGloss: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    marginTop: 2,
  },
  surfaceForms: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    marginTop: 3,
  },
  targetStats: {
    alignItems: "flex-end",
    justifyContent: "center",
    minWidth: 92,
  },
  targetState: {
    fontSize: fonts.caption,
    fontWeight: "700",
  },
  targetSmall: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    marginTop: 2,
  },
  sentenceList: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    borderRadius: 8,
    overflow: "hidden",
  },
  sentenceRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  sentenceIndex: {
    color: colors.accent,
    fontSize: fonts.small,
    fontWeight: "700",
    width: 24,
  },
  sentenceDetails: {
    flex: 1,
  },
  sentenceText: {
    color: colors.text,
    fontSize: fonts.caption,
  },
  sentenceMeta: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
    marginTop: 2,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: fonts.caption,
  },
  error: {
    color: colors.missed,
    fontSize: fonts.body,
    textAlign: "center",
    marginTop: 40,
  },
});
