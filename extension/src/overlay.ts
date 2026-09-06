import { AnalyzedSentence, Cue, Settings, Token } from "./types";

export interface OverlayDeps {
  root: HTMLElement;
  video: HTMLVideoElement;
  russianCues: Cue[];
  settings: Settings;
  analyze: (texts: string[]) => Promise<AnalyzedSentence[]>;
  // Warm the translation cache for a cue's words as it appears on screen, so a
  // hover is a cache hit instead of a round trip to Google.
  prefetchWords?: (words: { text: string; pos: string }[]) => void;
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

function tokenToSpan(tok: Token, settings: Settings): HTMLElement {
  const span = document.createElement("span");
  const klass = ["sr-tok"];
  if (settings.showColors && tok.is_word) klass.push(`sr-${tok.color_class}`);
  span.className = klass.join(" ");
  span.textContent = settings.showStress ? tok.accented : tok.surface;
  if (settings.showTooltips && tok.is_word) {
    span.dataset.morph = JSON.stringify(tok.morph);
    // Unaccented form: what the tooltip compares against the lemma, so a word
    // that is already in its dictionary form doesn't get a redundant lemma row
    // just because the rendered text carries a stress mark.
    span.dataset.surface = tok.surface;
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
  const { root, video, russianCues, settings, analyze, prefetchWords, onAnalyzeError, onAnalyzeOk } = deps;

  const wrap = document.createElement("div");
  wrap.id = "sr-overlay";
  const pos = Math.max(0, Math.min(100, settings.overlayPosition));
  wrap.style.top = `${pos}%`;
  const ruLine = document.createElement("div");
  ruLine.className = "sr-line sr-ru";
  wrap.appendChild(ruLine);
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
      console.warn("[stressful-russian] analyze failed", e);
      onAnalyzeError?.(e);
    } finally {
      need.forEach((n) => pending.delete(n.idx));
    }
  }

  // The lemmas the tooltip would ask for if the user hovered each word of this
  // cue. Deduped here; api.ts dedupes again across cues.
  function prefetchFor(sentence: AnalyzedSentence) {
    if (!prefetchWords || !settings.showTooltips) return;
    const seen = new Set<string>();
    const words: { text: string; pos: string }[] = [];
    for (const tok of sentence.tokens) {
      if (!tok.is_word) continue;
      const w = (tok.lemma || tok.surface).trim();
      // Keyed by word+POS to match the gloss cache: the same lemma under two
      // readings is two different entries.
      const key = `${w}|${tok.pos}`;
      if (!w || seen.has(key)) continue;
      seen.add(key);
      words.push({ text: w, pos: tok.pos });
    }
    if (words.length > 0) prefetchWords(words);
  }

  let currentIdx = -2;
  let raf = 0;
  let stopped = false;

  function loop() {
    if (stopped) return;
    raf = requestAnimationFrame(loop);

    // During ads YouTube plays the ad in the SAME <video> element, so
    // currentTime resets toward 0 and we'd wrongly show the video's first cue.
    // #movie_player carries the `ad-showing` class while an ad plays.
    if (root.classList.contains("ad-showing")) {
      if (ruLine.textContent) ruLine.textContent = "";
      currentIdx = -2; // force a re-render once the ad ends
      return;
    }

    const t = video.currentTime;
    const idx = findCueIndex(russianCues, t);

    if (idx === currentIdx) return;
    currentIdx = idx;
    if (idx < 0) {
      ruLine.textContent = "";
      return;
    }
    const cue = russianCues[idx];
    const analyzedSentence = analyzed.get(idx);
    if (analyzedSentence) {
      renderAnalyzed(ruLine, analyzedSentence, settings);
      prefetchFor(analyzedSentence);
    } else {
      ruLine.textContent = cue.text;
      ensureAnalyzed([idx]).then(() => {
        if (currentIdx === idx) {
          const s = analyzed.get(idx);
          if (s) {
            renderAnalyzed(ruLine, s, settings);
            prefetchFor(s);
          }
        }
      });
    }
    const prefetch: number[] = [];
    for (let k = 1; k <= PREFETCH_AHEAD; k++) prefetch.push(idx + k);
    ensureAnalyzed(prefetch);
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
