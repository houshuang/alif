import { renderTokens } from "../polyglot-render-helpers";
import type { TokenView } from "../polyglot-api";

function tok(surface: string, overrides: Partial<TokenView> = {}): TokenView {
  return {
    position: 0,
    surface,
    is_punctuation: /^[.,;:!?·»()«…]$/.test(surface),
    sentence_index: 0,
    lemma_id: 1,
    lemma_form: surface,
    lemma_bare: surface,
    pos: null,
    gloss_en: null,
    is_function_word: false,
    is_heading: false,
    is_known: false,
    is_acquiring: false,
    is_encountered: false,
    is_unknown: false,
    is_ignored: false,
    is_new: false,
    is_oov: false,
    ...overrides,
  };
}

function joined(tokens: TokenView[]): string {
  return renderTokens(tokens)
    .map((s) => s.leadingSpace + s.surface)
    .join("");
}

describe("renderTokens — punctuation spacing", () => {
  it("attaches comma to previous word, not next", () => {
    // The bug we shipped to prod: `Τίγρη ,στις` had a space BEFORE the comma.
    expect(joined([tok("Τίγρη"), tok(","), tok("στις"), tok("όχθες")]))
      .toBe("Τίγρη, στις όχθες");
  });

  it("attaches period to previous word", () => {
    expect(joined([tok("νευί"), tok("."), tok("Από"), tok("τις")]))
      .toBe("νευί. Από τις");
  });

  it("attaches Greek interpunct (·) to previous word", () => {
    expect(joined([tok("όχθες"), tok("·"), tok("Από")]))
      .toBe("όχθες· Από");
  });

  it("no leading space before opening bracket's content", () => {
    expect(joined([tok("("), tok("παρένθεση"), tok(")"), tok("τέλος")]))
      .toBe("(παρένθεση) τέλος");
  });

  it("Greek opening quote « bonds to next word", () => {
    expect(joined([tok("«"), tok("έπος"), tok("»"), tok("του")]))
      .toBe("«έπος» του");
  });
});

describe("renderTokens — soft-hyphen joining", () => {
  it("joins ['Νι', '-νευί'] into 'Νινευί'", () => {
    expect(joined([tok("Νι"), tok("-νευί"), tok(".")]))
      .toBe("Νινευί.");
  });

  it("joins ['άλ', '-', 'λα'] (standalone hyphen) into 'άλλα'", () => {
    expect(joined([tok("άλ"), tok("-"), tok("λα")]))
      .toBe("άλλα");
  });

  it("joins ['καλλιερ', '-γήσουν'] into 'καλλιεργήσουν'", () => {
    expect(joined([tok("καλλιερ"), tok("-γήσουν"), tok(".")]))
      .toBe("καλλιεργήσουν.");
  });

  it("joins three pieces: ['εί', '-ναι']", () => {
    expect(joined([tok("εί"), tok("-ναι"), tok("η")]))
      .toBe("είναι η");
  });

  it("joins word-ends-with-hyphen + next-alpha", () => {
    // PDF sometimes splits as ["ανατολι-", "κά"] (trailing dash on the
    // first piece). The renderer should drop the trailing dash and join.
    expect(joined([tok("ανατολι-"), tok("κά")]))
      .toBe("ανατολικά");
  });

  it("does NOT join standalone hyphen between non-alpha tokens", () => {
    // A dash between digits ("1981-1990") should not be eaten. We use
    // alphabetic-only on both sides, so digits keep the dash visible.
    const tokens = [
      tok("1981", { is_punctuation: false }),
      tok("-"),
      tok("1990", { is_punctuation: false }),
    ];
    // Digits aren't alphabetic so the standalone-hyphen rule doesn't fire.
    // The result will be "1981 - 1990" with normal word spacing — not
    // beautiful but at least nothing is mangled.
    expect(joined(tokens)).toBe("1981 - 1990");
  });

  it("preserves a single-token compound like 'Αυστραλο-Ασιατικός'", () => {
    // The PDF likely keeps such compounds as ONE token with no surrounding
    // spaces, so it never enters our hyphen-join code path.
    expect(joined([tok("Αυστραλο-Ασιατικός"), tok(",")]))
      .toBe("Αυστραλο-Ασιατικός,");
  });

  it("does NOT join words across an en-dash (Greek parenthetical)", () => {
    // The visible bug at page 11: tokens around `Τίγρης – ανατολικά – και`
    // had the en-dashes silently consumed, producing `Τίγρηςανατολικάκαι`.
    // En-dash (U+2013) is a parenthetical marker in Greek, NOT a
    // line-break soft-hyphen. The dashes must survive into the output.
    const tokens = [
      tok("Τίγρης"), tok("–", { is_punctuation: true }),
      tok("ανατολικά"), tok("–", { is_punctuation: true }),
      tok("και"),
    ];
    const out = joined(tokens);
    expect(out).toContain("Τίγρης");
    expect(out).toContain("ανατολικά");
    expect(out).toContain("και");
    expect(out).toContain("–");
    expect(out).not.toContain("Τίγρηςανατολικά");
    expect(out).not.toContain("ανατολικάκαι");
  });

  it("does NOT join words across a non-breaking hyphen (U+2011)", () => {
    // Same logic: non-breaking hyphen explicitly does NOT line-break, so
    // dropping it would be wrong.
    const tokens = [tok("A"), tok("‑"), tok("B")];
    expect(joined(tokens)).toContain("‑");
  });
});

describe("renderTokens — combined real-world example", () => {
  it("handles the screenshot's mangled prose", () => {
    // From the iOS screenshot at page 11: tokens (approx) that produced
    // `Τίγρη ,στις όχθες του οποίου ήταν χτισμένη η Νι -νευί .Από τις`
    // We expect a clean: `Τίγρη, στις όχθες του οποίου ήταν χτισμένη η Νινευί. Από τις`
    const tokens = [
      tok("Τίγρη"), tok(","),
      tok("στις"), tok("όχθες"), tok("του"), tok("οποίου"),
      tok("ήταν"), tok("χτισμένη"), tok("η"),
      tok("Νι"), tok("-νευί"), tok("."),
      tok("Από"), tok("τις"),
    ];
    expect(joined(tokens))
      .toBe("Τίγρη, στις όχθες του οποίου ήταν χτισμένη η Νινευί. Από τις");
  });
});
