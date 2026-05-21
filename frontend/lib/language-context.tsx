/**
 * Active-language context — drives which set of tabs the user sees.
 *
 * Two app surfaces co-exist in this Expo project:
 *   - "ar" (Arabic, Alif backend on port 3000/8000): the original Alif app.
 *   - "el" (Modern Greek, Polyglot backend on port 3001): the new reading app.
 *
 * `useLanguage()` returns the active code + a setter. State persists across
 * launches via AsyncStorage. The Globe tab opens the language picker; on
 * selection, the active language flips and the relevant set of tabs becomes
 * visible (handled in _layout.tsx).
 */
import React, { createContext, useContext, useEffect, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { AppLanguage } from "./language-routes";

export type { AppLanguage } from "./language-routes";
export { routeLanguage, homePathFor } from "./language-routes";

const STORAGE_KEY = "@app:active-language";

type LanguageContextValue = {
  language: AppLanguage;
  setLanguage: (lang: AppLanguage) => void;
  ready: boolean;       // false while loading from AsyncStorage
};

const LanguageContext = createContext<LanguageContextValue>({
  language: "ar",
  setLanguage: () => {},
  ready: false,
});

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<AppLanguage>("ar");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const stored = await AsyncStorage.getItem(STORAGE_KEY);
        if (stored === "ar" || stored === "el") {
          setLanguageState(stored);
        }
      } catch {
        // ignore — fall back to default 'ar'
      } finally {
        setReady(true);
      }
    })();
  }, []);

  const setLanguage = (lang: AppLanguage) => {
    setLanguageState(lang);
    AsyncStorage.setItem(STORAGE_KEY, lang).catch(() => {});
  };

  return (
    <LanguageContext.Provider value={{ language, setLanguage, ready }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}
