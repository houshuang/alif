import { confusionSuggestions, isNamedMixUp } from "../confusion-suggestions";
import { ConfusionAnalysis } from "../../types";

const visual = (lemma_id: number) => ({ lemma_id, lemma_ar: `v${lemma_id}`, lemma_ar_bare: "", gloss_en: null, pos: null,
  edit_distance: 1, rasm_distance: 0, diff_positions: [], knowledge_state: null, key_forms: {} });
const phonetic = (lemma_id: number, phonetic_distance: number) => ({ lemma_id, lemma_ar: `p${lemma_id}`, gloss_en: null,
  phonetic_distance, confused_pairs: [], knowledge_state: null });

describe("confusionSuggestions", () => {
  it("puts exact sound-alikes first, drops the word itself and duplicates, and caps the list", () => {
    const data = { similar_words: [visual(1), visual(2), visual(9), visual(3)],
      phonetic_similar: [phonetic(4, 1), phonetic(5, 0), phonetic(2, 0)] } as unknown as ConfusionAnalysis;
    expect(confusionSuggestions(data, 9, 4).map(w => w.lemma_id)).toEqual([5, 2, 1, 3]);
    expect(confusionSuggestions(data, 9).map(w => w.lemma_id)).toEqual([5, 2, 1, 3, 4]);
    expect(confusionSuggestions(null, 9)).toEqual([]);
  });
});

describe("isNamedMixUp", () => {
  it("counts picked words and typed words, not forgetting notes", () => {
    const base = { failed_lemma_id: 1, candidates_shown: [] };
    expect(isNamedMixUp({ ...base, capture_method: "suggested_pick", confused_with_lemma_id: 2 })).toBe(true);
    expect(isNamedMixUp({ ...base, capture_method: "free_text", confused_with_text: "happy" })).toBe(true);
    expect(isNamedMixUp({ ...base, capture_method: "free_text", confused_with_text: " Forgot " })).toBe(false);
    expect(isNamedMixUp(undefined)).toBe(false);
  });
});
