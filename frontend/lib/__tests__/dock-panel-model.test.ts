import { dockPanelModel, headlineMeanings } from "../review/dock-panel-model";
import type { WordLookupResult } from "../types";

function result(over: Partial<WordLookupResult> = {}): WordLookupResult {
  return {
    lemma_id: 10,
    lemma_ar: "اِسْتَخْدَمَ",
    gloss_en: "to use",
    transliteration: "istakhdama",
    root: "خ.د.م",
    root_meaning: "serving",
    root_id: 3,
    pos: "verb",
    forms_json: {
      present: "يَسْتَخْدِمُ",
      masdar: "اِسْتِخْدَام",
      active_participle: "مُسْتَخْدِم",
    },
    example_ar: null,
    example_en: null,
    grammar_details: [],
    frequency_rank: null,
    cefr_level: "A2",
    root_family: [
      { lemma_id: 1, lemma_ar: "خَدَمَ", gloss_en: "to serve", pos: "verb", transliteration: null, state: "known" },
      { lemma_id: 2, lemma_ar: "خَادِم", gloss_en: "servant", pos: "noun", transliteration: null, state: "new" },
      { lemma_id: 4, lemma_ar: "خِدْمَة", gloss_en: "service", pos: "noun", transliteration: null, state: "learning" },
      { lemma_id: 5, lemma_ar: "اِسْتِخْدَام", gloss_en: "use", pos: "noun", transliteration: null, state: "known" },
    ],
    ...over,
  };
}

const card = {
  version: 1,
  chain: [
    { ar: "خَدَمَ", en: "to serve", tag: "I", lemma_id: 1 },
    { ar: "اِسْتَخْدَمَ", en: "to seek service: use", tag: "X", lemma_id: 10 },
    { ar: "مُسْتَخْدِم", en: "user", tag: "doer", lemma_id: null },
  ],
  forms: { masdar: "use, usage", active_participle: "user" },
  phrases: [{ ar: "سَهْلُ الاِسْتِخْدَامِ", en: "easy to use", attested: true }],
};

describe("dockPanelModel", () => {
  it("without a word card shows forms and root words only", () => {
    const m = dockPanelModel(result(), "يَسْتَخْدِمُونَهَا", null);
    expect(m.chain).toEqual([]);
    expect(m.phrases).toEqual([]);
    expect(m.strip.map((f) => f.key)).toEqual(["present", "masdar", "active_participle"]);
    expect(m.strip.every((f) => f.en === null)).toBe(true);
  });

  it("drops forms and root words already in the chain or the forms strip", () => {
    const m = dockPanelModel(result({ word_card: card }), null, null);
    // مُسْتَخْدِم is in the chain, so it leaves the strip; خَدَمَ leaves the root list.
    expect(m.strip.map((f) => f.key)).toEqual(["present", "masdar"]);
    expect(m.strip.find((f) => f.key === "masdar")?.en).toBe("use, usage");
    // اِسْتِخْدَام is in the strip, so the root list does not repeat it; known before learning before new.
    expect(m.family.map((w) => w.lemma_id)).toEqual([4, 2]);
  });

  it("names the form the sentence uses from a bare match", () => {
    const m = dockPanelModel(result(), "يستخدم", null);
    expect(m.currentKey).toBe("present");
    expect(m.formName).toBe("present");
  });

  it("ignores a decomposition that belongs to another surface", () => {
    const analysis = {
      surface_form: "other",
      decomposition: { prefix_clitics: [], stem: "", suffix_clitics: [], matched_form_key: "masdar", matched_form_label: "" },
    } as never;
    expect(dockPanelModel(result(), "يستخدم", analysis).currentKey).toBe("present");
  });

  it("ignores a one-step chain", () => {
    const m = dockPanelModel(result({ word_card: { ...card, chain: card.chain.slice(0, 1) } }), null, null);
    expect(m.chain).toEqual([]);
  });
});

describe("headlineMeanings", () => {
  it("leads with the most common sense and keeps the gloss when there are no senses", () => {
    const senses = [{ en: "to solve" }, { en: "to settle in, arrive" }, { en: "to befall" }];
    expect(headlineMeanings("to place or rank; to settle in; to solve; to befall", senses))
      .toEqual({ main: "to solve", others: ["to settle in, arrive", "to befall"] });
    expect(headlineMeanings("solution", [])).toEqual({ main: "solution", others: [] });
    expect(headlineMeanings(null, undefined)).toEqual({ main: "", others: [] });
  });
});
