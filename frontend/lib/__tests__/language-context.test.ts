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
 * The manifest below forces an explicit decision per screen. Any new file
 * in `app/` that isn't listed here fails the test with instructions.
 */
import * as fs from "fs";
import * as path from "path";
import { routeLanguage, homePathFor, type AppLanguage } from "../language-routes";

const APP_DIR = path.resolve(__dirname, "../../app");

// Explicit language assignment for every top-level screen in `frontend/app/`.
// Filename (without `.tsx`) → owning language. Detail-route subdirectories
// (e.g. `word/[id]`, `story/[id]`) are validated separately below.
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
  // ─── Greek (Polyglot) screens ───────────────────────────────────────
  polyglot: "el",
  "polyglot-review": "el",
  "polyglot-stats": "el",
  // ─── Shared (Globe tab) ─────────────────────────────────────────────
  languages: "shared",
};

function routeForFile(name: string): string {
  return name === "index" ? "/" : `/${name}`;
}

describe("routeLanguage classifier", () => {
  test("every screen in the manifest classifies as expected", () => {
    for (const [name, lang] of Object.entries(EXPECTED)) {
      expect(routeLanguage(routeForFile(name))).toBe(lang);
    }
  });

  test("manifest matches every .tsx file in app/ — add new screens here", () => {
    const onDisk = fs
      .readdirSync(APP_DIR)
      .filter((f) => f.endsWith(".tsx") && f !== "_layout.tsx")
      .map((f) => f.replace(/\.tsx$/, ""));

    const missing = onDisk.filter((s) => !(s in EXPECTED));
    const stale = Object.keys(EXPECTED).filter((s) => !onDisk.includes(s));

    if (missing.length > 0) {
      throw new Error(
        `New screen file(s) in frontend/app/ without language classification: ${missing.join(", ")}.\n` +
          `Add an entry to EXPECTED in this test file. If the screen is Greek, ` +
          `prefer renaming it to 'polyglot-<name>.tsx' so the URL-prefix rule ` +
          `classifies it automatically (see routeLanguage in lib/language-routes.ts).`,
      );
    }
    if (stale.length > 0) {
      throw new Error(
        `Manifest references screen(s) that no longer exist in frontend/app/: ${stale.join(", ")}. ` +
          `Remove from EXPECTED.`,
      );
    }
  });

  test("detail-route directories under Arabic side classify as 'ar'", () => {
    expect(routeLanguage("/word/123")).toBe("ar");
    expect(routeLanguage("/story/abc")).toBe("ar");
    expect(routeLanguage("/root/42")).toBe("ar");
    expect(routeLanguage("/pattern/CaCaCa")).toBe("ar");
  });

  test("polyglot deep paths and future polyglot-* screens classify as 'el'", () => {
    expect(routeLanguage("/polyglot/some-deep-path")).toBe("el");
    expect(routeLanguage("/polyglot-anything-new")).toBe("el");
    expect(routeLanguage("/polyglot-lemma/1245")).toBe("el");
  });

  test("homePathFor returns the right entry route per language", () => {
    expect(homePathFor("ar")).toBe("/");
    expect(homePathFor("el")).toBe("/polyglot");
  });
});
