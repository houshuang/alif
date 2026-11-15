import type { SnapSentence } from "./types";

export type SnapToken = {
  text: string;
  key: string;
  /** Punctuation-, harakat- and case-free form used to match links. */
  match: string;
  isWord: boolean;
};

export type AlignedSentence = {
  index: number;
  paragraph: number;
  ar: string;
  arTokens: SnapToken[];
  enTokens: SnapToken[];
  /** Per link: the Arabic match keys and the English token indices it covers. */
  links: { ar: Set<string>; en: Set<number> }[];
};

const ARABIC_LETTER = /[ء-ي]/;

export function matchKey(token: string): string {
  return token.replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
}

function tokens(text: string, prefix: string, isWord: (part: string) => boolean): SnapToken[] {
  return text
    .split(/(\s+)/)
    .filter((part) => part.length > 0)
    .map((part, i) => ({
      text: part,
      key: `${prefix}${i}`,
      match: matchKey(part),
      isWord: !/^\s+$/.test(part) && isWord(part),
    }));
}

/** English token indices for `phrase`, preferring an occurrence no earlier link took. */
function findEnglishSpan(enTokens: SnapToken[], phrase: string, used: Set<number>): Set<number> {
  const wanted = phrase.split(/\s+/).map(matchKey).filter(Boolean);
  const words = enTokens.map((t, i) => ({ i, m: t.match })).filter((w) => w.m);
  let fallback: Set<number> | null = null;
  for (let start = 0; start + wanted.length <= words.length; start++) {
    if (!wanted.every((w, j) => words[start + j].m === w)) continue;
    const span = new Set(words.slice(start, start + wanted.length).map((w) => w.i));
    if ([...span].every((i) => !used.has(i))) return span;
    fallback = fallback ?? span;
  }
  return fallback ?? new Set();
}

export function alignSentences(sentences: SnapSentence[]): AlignedSentence[] {
  return sentences.map((s, si) => {
    const arTokens = tokens(s.ar, `a${si}-`, (p) => ARABIC_LETTER.test(p));
    const enTokens = tokens(s.en, `e${si}-`, (p) => matchKey(p).length > 0);
    const used = new Set<number>();
    const links = (s.links || []).map((link) => {
      const en = findEnglishSpan(enTokens, link.en, used);
      en.forEach((i) => used.add(i));
      return { ar: new Set(link.ar.split(/\s+/).map(matchKey).filter(Boolean)), en };
    }).filter((link) => link.ar.size > 0 && link.en.size > 0);
    return { index: si, paragraph: s.p, ar: s.ar, arTokens, enTokens, links };
  });
}

export function linkForArabic(sentence: AlignedSentence, token: SnapToken): number | null {
  const i = sentence.links.findIndex((l) => l.ar.has(token.match));
  return i >= 0 ? i : null;
}

export function linkForEnglish(sentence: AlignedSentence, tokenIndex: number): number | null {
  const i = sentence.links.findIndex((l) => l.en.has(tokenIndex));
  return i >= 0 ? i : null;
}

/** First Arabic token covered by a link, used to open its definition. */
export function firstArabicToken(sentence: AlignedSentence, link: number): SnapToken | null {
  const keys = sentence.links[link]?.ar;
  return sentence.arTokens.find((t) => t.isWord && keys?.has(t.match)) ?? null;
}

/** Group sentences into paragraphs, preserving order. */
export function paragraphsOf(sentences: AlignedSentence[]): AlignedSentence[][] {
  const out: AlignedSentence[][] = [];
  let last: number | null = null;
  for (const s of sentences) {
    if (s.paragraph !== last) {
      out.push([]);
      last = s.paragraph;
    }
    out[out.length - 1].push(s);
  }
  return out;
}
