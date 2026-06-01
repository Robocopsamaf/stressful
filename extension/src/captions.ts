import { CaptionTrackInfo, Cue } from "./types";

interface CaptionTrackRaw {
  baseUrl: string;
  languageCode: string;
  kind?: string;
  name?: { simpleText?: string };
}

interface PlayerResponse {
  captions?: {
    playerCaptionsTracklistRenderer?: {
      captionTracks?: CaptionTrackRaw[];
    };
  };
}

function readPlayerResponse(): PlayerResponse | null {
  const w = window as unknown as { ytInitialPlayerResponse?: PlayerResponse };
  if (w.ytInitialPlayerResponse) return w.ytInitialPlayerResponse;
  for (const script of Array.from(document.querySelectorAll("script"))) {
    const txt = script.textContent ?? "";
    const m = txt.match(/ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var|\(function)/s);
    if (m) {
      try {
        return JSON.parse(m[1]) as PlayerResponse;
      } catch {
        /* keep looking */
      }
    }
  }
  return null;
}

export async function findSourceTrack(prefs: readonly string[]): Promise<CaptionTrackInfo | null> {
  for (let i = 0; i < 20; i++) {
    const pr = readPlayerResponse();
    const tracks = pr?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
    if (tracks && tracks.length > 0) {
      for (const lang of prefs) {
        const matching = tracks.filter((t) => t.languageCode === lang);
        if (matching.length === 0) continue;
        const manual = matching.find((t) => t.kind !== "asr");
        const pick = manual ?? matching[0];
        return {
          baseUrl: pick.baseUrl,
          languageCode: pick.languageCode,
          kind: pick.kind,
          name: pick.name?.simpleText,
        };
      }
      return null;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return null;
}

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
}

export function requestCues(videoId: string, lang: string, tlang: string, timeoutMs = 120000): Promise<RequestedCues> {
  return new Promise((resolve) => {
    const id = `sr-${Date.now()}-${Math.random()}`;
    const timer = window.setTimeout(() => {
      window.removeEventListener("message", handler);
      resolve({ src: [], tr: [] });
    }, timeoutMs + 5000);
    function handler(ev: MessageEvent) {
      const d = ev.data as { type?: string; id?: string; src?: string; tr?: string };
      if (!d || d.type !== "sr-captions-resp" || d.id !== id) return;
      window.clearTimeout(timer);
      window.removeEventListener("message", handler);
      console.log("[stressful] captions resp", { srcBytes: (d.src ?? "").length, trBytes: (d.tr ?? "").length });
      resolve({ src: parseBody(d.src ?? ""), tr: parseBody(d.tr ?? "") });
    }
    window.addEventListener("message", handler);
    window.postMessage({ type: "sr-captions-req", id, videoId, lang, tlang, timeoutMs }, "*");
  });
}
