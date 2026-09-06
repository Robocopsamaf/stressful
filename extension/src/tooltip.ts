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

type TranslateWord = (word: string, pos: string) => Promise<string>;

let tip: HTMLDivElement | null = null;
let translateWord: TranslateWord = async () => "";
// Bumped on every hover so a slow translation that resolves after the pointer
// has moved on doesn't overwrite the tooltip for a different word.
let hoverSeq = 0;

function ensureTip(): HTMLDivElement {
  if (tip) return tip;
  tip = document.createElement("div");
  tip.id = "sr-tooltip";
  tip.style.display = "none";
  document.body.appendChild(tip);
  return tip;
}

function format(target: HTMLElement) {
  // The rendered word, i.e. accented when stress marks are on.
  const shown = target.textContent ?? "";
  // The unaccented form, used only to decide whether the lemma row would repeat
  // what the header already says.
  const surface = target.dataset.surface ?? shown;
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

  // Word + its translation (filled asynchronously).
  const header = document.createElement("div");
  header.className = "sr-tt-header";
  header.textContent = shown;
  t.appendChild(header);

  const trRow = document.createElement("div");
  trRow.className = "sr-tt-translation";
  trRow.textContent = "Fetching translation...";
  t.appendChild(trRow);

  // Dictionary form + part of speech.
  if (lemma || pos) {
    const meta = document.createElement("div");
    meta.className = "sr-tt-meta";
    const posLabel = pos ? POS_LABEL[pos] ?? pos.toLowerCase() : "";
    meta.textContent = [lemma && lemma !== surface ? lemma : "", posLabel]
      .filter(Boolean)
      .join(" · ");
    if (meta.textContent) t.appendChild(meta);
  }

  // Morphology.
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

  // Kick off the async word translation. Translate the lemma (dictionary form),
  // not the inflected surface — Russian inflection is heavy and an isolated
  // inflected form is often mis-sensed (e.g. instrumental "приветом" → "with a
  // greeting" instead of the noun "привет" → "greeting").
  const seq = ++hoverSeq;
  const word = (lemma || surface).trim();
  translateWord(word, pos)
    .then((tr) => {
      if (seq !== hoverSeq) return; // pointer moved to another word
      trRow.textContent = tr || "—";
      position(target); // size changed; re-anchor
    })
    .catch(() => {
      if (seq !== hoverSeq) return;
      trRow.textContent = "(translation unavailable)";
      position(target);
    });
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

export function mountTooltip(translate: TranslateWord) {
  translateWord = translate;
  document.addEventListener("mouseover", (ev) => {
    const target = ev.target as HTMLElement | null;
    if (!target?.matches?.(".sr-tok[data-morph]")) return;
    format(target);
    position(target);
  });
  document.addEventListener("mouseout", (ev) => {
    const target = ev.target as HTMLElement | null;
    if (!target?.matches?.(".sr-tok[data-morph]")) return;
    hoverSeq++; // invalidate any in-flight translation
    if (tip) tip.style.display = "none";
  });
}
