/**
 * Pure routing helpers for the Arabic ↔ Greek isolation in `app/_layout.tsx`.
 *
 * Kept in a non-React file so the manifest test in
 * `__tests__/language-context.test.ts` can import them without dragging the
 * full React/Expo context into the test runtime.
 *
 * The `AppLanguage` type lives here too (re-exported from `language-context`
 * for ergonomic consumers) for the same reason.
 */

export type AppLanguage = "ar" | "el";

// Single source of truth for "does this URL belong to the Arabic side or the
// Greek side?". `_layout.tsx` uses it to redirect mismatched routes after a
// language switch (so the tab bar and active screen never disagree).
//
//   /languages                          → shared (the Globe tab)
//   /polyglot, /polyglot-*, /polyglot/* → "el"   (Greek)
//   everything else                     → "ar"   (Arabic)
//
// New Greek screens just need to follow the `polyglot-*` filename convention
// and they inherit isolation automatically. The manifest test fails CI if a
// new file appears in `app/` that hasn't been explicitly classified.
export function routeLanguage(pathname: string): AppLanguage | "shared" {
  if (pathname === "/languages") return "shared";
  if (
    pathname === "/polyglot" ||
    pathname.startsWith("/polyglot/") ||
    pathname.startsWith("/polyglot-")
  ) {
    return "el";
  }
  return "ar";
}

// Entry route per language. Greek lands on Review (the first Greek tab) so
// switching into Greek drops the learner straight into sentence review rather
// than the Reading screen. Reading is still reachable via its own tab.
export function homePathFor(lang: AppLanguage): string {
  return lang === "el" ? "/polyglot-review" : "/";
}
