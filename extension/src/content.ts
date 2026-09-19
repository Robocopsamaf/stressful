import browser from "webextension-polyfill";
import { Cue, Settings } from "./types";
import { requestCues } from "./captions";
import { mountOverlay, Overlay } from "./overlay";
import { analyzeBatch, primeSettings, translateBatch } from "./api";
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
// Latest settings and the detected source language, used by the hover tooltip's
// on-demand word gloss. Both live at module scope because hovering happens long
// after setup() has returned.
let activeSettings: Settings | null = null;
let activeSourceLang = "ru";
// Bumped by every setup(). A setup that finds its token stale lost a race with
// an SPA navigation or a settings change, and must not touch the DOM again.
let gen = 0;

const SOURCE_LABELS = SUPPORTED_SOURCES.map((l) => LANG_LABEL[l] ?? l.toUpperCase()).join(" or ");

// The bridge caches what it captured, so a short wait costs nothing and lets us
// keep asking: the user may enable CC long after the page loaded.
const BRIDGE_WAIT_MS = 20000;
// Breathing room between retries. The bridge answers instantly once it holds a
// capture, so a track that parses to nothing would otherwise spin.
const RETRY_PAUSE_MS = 2000;

async function getSettings(): Promise<Settings> {
  const resp = (await browser.runtime.sendMessage({ type: "getSettings" })) as Settings;
  return resp;
}

// /watch and the embedded player carry the id in `?v=`; /shorts/, /live/ and
// the old /embed/ and /v/ forms carry it in the path.
const PATH_VIDEO_ID = /^\/(?:shorts|live|embed|v)\/([A-Za-z0-9_-]{6,})/;

function videoIdFromUrl(): string | null {
  try {
    const u = new URL(window.location.href);
    const v = u.searchParams.get("v");
    if (v) return v;
    return PATH_VIDEO_ID.exec(u.pathname)?.[1] ?? null;
  } catch {
    return null;
  }
}

// Gloss a single hovered word. Cheap and cached in api.ts — only words the user
// actually hovers, plus the current cue's prefetch, are ever requested.
async function translateWord(word: string, pos: string): Promise<string> {
  const s = activeSettings;
  if (!s) return "";
  const target = s.targetLang;
  if (!word || !target || target === activeSourceLang) return "";
  primeSettings(s);
  const [tr] = await translateBatch([word], target, activeSourceLang, [pos]);
  return tr ?? "";
}

// Warm the cache for every word of the cue now on screen, so by the time the
// pointer lands on a word its gloss is already there. One request per cue, not
// one per hover.
function prefetchWords(words: { text: string; pos: string }[]) {
  const s = activeSettings;
  if (!s || !s.showTooltips) return;
  const target = s.targetLang;
  if (!target || target === activeSourceLang) return;
  primeSettings(s);
  void translateBatch(
    words.map((w) => w.text),
    target,
    activeSourceLang,
    words.map((w) => w.pos),
  ).catch(() => {
    /* best effort — the hover path retries and reports for real */
  });
}

async function setup(settings: Settings, videoId: string) {
  const my = ++gen;
  overlay?.destroy();
  overlay = null;
  hideBanner();
  if (!settings.enabled) return;

  const tSetup = performance.now();
  primeSettings(settings);

  showBanner(`Enable YouTube CC and pick the ${SOURCE_LABELS} track to activate Stressful.`);

  // The source language is the one the bridge actually saw a timedtext request
  // for, not one guessed from the page: ytInitialPlayerResponse is unreachable
  // from the isolated world and goes stale across SPA navigation.
  //
  // No tlang is asked for. This branch renders one source line and glosses each
  // word on hover through the backend, so there is no second track to fetch.
  //
  // Keep asking until captions show up or this setup goes stale. A single long
  // wait would mean a user who enables CC late gets nothing until reload.
  let sourceCues: Cue[] = [];
  let sourceLang = "";
  const tCuesReq = performance.now();
  for (;;) {
    const got = await requestCues(videoId, SUPPORTED_SOURCES, "", BRIDGE_WAIT_MS);
    if (my !== gen) return;
    if (got.src.length > 0) {
      sourceCues = got.src;
      sourceLang = got.lang;
      break;
    }
    await new Promise((r) => setTimeout(r, RETRY_PAUSE_MS));
    if (my !== gen) return;
  }
  activeSourceLang = sourceLang;
  console.log("[stressful] requestCues done", {
    sourceLang,
    sourceCues: sourceCues.length,
    ms_since_setup: Math.round(performance.now() - tSetup),
    ms_waiting: Math.round(performance.now() - tCuesReq),
  });
  hideBanner();

  const video = document.querySelector<HTMLVideoElement>("video.html5-main-video");
  // Shorts mount their own player; #movie_player does not exist there, and
  // without this the id we now parse out of /shorts/ URLs would go nowhere.
  const playerRoot =
    document.querySelector<HTMLElement>("#movie_player") ??
    document.querySelector<HTMLElement>("#shorts-player");
  if (!video || !playerRoot) {
    console.warn("[stressful] cannot find video or player root");
    return;
  }

  overlay = mountOverlay({
    root: playerRoot,
    video,
    russianCues: sourceCues,
    settings,
    analyze: (texts) => analyzeBatch(texts, sourceLang),
    prefetchWords,
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
      "position:fixed;top:12px;right:56px;z-index:2147483646;background:#7a1f1f;color:#fff;padding:8px 12px;border-radius:4px;font:13px/1.4 'Segoe UI',Arial,sans-serif;max-width:320px;box-shadow:0 4px 12px rgba(0,0,0,0.5);";
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
  activeSettings = settings;
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
  activeSettings = newSettings;
  if (overlay) overlay.applySettings(newSettings);
  // targetLang is deliberately absent: it only affects the hover gloss, which
  // reads activeSettings live, so there's no need to re-fetch captions.
  const heavyChanged =
    newSettings.backendUrl !== prev.backendUrl ||
    newSettings.enabled !== prev.enabled;
  if (heavyChanged) {
    lastVideoId = null;
    tick();
  }
});

mountTooltip(translateWord);
mountSettingsPanel();
watchUrlChanges();
tick();
