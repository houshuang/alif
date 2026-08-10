import type { StatusBarStyle } from "expo-status-bar";

/**
 * Keep system text legible as the tab navigator moves between Alif's dark
 * screens and the light paper/editorial readers. Tab screens stay mounted
 * after navigation, so route-local StatusBar components can otherwise leave
 * their style active on an unrelated screen.
 */
export function statusBarStyleForPath(pathname: string): StatusBarStyle {
  const usesLightSurface =
    pathname === "/book-page" || pathname.startsWith("/polyglot");

  return usesLightSurface ? "dark" : "light";
}
