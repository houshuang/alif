import React, { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View, Modal, ScrollView, useWindowDimensions } from "react-native";
import { PairWord, UnresolvedConfusion } from "../confusion-learning";
import { confirmConfusor, searchConfusors } from "../api";
import { colors, fontFamily } from "../theme";
export default function ManualMatch({ capture, onSaved }: {capture: UnresolvedConfusion; onSaved: () => void}) {
  const [query, setQuery] = useState(capture.text), [options, setOptions] = useState<PairWord[]>(capture.candidates);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  async function search() { setBusy(true); setError(null); try { setOptions(await searchConfusors(query)); } catch { setError("Could not search. Try again when connected."); } finally { setBusy(false); } }
  async function confirm(id: number | null) { setBusy(true); setError(null); try { await confirmConfusor(capture.capture_id, id); onSaved(); } catch { setError("Could not save this match. Please retry."); } finally { setBusy(false); } }
  return <View style={styles.row}>
    <Text style={styles.arabic}>{capture.word.arabic}</Text>
    <Text style={styles.text}>You entered “{capture.text}”</Text>
    <Text style={styles.meta}>{capture.state === "not_a_word" ? "Saved as a forgetting note. You can still choose a word below." : "Which word did you mean?"}</Text>
    <View style={styles.search}><TextInput accessibilityLabel={`Find confusor for ${capture.word.arabic}`} value={query} onChangeText={setQuery} onSubmitEditing={search} style={styles.input} /><Pressable accessibilityRole="button" disabled={busy} onPress={search} style={styles.action}><Text style={styles.link}>Search</Text></Pressable></View>
    {options.filter(w => w.id !== capture.word.id).map(w => <Pressable key={w.id} accessibilityRole="button" disabled={busy} onPress={() => confirm(w.id)} style={styles.option}><Text style={styles.arabicSmall}>{w.arabic}</Text><Text style={styles.text}>{w.gloss}</Text></Pressable>)}
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
  arabic:{color:colors.arabic,fontFamily:fontFamily.arabic,fontSize:30,lineHeight:46,writingDirection:"rtl",textAlign:"right"},
  arabicSmall:{color:colors.arabic,fontFamily:fontFamily.arabic,fontSize:25,lineHeight:40,writingDirection:"rtl"},
  text:{color:colors.text,fontSize:15,lineHeight:23},meta:{color:colors.textSecondary,fontSize:12,lineHeight:18,marginTop:5},
  search:{flexDirection:"row",gap:12,marginTop:10}, input:{flex:1,minHeight:44,paddingHorizontal:10,color:colors.text,borderWidth:1,borderColor:colors.border,borderRadius:6},
  action:{minHeight:44,justifyContent:"center"},link:{color:colors.accent,fontSize:15},error:{color:colors.missed,marginTop:10},
  option:{minHeight:48,flexDirection:"row",alignItems:"center",gap:12,flexWrap:"wrap"},
});
