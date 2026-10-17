import { afterEach, describe, expect, it, vi } from "vitest";
import { parseBody, parseJson3, parseXml } from "../src/captions-parse";

const json3 = (events: unknown[]) => JSON.stringify({ events });

describe("parseJson3", () => {
  it("converts milliseconds to seconds and joins segments", () => {
    const body = json3([
      { tStartMs: 1500, dDurationMs: 2250, segs: [{ utf8: "При" }, { utf8: "вет" }] },
    ]);
    expect(parseJson3(body)).toEqual([{ start: 1.5, dur: 2.25, text: "Привет" }]);
  });

  it.each([
    ["no segs", { tStartMs: 0, dDurationMs: 1000 }],
    ["no start", { dDurationMs: 1000, segs: [{ utf8: "x" }] }],
    ["null start", { tStartMs: null, segs: [{ utf8: "x" }] }],
    ["blank text", { tStartMs: 0, segs: [{ utf8: " \n " }] }],
    ["empty segs", { tStartMs: 0, segs: [] }],
  ])("skips an event with %s", (_name, ev) => {
    expect(parseJson3(json3([ev, { tStartMs: 7000, segs: [{ utf8: "ok" }] }]))).toEqual([
      { start: 7, dur: 0, text: "ok" },
    ]);
  });

  it("keeps an event that starts at zero", () => {
    expect(parseJson3(json3([{ tStartMs: 0, segs: [{ utf8: "a" }] }]))).toEqual([
      { start: 0, dur: 0, text: "a" },
    ]);
  });

  it("turns newlines into spaces, trims, and tolerates segments without text", () => {
    const body = json3([{ tStartMs: 0, segs: [{ utf8: "  один\nдва" }, {}, { utf8: "\nтри\n " }] }]);
    expect(parseJson3(body)[0].text).toBe("один два три");
  });

  it("replaces every newline, not just the first", () => {
    expect(parseJson3(json3([{ tStartMs: 0, segs: [{ utf8: "a\nb\nc" }] }]))[0].text).toBe("a b c");
  });

  it("returns nothing for a document without events", () => {
    expect(parseJson3("{}")).toEqual([]);
  });
});

describe("parseXml", () => {
  it("reads start, dur and decodes entities", () => {
    const body =
      '<?xml version="1.0"?><transcript>' +
      '<text start="1.25" dur="2.5">Tom &amp;amp; Jerry &amp;lt;3 &amp;gt; &amp;quot;hi&amp;quot; it&amp;#39;s</text>' +
      "</transcript>";
    expect(parseXml(body)).toEqual([{ start: 1.25, dur: 2.5, text: `Tom & Jerry <3 > "hi" it's` }]);
  });

  it("decodes every occurrence of each entity", () => {
    const body =
      "<transcript><text start=\"0\">&amp;amp;&amp;amp; &amp;lt;&amp;lt; &amp;gt;&amp;gt; " +
      "&amp;quot;&amp;quot; &amp;#39;&amp;#39;</text></transcript>";
    expect(parseXml(body)[0].text).toBe(`&& << >> "" ''`);
  });

  it("defaults a missing start and dur to zero", () => {
    expect(parseXml("<transcript><text>a</text></transcript>")).toEqual([{ start: 0, dur: 0, text: "a" }]);
  });

  it("joins lines, trims and skips blank cues", () => {
    const body = '<transcript><text start="1">  a\nb\nc </text><text start="2">\n </text></transcript>';
    expect(parseXml(body)).toEqual([{ start: 1, dur: 0, text: "a b c" }]);
  });
});

describe("parseBody", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns nothing for an empty body, without warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(parseBody("")).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
  });

  it("dispatches on a leading '<', ignoring leading whitespace", () => {
    expect(parseBody('  \n<transcript><text start="3">x</text></transcript>')).toEqual([
      { start: 3, dur: 0, text: "x" },
    ]);
  });

  it("treats anything else as json3", () => {
    expect(parseBody(json3([{ tStartMs: 3000, segs: [{ utf8: "x" }] }]))).toEqual([
      { start: 3, dur: 0, text: "x" },
    ]);
  });

  it("returns nothing and warns on a malformed body", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(parseBody("{not json")).toEqual([]);
    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0][0]).toBe("[stressful] caption parse failed");
  });
});
