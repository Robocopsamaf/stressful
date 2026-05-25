import browser from "webextension-polyfill";
import { DEFAULT_SETTINGS, RuntimeMessage, Settings } from "./types";

const STORAGE_KEY = "settings";

async function loadSettings(): Promise<Settings> {
  const stored = await browser.storage.sync.get(STORAGE_KEY);
  const partial = (stored?.[STORAGE_KEY] ?? {}) as Partial<Settings>;
  return { ...DEFAULT_SETTINGS, ...partial };
}

async function saveSettings(partial: Partial<Settings>): Promise<Settings> {
  const current = await loadSettings();
  const next = { ...current, ...partial };
  await browser.storage.sync.set({ [STORAGE_KEY]: next });
  return next;
}

browser.runtime.onInstalled.addListener(async () => {
  const stored = await browser.storage.sync.get(STORAGE_KEY);
  if (!stored?.[STORAGE_KEY]) {
    await browser.storage.sync.set({ [STORAGE_KEY]: DEFAULT_SETTINGS });
  }
});

browser.runtime.onMessage.addListener((rawMsg: unknown) => {
  const msg = rawMsg as RuntimeMessage;
  if (msg.type === "getSettings") return loadSettings();
  if (msg.type === "setSettings") return saveSettings(msg.settings);
  return undefined;
});
