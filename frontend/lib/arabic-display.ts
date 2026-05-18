import type { WordForms } from "./types";

const DIACRITICS_RE = /[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]/g;
const ARABIC_BASE_RE = /[\u0621-\u064A\u066E-\u06D3\u06FA-\u06FF]/;
const CASE_ENDINGS = new Set(["\u064B", "\u064C", "\u064D", "\u064E", "\u064F", "\u0650"]);
const SHADDA_OR_SUKUN = new Set(["\u0651", "\u0652"]);
const TATWEEL_RE = /\u0640/g;

export function stripArabicDiacritics(text: string): string {
  return text.replace(DIACRITICS_RE, "");
}

export function bareArabicForm(text: string): string {
  let s = stripArabicDiacritics(text).replace(TATWEEL_RE, "");
  if (s.startsWith("\u0627\u0644")) s = s.slice(2);
  return s;
}

export function lexicalVocalizationScore(text: string | null | undefined): number {
  const chars = Array.from((text ?? "").replace(TATWEEL_RE, ""));
  const baseIndexes = chars
    .map((ch, index) => (ARABIC_BASE_RE.test(ch) ? index : -1))
    .filter((index) => index >= 0);
  if (baseIndexes.length === 0) return 0;

  const finalBaseIndex = baseIndexes[baseIndexes.length - 1];
  let score = 0;
  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i];
    DIACRITICS_RE.lastIndex = 0;
    if (!DIACRITICS_RE.test(ch)) continue;

    let previousBase = -1;
    for (let j = i - 1; j >= 0; j--) {
      if (ARABIC_BASE_RE.test(chars[j])) {
        previousBase = j;
        break;
      }
    }

    if (
      baseIndexes.length > 1 &&
      previousBase === finalBaseIndex &&
      CASE_ENDINGS.has(ch) &&
      !SHADDA_OR_SUKUN.has(ch)
    ) {
      continue;
    }
    score += 1;
  }
  return score;
}

export function bestVocalizedDisplayForm({
  lemmaAr,
  lemmaTransliteration,
  surfaceForm,
  surfaceTranslit,
  forms,
  formsTranslit,
}: {
  lemmaAr: string | null | undefined;
  lemmaTransliteration?: string | null;
  surfaceForm?: string | null;
  surfaceTranslit?: string | null;
  forms?: WordForms | Record<string, unknown> | null;
  formsTranslit?: Record<string, string> | null;
}): { arabic: string | null; transliteration: string | null; formKey: string | null } {
  const currentArabic = lemmaAr?.trim() || null;
  if (!currentArabic) {
    return { arabic: null, transliteration: lemmaTransliteration ?? null, formKey: null };
  }

  const currentScore = lexicalVocalizationScore(currentArabic);
  const currentBare = bareArabicForm(currentArabic);
  const surfaceBare = surfaceForm ? bareArabicForm(surfaceForm) : null;

  let best = {
    arabic: currentArabic,
    transliteration: lemmaTransliteration ?? null,
    formKey: null as string | null,
    score: currentScore,
  };

  const consider = (arabic: string | null | undefined, transliteration: string | null | undefined, formKey: string | null) => {
    const value = arabic?.trim();
    if (!value) return;
    const bare = bareArabicForm(value);
    if (bare !== currentBare && (!surfaceBare || bare !== surfaceBare)) return;
    const score = lexicalVocalizationScore(value);
    if (score > best.score) {
      best = {
        arabic: value,
        transliteration: transliteration ?? best.transliteration,
        formKey,
        score,
      };
    }
  };

  if (currentScore === 0) {
    consider(surfaceForm, surfaceTranslit, null);
    if (forms && typeof forms === "object") {
      for (const [key, value] of Object.entries(forms)) {
        if (!value || typeof value !== "string") continue;
        consider(value, formsTranslit?.[key] ?? null, key);
      }
    }
  }

  return {
    arabic: best.arabic,
    transliteration: best.transliteration,
    formKey: best.formKey,
  };
}
