import { bareArabicForm } from "../arabic-display";
import type { ConfusionAnalysis, WordLookupResult } from "../types";
import { UNTRUSTED_FORM_KEYS } from "./surface-parts";

/** What the reading-dock word panel (DockWordPanel) shows, chosen without React
 *  so it can be tested: the sentence's form, the chain, the other forms, root
 *  words (minus anything already in the chain or forms) and phrases. */

const ACTIVE_STATES = new Set(["learning", "acquiring", "lapsed"]);

function rank(state: string): number {
  if (state === "known") return 0;
  return ACTIVE_STATES.has(state) ? 1 : 2;
}

const FORM_ORDER: Record<string, string[]> = {
  verb: ["present", "masdar", "active_participle", "passive_participle"],
  noun: ["plural", "feminine"],
  adj: ["feminine", "plural", "elative"],
  adjective: ["feminine", "plural", "elative"],
};
/** Short role under each form in the strip. */
export const FORM_ROLE: Record<string, string> = {
  present: "present",
  masdar: "-ing",
  active_participle: "doer",
  passive_participle: "done",
  plural: "pl",
  feminine: "f",
  elative: "more",
  imperative: "imp",
};
/** Which form the sentence uses, for the header's meta line. */
const FORM_NAME: Record<string, string> = {
  present: "present",
  masdar: "verbal noun",
  active_participle: "participle",
  passive_participle: "passive participle",
  plural: "plural",
  feminine: "feminine",
  elative: "comparative",
  imperative: "imperative",
};
const ROOT_WORDS = 6;

export function shortGloss(gloss: string | null | undefined): string {
  return (gloss ?? "").split(/[,;]/)[0].trim();
}

/** Pure selection behind the panel, kept separate so it can be tested. */
export function dockPanelModel(
  result: WordLookupResult,
  surfaceForm: string | null,
  confusionData: ConfusionAnalysis | null,
) {
  const card = result.word_card ?? null;
  const forms = (result.forms_json ?? {}) as Record<string, string | undefined>;
  // Which stored form the sentence shows: the decomposition when it belongs to
  // this surface and is trusted, otherwise a bare match against forms_json.
  const analysis = confusionData && confusionData.surface_form === surfaceForm ? confusionData : null;
  const decompKey = analysis?.decomposition?.matched_form_key ?? null;
  let currentKey: string | null = decompKey && !UNTRUSTED_FORM_KEYS.has(decompKey) ? decompKey : null;
  if (!currentKey && surfaceForm) {
    const surfBare = bareArabicForm(surfaceForm);
    for (const [key, value] of Object.entries(forms)) {
      if (typeof value === "string" && FORM_ROLE[key] && bareArabicForm(value) === surfBare) {
        currentKey = key;
        break;
      }
    }
  }
  const formName = currentKey ? FORM_NAME[currentKey] ?? null : null;

  const chain = card && card.chain.length >= 2 ? card.chain : [];
  const chainBares = new Set(chain.map((n) => bareArabicForm(n.ar)));
  const chainIds = new Set(chain.map((n) => n.lemma_id).filter((id): id is number => id != null));

  const order = FORM_ORDER[(result.pos || "").toLowerCase()] ?? ["plural", "feminine"];
  const strip = order
    .filter((key) => typeof forms[key] === "string" && forms[key])
    .map((key) => ({ key, ar: forms[key] as string, en: card?.forms?.[key] ?? null }))
    .filter((f) => !chainBares.has(bareArabicForm(f.ar)));
  const stripBares = new Set(strip.map((f) => bareArabicForm(f.ar)));

  const family = result.root_family
    .filter((s) => s.lemma_id !== result.lemma_id && !chainIds.has(s.lemma_id))
    .filter((s) => !chainBares.has(bareArabicForm(s.lemma_ar)) && !stripBares.has(bareArabicForm(s.lemma_ar)))
    .sort((a, b) => rank(a.state) - rank(b.state))
    .slice(0, ROOT_WORDS);

  const phrases = card?.phrases ?? [];
  const senses = card?.senses ?? [];

  return { currentKey, formName, chain, strip, family, phrases, senses };
}


/** The meaning line for a word: with a senses list (most common first), the most
 *  common meaning leads and the others follow; otherwise the stored gloss as is. */
export function headlineMeanings(
  gloss: string | null | undefined,
  senses: { en: string }[] | null | undefined,
): { main: string; others: string[] } {
  if (senses && senses.length >= 2) {
    return { main: senses[0].en, others: senses.slice(1).map((s) => s.en) };
  }
  return { main: gloss ?? "", others: [] };
}
