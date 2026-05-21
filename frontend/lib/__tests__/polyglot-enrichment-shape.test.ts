/**
 * Shape parity guard between the backend Pydantic `LemmaEnrichment` model and
 * the frontend `LemmaEnrichment` TypeScript interface.
 *
 * The backend service writes a JSON payload validated by Pydantic. The
 * frontend reads the same JSON as a typed object. If either side drifts (a
 * field renamed, an enum value added, a required field made optional) without
 * the other catching up, runtime crashes happen at the user.
 *
 * This test loads a real Sonnet-generated enrichment fixture (4 lemmas
 * exercising etymology, multi-stage diachrony, multi-language cognates,
 * literary quotes, register), narrows it to the TS type, and asserts every
 * required field is populated. ts-jest catches type-level drift at compile
 * time; the runtime assertions catch shape-level drift the type system can't
 * see (e.g. era enum values that don't match POLYGLOT_ERA_COLORS).
 *
 * When updating the schema: regenerate the fixture via
 * artifacts/polyglot-enrichment-poc/build_prompt.py then run
 * `cp artifacts/polyglot-enrichment-poc/enrichment.json
 *     frontend/lib/__tests__/fixtures/polyglot-enrichment.json`
 * before pushing.
 */
import { readFileSync } from "fs";
import { join } from "path";
import {
  LemmaCognate,
  LemmaDiachronyStage,
  LemmaEnrichment,
  LemmaEra,
  LemmaQuote,
} from "../polyglot-api";
import { POLYGLOT_ERA_COLORS } from "../polyglot-design-colors";

type Fixture = {
  lemmas: { lemma_form: string; enrichment: LemmaEnrichment }[];
};

const KNOWN_ERAS: ReadonlySet<LemmaEra> = new Set(
  Object.keys(POLYGLOT_ERA_COLORS) as LemmaEra[],
);

function loadFixture(): Fixture {
  const path = join(__dirname, "fixtures/polyglot-enrichment.json");
  return JSON.parse(readFileSync(path, "utf8")) as Fixture;
}

describe("LemmaEnrichment shape parity", () => {
  const fixture = loadFixture();

  test("fixture contains the 4 POC lemmas", () => {
    const forms = fixture.lemmas.map((l) => l.lemma_form);
    expect(forms).toEqual(["άλογο", "λόγος", "καρδιά", "φιλία"]);
  });

  test.each(loadFixture().lemmas)(
    "$lemma_form: parses into LemmaEnrichment with all required fields",
    ({ lemma_form, enrichment }) => {
      expect(enrichment.version).toBe(1);

      // Etymology — origin_note is the only required string in the schema.
      expect(enrichment.etymology).not.toBeNull();
      expect(typeof enrichment.etymology!.origin_note).toBe("string");
      expect(enrichment.etymology!.origin_note.length).toBeGreaterThan(20);

      // Diachrony — at least one stage; every stage has a known era.
      expect(enrichment.diachrony.length).toBeGreaterThan(0);
      for (const stage of enrichment.diachrony) {
        expect(KNOWN_ERAS.has(stage.era)).toBe(true);
        expect(stage.form.length).toBeGreaterThan(0);
        expect(stage.meaning.length).toBeGreaterThan(0);
      }

      // Cognates — at least one; every entry has the required trio.
      expect(enrichment.cognates.length).toBeGreaterThan(0);
      for (const cog of enrichment.cognates) {
        expect(cog.language.length).toBeGreaterThan(0);
        expect(cog.form.length).toBeGreaterThan(0);
        expect(cog.relation.length).toBeGreaterThan(0);
      }

      // Quotes — required and at least one for the POC lemmas (philosophically
      // famous words — anything weighty enough to enrich should have a quote).
      expect(enrichment.quotes.length).toBeGreaterThan(0);
      for (const q of enrichment.quotes) {
        expect(KNOWN_ERAS.has(q.era)).toBe(true);
        expect(q.text.length).toBeGreaterThan(0);
        expect(q.translation_en.length).toBeGreaterThan(0);
        expect(q.source.length).toBeGreaterThan(0);
      }

      // Register — when present, collocations and false-friends are arrays.
      if (enrichment.register) {
        expect(Array.isArray(enrichment.register.collocations)).toBe(true);
        expect(Array.isArray(enrichment.register.false_friends_en)).toBe(true);
      }

      // Silence unused-var warning while keeping the destructure explicit.
      void lemma_form;
    },
  );

  test("every era in every stage maps to a design-token color", () => {
    for (const { enrichment } of fixture.lemmas) {
      for (const stage of enrichment.diachrony) {
        expect(POLYGLOT_ERA_COLORS[stage.era]).toBeDefined();
      }
      for (const q of enrichment.quotes) {
        expect(POLYGLOT_ERA_COLORS[q.era]).toBeDefined();
      }
    }
  });
});

// Compile-time narrowing helper — if any of these field names drift, TS will
// flag this file. Runtime not exercised.
function _typeOnlyCheck(e: LemmaEnrichment) {
  const _a: number = e.version;
  const _b: LemmaDiachronyStage[] = e.diachrony;
  const _c: LemmaCognate[] = e.cognates;
  const _d: LemmaQuote[] = e.quotes;
  void [_a, _b, _c, _d];
}
void _typeOnlyCheck;
