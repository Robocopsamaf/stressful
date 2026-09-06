# Stressful

A Firefox + Chrome extension that overlays YouTube videos with a **single** subtitle line in
the spoken language — Russian or Ukrainian — stressed vowels marked (e.g. `приве́т`, `приві́т`)
and color-coded by part of speech.

**Hover any word** to get its meaning plus full grammatical information — dictionary form,
part of speech, and morphology (case, number, gender, tense, person, aspect, mood).

The source language is auto-detected from the available YouTube caption tracks (ru preferred,
then uk). Meanings come from the English Wiktionary, filtered to the part of speech SpaCy
assigned the word in that sentence. So `дело` as a noun gives "affair, matter, concern; work,
business", and the same spelling read as a verb gives the past-tense form instead — the sense
you're actually looking at, rather than whichever one a translation engine happened to pick.

There is no second, translated subtitle line by design. Translation happens on demand, one
word at a time, when you hover — so the source language stays in front of you instead of your
eye dropping to the English.

## Architecture

```
┌───────────────────┐   timedtext (intercepted)   ┌────────────────────┐
│  YouTube watch    │ ──────────────────────────► │  Browser extension │
│  page             │                             │  (TypeScript, MV3) │
└───────────────────┘                             └─────────┬──────────┘
                                                            │ POST /analyze   (per cue)
                                                            │ POST /translate (per hovered word)
                                                            ▼
                                              ┌──────────────────────────┐
                                              │ Local FastAPI backend    │
                                              │ SpaCy + ruaccent +       │
                                              │ ukrainian-word-stress +  │
                                              │ deep-translator (Python) │
                                              │ http://localhost:8765    │
                                              └──────────────────────────┘
```

Browsers cannot run SpaCy / ruaccent / ukrainian-word-stress, so a small Python backend
(FastAPI) runs locally. The extension captures the caption track (ru or uk) and batches its
cues to `/analyze`, with a `source` lang field, for per-token POS + morphology + stress, which
it renders as the color-coded overlay. When you hover a word, its **lemma** and part of speech
go to `/translate` for a gloss (cached, so each word is fetched at most once). The words of
each cue are also prefetched as it appears, so a hover is normally a cache hit rather than a
round trip. Glosses come from the English Wiktionary; a non-English target language or a word
with no entry falls back to Google Translate via `deep-translator`.

## Repository layout

```
stressful/
├── readme.md            ← this file
├── DEVELOPMENT.md       ← full dev setup walkthrough
├── backend/             ← FastAPI + SpaCy + ruaccent + ukrainian-word-stress
│   └── README.md        ← backend install & smoke-test
├── extension/           ← MV3 extension (TypeScript) — Firefox + Chrome
│   └── README.md        ← extension build & load
└── .gitignore
```

## Quick start

1. **Backend** (one terminal):

   ```bash
   cd backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python -m spacy download ru_core_news_sm
   python -m spacy download uk_core_news_sm
   python -c "from ukrainian_word_stress import Stressifier; Stressifier()('привіт')"   # warms ~500MB Stanza data
   ./run.sh                       # http://127.0.0.1:8765
   ```

2. **Extension** (another terminal):

   ```bash
   cd extension
   npm install
   npm run build                  # builds both dist-firefox/ and dist-chrome/
   ```

3. Load extension:

   - **Firefox** — `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → pick `extension/dist-firefox/manifest.json`
   - **Chrome** (or Edge / Brave) — `chrome://extensions` → enable **Developer mode** → **Load unpacked** → pick `extension/dist-chrome/`

4. Open a YouTube video that has a Russian or Ukrainian caption track. Click YouTube's **CC** button and select that track (gear icon → Subtitles/CC → Russian / Ukrainian). The extension intercepts the caption fetch, hides the native caption window, renders the single color-coded source line, and you can hover any word for its meaning and grammar.

Full walkthrough in [DEVELOPMENT.md](DEVELOPMENT.md).

## Configuration

Right-click the extension's toolbar icon → **Manage Extension → Options**:

- **Hover translation language** — the language hovered words are glossed into. English gets POS-matched Wiktionary senses; other languages fall back to Google Translate.
- **Backend URL** — defaults to `http://localhost:8765`.
- Toggles for stress marks, POS colors, and hover tooltips.

## Why must the user click YouTube's CC button?

YouTube's caption endpoint now requires a session-bound proof-of-origin token (`pot`) the extension cannot generate. The native player produces a valid signed URL only when the user enables CC through its own UI. Our extension monkey-patches `fetch`/`XMLHttpRequest` in the page's MAIN world so it can read the response body once the native player has triggered the request.

## Status

Proof of concept. Personal use only — not on AMO or the Chrome Web Store. Tested on Firefox 115+ and Chromium 111+ on macOS.

## Stack

- TypeScript, esbuild, `webextension-polyfill`, web-ext (extension)
- Python 3.9+, FastAPI, SpaCy `ru_core_news_sm` + `uk_core_news_sm`, ruaccent `1.5.8.3` (tiny mode), `ukrainian-word-stress` `1.1.1` (dictionary-based, U+0301 combining acute), English Wiktionary REST API (POS-matched word senses), `deep-translator` (Google Translate fallback), `transformers<5` (ruaccent's ONNX models need the legacy tokenizer contract)

## Credits

Word senses come from the [English Wiktionary](https://en.wiktionary.org), whose content is
licensed [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
