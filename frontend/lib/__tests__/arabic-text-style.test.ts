import * as fs from "fs";
import * as path from "path";
import { arabicText, fontFamily } from "../theme";

const ROOT = path.resolve(__dirname, "../..");
const SOURCE_DIRS = ["app", "lib", "components"];

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "__tests__" ? [] : sourceFiles(full);
    return entry.name.endsWith(".tsx") ? [full] : [];
  });
}

describe("arabicText", () => {
  it("adds the room iOS crops above a tight Scheherazade line", () => {
    // 32pt at 46pt line height: 0.70 em kept below leaves 23.6pt above, marks need 43.2pt.
    expect(arabicText(32, 46)).toEqual({ fontFamily: fontFamily.arabic, fontSize: 32, lineHeight: 46, paddingTop: 20 });
  });

  it("adds nothing when the line already has room", () => {
    expect(arabicText(20, 42).paddingTop).toBe(0);
  });

  it("uses the metrics of the font it is given", () => {
    expect(arabicText(22, 27, fontFamily.arabicNoto).paddingTop).toBeLessThan(arabicText(22, 27).paddingTop);
  });
});

describe("Arabic styles go through arabicText", () => {
  it("has no hand-set lineHeight on Arabic text", () => {
    const offenders: string[] = [];
    for (const dir of SOURCE_DIRS) {
      for (const file of sourceFiles(path.join(ROOT, dir))) {
        const src = fs.readFileSync(file, "utf8");
        for (const m of src.matchAll(/\{[^{}]*\}/g)) {
          const obj = m[0];
          if (!/fontFamily:\s*(fontFamily\.arabic|arabicFont)/.test(obj)) continue;
          if (!/lineHeight:/.test(obj) || obj.includes("arabic-line-ok:")) continue;
          const line = src.slice(0, m.index).split("\n").length;
          offenders.push(`${path.relative(ROOT, file)}:${line}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("has no Arabic-named style that changes lineHeight on its own", () => {
    // A bare lineHeight override keeps the base style's paddingTop, which was
    // sized for the old line height (the revealed sentence cropped its shadda).
    const offenders: string[] = [];
    for (const dir of SOURCE_DIRS) {
      for (const file of sourceFiles(path.join(ROOT, dir))) {
        const src = fs.readFileSync(file, "utf8");
        for (const m of src.matchAll(/\b\w*(?:Arabic|arabic)\w*:\s*\{[^{}]*lineHeight:[^{}]*\}/g)) {
          if (m[0].includes("arabic-line-ok:")) continue;
          offenders.push(`${path.relative(ROOT, file)}:${src.slice(0, m.index).split("\n").length}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
