/**
 * Source-guard regression tests for `frontend/app/_layout.tsx`.
 *
 * Bug fixed 2026-05-25: the `polyglot-stats` <Tabs.Screen> had a hardcoded
 * `title: "Modern Greek Stats"`, which the bottom-tab navigator renders as
 * the native header bar. When the user switched to Latin, the body of the
 * stats screen swapped to Latin but the header stayed "Modern Greek Stats".
 * Fix: `headerShown: false` so the screen renders its own language-aware
 * header (eyebrow + native name), matching the other polyglot screens.
 *
 * These tests are intentionally text-level — rendering the navigator in Jest
 * would require the full Expo Router + React Native stack. They scan the
 * layout source and assert structural invariants:
 *
 *   1. The `polyglot-stats` tab Screen has `headerShown: false`.
 *   2. No `polyglot-*` tab Screen has a `title:` that contains a
 *      language-specific word — those leak the wrong language under the
 *      shared (el+la) polyglot screens.
 *
 * The tests are brittle to layout reformatting on purpose: a code change
 * that drops `headerShown` or reintroduces a language-flavoured title will
 * fail here even before anyone notices the regression on-screen.
 */
import { readFileSync } from "fs";
import path from "path";

const LAYOUT_PATH = path.resolve(__dirname, "../../app/_layout.tsx");
const layoutSource = readFileSync(LAYOUT_PATH, "utf-8");

/** Extracts the options object source for each `<Tabs.Screen name="..." options={{...}}>`. */
function extractTabScreens(source: string): { name: string; options: string }[] {
  const out: { name: string; options: string }[] = [];
  const re = /<Tabs\.Screen[\s\S]*?name="([^"]+)"[\s\S]*?options=\{\{([\s\S]*?)\}\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    out.push({ name: m[1], options: m[2] });
  }
  return out;
}

const screens = extractTabScreens(layoutSource);
const polyglotScreens = screens.filter((s) => s.name.startsWith("polyglot"));

describe("_layout.tsx: polyglot tab navigator", () => {
  it("finds the expected polyglot tab Screens", () => {
    const names = polyglotScreens.map((s) => s.name).sort();
    // Don't pin the exact set (new polyglot screens are expected), just that
    // the three known ones still register here.
    expect(names).toEqual(expect.arrayContaining(["polyglot", "polyglot-review", "polyglot-stats"]));
  });

  it("polyglot-stats hides the native header (in-screen header renders the language)", () => {
    const stats = polyglotScreens.find((s) => s.name === "polyglot-stats");
    expect(stats).toBeDefined();
    // Match `headerShown: false` (with any whitespace). If a future commit
    // drops this, the native header bar's static title leaks back through.
    expect(stats!.options).toMatch(/headerShown\s*:\s*false/);
  });

  it("no polyglot tab Screen has a language-specific title (regression: 'Modern Greek Stats')", () => {
    // These are the polyglot surface languages plus the obvious leak words.
    // A bare `title: "Stats"` / `"Review"` / `"Reading"` is fine.
    const leakyWords = [
      "Modern Greek",
      "Ancient Greek",
      "Greek",
      "Latin",
      "ελληνικά",
      "Latina",
    ];
    for (const screen of polyglotScreens) {
      // Grab the value of the `title:` field (string literal).
      const titleMatch = /title\s*:\s*"([^"]+)"/.exec(screen.options);
      if (!titleMatch) continue;
      const title = titleMatch[1];
      for (const word of leakyWords) {
        expect(title).not.toMatch(new RegExp(word, "i"));
      }
    }
  });
});
