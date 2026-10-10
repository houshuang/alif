import { isRememberedTab, resumeTab } from "../last-tab";
it("resumes Parallel from a normal cold start in any active language", () => {
  for (const language of ["ar", "el", "la"] as const) expect(resumeTab("/", false, "/parallel", language)).toBe("/parallel");
});
it("preserves explicit bookmarks, query links and native deep links", () => {
  expect(resumeTab("/read", false, "/parallel", "ar")).toBeNull();
  expect(resumeTab("/book-page", true, "/parallel", "ar")).toBeNull();
  expect(resumeTab("/", true, "/parallel", "ar")).toBeNull();
  expect(resumeTab("/", false, "/parallel", "ar", true)).toBeNull();
});
it("does not restore obsolete/detail routes or the wrong language surface", () => {
  for (const path of [null, "garbage", "/word/2", "/read", "/polyglot-review"]) expect(resumeTab("/", false, path, "ar")).toBeNull();
  expect(resumeTab("/", false, "/stats", "ar")).toBe("/stats");
  expect(resumeTab("/", false, "/polyglot-review", "la")).toBe("/polyglot-review");
  expect(isRememberedTab("/book-page")).toBe(false);
});
