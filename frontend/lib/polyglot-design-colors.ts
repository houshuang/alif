/**
 * Polyglot design tokens — pure constants, no React Native imports.
 *
 * Split from polyglot-design-tokens.ts so Jest (ts-jest, node env) can import
 * the era color map without pulling react-native. The full token surface
 * (including Platform-dependent font fallbacks) lives in
 * polyglot-design-tokens.ts; it re-exports these constants.
 */

/**
 * Era → color. Used by the diachrony timeline dots, the era pills inside
 * quote citations, and the chip in the lemma header. Five distinct hues across
 * the warm/cool spectrum so the reader's eye instantly places a form in time.
 */
export const POLYGLOT_ERA_COLORS = {
  Mycenaean: "#3a5a4a",
  Homeric: "#5a8a6a",
  Classical: "#4a7c8a",
  Koine: "#6a5a8a",
  Byzantine: "#8a5a4a",
  Modern: "#c97a3a",
} as const;

export type PolyglotEra = keyof typeof POLYGLOT_ERA_COLORS;

/**
 * Base palette. Light/editorial surface, neutral text scale. Cognates and
 * quote sections each have their own accent so the user can scan the
 * detail page by color band.
 */
export const POLYGLOT_COLORS = {
  bg: "#fafaf8",
  surface: "#ffffff",
  surfaceMuted: "#f3f1ec",
  text: "#1a1a1a",
  textSecondary: "#6b6b6b",
  textTertiary: "#9b9b9b",
  border: "#ececec",
  borderStrong: "#d4c8b0",
  accent: "#2c5f8d",
  etymology: "#c97a3a",
  cognate: "#2e7d6b",
  quote: "#7d3a7a",
  warning: "#c14a3a",
  etymologyTint: "#fdf2e9",
  cognateTint: "#e8f4ef",
  quoteTint: "#f5edf4",
  warningTint: "#fdebe7",
} as const;

export function eraColor(era: string | null | undefined): string {
  if (!era) return POLYGLOT_COLORS.textSecondary;
  return (POLYGLOT_ERA_COLORS as Record<string, string>)[era] ?? POLYGLOT_COLORS.textSecondary;
}

export const POLYGLOT_TYPE = {
  heroGreek: 56,
  heroGreekLarge: 72,
  bodyGreek: 20,
  stageForm: 21,
  gloss: 20,
  glossInline: 18,
  cogForm: 15,
  cogGreek: 17,
  body: 14,
  bodySmall: 13,
  meta: 12,
  micro: 10,
} as const;

export const POLYGLOT_SPACING = {
  pageH: 16,
  sectionV: 18,
  rowGap: 10,
  chipGap: 5,
  bottomFloat: 60,
} as const;

export const POLYGLOT_RADIUS = {
  card: 14,
  chip: 999,
  pill: 6,
  callout: 8,
} as const;
