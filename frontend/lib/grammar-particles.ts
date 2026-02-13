/**
 * Grammar particle reference data.
 * These are core structural words excluded from FSRS scheduling.
 * Tapping shows grammar info instead of word lookup.
 */

export interface GrammarParticleInfo {
  ar: string;
  transliteration: string;
  meaning: string;
  category: string;
  description: string;
  examples: { ar: string; en: string }[];
  grammar_note: string;
}

const GRAMMAR_PARTICLES: Record<string, GrammarParticleInfo> = {
  "في": {
    ar: "في",
    transliteration: "fī",
    meaning: "in, at",
    category: "Preposition",
    description: "Locative and temporal preposition. One of the most common words in Arabic.",
    examples: [
      { ar: "في البَيْتِ", en: "in the house" },
      { ar: "في الصَّباحِ", en: "in the morning" },
    ],
    grammar_note: "Takes مجرور (genitive) — the noun after في gets kasra ending.",
  },
  "من": {
    ar: "من",
    transliteration: "min",
    meaning: "from, of",
    category: "Preposition",
    description: "Indicates origin, source, or partitive. Also used in comparatives.",
    examples: [
      { ar: "من المَدْرَسَةِ", en: "from the school" },
      { ar: "أكبَرُ من", en: "bigger than" },
    ],
    grammar_note: "Takes مجرور (genitive). In comparatives: أفعل + من.",
  },
  "على": {
    ar: "على",
    transliteration: "ʿalā",
    meaning: "on, upon, about",
    category: "Preposition",
    description: "Indicates position on a surface, obligation, or topic.",
    examples: [
      { ar: "على الطّاوِلَةِ", en: "on the table" },
      { ar: "عَلَيْهِ أن", en: "he must" },
    ],
    grammar_note: "Takes مجرور (genitive). With pronoun suffixes: عليّ، عليك، عليه.",
  },
  "إلى": {
    ar: "إلى",
    transliteration: "ilā",
    meaning: "to, toward",
    category: "Preposition",
    description: "Indicates direction or destination.",
    examples: [
      { ar: "إلى البَيْتِ", en: "to the house" },
      { ar: "بالإضافَةِ إلى", en: "in addition to" },
    ],
    grammar_note: "Takes مجرور (genitive). With pronoun suffixes: إليّ، إليك، إليه.",
  },
  "عن": {
    ar: "عن",
    transliteration: "ʿan",
    meaning: "about, from, away from",
    category: "Preposition",
    description: "Indicates topic, separation, or source of information.",
    examples: [
      { ar: "عن المَوْضوعِ", en: "about the topic" },
      { ar: "بَعيدٌ عن", en: "far from" },
    ],
    grammar_note: "Takes مجرور (genitive). With pronoun suffixes: عنّي، عنك، عنه.",
  },
  "مع": {
    ar: "مع",
    transliteration: "maʿa",
    meaning: "with",
    category: "Preposition",
    description: "Indicates accompaniment or togetherness.",
    examples: [
      { ar: "مَعَ الأصْدِقاءِ", en: "with friends" },
      { ar: "مَعَ السَّلامَةِ", en: "goodbye (with safety)" },
    ],
    grammar_note: "Takes مجرور (genitive). With pronoun suffixes: معي، معك، معه.",
  },
  "ب": {
    ar: "بـ",
    transliteration: "bi-",
    meaning: "with, by, in",
    category: "Proclitic preposition",
    description: "Attaches to the next word. Indicates instrument, manner, or accompaniment.",
    examples: [
      { ar: "بالقَلَمِ", en: "with the pen" },
      { ar: "بسُرْعَةٍ", en: "quickly (with speed)" },
    ],
    grammar_note: "Prefix clitic — attaches directly to nouns. بال = بـ + ال (with the).",
  },
  "ل": {
    ar: "لـ",
    transliteration: "li-",
    meaning: "for, to, belonging to",
    category: "Proclitic preposition",
    description: "Attaches to the next word. Indicates purpose, possession, or beneficiary.",
    examples: [
      { ar: "للطّالِبِ", en: "for the student" },
      { ar: "لِماذا", en: "why (for what)" },
    ],
    grammar_note: "Prefix clitic. لل = لـ + ال (for the). Also used for emphasis: لَـ + verb.",
  },
  "ك": {
    ar: "كـ",
    transliteration: "ka-",
    meaning: "like, as",
    category: "Proclitic preposition",
    description: "Attaches to the next word. Indicates similarity or comparison.",
    examples: [
      { ar: "كالعادَةِ", en: "as usual" },
      { ar: "كَبيرٌ كالجَبَلِ", en: "big as a mountain" },
    ],
    grammar_note: "Prefix clitic. كال = كـ + ال (like the).",
  },
  "و": {
    ar: "و",
    transliteration: "wa-",
    meaning: "and",
    category: "Conjunction / proclitic",
    description: "The most common Arabic conjunction. Can be standalone or attached as prefix.",
    examples: [
      { ar: "الوَلَدُ والبِنْتُ", en: "the boy and the girl" },
      { ar: "وصَلَ وجَلَسَ", en: "he arrived and sat" },
    ],
    grammar_note: "As prefix: والكتاب = و + الكتاب. Also starts oaths: واللهِ.",
  },
  "ف": {
    ar: "فـ",
    transliteration: "fa-",
    meaning: "so, then, and so",
    category: "Conjunction / proclitic",
    description: "Indicates sequence, result, or immediate succession. Stronger than و.",
    examples: [
      { ar: "جاءَ فَجَلَسَ", en: "he came and (then) sat" },
      { ar: "فَهِمْتُ", en: "so I understood" },
    ],
    grammar_note: "Prefix clitic. Implies causation or quick sequence (unlike و which is neutral).",
  },
  "ال": {
    ar: "الـ",
    transliteration: "al-",
    meaning: "the",
    category: "Definite article",
    description: "Makes nouns definite. Assimilates with sun letters (ت، ث، د، ذ، ر، ز، س، ش، ص، ض، ط، ظ، ل، ن).",
    examples: [
      { ar: "الكِتابُ", en: "the book" },
      { ar: "الشَّمْسُ", en: "the sun (ash-shams)" },
    ],
    grammar_note: "Sun letter assimilation: الشمس = ash-shams (not al-shams). Moon letters keep al-.",
  },
};

// Build a normalized lookup (strip diacritics for matching)
function stripDiacritics(text: string): string {
  return text.replace(/[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]/g, "");
}

const _normalizedLookup: Record<string, GrammarParticleInfo> = {};
for (const [key, info] of Object.entries(GRAMMAR_PARTICLES)) {
  _normalizedLookup[stripDiacritics(key)] = info;
}

export function getGrammarParticleInfo(surfaceForm: string): GrammarParticleInfo | null {
  const bare = stripDiacritics(surfaceForm);
  return _normalizedLookup[bare] ?? null;
}

export default GRAMMAR_PARTICLES;
