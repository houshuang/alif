import type { AppLanguage } from "./language-routes";
import { routeLanguage, routeMatchesLanguage } from "./language-routes";

export const LAST_TAB_KEY = "@alif:last-tab:v1";
const TABS = new Set(["/", "/parallel", "/snap", "/podcast", "/stats", "/explore", "/stories", "/more", "/polyglot-review", "/polyglot", "/polyglot-stats"]);
export function isRememberedTab(path: string): boolean { return TABS.has(path); }
export function resumeTab(current: string, hasParams: boolean, stored: string | null,
  language: AppLanguage, explicitNativeLink = false): string | null {
  if (current !== "/" || hasParams || explicitNativeLink || !stored || !isRememberedTab(stored)) return null;
  return routeMatchesLanguage(routeLanguage(stored), language) ? stored : null;
}
