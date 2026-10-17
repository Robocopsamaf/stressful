import { Cue } from "./types";
import { parseBody } from "./captions-parse";

export interface RequestedCues {
  src: Cue[];
  tr: Cue[];
  /** Caption language the bridge actually captured, "" when nothing arrived. */
  lang: string;
}

/**
 * Ask the page-world bridge for whichever of `langs` the user turns on.
 *
 * The source language is deliberately not decided here: `ytInitialPlayerResponse`
 * is unreachable from the isolated world, and the server-rendered copy in the
 * DOM still describes the *first* video after an SPA navigation. The bridge
 * sees the real timedtext request instead, so it reports the language back.
 */
export function requestCues(
  videoId: string,
  langs: readonly string[],
  tlang: string,
  timeoutMs = 20000,
): Promise<RequestedCues> {
  return new Promise((resolve) => {
    const id = `sr-${Date.now()}-${Math.random()}`;
    const empty: RequestedCues = { src: [], tr: [], lang: "" };
    const timer = window.setTimeout(() => {
      window.removeEventListener("message", handler);
      resolve(empty);
    }, timeoutMs + 5000);
    function handler(ev: MessageEvent) {
      const d = ev.data as { type?: string; id?: string; src?: string; tr?: string; lang?: string };
      if (!d || d.type !== "sr-captions-resp" || d.id !== id) return;
      window.clearTimeout(timer);
      window.removeEventListener("message", handler);
      console.log("[stressful] captions resp", {
        lang: d.lang,
        srcBytes: (d.src ?? "").length,
        trBytes: (d.tr ?? "").length,
      });
      resolve({ src: parseBody(d.src ?? ""), tr: parseBody(d.tr ?? ""), lang: d.lang ?? "" });
    }
    window.addEventListener("message", handler);
    window.postMessage(
      { type: "sr-captions-req", id, videoId, langs: Array.from(langs), tlang, timeoutMs },
      "*",
    );
  });
}
