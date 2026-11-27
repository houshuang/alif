import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fontFamily, arabicText } from "../theme";
import { ConfusionAnalysis, WordLookupResult } from "../types";
import { getCefrColor } from "../frequency";
import { bareArabicForm, bestVocalizedDisplayForm } from "../arabic-display";
import { dockPanelModel, FORM_ROLE, headlineMeanings, shortGloss } from "./dock-panel-model";
import { ChainRow, PhraseList, SenseList } from "./WordCardParts";

/** The word panel docked under a marked word in reading review (Stack layout,
 *  2026-11-25): header, derivation chain, meanings, other forms, set phrases, root words.
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

  const { currentKey, formName, chain, strip, family, phrases, senses } = dockPanelModel(result, surfaceForm, confusionData ?? null);
  const meaning = headlineMeanings(result.gloss_en, senses);
  // The parsed form (tense, person, number) when the analysis is sure; else the stored form's name.
  const formLine = confusionData?.form_description || formName;

  return (
    <View>
      <Pressable
        onPress={onMinimize}
        accessibilityRole="button"
        accessibilityLabel="Minimize word explanation"
        hitSlop={{ top: 10, bottom: 4, left: 60, right: 60 }}
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
          <Text style={s.gloss}>
            {meaning.main || "?"}
            {meaning.others.length > 0 ? <Text style={s.otherMeanings}>{` · ${meaning.others.join(" · ")}`}</Text> : null}
          </Text>
          <Text style={s.meta} numberOfLines={2}>
            {translit ? <Text style={s.metaTranslit}>{translit}</Text> : null}
            {result.pos ? ` · ${result.pos.replace(/_/g, " ")}` : ""}
            {result.cefr_level ? (
              <Text style={{ color: getCefrColor(result.cefr_level), fontWeight: "700" }}>
                {` · ${result.cefr_level.toUpperCase()}`}
              </Text>
            ) : null}
            {formLine ? <Text style={{ color: mark }}>{` · ${formLine}`}</Text> : null}
          </Text>
        </View>
        <Ionicons name="chevron-forward" size={16} color={colors.textSecondary} />
      </Pressable>

      {chain.length > 0 && (
        <View style={s.section}>
          <ChainRow chain={chain} highlight={mark} onNavigateToDetail={onNavigateToDetail} />
        </View>
      )}

      {senses.length > 0 && (
        <View style={s.section}>
          <SenseList senses={senses} />
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
          <PhraseList phrases={phrases} />
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
  grab: { alignItems: "center", paddingTop: 8, paddingBottom: 6 },
  grabBar: { width: 34, height: 4, borderRadius: 2, backgroundColor: "#3a3a5c" },
  head: { flexDirection: "row", alignItems: "center", gap: 10, paddingBottom: 4 },
  lemma: { ...arabicText(28, 42), color: colors.arabic, writingDirection: "rtl", flexShrink: 1 },
  headText: { flex: 1, minWidth: 90 },
  gloss: { color: colors.text, fontSize: 16, fontWeight: "700" },
  otherMeanings: { color: colors.textSecondary, fontSize: 13, fontWeight: "400" },
  meta: { color: colors.textSecondary, fontSize: 11.5, marginTop: 1 },
  metaTranslit: { fontFamily: fontFamily.translit },
  section: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    paddingVertical: 6,
  },
  strip: { flexDirection: "row-reverse", flexWrap: "wrap", columnGap: 14, rowGap: 4 },
  form: { alignItems: "center" },
  formAr: { ...arabicText(20, 34), color: colors.arabic },
  formRole: { color: colors.textSecondary, fontSize: 10.5, lineHeight: 13, marginTop: 1 },
  formEn: { color: "#b8b8cc" },
  rootHead: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  rootLetters: { ...arabicText(18, 30), color: colors.targetWord },
  rootMeaning: { color: colors.textSecondary, fontSize: 11, flexShrink: 1 },
  grid: { flexDirection: "row", flexWrap: "wrap" },
  sib: { width: "50%", flexDirection: "row", alignItems: "center", gap: 5, paddingRight: 6 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  sibAr: { ...arabicText(18, 30), color: colors.text },
  sibEn: { color: "#c8c8d8", fontSize: 11.5, flexShrink: 1 },
});
