import React, { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View, Modal, ScrollView, useWindowDimensions } from "react-native";
import { PairWord, UnresolvedConfusion } from "../confusion-learning";
import { confirmConfusor, getConfusionHelp, searchConfusors } from "../api";
import { confusionSuggestions, SuggestedWord } from "./confusion-suggestions";
import { colors, fontFamily, arabicText } from "../theme";
export default function ManualMatch({ capture, onSaved }: {capture: UnresolvedConfusion; onSaved: () => void}) {
  const [query, setQuery] = useState(capture.text), [options, setOptions] = useState<PairWord[]>(capture.candidates);
  const [busy, setBusy] = useState(false), [searching, setSearching] = useState(false), [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState<string | null>(null);
  const [similar, setSimilar] = useState<SuggestedWord[]>([]);
  useEffect(() => {
    let live = true;
    getConfusionHelp(capture.word.id, capture.word.arabic).then(data => { if (live) setSimilar(confusionSuggestions(data, capture.word.id)); });
    return () => { live = false; };
  }, [capture.word.id, capture.word.arabic]);
  async function search() {
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true); setError(null);
    try { setOptions(await searchConfusors(q)); setSearched(q); }
    catch { setError("Could not search. Try again when connected."); }
    finally { setSearching(false); }
  }
  async function confirm(id: number | null) { setBusy(true); setError(null); try { await confirmConfusor(capture.capture_id, id); onSaved(); } catch { setError("Could not save this match. Please retry."); } finally { setBusy(false); } }
  const results = options.filter(w => w.id !== capture.word.id);
  const shownIds = new Set(results.map(w => w.id));
  const lookalikes = similar.filter(w => !shownIds.has(w.lemma_id));
  return <View style={styles.row}>
    <Text style={styles.arabic}>{capture.word.arabic}</Text>
    <Text style={styles.text}>You entered “{capture.text}”</Text>
    <Text style={styles.meta}>{capture.state === "not_a_word" ? "Saved as a forgetting note. You can still choose a word below." : "Which word did you mean?"}</Text>
    {lookalikes.length > 0 && <>
      <Text style={styles.section}>Sounds or looks like {capture.word.arabic}</Text>
      {lookalikes.map(w => <Pressable key={`s${w.lemma_id}`} accessibilityRole="button" disabled={busy} onPress={() => confirm(w.lemma_id)} style={styles.option}><Text style={styles.arabicSmall}>{w.lemma_ar}</Text><Text style={styles.text}>{w.gloss_en ?? ""}</Text></Pressable>)}
    </>}
    <View style={styles.search}>
      <TextInput accessibilityLabel={`Find confusor for ${capture.word.arabic}`} value={query} onChangeText={setQuery} onSubmitEditing={search}
        placeholder="Meaning, Arabic or sound (saida)" placeholderTextColor={colors.textSecondary} returnKeyType="search" style={styles.input} />
      <Pressable accessibilityRole="button" accessibilityLabel="Search words" disabled={busy || searching} onPress={search} style={styles.searchButton}>
        {searching ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.link}>Search</Text>}
      </Pressable>
    </View>
    {searched !== null && !searching && results.length === 0 && <Text style={styles.meta}>No matches for “{searched}”</Text>}
    {results.map(w => <Pressable key={w.id} accessibilityRole="button" disabled={busy || searching} onPress={() => confirm(w.id)} style={[styles.option, searching && { opacity: 0.4 }]}>
      <Text style={styles.arabicSmall}>{w.arabic}</Text><Text style={styles.text}>{w.gloss}</Text>{!!w.transliteration && <Text style={styles.meta}>{w.transliteration}</Text>}
    </Pressable>)}
    <Pressable accessibilityRole="button" disabled={busy} onPress={() => confirm(null)} style={styles.action}><Text style={styles.meta}>I forgot it; no other word</Text></Pressable>
    {!!error && <Text style={styles.error}>{error}</Text>}
  </View>;
}

export function ConfusorPrompt({capture, onSaved, onClose}: {capture: UnresolvedConfusion; onSaved: () => void; onClose: () => void}) {
  const {height,width}=useWindowDimensions();
  return <Modal transparent animationType="none" onRequestClose={onClose}><View style={styles.overlay}>
    <View accessibilityViewIsModal style={[styles.card,{maxHeight:height*.88,width:Math.min(520,width-24)}]}>
      <ScrollView contentContainerStyle={{padding:16}}><Text style={styles.title}>Which word did you mean?</Text>
        <ManualMatch capture={capture} onSaved={onSaved} />
        <Pressable accessibilityRole="button" onPress={onClose} style={styles.action}><Text style={styles.link}>Match it later · back to reading</Text></Pressable>
      </ScrollView>
    </View>
  </View></Modal>;
}
const styles=StyleSheet.create({
  overlay:{flex:1,backgroundColor:"rgba(0,0,0,.55)",justifyContent:"center",alignItems:"center"},
  card:{backgroundColor:colors.surface,borderWidth:1,borderColor:colors.border,borderRadius:14,overflow:"hidden"},
  title:{color:colors.text,fontSize:18,fontWeight:"600"}, row:{marginTop:12},
  arabic:{ ...arabicText(30, 46), color:colors.arabic,writingDirection:"rtl",textAlign:"right" },
  arabicSmall:{ ...arabicText(25, 40), color:colors.arabic,writingDirection:"rtl" },
  text:{color:colors.text,fontSize:15,lineHeight:23},meta:{color:colors.textSecondary,fontSize:12,lineHeight:18,marginTop:5},
  search:{flexDirection:"row",gap:12,marginTop:10}, input:{flex:1,minHeight:44,paddingHorizontal:10,color:colors.text,borderWidth:1,borderColor:colors.border,borderRadius:6},
  action:{minHeight:44,justifyContent:"center"},searchButton:{minHeight:44,minWidth:64,alignItems:"center",justifyContent:"center"},
  section:{color:colors.textSecondary,fontSize:12,fontWeight:"600",marginTop:14},link:{color:colors.accent,fontSize:15},error:{color:colors.missed,marginTop:10},
  option:{minHeight:48,flexDirection:"row",alignItems:"center",gap:12,flexWrap:"wrap"},
});
