/**
 * Guardrails for the Arabic ↔ Greek route isolation in `app/_layout.tsx`.
 *
 * Why this test exists: tab visibility in `_layout.tsx` is driven by the
 * active language, and the redirect effect uses `routeLanguage()` to decide
 * whether to bounce the user to their language's home. If someone adds a
 * new screen file to `frontend/app/` without thinking about which language
 * it belongs to, the most likely failure is that the user sees a Greek
 * screen under an Arabic tab bar (or vice versa) — exactly the bug this
 * isolation was added to prevent.
 *
 * The manifests below force an explicit decision per screen — top-level
 * screens AND detail-route subdirectories (e.g. `word/[id]`,
 * `polyglot-lemma/[id]`). The directory walk is recursive, so any new file
 * anywhere under `app/` that isn't classified fails the test with
 * instructions, and every classified screen's representative route is
 * checked to actually resolve to its declared language.
 */
import * as fs from "fs";
import * as path from "path";
import {
  routeLanguage,
  homePathFor,
  routeMatchesLanguage,
  type AppLanguage,
} from "../language-routes";

const APP_DIR = path.resolve(__dirname, "../../app");

// Explicit language assignment for every top-level screen in `frontend/app/`.
// Filename (without `.tsx`) → owning language.
const EXPECTED: Record<string, AppLanguage | "shared"> = {
  // ─── Arabic (Alif) screens ──────────────────────────────────────────
  index: "ar",
  listening: "ar",
  podcast: "ar",
  stats: "ar",
  explore: "ar",
  stories: "ar",
  more: "ar",
  scanner: "ar",
  chats: "ar",
  learn: "ar",
  "book-import": "ar",
  "book-page": "ar",
  "review-lab": "ar",
  words: "ar",
  snap: "ar",
  // ─── Greek (Polyglot) screens ───────────────────────────────────────
  polyglot: "el",
  "polyglot-review": "el",
  "polyglot-stats": "el",
  // ─── Shared (Globe tab) ─────────────────────────────────────────────
  languages: "shared",
};

// Detail-route subdirectories (e.g. `app/word/[id].tsx`). Directory name →
// owning language. A Greek detail route must live under a `polyglot-*`
// directory so the URL-prefix rule in routeLanguage classifies it.
const DIR_EXPECTED: Record<string, AppLanguage | "shared"> = {
  word: "ar",
  story: "ar",
  root: "ar",
  pattern: "ar",
  "polyglot-lemma": "el",
};

function routeForFile(name: string): string {
  return name === "index" ? "/" : `/${name}`;
}

// Convert an `app/`-relative screen path to a representative URL, replacing
// dynamic segments (`[id]`) with a placeholder so routeLanguage can classify
// it. `index.tsx` at the root maps to `/`.
function relPathToRoute(rel: string): string {
  const segments = rel
    .replace(/\.tsx$/, "")
    .split(path.sep)
    .map((seg) => seg.replace(/\[[^\]]+\]/g, "x"));
  if (segments.length === 1 && segments[0] === "index") return "/";
  return "/" + segments.join("/");
}

// All screen files under `app/` (recursive), returned as `app/`-relative
// paths, excluding `_layout.tsx` (infrastructure, not a routable screen).
function walkScreens(dir: string, base: string = dir): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walkScreens(full, base));
    } else if (entry.name.endsWith(".tsx") && entry.name !== "_layout.tsx") {
      out.push(path.relative(base, full));
    }
  }
  return out;
}

// The declared language for an on-disk screen, or undefined if unclassified.
function declaredLanguage(rel: string): AppLanguage | "shared" | undefined {
  const parts = rel.split(path.sep);
  if (parts.length === 1) return EXPECTED[parts[0].replace(/\.tsx$/, "")];
  return DIR_EXPECTED[parts[0]];
}

describe("routeLanguage classifier", () => {
  test("every top-level screen in the manifest classifies as expected", () => {
    for (const [name, lang] of Object.entries(EXPECTED)) {
      expect(routeLanguage(routeForFile(name))).toBe(lang);
    }
  });

  test("every detail-route subdirectory classifies as expected", () => {
    for (const [dir, lang] of Object.entries(DIR_EXPECTED)) {
      expect(routeLanguage(`/${dir}/x`)).toBe(lang);
    }
  });

  test("every .tsx file in app/ (incl. subdirectories) is classified — add new screens here", () => {
    const onDisk = walkScreens(APP_DIR);

    const topLevel = onDisk
      .filter((rel) => !rel.includes(path.sep))
      .map((rel) => rel.replace(/\.tsx$/, ""));
    const subdirs = [
      ...new Set(
        onDisk.filter((rel) => rel.includes(path.sep)).map((rel) => rel.split(path.sep)[0]),
      ),
    ];

    const missingTop = topLevel.filter((s) => !(s in EXPECTED));
    const staleTop = Object.keys(EXPECTED).filter((s) => !topLevel.includes(s));
    const missingDir = subdirs.filter((d) => !(d in DIR_EXPECTED));
    const staleDir = Object.keys(DIR_EXPECTED).filter((d) => !subdirs.includes(d));

    if (missingTop.length > 0) {
      throw new Error(
        `New top-level screen file(s) in frontend/app/ without language classification: ${missingTop.join(", ")}.\n` +
          `Add an entry to EXPECTED in this test file. If the screen is Greek, ` +
          `prefer renaming it to 'polyglot-<name>.tsx' so the URL-prefix rule ` +
          `classifies it automatically (see routeLanguage in lib/language-routes.ts).`,
      );
    }
    if (missingDir.length > 0) {
      throw new Error(
        `New detail-route subdirectory/-ies in frontend/app/ without language classification: ${missingDir.join(", ")}.\n` +
          `Add an entry to DIR_EXPECTED in this test file. A Greek detail route ` +
          `must live under a 'polyglot-*' directory so routeLanguage classifies it as 'el'.`,
      );
    }
    if (staleTop.length > 0) {
      throw new Error(
        `EXPECTED references top-level screen(s) that no longer exist in frontend/app/: ${staleTop.join(", ")}. Remove them.`,
      );
    }
    if (staleDir.length > 0) {
      throw new Error(
        `DIR_EXPECTED references subdirectory/-ies that no longer exist in frontend/app/: ${staleDir.join(", ")}. Remove them.`,
      );
    }
  });

  test("every on-disk screen's representative route resolves to its declared language", () => {
    for (const rel of walkScreens(APP_DIR)) {
      const declared = declaredLanguage(rel);
      if (declared === undefined) continue; // missing-classification covered above
      // Tuple form so a failure names the offending file.
      expect([rel, routeLanguage(relPathToRoute(rel))]).toEqual([rel, declared]);
    }
  });

  test("polyglot deep paths and future polyglot-* screens classify as 'el'", () => {
    expect(routeLanguage("/polyglot/some-deep-path")).toBe("el");
    expect(routeLanguage("/polyglot-anything-new")).toBe("el");
    expect(routeLanguage("/polyglot-lemma/1245")).toBe("el");
  });

  test("homePathFor returns the right entry route per language", () => {
    expect(homePathFor("ar")).toBe("/");
    expect(homePathFor("el")).toBe("/polyglot-review");
    // Latin shares the Polyglot surface — same entry route as Greek.
    expect(homePathFor("la")).toBe("/polyglot-review");
  });

  test("routeMatchesLanguage: a polyglot route matches both el and la actives", () => {
    const polyRoute = routeLanguage("/polyglot-review"); // "el" surface marker
    expect(routeMatchesLanguage(polyRoute, "el")).toBe(true);
    expect(routeMatchesLanguage(polyRoute, "la")).toBe(true);
    expect(routeMatchesLanguage(polyRoute, "ar")).toBe(false);

    const arRoute = routeLanguage("/"); // "ar"
    expect(routeMatchesLanguage(arRoute, "ar")).toBe(true);
    expect(routeMatchesLanguage(arRoute, "el")).toBe(false);
    expect(routeMatchesLanguage(arRoute, "la")).toBe(false);

    // Globe tab matches anything.
    expect(routeMatchesLanguage("shared", "la")).toBe(true);
  });
});
