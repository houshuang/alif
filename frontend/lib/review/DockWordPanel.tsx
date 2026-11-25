import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily, arabicText } from "../theme";
import { ConfusionAnalysis, WordLookupResult } from "../types";
import { getCefrColor } from "../frequency";
import { bareArabicForm, bestVocalizedDisplayForm } from "../arabic-display";
import { dockPanelModel, FORM_ROLE, shortGloss } from "./dock-panel-model";

/** The word panel docked under a marked word in reading review (Stack layout,
 *  2026-11-25): header, derivation chain, other forms, set phrases, root words.
 *  No row labels; each section is recognisable from its content. The chain,
 *  form glosses and phrases come from `word_card`, written lazily by the
 *  backend; until a word has one, the panel shows forms and root words only. */

type Mark = "missed" | "did_not_recognize";

const DOT: Record<string, string> = {
  known: colors.gotIt,
  learning: colors.stateLearning,
  acquiring: colors.stateLearning,
  lapsed: colors.stateLearning,
};

export default function DockWordPanel({
  result,
  surfaceForm,
  surfaceTranslit,
  markState,
  confusionData,
  onMinimize,
  onOpenDetails,
  onNavigateToDetail,
  onNavigateToRoot,
}: {
  result: WordLookupResult;
  surfaceForm: string | null;
  surfaceTranslit?: string | null;
  markState: Mark | null;
  confusionData?: ConfusionAnalysis | null;
  onMinimize?: () => void;
  onOpenDetails?: () => void;
  onNavigateToDetail?: (lemmaId: number) => void;
  onNavigateToRoot?: (rootId: number) => void;
}) {
  const mark = markState === "missed" ? colors.missed : colors.confused;

  const surfaceIsLemma = !!surfaceForm && bareArabicForm(surfaceForm) === bareArabicForm(result.lemma_ar);
  const display = bestVocalizedDisplayForm({
    lemmaAr: result.lemma_ar,
    lemmaTransliteration: result.transliteration,
    surfaceForm: surfaceIsLemma ? surfaceForm : null,
    surfaceTranslit: surfaceIsLemma ? surfaceTranslit : null,
    forms: result.forms_json,
    formsTranslit: result.forms_translit,
  });
  const lemmaAr = display.arabic || result.lemma_ar;
  const translit = display.transliteration || result.transliteration;

  const { currentKey, formName, chain, strip, family, phrases } = dockPanelModel(result, surfaceForm, confusionData ?? null);

  return (
    <View>
      <Pressable
        onPress={onMinimize}
        accessibilityRole="button"
        accessibilityLabel="Minimize word explanation"
        hitSlop={{ top: 6, bottom: 2, left: 40, right: 40 }}
        style={s.grab}
      >
        <View style={s.grabBar} />
      </Pressable>

      <Pressable
        onPress={onOpenDetails}
        accessibilityRole="button"
        accessibilityLabel="Open full word card"
        style={s.head}
      >
        <Text style={s.lemma}>{lemmaAr}</Text>
        <View style={s.headText}>
          <Text style={s.gloss}>{result.gloss_en ?? "?"}</Text>
          <Text style={s.meta} numberOfLines={2}>
            {translit ? <Text style={s.metaTranslit}>{translit}</Text> : null}
            {result.pos ? ` · ${result.pos.replace(/_/g, " ")}` : ""}
            {result.cefr_level ? (
              <Text style={{ color: getCefrColor(result.cefr_level), fontWeight: "700" }}>
                {` · ${result.cefr_level.toUpperCase()}`}
              </Text>
            ) : null}
            {formName ? <Text style={{ color: mark }}>{` · ${formName}`}</Text> : null}
          </Text>
        </View>
        <Ionicons name="chevron-forward" size={16} color={colors.textSecondary} />
      </Pressable>

      {chain.length > 0 && (
        <View style={[s.section, s.chain]}>
          {chain.map((node, i) => {
            const isThis = i === chain.length - 1;
            const tappable = !isThis && node.lemma_id != null && !!onNavigateToDetail;
            return (
              <React.Fragment key={`${node.ar}-${i}`}>
                {i > 0 && <Text style={s.arrow}>←</Text>}
                <Pressable
                  style={s.node}
                  disabled={!tappable}
                  onPress={tappable ? () => onNavigateToDetail!(node.lemma_id!) : undefined}
                >
                  <Text style={[s.nodeAr, isThis && { color: mark }]}>{node.ar}</Text>
                  <Text style={s.nodeEn}>{node.en}</Text>
                  <Text style={s.nodeTag}>{node.tag}</Text>
                </Pressable>
              </React.Fragment>
            );
          })}
        </View>
      )}

      {strip.length > 0 && (
        <View style={[s.section, s.strip]}>
          {strip.map((f) => (
            <View key={f.key} style={s.form}>
              <Text style={[s.formAr, f.key === currentKey && { color: mark }]}>{f.ar}</Text>
              <Text style={s.formRole}>
                {FORM_ROLE[f.key]}
                {f.en ? <Text style={s.formEn}>{` · ${f.en}`}</Text> : null}
              </Text>
            </View>
          ))}
        </View>
      )}

      {phrases.length > 0 && (
        <View style={s.section}>
          {phrases.map((p) => (
            <View key={p.ar} style={s.phrase}>
              <Text style={s.phraseEn}>{p.en}</Text>
              <Text style={s.phraseAr}>{p.ar}</Text>
            </View>
          ))}
        </View>
      )}

      {result.root && (
        <View style={s.section}>
          <Pressable
            style={s.rootHead}
            disabled={!onNavigateToRoot || !result.root_id}
            onPress={onNavigateToRoot && result.root_id ? () => onNavigateToRoot(result.root_id!) : undefined}
          >
            <Text style={s.rootLetters}>{result.root}</Text>
            {result.root_meaning ? <Text style={s.rootMeaning} numberOfLines={1}>{result.root_meaning}</Text> : null}
          </Pressable>
          {family.length > 0 && (
            <View style={s.grid}>
              {family.map((w) => (
                <Pressable
                  key={w.lemma_id}
                  style={s.sib}
                  disabled={!onNavigateToDetail}
                  onPress={onNavigateToDetail ? () => onNavigateToDetail(w.lemma_id) : undefined}
                >
                  <View style={[s.dot, { backgroundColor: DOT[w.state] ?? colors.stateEncountered }]} />
                  <Text style={s.sibAr}>{w.lemma_ar}</Text>
                  <Text style={s.sibEn} numberOfLines={1}>{shortGloss(w.gloss_en) || "?"}</Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  grab: { alignItems: "center", paddingTop: 6, paddingBottom: 2 },
  grabBar: { width: 34, height: 4, borderRadius: 2, backgroundColor: "#3a3a5c" },
  head: { flexDirection: "row", alignItems: "center", gap: 10, paddingBottom: 4 },
  lemma: { ...arabicText(28, 42), color: colors.arabic, writingDirection: "rtl", flexShrink: 1 },
  headText: { flex: 1, minWidth: 90 },
  gloss: { color: colors.text, fontSize: 16, fontWeight: "700" },
  meta: { color: colors.textSecondary, fontSize: 11.5, marginTop: 1 },
  metaTranslit: { fontFamily: fontFamily.translit },
  section: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    paddingVertical: 6,
  },
  chain: { flexDirection: "row-reverse", alignItems: "flex-start", justifyContent: "flex-end", gap: 2 },
  node: { flexShrink: 1, flexBasis: 0, flexGrow: 1, maxWidth: 120, alignItems: "center" },
  nodeAr: { ...arabicText(20, 34), color: colors.arabic, textAlign: "center" },
  nodeEn: { color: "#b8b8cc", fontSize: 10.5, lineHeight: 13, textAlign: "center", marginTop: 1 },
  nodeTag: { color: colors.textSecondary, fontSize: 9.5, letterSpacing: 0.4, textAlign: "center" },
  arrow: { color: "#4a4a6a", fontSize: 13, paddingTop: 10 },
  strip: { flexDirection: "row-reverse", flexWrap: "wrap", columnGap: 14, rowGap: 4 },
  form: { alignItems: "center" },
  formAr: { ...arabicText(20, 34), color: colors.arabic },
  formRole: { color: colors.textSecondary, fontSize: 10.5, lineHeight: 13, marginTop: 1 },
  formEn: { color: "#b8b8cc" },
  phrase: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  phraseEn: { color: "#c8c8d8", fontSize: 12, flexShrink: 1 },
  phraseAr: { ...arabicText(19, 33), color: colors.text, writingDirection: "rtl", flexShrink: 1, textAlign: "right" },
  rootHead: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  rootLetters: { ...arabicText(18, 30), color: colors.targetWord },
  rootMeaning: { color: colors.textSecondary, fontSize: 11, flexShrink: 1 },
  grid: { flexDirection: "row", flexWrap: "wrap" },
  sib: { width: "50%", flexDirection: "row", alignItems: "center", gap: 5, paddingRight: 6 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  sibAr: { ...arabicText(18, 30), color: colors.text },
  sibEn: { color: "#c8c8d8", fontSize: 11.5, flexShrink: 1 },
});
