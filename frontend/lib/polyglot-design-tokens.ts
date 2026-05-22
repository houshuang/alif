/**
 * Polyglot design tokens — Modern Editorial.
 *
 * Single source of truth for the polyglot visual language across the reader,
 * sentence review, intro card, lookup card, and lemma detail screen.
 *
 * Mirrors the locked design choices from the 2026-05-21 design-explorer round
 * (Round 2: "Detail iPhone · Modern Editorial" + "Editorial w/ λόγος"). When
 * tweaking a color or font size, change it here and every polyglot surface
 * follows.
 *
 * Why a separate file instead of frontend/lib/theme.ts: theme.ts is Alif's
 * Arabic palette (dark surface, Arabic font stack). Polyglot is the opposite
 * aesthetic: light surface, EB Garamond for Greek, color-coded eras.
 *
 * Color/spacing/type constants live in polyglot-design-colors.ts (pure, no RN
 * imports — Jest can read them). Font fallbacks need Platform and live here.
 */
import { Platform } from "react-native";

export {
  POLYGLOT_ERA_COLORS,
  POLYGLOT_COLORS,
  POLYGLOT_TYPE,
  POLYGLOT_SPACING,
  POLYGLOT_RADIUS,
  eraColor,
} from "./polyglot-design-colors";
export type { PolyglotEra } from "./polyglot-design-colors";

/**
 * Greek faces. Both display and body are EB Garamond, registered in
 * app/_layout.tsx via @expo-google-fonts/eb-garamond. expo-font registers each
 * weight under its exact JS-constant name on iOS / Android / web alike, so we
 * reference those keys directly. (The previous "Cormorant Garamond" string was
 * never registered anywhere, so Greek silently fell back to Georgia/system — the
 * bug this replaces. There is no Platform.select here on purpose: the registered
 * family name is identical across platforms.)
 *
 * EB Garamond carries Greek (monotonic + most polytonic) and Latin, covering
 * all three Polyglot languages. No italic face is loaded — Polyglot never
 * renders italic Greek, because the faux-slant of an upright face looks wrong.
 * Use weight for emphasis: greekDisplay (SemiBold) for headline forms,
 * greekBody (Regular) for sentence reading and translations.
 */
export const POLYGLOT_FONTS = {
  greekDisplay: "EBGaramond_600SemiBold",
  greekBody: "EBGaramond_400Regular",
  uiSans: Platform.select({
    ios: "System",
    android: "sans-serif",
    default: "-apple-system, 'Inter', system-ui, sans-serif",
  }),
  mono: Platform.select({
    ios: "Menlo",
    android: "monospace",
    default: "'JetBrains Mono', monospace",
  }),
} as const;
