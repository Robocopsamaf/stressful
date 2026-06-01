import { buildSettingsForm, getSettings } from "./settings-form";

let gear: HTMLButtonElement | null = null;
let panel: HTMLDivElement | null = null;
let mounted = false;

function show() {
  if (panel) panel.style.display = "block";
}

function hide() {
  if (panel) panel.style.display = "none";
}

async function rebuild(): Promise<HTMLDivElement> {
  const wrap = document.createElement("div");
  wrap.id = "sr-panel";
  const header = document.createElement("div");
  header.className = "sr-panel-header";
  const title = document.createElement("span");
  title.className = "sr-panel-title";
  title.textContent = "Stressful";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "sr-panel-close";
  close.setAttribute("aria-label", "Close");
  close.textContent = "×";
  close.addEventListener("click", () => hide());
  header.appendChild(title);
  header.appendChild(close);
  wrap.appendChild(header);

  const settings = await getSettings();
  const { form } = buildSettingsForm(settings);
  wrap.appendChild(form);
  return wrap;
}

async function toggle() {
  if (!panel) return;
  if (panel.style.display === "none") {
    const fresh = await rebuild();
    panel.replaceWith(fresh);
    panel = fresh;
    show();
  } else {
    hide();
  }
}

export async function mountSettingsPanel() {
  if (mounted) return;
  mounted = true;

  gear = document.createElement("button");
  gear.id = "sr-gear";
  gear.type = "button";
  gear.title = "Stressful settings";
  gear.setAttribute("aria-label", "Stressful settings");
  gear.textContent = "⚙";
  gear.addEventListener("click", () => toggle());
  document.body.appendChild(gear);

  panel = await rebuild();
  panel.style.display = "none";
  document.body.appendChild(panel);

  document.addEventListener("click", (ev) => {
    const t = ev.target as Node | null;
    if (!panel || panel.style.display === "none") return;
    if (panel.contains(t) || gear?.contains(t)) return;
    hide();
  });
}
