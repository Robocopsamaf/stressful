import { AnalyzedSentence, AnalyzeResponse, Settings } from "./types";

let backendUrl = "http://localhost:8765";
const cache = new Map<string, AnalyzedSentence>();
const translateCache = new Map<string, string>();

export function primeSettings(s: Settings) {
  backendUrl = s.backendUrl.replace(/\/$/, "");
}

interface TranslateResponse {
  translations: string[];
}

export async function translateBatch(texts: string[], target: string, source = "ru"): Promise<string[]> {
  if (texts.length === 0 || !target || target === source) return texts.map(() => "");
  const need: string[] = [];
  const idx: number[] = [];
  const out = new Array<string>(texts.length);
  texts.forEach((t, i) => {
    const key = `${source}|${target}|${t}`;
    const cached = translateCache.get(key);
    if (cached !== undefined) {
      out[i] = cached;
    } else if (!t.trim()) {
      out[i] = "";
    } else {
      need.push(t);
      idx.push(i);
    }
  });
  if (need.length > 0) {
    const resp = await fetch(`${backendUrl}/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texts: need, target, source }),
    });
    if (!resp.ok) throw new Error(`translate failed: ${resp.status}`);
    const data = (await resp.json()) as TranslateResponse;
    data.translations.forEach((tr, k) => {
      const src = need[k];
      // Don't cache blanks (rate-limited / failed) so they get retried later.
      if (tr) translateCache.set(`${source}|${target}|${src}`, tr);
      out[idx[k]] = tr;
    });
  }
  return out;
}

export async function analyzeBatch(texts: string[], source = "ru"): Promise<AnalyzedSentence[]> {
  const keyOf = (t: string) => `${source}|${t}`;
  const need: string[] = [];
  const indexMap: number[] = [];
  texts.forEach((t, i) => {
    if (!cache.has(keyOf(t))) {
      need.push(t);
      indexMap.push(i);
    }
  });
  if (need.length > 0) {
    const resp = await fetch(`${backendUrl}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sentences: need, source }),
    });
    if (!resp.ok) throw new Error(`analyze failed: ${resp.status}`);
    const data = (await resp.json()) as AnalyzeResponse;
    data.sentences.forEach((s, k) => cache.set(keyOf(need[k]), s));
  }
  return texts.map((t) => cache.get(keyOf(t))!);
}
