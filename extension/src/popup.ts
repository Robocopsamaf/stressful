import { buildSettingsForm, getSettings } from "./settings-form";

async function init() {
  const root = document.getElementById("sr-popup-root");
  if (!root) return;
  const settings = await getSettings();
  const { form } = buildSettingsForm(settings);
  root.appendChild(form);
}

init().catch((e) => console.error("[stressful] popup init failed", e));
