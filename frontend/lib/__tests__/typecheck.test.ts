/**
 * Runs `tsc --noEmit` to catch compile errors across the entire frontend.
 * This prevents crashes caused by missing imports, wrong types, etc.
 */
import { execSync } from "child_process";
import path from "path";

const ROOT = path.resolve(__dirname, "../..");

test("TypeScript compiles without errors", () => {
  try {
    execSync("npx tsc --noEmit", { cwd: ROOT, encoding: "utf-8", timeout: 60_000 });
  } catch (err: any) {
    // stdout/stderr contains the TS errors
    const output = (err.stdout || "") + (err.stderr || "");
    fail(`TypeScript compilation failed:\n${output}`);
  }
});
