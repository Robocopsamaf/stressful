import { AnalyzedSentence, AnalyzeResponse, Settings } from "./types";

let backendUrl = "http://localhost:8765";
const cache = new Map<string, AnalyzedSentence>();
const translateCache = new Map<string, string>();
// Words whose request is currently in the air. The cue prefetch and a hover on
// a word from that same cue would otherwise ask for it twice, and this endpoint
// is rate-sensitive enough that duplicate requests cost real translations.
const inFlight = new Map<string, Promise<string>>();

export function primeSettings(s: Settings) {
  backendUrl = s.backendUrl.replace(/\/$/, "");
}

interface TranslateResponse {
  translations: string[];
}

// `pos` is parallel to `texts` (SpaCy UPOS per word). It picks the Wiktionary
// sense, so it's part of the cache key: the same word under two parts of speech
// is two different glosses.
export async function translateBatch(
  texts: string[],
  target: string,
  source = "ru",
  pos: string[] = [],
): Promise<string[]> {
  if (texts.length === 0 || !target || target === source) return texts.map(() => "");
  const out = new Array<string>(texts.length);
  const need: string[] = [];
  const needPos: string[] = [];
  const idx: number[] = [];
  const joins: Promise<void>[] = [];

  texts.forEach((t, i) => {
    const key = `${source}|${target}|${pos[i] ?? ""}|${t}`;
    const cached = translateCache.get(key);
    if (cached !== undefined) {
      out[i] = cached;
      return;
    }
    if (!t.trim()) {
      out[i] = "";
      return;
    }
    const pending = inFlight.get(key);
    if (pending) {
      joins.push(
        pending.then((tr) => {
          out[i] = tr;
        }),
      );
      return;
    }
    need.push(t);
    needPos.push(pos[i] ?? "");
    idx.push(i);
  });

  if (need.length > 0) {
    const request = (async () => {
      const resp = await fetch(`${backendUrl}/translate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texts: need, target, source, pos: needPos }),
      });
      if (!resp.ok) throw new Error(`translate failed: ${resp.status}`);
      return ((await resp.json()) as TranslateResponse).translations;
    })();

    // Publish one entry per word before awaiting, so a caller arriving while
    // this is still in the air joins it instead of starting a second request.
    need.forEach((t, k) => {
      inFlight.set(
        `${source}|${target}|${needPos[k]}|${t}`,
        request.then((trs) => trs[k] ?? "").catch(() => ""),
      );
    });

    try {
      const translations = await request;
      translations.forEach((tr, k) => {
        const src = need[k];
        // Don't cache blanks (rate-limited / failed) so they get retried later.
        if (tr) translateCache.set(`${source}|${target}|${needPos[k]}|${src}`, tr);
        out[idx[k]] = tr;
      });
    } finally {
      need.forEach((t, k) => inFlight.delete(`${source}|${target}|${needPos[k]}|${t}`));
    }
  }

  await Promise.all(joins);
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
