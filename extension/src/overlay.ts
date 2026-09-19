import { AnalyzedSentence, Cue, Settings, Token } from "./types";
import { hideTooltip } from "./tooltip";

export interface OverlayDeps {
  root: HTMLElement;
  video: HTMLVideoElement;
  russianCues: Cue[];
  /** Dual mode only: a YouTube `tlang` track, with its own cue timings. */
  translatedCues?: Cue[];
  /** Dual mode only: backend translations, index-aligned 1:1 with `russianCues`
   *  and filled in asynchronously as the backfill progresses. */
  translatedByIdx?: (string | undefined)[];
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

// Dual mode: the `tlang` cue covering the midpoint of a source cue. The two
// tracks are timed independently, so there is no index correspondence to use.
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
    // Unaccented form: what the tooltip compares against the lemma, so a word
    // that is already in its dictionary form doesn't get a redundant lemma row
    // just because the rendered text carries a stress mark.
    span.dataset.surface = tok.surface;
    span.dataset.lemma = tok.lemma;
    span.dataset.pos = tok.pos;
  }
  return span;
}

// Fallback only, for responses from a backend that predates Token.ws. No
// punctuation rule gets кто-то right, which is why the tokenizer now tells us.
const CLOSE_PUNCT = new Set([",", ".", "!", "?", ";", ":", ")", "]", "}", "»", "”", "’", "…"]);
const OPEN_PUNCT = new Set(["(", "[", "{", "«", "“", "‘"]);

function renderAnalyzed(target: HTMLElement, sentence: AnalyzedSentence, settings: Settings) {
  // The hovered span is about to be removed, and mouseout never fires for a
  // removed node, so the tooltip would hang around pointing at nothing.
  hideTooltip();
  target.innerHTML = "";
  const tokens = sentence.tokens;
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (i > 0) {
      const prev = tokens[i - 1];
      const needSpace =
        prev.ws === undefined
          ? !CLOSE_PUNCT.has(tok.surface) && !OPEN_PUNCT.has(prev.surface)
          : prev.ws;
      if (needSpace) target.appendChild(document.createTextNode(" "));
    }
    target.appendChild(tokenToSpan(tok, settings));
  }
}

export function mountOverlay(deps: OverlayDeps): Overlay {
  const {
    root,
    video,
    russianCues,
    translatedCues = [],
    translatedByIdx,
    settings,
    analyze,
    prefetchWords,
    onAnalyzeError,
    onAnalyzeOk,
  } = deps;

  const wrap = document.createElement("div");
  wrap.id = "sr-overlay";
  const pos = Math.max(0, Math.min(100, settings.overlayPosition));
  wrap.style.top = `${pos}%`;
  const ruLine = document.createElement("div");
  ruLine.className = "sr-line sr-ru";
  wrap.appendChild(ruLine);
  // The second line exists only in dual mode. A mode change is treated as a
  // heavy settings change in content.ts, which tears this overlay down and
  // mounts a new one, so `applySettings` never has to add or remove it.
  let trLine: HTMLDivElement | null = null;
  if (settings.subtitleMode === "dual") {
    trLine = document.createElement("div");
    trLine.className = "sr-line sr-tr";
    wrap.appendChild(trLine);
  }
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
      // Never store undefined: `analyzed.has` would then block every retry and
      // leave that cue plain for the rest of the session.
      result.forEach((s, k) => {
        if (s && need[k]) analyzed.set(need[k].idx, s);
      });
      onAnalyzeOk?.();
    } catch (e) {
      console.warn("[stressful] analyze failed", e);
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
  let lastTrText = "";
  let raf = 0;
  let stopped = false;

  function loop() {
    if (stopped) return;
    raf = requestAnimationFrame(loop);

    // During ads YouTube plays the ad in the SAME <video> element, so
    // currentTime resets toward 0 and we'd wrongly show the video's first cue.
    // #movie_player carries the `ad-showing` class while an ad plays.
    if (root.classList.contains("ad-showing")) {
      if (ruLine.textContent) {
        // mouseout never fires for a node we remove, so drop the tooltip too.
        hideTooltip();
        ruLine.textContent = "";
      }
      if (trLine && trLine.textContent) trLine.textContent = "";
      lastTrText = "";
      currentIdx = -2; // force a re-render once the ad ends
      return;
    }

    const t = video.currentTime;
    const idx = findCueIndex(russianCues, t);

    // Dual mode refreshes the translated line every frame rather than only on a
    // cue change: `backfillTranslations` lands mid-cue, and the line has to
    // appear when it does. Prefer the index-keyed map (exactly 1:1 with the
    // source cues); fall back to the time-based lookup for a YouTube `tlang`
    // track, whose cues carry their own timings.
    if (trLine) {
      let next = "";
      if (idx >= 0) {
        const fromIdx = translatedByIdx ? translatedByIdx[idx] : undefined;
        if (fromIdx) {
          next = fromIdx;
        } else if (translatedCues.length > 0) {
          const tcue = findTranslatedFor(translatedCues, russianCues[idx]);
          if (tcue) next = tcue.text;
        }
      }
      if (next !== lastTrText) {
        trLine.textContent = next;
        lastTrText = next;
      }
    }

    if (idx === currentIdx) return;
    currentIdx = idx;
    if (idx < 0) {
      hideTooltip();
      ruLine.textContent = "";
      return;
    }
    const cue = russianCues[idx];
    const analyzedSentence = analyzed.get(idx);
    if (analyzedSentence) {
      renderAnalyzed(ruLine, analyzedSentence, settings);
      prefetchFor(analyzedSentence);
    } else {
      hideTooltip();
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
      hideTooltip();
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
