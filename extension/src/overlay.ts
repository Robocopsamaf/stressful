import { AnalyzedSentence, Cue, Settings, Token } from "./types";

export interface OverlayDeps {
  root: HTMLElement;
  video: HTMLVideoElement;
  russianCues: Cue[];
  translatedCues: Cue[];
  translatedByIdx?: (string | undefined)[];
  settings: Settings;
  analyze: (texts: string[]) => Promise<AnalyzedSentence[]>;
  onAnalyzeError?: (err: unknown) => void;
  onAnalyzeOk?: () => void;
}

export interface Overlay {
  destroy(): void;
  applySettings(s: Settings): void;
}

const PREFETCH_AHEAD = 30;

function findCueIndex(cues: Cue[], t: number): number {
  let lo = 0;
  let hi = cues.length - 1;
  let best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    const c = cues[mid];
    if (c.start <= t) {
      best = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (best < 0) return -1;
  const c = cues[best];
  if (c.start + Math.max(c.dur, 0.1) < t) return -1;
  return best;
}

function findTranslatedFor(cues: Cue[], target: Cue): Cue | null {
  const mid = target.start + target.dur / 2;
  let lo = 0;
  let hi = cues.length - 1;
  while (lo <= hi) {
    const m = (lo + hi) >>> 1;
    const c = cues[m];
    if (c.start <= mid && c.start + Math.max(c.dur, 0.1) >= mid) return c;
    if (c.start < mid) lo = m + 1;
    else hi = m - 1;
  }
  return null;
}

function tokenToSpan(tok: Token, settings: Settings): HTMLElement {
  const span = document.createElement("span");
  const klass = ["sr-tok"];
  if (settings.showColors && tok.is_word) klass.push(`sr-${tok.color_class}`);
  span.className = klass.join(" ");
  span.textContent = settings.showStress ? tok.accented : tok.surface;
  if (settings.showTooltips && tok.is_word) {
    span.dataset.morph = JSON.stringify(tok.morph);
    span.dataset.lemma = tok.lemma;
    span.dataset.pos = tok.pos;
  }
  return span;
}

const CLOSE_PUNCT = new Set([",", ".", "!", "?", ";", ":", ")", "]", "}", "»", "”", "’", "…"]);
const OPEN_PUNCT = new Set(["(", "[", "{", "«", "“", "‘"]);

function renderAnalyzed(target: HTMLElement, sentence: AnalyzedSentence, settings: Settings) {
  target.innerHTML = "";
  const tokens = sentence.tokens;
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (i > 0) {
      const prev = tokens[i - 1];
      const needSpace = !CLOSE_PUNCT.has(tok.surface) && !OPEN_PUNCT.has(prev.surface);
      if (needSpace) target.appendChild(document.createTextNode(" "));
    }
    target.appendChild(tokenToSpan(tok, settings));
  }
}

export function mountOverlay(deps: OverlayDeps): Overlay {
  const { root, video, russianCues, translatedCues, translatedByIdx, settings, analyze, onAnalyzeError, onAnalyzeOk } = deps;

  const wrap = document.createElement("div");
  wrap.id = "sr-overlay";
  const pos = Math.max(0, Math.min(100, settings.overlayPosition));
  wrap.style.top = `${pos}%`;
  const ruLine = document.createElement("div");
  ruLine.className = "sr-line sr-ru";
  const trLine = document.createElement("div");
  trLine.className = "sr-line sr-tr";
  wrap.appendChild(ruLine);
  wrap.appendChild(trLine);
  root.appendChild(wrap);

  const analyzed = new Map<number, AnalyzedSentence>();
  const pending = new Set<number>();

  async function ensureAnalyzed(indices: number[]) {
    const need: { idx: number; text: string }[] = [];
    for (const i of indices) {
      if (i < 0 || i >= russianCues.length) continue;
      if (analyzed.has(i) || pending.has(i)) continue;
      pending.add(i);
      need.push({ idx: i, text: russianCues[i].text });
    }
    if (need.length === 0) return;
    try {
      const result = await analyze(need.map((n) => n.text));
      result.forEach((s, k) => analyzed.set(need[k].idx, s));
      onAnalyzeOk?.();
    } catch (e) {
      console.warn("[stressful] analyze failed", e);
      onAnalyzeError?.(e);
    } finally {
      need.forEach((n) => pending.delete(n.idx));
    }
  }

  let currentIdx = -2;
  let lastTrText = "";
  let raf = 0;
  let stopped = false;

  function loop() {
    if (stopped) return;
    raf = requestAnimationFrame(loop);
    const t = video.currentTime;
    const idx = findCueIndex(russianCues, t);

    if (idx !== currentIdx) {
      currentIdx = idx;
      if (idx < 0) {
        ruLine.textContent = "";
        trLine.textContent = "";
        lastTrText = "";
        return;
      }
      const cue = russianCues[idx];
      const analyzedSentence = analyzed.get(idx);
      if (analyzedSentence) {
        renderAnalyzed(ruLine, analyzedSentence, settings);
      } else {
        ruLine.textContent = cue.text;
        ensureAnalyzed([idx]).then(() => {
          if (currentIdx === idx) {
            const s = analyzed.get(idx);
            if (s) renderAnalyzed(ruLine, s, settings);
          }
        });
      }
      const prefetch: number[] = [];
      for (let k = 1; k <= PREFETCH_AHEAD; k++) prefetch.push(idx + k);
      ensureAnalyzed(prefetch);
    }

    // Update translation each frame so async backfill becomes visible mid-cue.
    // Prefer the index-keyed map (exact 1:1 with source cues) when available;
    // fall back to time-based lookup for the YT-tlang track whose cues have
    // their own timings.
    let next = "";
    if (currentIdx >= 0) {
      const fromIdx = translatedByIdx ? translatedByIdx[currentIdx] : undefined;
      if (fromIdx) {
        next = fromIdx;
      } else if (translatedCues.length > 0) {
        const tcue = findTranslatedFor(translatedCues, russianCues[currentIdx]);
        if (tcue) next = tcue.text;
      }
    }
    if (next !== lastTrText) {
      trLine.textContent = next;
      lastTrText = next;
    }
  }

  raf = requestAnimationFrame(loop);

  return {
    destroy() {
      stopped = true;
      cancelAnimationFrame(raf);
      wrap.remove();
    },
    applySettings(s: Settings) {
      const next = Math.max(0, Math.min(100, s.overlayPosition));
      wrap.style.top = `${next}%`;
      Object.assign(settings, s);
      currentIdx = -2;
    },
  };
}
