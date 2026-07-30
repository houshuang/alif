import { ReintroCard, SentenceReviewItem } from "../types";

export const REINTRO_TEST_WINDOW_CARDS = 5;

export function itemHasExactReintroTest(
  item: SentenceReviewItem,
  card: ReintroCard,
): boolean {
  if (card.test_sentence_id == null) return false;
  const dueIds = new Set(item.selection_info?.due_lemma_ids ?? []);
  if (!dueIds.has(card.lemma_id)) return false;
  return item.words.some((word) => {
    if (!word.is_due || word.sentence_id !== card.test_sentence_id) return false;
    return (
      word.lemma_id === card.lemma_id ||
      word.canonical_lemma_id === card.lemma_id
    );
  });
}

export function reintrosWithinVisibleWindow(
  items: SentenceReviewItem[],
  cards: ReintroCard[],
  slots: Array<{ type: string; itemIndex?: number }>,
): ReintroCard[] {
  return cards.filter((card, cardIndex) => {
    const testSlotIndex = slots.findIndex(
      (slot) =>
        slot.type === "sentence" &&
        typeof slot.itemIndex === "number" &&
        itemHasExactReintroTest(items[slot.itemIndex], card),
    );
    if (testSlotIndex < 0) return false;
    const visibleDistance =
      cards.length - cardIndex - 1 + testSlotIndex + 1;
    const allowedDistance = Math.min(
      REINTRO_TEST_WINDOW_CARDS,
      card.max_test_card_distance ?? REINTRO_TEST_WINDOW_CARDS,
    );
    return visibleDistance <= allowedDistance;
  });
}
