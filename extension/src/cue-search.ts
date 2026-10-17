import { Cue } from "./types";

export function findCueIndex(cues: Cue[], t: number): number {
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
export function findTranslatedFor(cues: Cue[], target: Cue): Cue | null {
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
