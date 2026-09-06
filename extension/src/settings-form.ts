import browser from "webextension-polyfill";
import { DEFAULT_SETTINGS, LANGS, Settings } from "./types";

export async function getSettings(): Promise<Settings> {
  const resp = (await browser.runtime.sendMessage({ type: "getSettings" })) as Settings;
  return { ...DEFAULT_SETTINGS, ...resp };
}

export async function saveSettings(partial: Partial<Settings>): Promise<Settings> {
  return (await browser.runtime.sendMessage({ type: "setSettings", settings: partial })) as Settings;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string> = {},
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

export interface BuiltForm {
  form: HTMLFormElement;
  setStatus(text: string): void;
}

export function buildSettingsForm(initial: Settings): BuiltForm {
  const form = el("form", { class: "sr-panel-form" });

  const enabledLabel = el("label", { class: "sr-panel-check" });
  const enabledInput = el("input", { type: "checkbox", name: "enabled" }) as HTMLInputElement;
  enabledInput.checked = initial.enabled;
  enabledLabel.appendChild(enabledInput);
  enabledLabel.appendChild(el("span", {}, "Enabled"));
  form.appendChild(enabledLabel);

  const langWrap = el("label", { class: "sr-panel-field" });
  langWrap.appendChild(el("span", {}, "Hover translation language"));
  const langSel = el("select", { name: "targetLang" }) as HTMLSelectElement;
  for (const { code, label } of LANGS) {
    langSel.appendChild(el("option", { value: code }, `${label} (${code})`));
  }
  langSel.value = initial.targetLang;
  langWrap.appendChild(langSel);
  form.appendChild(langWrap);

  const posWrap = el("label", { class: "sr-panel-field" });
  const posLabel = el("span", {});
  const posVal = el("output", { class: "sr-panel-pos-val" }, `${initial.overlayPosition}%`);
  posLabel.appendChild(document.createTextNode("Vertical position: "));
  posLabel.appendChild(posVal);
  posLabel.appendChild(document.createTextNode(" from top"));
  posWrap.appendChild(posLabel);
  const posInput = el("input", {
    type: "range",
    name: "overlayPosition",
    min: "0",
    max: "95",
    step: "1",
  }) as HTMLInputElement;
  posInput.value = String(initial.overlayPosition);
  posWrap.appendChild(posInput);
  form.appendChild(posWrap);

  const toggles: { key: "showStress" | "showColors" | "showTooltips"; label: string }[] = [
    { key: "showStress", label: "Stress marks" },
    { key: "showColors", label: "POS colors" },
    { key: "showTooltips", label: "Morphology tooltip on hover" },
  ];
  const toggleInputs: Record<string, HTMLInputElement> = {};
  for (const t of toggles) {
    const lab = el("label", { class: "sr-panel-check" });
    const inp = el("input", { type: "checkbox", name: t.key }) as HTMLInputElement;
    inp.checked = Boolean(initial[t.key]);
    lab.appendChild(inp);
    lab.appendChild(el("span", {}, t.label));
    form.appendChild(lab);
    toggleInputs[t.key] = inp;
  }

  const backendWrap = el("label", { class: "sr-panel-field" });
  backendWrap.appendChild(el("span", {}, "Backend URL"));
  const backendInput = el("input", { type: "url", name: "backendUrl" }) as HTMLInputElement;
  backendInput.value = initial.backendUrl;
  backendWrap.appendChild(backendInput);
  form.appendChild(backendWrap);

  const actions = el("div", { class: "sr-panel-actions" });
  const saveBtn = el("button", { type: "submit", class: "sr-panel-save" }, "Save");
  const status = el("span", { class: "sr-panel-status" });
  actions.appendChild(saveBtn);
  actions.appendChild(status);
  form.appendChild(actions);

  posInput.addEventListener("input", () => {
    posVal.textContent = `${posInput.value}%`;
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const partial: Partial<Settings> = {
      enabled: enabledInput.checked,
      targetLang: langSel.value,
      overlayPosition: parseInt(posInput.value, 10),
      showStress: toggleInputs.showStress.checked,
      showColors: toggleInputs.showColors.checked,
      showTooltips: toggleInputs.showTooltips.checked,
      backendUrl: backendInput.value.trim() || DEFAULT_SETTINGS.backendUrl,
    };
    await saveSettings(partial);
    status.textContent = "Saved.";
    window.setTimeout(() => {
      status.textContent = "";
    }, 1500);
  });

  return {
    form,
    setStatus(text: string) {
      status.textContent = text;
    },
  };
}
