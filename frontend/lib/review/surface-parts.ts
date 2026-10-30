const DIACRITIC = /[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]/;

// Letters, tatweel and marks; anything else at the edges (، . ؟ quotes) is punctuation.
const NOT_WORD_END = /[^\u0621-\u063A\u0640-\u065F\u0670-\u06D3]+$/u;
const NOT_WORD_START = /^[^\u0621-\u063A\u0640-\u065F\u0670-\u06D3]+/u;

/** Base letter + following diacritics. */
function splitArabicGraphemes(text: string): string[] {
  const groups: string[] = [];
  for (const ch of text) {
    if (DIACRITIC.test(ch) && groups.length > 0) groups[groups.length - 1] += ch;
    else groups.push(ch);
  }
  return groups;
}

// Bare decomposition matches cannot tell a present 1sg (أَسْمَعُ) from an
// imperative (اِسْمَعْ), so an imperative match is not trusted.
export const UNTRUSTED_FORM_KEYS = new Set(["imperative"]);

function foldLetter(ch: string): string {
  return ch.replace(/[\u0622\u0623\u0625\u0671]/g, "\u0627").replace(/\u0629/g, "\u0647").replace(/\u0649/g, "\u064A");
}

/** Cut the vocalized surface into its clitic pieces, so the parts row shows
 *  exactly what is printed in the sentence. Returns null if the bare clitics
 *  cannot be lined up with the surface letters. */
export function sliceSurface(
  surface: string,
  prefixes: string[],
  suffixes: string[],
): { prefixes: string[]; stem: string; suffixes: string[] } | null {
  const trimmed = surface.replace(NOT_WORD_END, "").replace(NOT_WORD_START, "");
  const graphemes = splitArabicGraphemes(trimmed).filter((g) => g !== "\u0640");
  const base = (g: string) => foldLetter(g[0]);
  let start = 0;
  const pre: string[] = [];
  for (const clitic of prefixes) {
    let piece = "";
    for (const ch of clitic) {
      if (start < graphemes.length && base(graphemes[start]) === foldLetter(ch)) {
        piece += graphemes[start++];
      } else if (foldLetter(ch) !== "\u0627") {
        return null; // only an alif may be elided (لِ + الْ → لِلْ)
      }
    }
    if (!piece) return null;
    pre.push(piece);
  }
  let end = graphemes.length;
  const suf: string[] = [];
  for (const clitic of [...suffixes].reverse()) {
    let piece = "";
    for (const ch of [...clitic].reverse()) {
      if (end > start && base(graphemes[end - 1]) === foldLetter(ch)) {
        piece = graphemes[--end] + piece;
      } else {
        return null;
      }
    }
    suf.unshift(piece);
  }
  if (end - start < 2) return null;
  return { prefixes: pre, stem: graphemes.slice(start, end).join(""), suffixes: suf };
}

