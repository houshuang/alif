import React, { useEffect, useState } from "react";
import { Modal, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { WordLookupResult, ConfusionAnalysis, ConfusionCaptureIn, WordDetail, WordSentenceStat } from "../types";
import { colors, fontFamily, ltr } from "../theme";
import { getWordDetail } from "../api";
import { showMnemonic } from "../feature-flags";
import { GrammarRow, PatternExamples } from "../WordCardComponents";
import { getGrammarParticleInfo } from "../grammar-particles";
import { FocusWordMark } from "./WordInfoCard";
import { UNTRUSTED_FORM_KEYS } from "./surface-parts";

const bare = (s: string) => s.replace(/[\u064B-\u065F\u0670\u0640]/g, "").replace(/[،؛؟.,!?…:;()«»]/g, "");

function Disclosure({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return <View style={styles.disclosure}>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(!open)} style={styles.disclosureButton}>
      <Text style={styles.heading}>{title}</Text>
      <Ionicons name={open ? "chevron-up-outline" : "chevron-down-outline"} size={18} color={colors.textSecondary} />
    </Pressable>
    {open && <View style={styles.disclosureContent}>{children}</View>}
  </View>;
}

function Encounter({ sentence }: { sentence: WordSentenceStat }) {
  const surfaces = new Set(sentence.surface_forms.map(bare));
  return <View style={styles.encounter}>
    <Text style={styles.arabicSentence}>{sentence.sentence_arabic.split(/(\s+)/).map((part, i) =>
      <Text key={i} style={surfaces.has(bare(part)) ? styles.highlight : undefined}>{part}</Text>)}</Text>
    {!!sentence.sentence_english && <Text style={styles.text}>{ltr(sentence.sentence_english)}</Text>}
    <Text style={styles.meta}>{sentence.missed_count > 0 || sentence.confused_count > 0 ? "Previously difficult" : sentence.understood_count > 0 ? "Previously understood" : "Earlier encounter"}
      {sentence.last_reviewed_at ? ` · ${new Date(sentence.last_reviewed_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : ""}</Text>
  </View>;
}

export function WordDetailsModal({ visible, onClose, result, surfaceForm, surfaceTranslit, confusionData, capture,
  currentSentenceId, onWord, onRoot, onPattern }: {
  visible: boolean; onClose: () => void; result: WordLookupResult | null;
  surfaceForm: string | null; surfaceTranslit?: string | null; markState: FocusWordMark | null;
  confusionData: ConfusionAnalysis | null; capture?: ConfusionCaptureIn; currentSentenceId?: number | null;
  onWord: (id: number) => void; onRoot: (id: number) => void; onPattern: (wazn: string) => void;
}) {
  const { width, height } = useWindowDimensions();
  const [detail, setDetail] = useState<WordDetail | null>(null);
  const [historyState, setHistoryState] = useState<"loading" | "ready" | "error">("loading");
  const [retry, setRetry] = useState(0);
  const lemmaId = result?.lemma_id;
  useEffect(() => {
    if (!visible || lemmaId == null) return;
    let active = true;
    setDetail(null); setHistoryState("loading");
    getWordDetail(lemmaId).then(data => {
      if (active) { setDetail(data); setHistoryState("ready"); }
    }).catch(() => { if (active) setHistoryState("error"); });
    return () => { active = false; };
  }, [visible, lemmaId, retry]);
  const currentDetail = detail?.id === lemmaId ? detail : null;
  const particle = surfaceForm ? getGrammarParticleInfo(surfaceForm) : null;
  const e = result?.etymology_json, h = result?.memory_hooks_json;
  const analysis = confusionData?.surface_form === surfaceForm ? confusionData : null;
  const matchedKey = analysis?.decomposition?.matched_form_key ?? analysis?.morphology?.form_key;
  const explanation = analysis?.morphology?.explanation;
  const candidates = [...(confusionData?.similar_words ?? []), ...(confusionData?.phonetic_similar ?? [])];
  const confusor = candidates.find(w => w.lemma_id === capture?.confused_with_lemma_id);
  const previous = (currentDetail?.sentence_stats ?? []).filter(s => s.sentence_id !== currentSentenceId && s.seen_count > 0);
  const difficult = previous.find(s => s.missed_count > 0 || s.confused_count > 0);
  const successful = previous.find(s => s.understood_count > 0 && s.sentence_id !== difficult?.sentence_id);
  const encounters = [difficult, successful, ...previous].filter((s): s is WordSentenceStat => !!s)
    .filter((s, i, all) => all.findIndex(x => x.sentence_id === s.sentence_id) === i).slice(0, 2);
  const forms = Object.entries(result?.forms_json ?? {}).filter(([key, value]) => typeof value === "string" && value && !UNTRUSTED_FORM_KEYS.has(key) && !["gender", "verb_form"].includes(key));
  const section = (title: string, text: string | null | undefined) => text ? <View style={styles.note} key={title}>
    <Text style={styles.label}>{title}</Text><Text style={styles.text}>{ltr(text)}</Text>
  </View> : null;
  const navigate = (fn: () => void) => { onClose(); fn(); };
  return <Modal visible={visible} transparent animationType="none" onRequestClose={onClose}>
    <View style={styles.overlay}>
      <Pressable style={StyleSheet.absoluteFill} accessibilityRole="button" accessibilityLabel="Dismiss full word card" onPress={onClose} />
      <View accessibilityViewIsModal style={[styles.card, { width: Math.min(580, width - 24), maxHeight: height * 0.86 }]}>
        <View style={styles.header}>
          <Text style={styles.title}>Word details</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Close full word card" onPress={onClose} style={styles.close}>
            <Ionicons name="close-outline" size={25} color={colors.textSecondary} />
          </Pressable>
        </View>
        <ScrollView key={`${lemmaId}:${surfaceForm}`} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={styles.surface}>{surfaceForm}</Text>
          <Text style={styles.meaning}>{particle?.meaning ?? result?.gloss_en ?? "Word explanation"}</Text>
          {!!(surfaceTranslit || result?.transliteration) && <Text style={styles.pronunciation}>{surfaceTranslit || result?.transliteration}</Text>}
          <View style={styles.section}>
            <Text style={styles.heading}>Form in this sentence</Text>
            {!!result && <View style={styles.dictionaryRow}><Text style={styles.meta}>Dictionary form</Text><Text style={styles.arabic}>{result.lemma_ar}</Text><Text style={styles.meta}>{result.pos}</Text></View>}
            {!!explanation && <Text style={styles.text}>{ltr(explanation)}</Text>}
            {!!analysis?.decomposition && <Text style={styles.text}>{ltr([
              ...analysis.decomposition.prefix_clitics.map(c => `${c.text} (${c.label})`),
              analysis.decomposition.matched_form_label,
              ...analysis.decomposition.suffix_clitics.map(c => `${c.text} (${c.label})`),
            ].filter(Boolean).join(" + "))}</Text>}
            {particle && <Text style={styles.text}>{ltr(particle.description)}</Text>}
            <GrammarRow details={result?.grammar_details} />
            {forms.length > 0 && <View style={styles.forms}>{forms.map(([key, value]) => {
              const active = key === matchedKey || (!!surfaceForm && bare(String(value)) === bare(surfaceForm));
              return <View key={key} style={[styles.form, active && styles.activeForm]}>
                <Text style={styles.meta}>{key.replace(/_/g, " ")}{active ? " · here" : ""}</Text>
                <Text style={styles.arabic}>{String(value)}</Text>
                {!!result?.forms_translit?.[key] && <Text style={styles.meta}>{result.forms_translit[key]}</Text>}
              </View>;
            })}</View>}
          </View>
          {capture && <View style={styles.section}>
            <Text style={styles.heading}>You mixed this up with</Text>
            <View style={styles.comparison}>
              <View style={styles.comparisonWord}><Text style={styles.arabic}>{result?.lemma_ar}</Text><Text style={styles.text}>{result?.gloss_en}</Text><Text style={styles.meta}>This sentence</Text></View>
              <Ionicons name="swap-horizontal-outline" size={20} color={colors.textSecondary} />
              <View style={styles.comparisonWord}><Text style={confusor ? styles.arabic : styles.text}>{confusor?.lemma_ar ?? capture.confused_with_text ?? "Saved word"}</Text><Text style={styles.text}>{confusor?.gloss_en}</Text><Text style={styles.meta}>Your reported confusion</Text></View>
            </View>
          </View>}
          {(!!h?.collocations?.length || !!h?.usage_context || !!result?.example_ar || !!particle?.examples.length) && <View style={styles.section}>
            <Text style={styles.heading}>Phrases and usage</Text>
            {h?.collocations?.map((c, i) => <View key={i} style={styles.phrase}><Text style={styles.arabicSentence}>{c.ar}</Text><Text style={styles.text}>{ltr(c.en)}</Text></View>)}
            {!!h?.usage_context && <Text style={styles.text}>{ltr(h.usage_context)}</Text>}
            {result?.example_ar && <View style={styles.phrase}><Text style={styles.arabicSentence}>{result.example_ar}</Text><Text style={styles.text}>{ltr(result.example_en ?? "")}</Text></View>}
            {particle?.examples.map((x, i) => <View key={i} style={styles.phrase}><Text style={styles.arabicSentence}>{x.ar}</Text><Text style={styles.text}>{ltr(x.en)}</Text></View>)}
          </View>}
          <View style={styles.section}>
            <Text style={styles.heading}>Your previous encounters</Text>
            {historyState === "loading" ? <Text style={styles.meta}>Loading previous sentences…</Text> : historyState === "error" ?
              <Pressable accessibilityRole="button" onPress={() => setRetry(n => n + 1)} style={styles.retry}><Text style={styles.text}>History unavailable. Tap to retry.</Text></Pressable> :
              encounters.length ? encounters.map(s => <Encounter key={s.sentence_id} sentence={s} />) : <Text style={styles.meta}>No earlier reviewed sentences for this word yet.</Text>}
            {currentDetail?.source_info?.story_title && <Text style={styles.meta}>From {currentDetail.source_info.story_title}</Text>}
          </View>
          {(result?.root || result?.wazn || !!result?.root_family.length || !!result?.pattern_examples?.length || !!e?.derivation) && <Disclosure title="Root, pattern and related words">
            {result?.root && <Pressable accessibilityRole="button" onPress={() => result.root_id && navigate(() => onRoot(result.root_id!))} disabled={!result.root_id} style={styles.rootRow}><Text style={styles.arabic}>{result.root}</Text><Text style={styles.text}>{result.root_meaning || e?.root_meaning}</Text></Pressable>}
            {result?.wazn && <Pressable accessibilityRole="button" onPress={() => navigate(() => onPattern(result.wazn!))} style={styles.retry}><Text style={styles.text}>{result.wazn}{result.wazn_meaning ? ` — ${result.wazn_meaning}` : ""}</Text></Pressable>}
            {result?.root_family.map(w => <Pressable key={w.lemma_id} accessibilityRole="button" onPress={() => navigate(() => onWord(w.lemma_id))} style={styles.familyRow}><Text style={styles.arabic}>{w.lemma_ar}</Text><View style={styles.familyGloss}><Text style={styles.text}>{w.gloss_en}</Text><Text style={styles.meta}>{w.transliteration}{w.state === "known" ? " · known" : ""}</Text></View></Pressable>)}
            <PatternExamples examples={result?.pattern_examples} onPress={id => navigate(() => onWord(id))} />
            {section("Derivation", e?.derivation)}
          </Disclosure>}
          {(showMnemonic(h) || !!h?.cognates?.length || !!e?.semantic_field || !!e?.related_loanwords?.length || !!e?.cultural_note || !!h?.fun_fact || !!particle?.grammar_note) && <Disclosure title="Memory and language notes">
            {showMnemonic(h) && section("Memory hook", h?.mnemonic)}
            {section("Cognates", h?.cognates?.map(c => `${c.lang}: ${c.word}${c.note ? ` — ${c.note}` : ""}`).join("\n"))}
            {section("Semantic field", Array.isArray(e?.semantic_field) ? e.semantic_field.join(", ") : e?.semantic_field)}
            {section("Related loanwords", e?.related_loanwords?.join(", "))}{section("Cultural note", e?.cultural_note)}
            {section("Word fact", h?.fun_fact)}{section("Particle usage", particle?.grammar_note)}
          </Disclosure>}
          <Disclosure title="Learning history">
            {currentDetail && <Text style={styles.text}>{currentDetail.times_reviewed} reviews · {currentDetail.correct_count} correct{currentDetail.cefr_level ? ` · ${currentDetail.cefr_level}` : ""}{currentDetail.frequency_rank ? ` · frequency #${currentDetail.frequency_rank}` : ""}</Text>}
            <Text style={styles.meta}>Review outcomes include assisted recognition and sentence context. They do not measure unassisted reading fluency.</Text>
            <Pressable accessibilityRole="link" onPress={() => lemmaId != null && navigate(() => onWord(lemmaId))} style={styles.retry}><Text style={styles.link}>Open full word page</Text><Ionicons name="arrow-forward-outline" size={16} color={colors.accent} /></Pressable>
          </Disclosure>
        </ScrollView>
      </View>
    </View>
  </Modal>;
}
const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: "center", alignItems: "center", padding: 12, backgroundColor: "rgba(0,0,0,0.48)" },
  card: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: 14, overflow: "hidden" },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingLeft: 16, paddingRight: 6, borderBottomWidth: 1, borderBottomColor: colors.border },
  title: { color: colors.text, fontSize: 16, fontWeight: "600" },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  content: { paddingHorizontal: 16, paddingBottom: 18, paddingTop: 6 },
  surface: { color: colors.arabic, fontSize: 38, lineHeight: 58, fontFamily: fontFamily.arabic, textAlign: "center", writingDirection: "rtl" },
  meaning: { color: colors.text, fontSize: 20, fontWeight: "600", textAlign: "center" },
  pronunciation: { color: colors.textSecondary, fontSize: 14, textAlign: "center", marginTop: 4 },
  section: { marginTop: 22 },
  heading: { color: colors.text, fontSize: 15, fontWeight: "600", marginBottom: 6 },
  label: { color: colors.textSecondary, fontSize: 12, fontWeight: "600", marginBottom: 4 },
  text: { color: colors.text, fontSize: 14, lineHeight: 21 },
  meta: { color: colors.textSecondary, fontSize: 12, lineHeight: 18 },
  arabic: { color: colors.arabic, fontSize: 24, lineHeight: 38, fontFamily: fontFamily.arabic, writingDirection: "rtl" },
  arabicSentence: { color: colors.arabic, fontSize: 26, lineHeight: 42, fontFamily: fontFamily.arabic, writingDirection: "rtl", textAlign: "right" },
  dictionaryRow: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 8 },
  forms: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 8 },
  form: { paddingVertical: 4, paddingHorizontal: 8, borderRadius: 6 },
  activeForm: { backgroundColor: colors.surfaceLight },
  phrase: { marginBottom: 8 },
  comparison: { flexDirection: "row", alignItems: "center", gap: 8 },
  comparisonWord: { flex: 1, minWidth: 0 },
  encounter: { marginBottom: 14 },
  highlight: { color: colors.accent, textDecorationLine: "underline" },
  disclosure: { borderTopWidth: 1, borderTopColor: colors.border, marginTop: 12 },
  disclosureButton: { minHeight: 48, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  disclosureContent: { paddingBottom: 8 },
  note: { marginTop: 12 },
  retry: { minHeight: 44, flexDirection: "row", alignItems: "center", gap: 8 },
  rootRow: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 12, minHeight: 44 },
  familyRow: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 44, marginTop: 4 },
  familyGloss: { flex: 1 },
  link: { color: colors.accent, fontSize: 14 },
});
