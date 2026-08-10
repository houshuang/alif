import { statusBarStyleForPath } from "../status-bar-style";

describe("statusBarStyleForPath", () => {
  it.each([
    "/polyglot",
    "/polyglot-review",
    "/polyglot-stats",
    "/polyglot-lemma/42",
    "/book-page",
  ])("uses dark system text on light route %s", (pathname) => {
    expect(statusBarStyleForPath(pathname)).toBe("dark");
  });

  it.each(["/", "/stats", "/review", "/languages", "/books"])(
    "uses light system text on dark route %s",
    (pathname) => {
      expect(statusBarStyleForPath(pathname)).toBe("light");
    },
  );
});
