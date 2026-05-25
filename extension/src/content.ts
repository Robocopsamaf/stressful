import browser from "webextension-polyfill";
import { Settings } from "./types";
import { findRussianTrack, requestCues } from "./captions";
import { mountOverlay, Overlay } from "./overlay";
import { analyzeBatch, primeSettings } from "./api";
import { mountTooltip } from "./tooltip";
import { mountSettingsPanel } from "./settings-panel";

console.log("[stressful-russian] content script loaded", window.location.href);

const STORAGE_KEY = "settings";

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

  primeSettings(settings);

  const track = await findRussianTrack();
  console.log("[stressful-russian] track=", track);
  if (!track) {
    console.info("[stressful-russian] no Russian caption track on this video");
    return;
  }

  showBanner("Enable YouTube CC and pick the Russian track to activate Stressful Russian.");

  const tlang = settings.targetLang && settings.targetLang !== "ru" ? settings.targetLang : "";
  const { ru: russianCues, tr: translatedCues } = await requestCues("ru", tlang);
  if (russianCues.length === 0) {
    showBanner("No Russian captions captured. Click YouTube CC button, select Russian track.");
    return;
  }
  hideBanner();

  const video = document.querySelector<HTMLVideoElement>("video.html5-main-video");
  const playerRoot = document.querySelector<HTMLElement>("#movie_player");
  if (!video || !playerRoot) {
    console.warn("[stressful-russian] cannot find video or player root");
    return;
  }

  overlay = mountOverlay({
    root: playerRoot,
    video,
    russianCues,
    translatedCues,
    settings,
    analyze: analyzeBatch,
    onAnalyzeError: () => showBanner(`Cannot reach analyzer at ${settings.backendUrl}. Start the backend (see backend/README.md).`),
    onAnalyzeOk: () => hideBanner(),
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
  console.log("[stressful-russian] tick, videoId=", id);
  if (!id) return;
  if (id === lastVideoId) return;
  lastVideoId = id;
  const settings = await getSettings();
  console.log("[stressful-russian] settings=", settings);
  setup(settings, id).catch((e) => console.error("[stressful-russian] setup failed", e));
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
