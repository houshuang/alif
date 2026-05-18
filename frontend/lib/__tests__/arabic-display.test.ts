import { bestVocalizedDisplayForm, lexicalVocalizationScore } from "../arabic-display";

describe("arabic display helpers", () => {
  it("treats final tanwin alone as not meaningfully vocalized", () => {
    expect(lexicalVocalizationScore("محظوظةً")).toBe(0);
    expect(lexicalVocalizationScore("مَحْظُوظَة")).toBeGreaterThan(0);
  });

  it("uses a matching vocalized form over a case-ending-only lemma", () => {
    const display = bestVocalizedDisplayForm({
      lemmaAr: "محظوظةً",
      lemmaTransliteration: "mḥẓwẓa",
      forms: { feminine: "مَحْظُوظَة" } as any,
      formsTranslit: { feminine: "maḥẓūẓa" },
    });

    expect(display.arabic).toBe("مَحْظُوظَة");
    expect(display.transliteration).toBe("maḥẓūẓa");
  });

  it("does not replace an already-vocalized lemma with a definite surface", () => {
    const display = bestVocalizedDisplayForm({
      lemmaAr: "كِتَاب",
      lemmaTransliteration: "kitāb",
      surfaceForm: "الْكِتَابُ",
      surfaceTranslit: "al-kitābu",
    });

    expect(display.arabic).toBe("كِتَاب");
    expect(display.transliteration).toBe("kitāb");
  });
});
