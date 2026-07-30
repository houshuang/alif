import {
  itemHasExactReintroTest,
  reintrosWithinVisibleWindow,
} from "../reintro-flow";
import { ReintroCard, SentenceReviewItem } from "../../types";

function item(
  sentenceId: number,
  lemmaId: number,
  isDue = true,
): SentenceReviewItem {
  return {
    card_type: "sentence",
    sentence_id: sentenceId,
    sentence_ids: [sentenceId],
    arabic_text: "اختبار",
    english_translation: "test",
    transliteration: null,
    audio_url: null,
    primary_lemma_id: lemmaId,
    primary_lemma_ar: "اختبار",
    primary_gloss_en: "test",
    grammar_features: [],
    selection_info: {
      reason: "test",
      due_lemma_ids: isDue ? [lemmaId] : [],
    },
    words: [{
      sentence_word_id: sentenceId,
      sentence_id: sentenceId,
      position: 0,
      lemma_id: lemmaId,
      canonical_lemma_id: lemmaId,
      surface_form: "اختبار",
      gloss_en: "test",
      stability: 1,
      is_due: isDue,
      is_function_word: false,
      knowledge_state: "learning",
      root: null,
      root_meaning: null,
      root_id: null,
      frequency_rank: null,
      cefr_level: null,
    }],
  };
}

function card(lemmaId: number, sentenceId?: number): ReintroCard {
  return {
    lemma_id: lemmaId,
    lemma_ar: "اختبار",
    gloss_en: "test",
    pos: "noun",
    transliteration: null,
    root: null,
    root_meaning: null,
    root_id: null,
    forms_json: null,
    example_ar: null,
    example_en: null,
    audio_url: null,
    grammar_features: [],
    grammar_details: [],
    times_seen: 5,
    root_family: [],
    test_sentence_id: sentenceId ?? null,
    max_test_card_distance: 5,
  };
}

describe("reintro sentence reservation", () => {
  test("requires metadata for an exact due-word sentence test", () => {
    const reviewItem = item(101, 1);

    expect(itemHasExactReintroTest(reviewItem, card(1))).toBe(false);
    expect(itemHasExactReintroTest(reviewItem, card(1, 101))).toBe(true);
    expect(itemHasExactReintroTest(item(101, 1, false), card(1, 101))).toBe(false);
  });

  test("drops a pairing beyond five visible cards", () => {
    const items = [item(101, 1), item(102, 2), item(103, 3)];
    const cards = [card(1, 101), card(2, 102), card(3, 103)];
    const slots = [
      { type: "experiment_intro" },
      { type: "sentence", itemIndex: 1 },
      { type: "sentence", itemIndex: 2 },
      { type: "sentence", itemIndex: 0 },
    ];

    expect(reintrosWithinVisibleWindow(items, cards, slots)).toEqual(cards.slice(1));
  });
});
