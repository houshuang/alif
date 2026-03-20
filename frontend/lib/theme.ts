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
  arabicNoto: "NotoSansArabic_400Regular",
  arabicNotoBold: "NotoSansArabic_700Bold",
  translit: "NotoSans_400Regular_Italic",
  translitRegular: "NotoSans_400Regular",
};

/** Ordered list of Arabic font options for cycling. */
export const arabicFonts = [
  { font: fontFamily.arabic, label: "Scheherazade" },
  { font: fontFamily.arabicAmiri, label: "Amiri" },
  { font: fontFamily.arabicNoto, label: "Noto" },
] as const;

/** Pick an Arabic font for a sentence card. Uses sentence_id for deterministic 3-way split. */
export function arabicFontForSentence(sentenceId: number | null | undefined): { font: string; label: string } {
  if (sentenceId == null) return arabicFonts[0];
  return arabicFonts[sentenceId % arabicFonts.length];
}

export const fonts = {
  arabicSentence: 38,
  arabicLarge: 36,
  arabicMedium: 24,
  arabicList: 20,
  body: 16,
  small: 14,
  caption: 12,
};
