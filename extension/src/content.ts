import browser from "webextension-polyfill";
import { Settings } from "./types";
import { findSourceTrack, requestCues } from "./captions";
import { mountOverlay, Overlay } from "./overlay";
import { analyzeBatch, primeSettings, translateBatch } from "./api";
import { Cue } from "./types";
import { mountTooltip } from "./tooltip";
import { mountSettingsPanel } from "./settings-panel";

console.log("[stressful] content script loaded", window.location.href);

const STORAGE_KEY = "settings";

// TODO: add "be" once Belarusian POS + stress libs exist.
const SUPPORTED_SOURCES = ["ru", "uk"] as const;

const LANG_LABEL: Record<string, string> = {
  ru: "Russian",
  uk: "Ukrainian",
};

let overlay: Overlay | null = null;
let lastVideoId: string | null = null;

async function getSettings(): Promise<Settings> {
  const resp = (await browser.runtime.sendMessage({ type: "getSettings" })) as Settings;
  return resp;
}

function videoIdFromUrl(): string | null {
  try {
    const u = new URL(window.location.href);
    return u.searchParams.get("v");
  } catch {
    return null;
  }
}

async function setup(settings: Settings, videoId: string) {
  overlay?.destroy();
  overlay = null;
  if (!settings.enabled) return;

  const tSetup = performance.now();
  primeSettings(settings);

  const track = await findSourceTrack(SUPPORTED_SOURCES);
  console.log("[stressful] track=", track, { ms_since_setup: Math.round(performance.now() - tSetup) });
  if (!track) {
    console.info("[stressful] no supported caption track on this video");
    return;
  }
  const sourceLang = track.languageCode;
  const sourceLabel = LANG_LABEL[sourceLang] ?? sourceLang.toUpperCase();

  showBanner(`Enable YouTube CC and pick the ${sourceLabel} track to activate Stressful.`);

  const tlang = settings.targetLang && settings.targetLang !== sourceLang ? settings.targetLang : "";
  const tCuesReq = performance.now();
  const { src: sourceCues, tr: ytTranslatedCues } = await requestCues(videoId, sourceLang, tlang);
  console.log("[stressful] requestCues done", {
    sourceCues: sourceCues.length,
    ytTr: ytTranslatedCues.length,
    ms_waiting: Math.round(performance.now() - tCuesReq),
  });
  if (sourceCues.length === 0) {
    showBanner(`No ${sourceLabel} captions captured. Click YouTube CC button, select ${sourceLabel} track.`);
    return;
  }
  hideBanner();

  const translatedCues: Cue[] = ytTranslatedCues.slice();
  const translatedByIdx: (string | undefined)[] = new Array(sourceCues.length);
  const needsBackendTranslate = tlang && translatedCues.length === 0;

  const video = document.querySelector<HTMLVideoElement>("video.html5-main-video");
  const playerRoot = document.querySelector<HTMLElement>("#movie_player");
  if (!video || !playerRoot) {
    console.warn("[stressful] cannot find video or player root");
    return;
  }

  overlay = mountOverlay({
    root: playerRoot,
    video,
    russianCues: sourceCues,
    translatedCues,
    translatedByIdx,
    settings,
    analyze: (texts) => analyzeBatch(texts, sourceLang),
    onAnalyzeError: () => showBanner(`Cannot reach analyzer at ${settings.backendUrl}. Start the backend (see backend/README.md).`),
    onAnalyzeOk: () => hideBanner(),
  });

  if (needsBackendTranslate) {
    void backfillTranslations(sourceCues, translatedByIdx, tlang, sourceLang, video);
  }
}

async function backfillTranslations(
  src: Cue[],
  translatedByIdx: (string | undefined)[],
  tlang: string,
  sourceLang: string,
  video: HTMLVideoElement,
) {
  const t0 = performance.now();
  console.log("[stressful] backend translate starting", { cues: src.length, tlang, sourceLang });
  // Build playback-ordered priority list: cues nearest the current playhead
  // first, expanding outward, so dual subs appear under what the user is
  // actually watching with minimal delay.
  const startTime = video.currentTime || 0;
  let pivot = 0;
  for (let i = 0; i < src.length; i++) {
    if (src[i].start <= startTime) pivot = i;
    else break;
  }
  const order: number[] = [];
  const seen = new Set<number>();
  for (let off = 0; off < src.length; off++) {
    for (const idx of [pivot + off, pivot - off - 1]) {
      if (idx >= 0 && idx < src.length && !seen.has(idx)) {
        seen.add(idx);
        order.push(idx);
      }
    }
  }

  // First chunk is tiny (3 cues) so the very first dual-sub line lands in
  // roughly one Google round-trip. Remaining chunks fire in parallel with
  // bounded concurrency so total fill time scales by (cues / concurrency)
  // instead of (cues / chunk).
  const FIRST = 3;
  const CHUNK = 15;
  const CONCURRENCY = 4;
  let lastPaint = 0;

  async function runChunk(indices: number[], label: string) {
    const cues = indices.map((i) => src[i]);
    const ts = performance.now();
    const tr = await translateBatch(cues.map((c) => c.text), tlang, sourceLang);
    indices.forEach((srcIdx, i) => {
      translatedByIdx[srcIdx] = tr[i] ?? "";
    });
    console.log("[stressful] translate chunk", label, {
      cues: cues.length,
      ms: Math.round(performance.now() - ts),
      since_start_ms: Math.round(performance.now() - t0),
    });
    lastPaint = performance.now();
  }

  try {
    if (order.length === 0) return;
    await runChunk(order.slice(0, FIRST), "first");

    const remaining: number[][] = [];
    for (let s = FIRST; s < order.length; s += CHUNK) {
      remaining.push(order.slice(s, s + CHUNK));
    }
    let next = 0;
    async function worker(id: number) {
      while (next < remaining.length) {
        const my = next++;
        await runChunk(remaining[my], `w${id}#${my}`);
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, (_, i) => worker(i)));
  } catch (e) {
    console.warn("[stressful] backend translate chunk failed", e);
    showBanner(`Translation unavailable: ${(e as Error).message}`);
    return;
  }
  console.log("[stressful] backend translate done", {
    total_ms: Math.round(performance.now() - t0),
    last_paint_ms: Math.round(lastPaint - t0),
  });
}

let banner: HTMLDivElement | null = null;

function showBanner(text: string) {
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "sr-banner";
    banner.style.cssText =
      "position:fixed;top:12px;right:12px;z-index:2147483646;background:#7a1f1f;color:#fff;padding:8px 12px;border-radius:4px;font:13px/1.4 'Segoe UI',Arial,sans-serif;max-width:320px;box-shadow:0 4px 12px rgba(0,0,0,0.5);";
    document.body.appendChild(banner);
  }
  banner.textContent = text;
  banner.style.display = "block";
}

function hideBanner() {
  if (banner) banner.style.display = "none";
}

async function tick() {
  const id = videoIdFromUrl();
  console.log("[stressful] tick, videoId=", id);
  if (!id) return;
  if (id === lastVideoId) return;
  lastVideoId = id;
  const settings = await getSettings();
  console.log("[stressful] settings=", settings);
  setup(settings, id).catch((e) => console.error("[stressful] setup failed", e));
}

function watchUrlChanges() {
  let lastHref = window.location.href;
  const obs = new MutationObserver(() => {
    if (window.location.href !== lastHref) {
      lastHref = window.location.href;
      tick();
    }
  });
  obs.observe(document.body, { childList: true, subtree: true });
}

browser.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync" || !changes[STORAGE_KEY]) return;
  const newSettings = (changes[STORAGE_KEY].newValue ?? {}) as Settings;
  const prev = (changes[STORAGE_KEY].oldValue ?? {}) as Settings;
  if (overlay) overlay.applySettings(newSettings);
  const heavyChanged =
    newSettings.targetLang !== prev.targetLang ||
    newSettings.backendUrl !== prev.backendUrl ||
    newSettings.enabled !== prev.enabled;
  if (heavyChanged) {
    lastVideoId = null;
    tick();
  }
});

mountTooltip();
mountSettingsPanel();
watchUrlChanges();
tick();
