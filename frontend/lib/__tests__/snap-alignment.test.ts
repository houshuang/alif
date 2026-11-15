import {
  alignSentences,
  firstArabicToken,
  linkForArabic,
  linkForEnglish,
  matchKey,
  paragraphsOf,
} from "../snap-alignment";

const page = [
  {
    p: 0,
    ar: "سَافَرْنَا إِلَى الْبَحْرِ الْمَيِّتِ.",
    en: "We traveled to the Dead Sea.",
    links: [
      { ar: "سَافَرْنَا", en: "We traveled" },
      { ar: "الْبَحْرِ الْمَيِّتِ", en: "Dead Sea" },
    ],
  },
  { p: 0, ar: "كان الماء مالحاً.", en: "The water was salty.", links: [{ ar: "الماء", en: "water" }] },
  { p: 1, ar: "رجعنا.", en: "We returned.", links: [] },
];

describe("snap alignment", () => {
  it("strips harakat and punctuation from match keys", () => {
    expect(matchKey("الْمَيِّتِ.")).toBe("الميت");
    expect(matchKey("Sea.")).toBe("sea");
  });

  it("maps an English word to its Arabic phrase and back", () => {
    const [s] = alignSentences(page);
    const seaIndex = s.enTokens.findIndex((t) => t.text === "Sea.");
    const link = linkForEnglish(s, seaIndex);
    expect(link).toBe(1);
    expect(firstArabicToken(s, link!)?.text).toBe("الْبَحْرِ");

    const dead = s.arTokens.find((t) => t.text === "الْمَيِّتِ.")!;
    expect(linkForArabic(s, dead)).toBe(1);
    const covered = [...s.links[1].en].map((i) => s.enTokens[i].text);
    expect(covered).toEqual(["Dead", "Sea."]);
  });

  it("leaves unlinked words unmapped", () => {
    const [s] = alignSentences(page);
    const to = s.enTokens.findIndex((t) => t.text === "to");
    expect(linkForEnglish(s, to)).toBeNull();
  });

  it("drops links whose English is not in the sentence", () => {
    const [s] = alignSentences([{ p: 0, ar: "كتاب", en: "A book", links: [{ ar: "كتاب", en: "novel" }] }]);
    expect(s.links).toEqual([]);
  });

  it("prefers an unused occurrence of a repeated English word", () => {
    const [s] = alignSentences([{
      p: 0, ar: "رأى رجلاً ورأى امرأة", en: "He saw a man and saw a woman",
      links: [{ ar: "رأى", en: "saw" }, { ar: "ورأى", en: "saw" }],
    }]);
    const spans = s.links.map((l) => [...l.en]);
    expect(spans[0]).not.toEqual(spans[1]);
  });

  it("groups sentences by paragraph in order", () => {
    const groups = paragraphsOf(alignSentences(page));
    expect(groups.map((g) => g.length)).toEqual([2, 1]);
  });
});
