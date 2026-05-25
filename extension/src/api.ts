import { AnalyzedSentence, AnalyzeResponse, Settings } from "./types";

let backendUrl = "http://localhost:8765";
const cache = new Map<string, AnalyzedSentence>();

export function primeSettings(s: Settings) {
  backendUrl = s.backendUrl.replace(/\/$/, "");
}

export async function analyzeBatch(texts: string[]): Promise<AnalyzedSentence[]> {
  const need: string[] = [];
  const indexMap: number[] = [];
  texts.forEach((t, i) => {
    if (!cache.has(t)) {
      need.push(t);
      indexMap.push(i);
    }
  });
  if (need.length > 0) {
    const resp = await fetch(`${backendUrl}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sentences: need }),
    });
    if (!resp.ok) throw new Error(`analyze failed: ${resp.status}`);
    const data = (await resp.json()) as AnalyzeResponse;
    data.sentences.forEach((s, k) => cache.set(need[k], s));
  }
  return texts.map((t) => cache.get(t)!);
}
