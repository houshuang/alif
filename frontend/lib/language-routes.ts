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

export type AppLanguage = "ar" | "el" | "la";

// Single source of truth for "which SURFACE does this URL belong to?".
// `_layout.tsx` uses it to redirect mismatched routes after a language switch
// (so the tab bar and active screen never disagree).
//
//   /languages                          → shared (the Globe tab)
//   /polyglot, /polyglot-*, /polyglot/* → "el"   (the Polyglot surface)
//   everything else                     → "ar"   (Arabic)
//
// NB: the Polyglot surface is shared by Modern Greek AND Latin — the same
// polyglot-* screens serve both, disambiguated at runtime by the active
// language (the screens read it from context and pass language_code to the
// backend). `routeLanguage` therefore returns the canonical "el" marker for
// every polyglot route; use `routeMatchesLanguage` to test against the active
// language. New polyglot screens just follow the `polyglot-*` filename
// convention and inherit isolation automatically.
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

// True when a route's surface matches the active language. A polyglot route
// ("el" surface) matches BOTH el and la actives; an Arabic route matches ar.
// "shared" (the Globe tab) matches anything.
export function routeMatchesLanguage(
  routeLang: AppLanguage | "shared",
  active: AppLanguage,
): boolean {
  if (routeLang === "shared") return true;
  if (routeLang === "el") return active === "el" || active === "la";
  return routeLang === active;
}

// Entry route per language. Greek and Latin both land on Review (the first
// Polyglot tab) so switching in drops the learner straight into sentence
// review. Reading is still reachable via its own tab.
export function homePathFor(lang: AppLanguage): string {
  return lang === "ar" ? "/" : "/polyglot-review";
}
