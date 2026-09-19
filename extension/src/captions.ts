import { Cue } from "./types";

interface Json3Seg {
  utf8?: string;
}
interface Json3Event {
  tStartMs?: number;
  dDurationMs?: number;
  segs?: Json3Seg[];
}
interface Json3Doc {
  events?: Json3Event[];
}

function parseJson3(body: string): Cue[] {
  const doc = JSON.parse(body) as Json3Doc;
  const cues: Cue[] = [];
  for (const ev of doc.events ?? []) {
    if (!ev.segs || ev.tStartMs == null) continue;
    const text = ev.segs
      .map((s) => s.utf8 ?? "")
      .join("")
      .replace(/\n/g, " ")
      .trim();
    if (!text) continue;
    cues.push({
      start: ev.tStartMs / 1000,
      dur: (ev.dDurationMs ?? 0) / 1000,
      text,
    });
  }
  return cues;
}

function parseXml(body: string): Cue[] {
  const doc = new DOMParser().parseFromString(body, "text/xml");
  const cues: Cue[] = [];
  for (const node of Array.from(doc.getElementsByTagName("text"))) {
    const start = parseFloat(node.getAttribute("start") ?? "0");
    const dur = parseFloat(node.getAttribute("dur") ?? "0");
    const raw = node.textContent ?? "";
    const text = raw
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/\n/g, " ")
      .trim();
    if (!text) continue;
    cues.push({ start, dur, text });
  }
  return cues;
}

function parseBody(body: string): Cue[] {
  if (!body) return [];
  try {
    return body.trimStart().startsWith("<") ? parseXml(body) : parseJson3(body);
  } catch (e) {
    console.warn("[stressful] caption parse failed", e);
    return [];
  }
}

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
