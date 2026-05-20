/**
 * Pure helpers for rendering polyglot page tokens as natural prose.
 *
 * Separated from `polyglot.tsx` so the punctuation + soft-hyphen logic is
 * unit-testable without spinning up React Native.
 *
 * Two distinct problems are solved here:
 *
 * 1. **Punctuation spacing.** The naive "append a space after every word"
 *    rule produces strings like `Τίγρη ,στις` (space before comma) because
 *    the comma is a separate token and the previous word's trailing space
 *    is already in the buffer. Greek and Latin alike want attaching
 *    punctuation (`.,;:!?·»)…`) flush against the previous word.
 *
 * 2. **PDF soft-hyphens.** When a PDF extractor preserves line-break
 *    hyphens, words split across line ends arrive as either
 *    `["Νι", "-νευί"]` or `["Νι", "-", "νευί"]`. We rejoin them into a
 *    single rendered word (`Νινευί`) so the prose flows like a book.
 */
import type { TokenView } from "./polyglot-api";

/** Characters that attach to the *previous* word with no leading space. */
const ATTACHING_PUNCT = new Set([
  ".", ",", ";", ":", "!", "?",
  "·",      // Greek άνω τελεία (interpunct, modern Greek's semicolon)
  ")", "]", "}",
  "»", "”", "›",
  "…",
]);

/** Characters that bond to the *next* word with no trailing space. */
const OPENING_PUNCT = new Set(["(", "[", "{", "«", "“", "‹"]);

function isAlphabetic(s: string): boolean {
  // Any letter (Unicode-aware). Excludes punctuation, digits, whitespace.
  return /[\p{L}]/u.test(s);
}

function isStandaloneHyphen(surface: string): boolean {
  return surface === "-" || surface === "‐" || surface === "‑" || surface === "–";
}

/**
 * A single span to render. `leadingSpace` is what should go *before*
 * `surface` in the rendered text. Punctuation pieces have leadingSpace=""
 * and `joinable=false` so the React renderer can skip wrapping them in
 * tap handlers.
 */
export type RenderedSpan = {
  leadingSpace: string;
  surface: string;
  /** Original TokenView this span maps back to (for tap/lookup). When two
   *  tokens were merged via soft-hyphen, this is the first of them. */
  token: TokenView;
  /** True when the original token was punctuation (not the merged
   *  hyphen — the actual punctuation token). */
  isPunctuation: boolean;
};

/**
 * Walk the token list and produce a sequence of renderable spans with
 * correct spacing and soft-hyphen joining.
 *
 * Empty surfaces and standalone hyphens that sit between two alphabetic
 * tokens are silently consumed (they were PDF line-break artifacts).
 * A token whose surface starts with a hyphen followed by letters — e.g.
 * `-νευί` — has the hyphen stripped and is concatenated to the previous
 * span with no leading space.
 */
export function renderTokens(tokens: readonly TokenView[]): RenderedSpan[] {
  const out: RenderedSpan[] = [];
  let previousWasOpening = false;
  // True when the *previous* span ended with a stripped soft-hyphen — the
  // next alphabetic token continues the same word, so it shouldn't get a
  // leading space.
  let previousHadDroppedHyphen = false;
  let i = 0;
  while (i < tokens.length) {
    const t = tokens[i];

    // Strip standalone soft-hyphens that sit between two alphabetic tokens.
    // E.g. ["καλλιερ", "-", "γήσουν"] → "καλλιεργήσουν" as a single span.
    if (
      isStandaloneHyphen(t.surface) &&
      out.length > 0 &&
      i + 1 < tokens.length &&
      isAlphabetic(out[out.length - 1].surface) &&
      isAlphabetic(tokens[i + 1].surface)
    ) {
      const next = tokens[i + 1];
      out[out.length - 1] = {
        ...out[out.length - 1],
        surface: out[out.length - 1].surface + next.surface,
      };
      i += 2;
      previousHadDroppedHyphen = false;
      continue;
    }

    // Hyphen-prefix tokens like `-νευί` (from `Νι\n-νευί` extraction):
    // strip the leading dash and append to the previous span without a space.
    if (
      /^[-‐‑–][\p{L}]/u.test(t.surface) &&
      out.length > 0 &&
      isAlphabetic(out[out.length - 1].surface)
    ) {
      out[out.length - 1] = {
        ...out[out.length - 1],
        surface: out[out.length - 1].surface + t.surface.slice(1),
      };
      i += 1;
      previousHadDroppedHyphen = false;
      continue;
    }

    // Words that themselves end with `-` followed immediately by another
    // alphabetic token: the trailing hyphen is a line-break artifact, drop
    // it now and remember so the next alphabetic span has no leading space.
    let surface = t.surface;
    let strippedTrailingHyphen = false;
    if (
      /[\p{L}][-‐‑–]$/u.test(surface) &&
      i + 1 < tokens.length &&
      isAlphabetic(tokens[i + 1].surface)
    ) {
      surface = surface.slice(0, -1);
      strippedTrailingHyphen = true;
    }

    if (surface.length === 0) {
      i += 1;
      continue;
    }

    const isPunct = t.is_punctuation;
    const attaches = ATTACHING_PUNCT.has(surface) || isPunct;
    const leadingSpace =
      out.length === 0 || attaches || previousWasOpening || previousHadDroppedHyphen
        ? ""
        : " ";

    out.push({
      leadingSpace,
      surface,
      token: t,
      isPunctuation: isPunct,
    });
    previousWasOpening = OPENING_PUNCT.has(surface);
    previousHadDroppedHyphen = strippedTrailingHyphen;
    i += 1;
  }
  return out;
}
