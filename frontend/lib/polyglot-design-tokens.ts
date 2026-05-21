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
 * aesthetic: light surface, Cormorant Garamond for Greek, color-coded eras.
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
 * Type scale. Greek display forms use Cormorant Garamond on iOS (loaded via
 * Expo Font at app boot); falls back to system serif elsewhere. Body text is
 * the system sans on iOS, sans-serif otherwise.
 */
export const POLYGLOT_FONTS = {
  greekDisplay: Platform.select({
    ios: "Cormorant Garamond",
    android: "serif",
    default: "'Cormorant Garamond', 'EB Garamond', Georgia, serif",
  }),
  // Body Greek (sentence rendering inside reader / review). Georgia has full
  // polytonic coverage on iOS; system serif elsewhere.
  greekBody: Platform.select({
    ios: "Georgia",
    android: "serif",
    default: "Georgia, 'Noto Serif', serif",
  }),
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
