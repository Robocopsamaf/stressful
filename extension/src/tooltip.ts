const MORPH_ORDER = [
  "Case",
  "Number",
  "Gender",
  "Animacy",
  "Tense",
  "Person",
  "Aspect",
  "Mood",
  "VerbForm",
  "Voice",
  "Degree",
];

const POS_LABEL: Record<string, string> = {
  VERB: "verb",
  AUX: "aux",
  NOUN: "noun",
  PROPN: "proper noun",
  ADJ: "adjective",
  PRON: "pronoun",
  DET: "determiner",
  NUM: "numeral",
  ADV: "adverb",
  ADP: "preposition",
  CCONJ: "conjunction",
  SCONJ: "subord. conj.",
  PART: "particle",
  INTJ: "interjection",
};

let tip: HTMLDivElement | null = null;

function ensureTip(): HTMLDivElement {
  if (tip) return tip;
  tip = document.createElement("div");
  tip.id = "sr-tooltip";
  tip.style.display = "none";
  document.body.appendChild(tip);
  return tip;
}

function format(target: HTMLElement) {
  const lemma = target.dataset.lemma ?? "";
  const pos = target.dataset.pos ?? "";
  let morph: Record<string, string> = {};
  try {
    morph = JSON.parse(target.dataset.morph ?? "{}") as Record<string, string>;
  } catch {
    /* ignore */
  }
  const t = ensureTip();
  t.innerHTML = "";

  const header = document.createElement("div");
  header.className = "sr-tt-header";
  header.textContent = `${lemma}${pos ? ` · ${POS_LABEL[pos] ?? pos.toLowerCase()}` : ""}`;
  t.appendChild(header);

  const seen = new Set<string>();
  const rows: [string, string][] = [];
  for (const key of MORPH_ORDER) {
    if (morph[key]) {
      rows.push([key, morph[key]]);
      seen.add(key);
    }
  }
  for (const [k, v] of Object.entries(morph)) {
    if (!seen.has(k)) rows.push([k, v]);
  }
  if (rows.length > 0) {
    const body = document.createElement("div");
    body.className = "sr-tt-body";
    body.textContent = rows.map(([k, v]) => `${k}: ${v}`).join(" · ");
    t.appendChild(body);
  }
}

function position(target: HTMLElement) {
  const t = ensureTip();
  const rect = target.getBoundingClientRect();
  t.style.display = "block";
  const tipRect = t.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  let top = rect.top - tipRect.height - 8;
  if (top < 8) top = rect.bottom + 8;
  left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
  t.style.left = `${Math.round(left + window.scrollX)}px`;
  t.style.top = `${Math.round(top + window.scrollY)}px`;
}

export function mountTooltip() {
  document.addEventListener("mouseover", (ev) => {
    const target = ev.target as HTMLElement | null;
    if (!target?.matches?.(".sr-tok[data-morph]")) return;
    format(target);
    position(target);
  });
  document.addEventListener("mouseout", (ev) => {
    const target = ev.target as HTMLElement | null;
    if (!target?.matches?.(".sr-tok[data-morph]")) return;
    if (tip) tip.style.display = "none";
  });
}
