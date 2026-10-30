import { sliceSurface } from "../review/surface-parts";

describe("sliceSurface", () => {
  it("keeps the vocalization of each printed piece", () => {
    expect(sliceSurface("وَشَوَارِعُهَا", ["و"], ["ها"])).toEqual({
      prefixes: ["وَ"],
      stem: "شَوَارِعُ",
      suffixes: ["هَا"],
    });
  });

  it("allows the elided alif of لِ + الْ", () => {
    expect(sliceSurface("لِلْأُسْرَةِ،", ["ل", "ال"], [])).toEqual({
      prefixes: ["لِ", "لْ"],
      stem: "أُسْرَةِ",
      suffixes: [],
    });
  });

  it("ignores trailing punctuation and folds hamza seats", () => {
    expect(sliceSurface("أَسْمَعُهُ.", [], ["ه"])).toEqual({
      prefixes: [],
      stem: "أَسْمَعُ",
      suffixes: ["هُ"],
    });
  });

  it("returns null when the clitics do not line up with the surface", () => {
    expect(sliceSurface("كِتَابٌ", ["و"], [])).toBeNull();
  });
});
