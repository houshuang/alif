import React from "react";
import { Modal, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { WordLookupResult, ConfusionAnalysis } from "../types";
import { colors, fontFamily } from "../theme";
import { showMnemonic } from "../feature-flags";
import { GrammarRow } from "../WordCardComponents";
import { getGrammarParticleInfo } from "../grammar-particles";
import { RevealedView, FocusWordMark } from "./WordInfoCard";

export function WordDetailsModal({ visible, onClose, result, surfaceForm, markState, confusionData,
  onWord, onRoot, onPattern }: {
  visible: boolean; onClose: () => void; result: WordLookupResult | null;
  surfaceForm: string | null; markState: FocusWordMark | null; confusionData: ConfusionAnalysis | null;
  onWord: (id: number) => void; onRoot: (id: number) => void; onPattern: (wazn: string) => void;
}) {
  const { width, height } = useWindowDimensions();
  const particle = surfaceForm ? getGrammarParticleInfo(surfaceForm) : null;
  const e = result?.etymology_json, h = result?.memory_hooks_json;
  const section = (title: string, text: string | null | undefined) => text ? <View style={styles.section} key={title}>
    <Text style={styles.label}>{title}</Text><Text style={styles.text}>{text}</Text>
  </View> : null;
  const navigate = (fn: () => void) => { onClose(); fn(); };
  return <Modal visible={visible} transparent animationType="none" onRequestClose={onClose}>
    <View style={styles.overlay}>
      <Pressable style={StyleSheet.absoluteFill} accessibilityRole="button" accessibilityLabel="Dismiss full word card" onPress={onClose} />
      <View accessibilityViewIsModal style={[styles.card, { width: Math.min(580, width - 24), maxHeight: height * 0.82 }]}>
        <View style={styles.header}>
          <Text style={styles.title}>Word details</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Close full word card" onPress={onClose} style={styles.close}>
            <Ionicons name="close-outline" size={25} color={colors.textSecondary} />
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {result && <RevealedView result={result} surfaceForm={surfaceForm} markState={markState} confusionData={confusionData}
            expanded showConfusionCandidates={false}
            onNavigateToDetail={id => navigate(() => onWord(id))}
            onNavigateToRoot={id => navigate(() => onRoot(id))}
            onNavigateToPattern={wazn => navigate(() => onPattern(wazn))} />}
          {particle && <>
            {section("Meaning", particle.meaning)}{section("Grammar", particle.description)}
            {particle.examples.map((x, i) => <View key={i} style={styles.section}><Text style={styles.arabic}>{x.ar}</Text><Text style={styles.text}>{x.en}</Text></View>)}
            {section("Usage", particle.grammar_note)}
          </>}
          {!!result?.frequency_rank && section("Frequency rank", `#${result.frequency_rank}`)}
          {result?.forms_json && <View style={styles.section}>
            <Text style={styles.label}>All forms</Text>
            {Object.entries(result.forms_json).filter(([,v]) => typeof v === "string" && v).map(([key, value]) =>
              <View key={key} style={styles.form}><Text style={styles.formLabel}>{key.replace(/_/g, " ")}</Text><Text style={styles.arabic}>{String(value)}</Text></View>)}
          </View>}
          <GrammarRow details={result?.grammar_details} />
          {section("Derivation", e?.derivation)}{section("Root meaning", e?.root_meaning)}
          {section("Semantic field", Array.isArray(e?.semantic_field) ? e.semantic_field.join(", ") : e?.semantic_field)}
          {section("Related loanwords", e?.related_loanwords?.join(", "))}{section("Cultural note", e?.cultural_note)}
          {showMnemonic(h) && section("Memory hook", h?.mnemonic)}
          {!!h?.cognates?.length && section("Cognates", h.cognates.map(c => `${c.lang}: ${c.word}${c.note ? ` — ${c.note}` : ""}`).join("\n"))}
          {!!h?.collocations?.length && <View style={styles.section}><Text style={styles.label}>Common phrases</Text>
            {h.collocations.map((c,i) => <View key={i} style={styles.phrase}><Text style={styles.arabic}>{c.ar}</Text><Text style={styles.text}>{c.en}</Text></View>)}
          </View>}
          {section("Usage context", h?.usage_context)}{section("Word fact", h?.fun_fact)}
          {result?.example_ar && <View style={styles.section}><Text style={styles.label}>Example</Text><Text style={styles.arabic}>{result.example_ar}</Text><Text style={styles.text}>{result.example_en}</Text></View>}
        </ScrollView>
      </View>
    </View>
  </Modal>;
}
const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: "center", alignItems: "center", padding: 12, backgroundColor: "rgba(0,0,0,0.48)" },
  card: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: 14, overflow: "hidden" },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingLeft: 16, paddingRight: 6 },
  title: { color: colors.text, fontSize: 18, fontWeight: "600" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  content: { paddingHorizontal: 16, paddingBottom: 18 },
  section: { marginTop: 16 },
  label: { color: colors.textSecondary, fontSize: 12, fontWeight: "600", marginBottom: 4 },
  text: { color: colors.text, fontSize: 15, lineHeight: 22 },
  arabic: { color: colors.arabic, fontSize: 24, lineHeight: 38, fontFamily: fontFamily.arabic, writingDirection: "rtl" },
  form: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  formLabel: { color: colors.textSecondary, fontSize: 12, flexShrink: 1 },
  phrase: { marginTop: 6 },
});
