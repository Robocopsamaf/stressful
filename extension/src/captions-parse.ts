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

export function parseJson3(body: string): Cue[] {
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

export function parseXml(body: string): Cue[] {
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

export function parseBody(body: string): Cue[] {
  if (!body) return [];
  try {
    return body.trimStart().startsWith("<") ? parseXml(body) : parseJson3(body);
  } catch (e) {
    console.warn("[stressful] caption parse failed", e);
    return [];
  }
}
