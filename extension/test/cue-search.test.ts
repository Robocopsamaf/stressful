import { describe, expect, it } from "vitest";
import { findCueIndex, findTranslatedFor } from "../src/cue-search";
import type { Cue } from "../src/types";

const cue = (start: number, dur: number, text = `${start}`): Cue => ({ start, dur, text });

// Gaps between cues on purpose: 0-2, 3-4, 10-10.05 (floored to 0.1), 20-25.
const cues = [cue(0, 2), cue(3, 1), cue(10, 0.05), cue(20, 5)];

describe("findCueIndex", () => {
  it.each([
    [-1, -1],
    [0, 0],
    [1, 0],
    [2, 0], // end is inclusive
    [2.01, -1], // gap
    [3, 1],
    [4, 1],
    [4.5, -1],
    [10, 2],
    [10.1, 2], // dur floored to 0.1
    [10.11, -1],
    [19.99, -1],
    [20, 3],
    [24.9, 3],
    [25, 3],
    [25.01, -1],
    [1000, -1],
  ])("t=%s -> %s", (t, idx) => {
    expect(findCueIndex(cues, t)).toBe(idx);
  });

  it("finds nothing in an empty track", () => {
    expect(findCueIndex([], 0)).toBe(-1);
  });

  it("finds every cue of a long track", () => {
    const many = Array.from({ length: 101 }, (_, i) => cue(i * 2, 1));
    for (let i = 0; i < many.length; i++) {
      expect(findCueIndex(many, i * 2 + 0.5)).toBe(i);
      expect(findCueIndex(many, i * 2 + 1.5)).toBe(-1);
    }
  });

  it("prefers the latest cue that has started when cues overlap", () => {
    expect(findCueIndex([cue(0, 10), cue(1, 10)], 5)).toBe(1);
  });

  it("treats a negative duration as the 0.1s floor", () => {
    expect(findCueIndex([cue(5, -3)], 5.1)).toBe(0);
    expect(findCueIndex([cue(5, -3)], 5.11)).toBe(-1);
  });
});

describe("findTranslatedFor", () => {
  const tr = [cue(0, 2, "a"), cue(3, 1, "b"), cue(10, 0, "c"), cue(20, 5, "d")];

  it.each([
    [cue(0, 2), "a"], // mid 1
    [cue(0, 4), "a"], // mid 2, inclusive end
    [cue(2, 3), "b"], // mid 3.5
    [cue(4, 0), "b"], // mid 4, inclusive end
    [cue(4, 1), null], // mid 4.5, gap
    [cue(9.9, 0.2), "c"], // mid 10, start inclusive
    [cue(10, 0.2), "c"], // mid 10.1, floored dur
    [cue(10, 0.4), null], // mid 10.2
    [cue(15, 20), "d"], // mid 25
    [cue(-5, 2), null], // before everything
    [cue(100, 2), null], // after everything
  ])("source %o -> %s", (src, text) => {
    expect(findTranslatedFor(tr, src)?.text ?? null).toBe(text);
  });

  it("finds nothing in an empty track", () => {
    expect(findTranslatedFor([], cue(0, 1))).toBeNull();
  });

  it("returns the cue object itself", () => {
    expect(findTranslatedFor(tr, cue(20, 2))).toBe(tr[3]);
  });

  it("finds every cue of a long track", () => {
    const many = Array.from({ length: 101 }, (_, i) => cue(i * 2, 1, `${i}`));
    for (let i = 0; i < many.length; i++) {
      expect(findTranslatedFor(many, cue(i * 2, 1))?.text).toBe(`${i}`);
      expect(findTranslatedFor(many, cue(i * 2 + 1, 1))).toBeNull();
    }
  });
});
