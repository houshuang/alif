import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { colors, arabicText } from "../theme";
import { WordCard } from "../types";

/** Word-card sections shared by the reading dock and the intro card. */

export function ChainRow({ chain, highlight, onNavigateToDetail }: {
  chain: WordCard["chain"];
  highlight: string;
  onNavigateToDetail?: (lemmaId: number) => void;
}) {
  return (
    <View style={s.chain}>
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
              <Text style={[s.nodeAr, isThis && { color: highlight }]}>{node.ar}</Text>
              <Text style={s.nodeEn}>{node.en}</Text>
              <Text style={s.nodeTag}>{node.tag}</Text>
            </Pressable>
          </React.Fragment>
        );
      })}
    </View>
  );
}

/** One row per meaning, most common first (highlighted): the meaning, its example
 *  and the example's translation. */
export function SenseList({ senses }: { senses: NonNullable<WordCard["senses"]> }) {
  return (
    <View style={s.senses}>
      {senses.map((sense, i) => (
        <View key={`${sense.en}-${i}`} style={s.sense}>
          <View style={s.senseText}>
            <Text style={[s.senseEn, i === 0 && s.senseMain]}>{sense.en}</Text>
            <Text style={s.senseExampleEn}>{sense.example_en}</Text>
          </View>
          <Text style={s.senseAr}>{sense.ar}</Text>
        </View>
      ))}
    </View>
  );
}

export function PhraseList({ phrases }: { phrases: WordCard["phrases"] }) {
  return (
    <View>
      {phrases.map((p) => (
        <View key={p.ar} style={s.phrase}>
          <Text style={s.phraseEn}>{p.en}</Text>
          <Text style={s.phraseAr}>{p.ar}</Text>
        </View>
      ))}
    </View>
  );
}

const s = StyleSheet.create({
  chain: { flexDirection: "row-reverse", alignItems: "flex-start", justifyContent: "flex-end", gap: 2 },
  node: { flexShrink: 1, flexBasis: 0, flexGrow: 1, maxWidth: 120, alignItems: "center" },
  nodeAr: { ...arabicText(20, 34), color: colors.arabic, textAlign: "center" },
  nodeEn: { color: "#b8b8cc", fontSize: 10.5, lineHeight: 13, textAlign: "center", marginTop: 1 },
  nodeTag: { color: colors.textSecondary, fontSize: 9.5, letterSpacing: 0.4, textAlign: "center" },
  arrow: { color: "#4a4a6a", fontSize: 13, paddingTop: 10 },
  senses: { gap: 4 },
  sense: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  senseText: { flexShrink: 1 },
  senseEn: { color: colors.textSecondary, fontSize: 13, fontWeight: "600" },
  senseMain: { color: colors.accent },
  senseExampleEn: { color: "#b8b8cc", fontSize: 11.5, lineHeight: 15 },
  senseAr: { ...arabicText(19, 33), color: colors.arabic, writingDirection: "rtl", flexShrink: 1, textAlign: "right" },
  phrase: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  phraseEn: { color: "#c8c8d8", fontSize: 12, flexShrink: 1 },
  phraseAr: { ...arabicText(19, 33), color: colors.text, writingDirection: "rtl", flexShrink: 1, textAlign: "right" },
});
