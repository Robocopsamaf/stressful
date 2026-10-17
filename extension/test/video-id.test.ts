import { describe, expect, it } from "vitest";
import { videoIdFromHref } from "../src/video-id";

describe("videoIdFromHref", () => {
  it.each([
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://www.youtube.com/watch?list=x&v=abc&t=3", "abc"],
    ["https://www.youtube.com/shorts/aB3_-x9", "aB3_-x9"],
    ["https://www.youtube.com/live/abcdef?si=1", "abcdef"],
    ["https://www.youtube.com/embed/abcdef/extra", "abcdef"],
    ["https://www.youtube.com/v/abcdef", "abcdef"],
    // ?v= wins over the path.
    ["https://www.youtube.com/shorts/pathid?v=queryid", "queryid"],
    // An empty ?v= falls through to the path.
    ["https://www.youtube.com/shorts/pathid?v=", "pathid"],
  ])("%s -> %s", (href, id) => {
    expect(videoIdFromHref(href)).toBe(id);
  });

  it.each([
    "https://www.youtube.com/",
    "https://www.youtube.com/feed/subscriptions",
    "https://www.youtube.com/shorts/abcde", // too short
    "https://www.youtube.com/shorts/",
    "https://www.youtube.com/channel/shorts/abcdef", // not at the root
    "https://www.youtube.com/xshorts/abcdef",
    "https://www.youtube.com/shorts/!!!!!!",
    "not a url",
    "",
  ])("%s -> null", (href) => {
    expect(videoIdFromHref(href)).toBeNull();
  });
});
