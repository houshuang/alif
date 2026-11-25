export const colors = {
  bg: "#0f0f1a",
  surface: "#1a1a2e",
  surfaceLight: "#252542",
  text: "#e8e8f0",
  textSecondary: "#8888a0",
  arabic: "#f0f0ff",
  accent: "#4a9eff",
  targetWord: "#7cb8ff",
  confused: "#f39c12",
  missed: "#e74c3c",
  gotIt: "#2ecc71",
  good: "#2ecc71",
  stateNew: "#8888a0",
  stateLearning: "#e67e22",
  stateKnown: "#2ecc71",
  stateAcquiring: "#3498db",
  stateEncountered: "#636380",
  border: "#2a2a45",
  listening: "#9b59b6",
  listeningBg: "#1a1028",
  noIdea: "#e67e22",
  cefrA1: "#2ecc71",
  cefrA2: "#27ae60",
  cefrB1: "#4a9eff",
  cefrB2: "#e67e22",
  cefrC1: "#e74c3c",
  cefrC2: "#9b59b6",
};

export const fontFamily = {
  arabic: "ScheherazadeNew_400Regular",
  arabicBold: "ScheherazadeNew_700Bold",
  arabicAmiri: "Amiri_400Regular",
  arabicAmiriBold: "Amiri_700Bold",
  arabicNoto: "NotoNaskhArabic_400Regular",
  arabicNotoBold: "NotoNaskhArabic_700Bold",
  translit: "NotoSans_400Regular_Italic",
  translitRegular: "NotoSans_400Regular",
};

/** Ordered list of Arabic font options for cycling.
 *  Font mixing is currently disabled — single font (Scheherazade) to reduce visual noise.
 *  To re-enable: add back Amiri/Noto entries (fontFamily definitions above are preserved). */
export const arabicFonts = [
  { font: fontFamily.arabic, label: "Scheherazade" },
] as const;

/** Pick an Arabic font for a sentence card. */
export function arabicFontForSentence(sentenceId: number | null | undefined): { font: string; label: string } {
  if (sentenceId == null) return arabicFonts[0];
  return arabicFonts[sentenceId % arabicFonts.length];
}

/** Per font, in em: how far shaped words with stacked marks reach above the
 *  baseline (measured with hb-shape on أُمُّ, إِلَّا, لِأَنَّ) and the descent the
 *  font reserves below it (hhea). */
const ARABIC_METRICS: Record<string, { above: number; descent: number }> = {
  [fontFamily.arabic]: { above: 1.35, descent: 0.7 },
  [fontFamily.arabicBold]: { above: 1.35, descent: 0.7 },
  [fontFamily.arabicAmiri]: { above: 1.35, descent: 0.63 },
  [fontFamily.arabicAmiriBold]: { above: 1.35, descent: 0.63 },
  [fontFamily.arabicNoto]: { above: 1.15, descent: 0.63 },
  [fontFamily.arabicNotoBold]: { above: 1.15, descent: 0.63 },
};

/** Style for Arabic text. Use it for every Arabic `fontSize`/`lineHeight` pair.
 *
 *  When `lineHeight` is below the font's natural height (2.04 em for Scheherazade), iOS keeps the
 *  descent and crops the top of the first line, which cuts off vowel marks and
 *  hamza. The returned `paddingTop` restores exactly the missing room, so the line
 *  spacing can stay as tight as the design wants. Glyphs above later lines draw
 *  into the previous line and are not cropped. */
export function arabicText(
  fontSize: number,
  lineHeight: number = Math.round(fontSize * 1.75),
  family: string = fontFamily.arabic,
) {
  const m = ARABIC_METRICS[family] ?? ARABIC_METRICS[fontFamily.arabic];
  const visibleAbove = lineHeight - m.descent * fontSize;
  const paddingTop = Math.max(0, Math.ceil(m.above * fontSize - visibleAbove));
  return { fontFamily: family, fontSize, lineHeight, paddingTop };
}

/** Prepend Left-to-Right Mark so BiDi renders mixed Arabic/English text as LTR.
 *  Without this, text starting with Arabic characters gets RTL paragraph direction on iOS. */
export const ltr = (s: string) => `\u200E${s}`;

export const fonts = {
  arabicSentence: 38,
  arabicLarge: 36,
  arabicMedium: 24,
  arabicList: 20,
  body: 16,
  small: 14,
  caption: 12,
};
