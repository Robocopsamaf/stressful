# Stressful

A Firefox + Chrome extension that overlays YouTube videos with dual subtitles:

- **Top line:** Russian or Ukrainian text with stressed vowels marked (e.g. `приве́т`, `приві́т`) and color-coded by part of speech.
- **Bottom line:** translation in a language you choose. Tries YouTube's auto-translate first; if YouTube rate-limits (HTTP 429) or returns empty, falls back to the local backend's `/translate` endpoint (Google Translate via `deep-translator`).

The source language is auto-detected from the available YouTube caption tracks (ru preferred, then uk). Hovering any source-language word shows its lemma, part of speech, and morphology (case, number, gender, tense, person, aspect, mood).

## Architecture

```
┌───────────────────┐   timedtext (intercepted)   ┌────────────────────┐
│  YouTube watch    │ ──────────────────────────► │  Firefox extension │
│  page (Firefox)   │ ◄── tlang (or 429 → skip)   │  (TypeScript, MV3) │
└───────────────────┘                             └─────────┬──────────┘
                                                            │ POST /analyze
                                                            │ POST /translate (fallback)
                                                            ▼
                                              ┌──────────────────────────┐
                                              │ Local FastAPI backend    │
                                              │ SpaCy + ruaccent +       │
                                              │ ukrainian-word-stress +  │
                                              │ deep-translator (Python) │
                                              │ http://localhost:8765    │
                                              └──────────────────────────┘
```

Browsers cannot run SpaCy / ruaccent / ukrainian-word-stress, so a small Python backend (FastAPI) runs locally. The extension fetches the captured caption track (ru or uk), tries YouTube's `tlang` for the translated track, and falls back to the backend's `/translate` when YouTube rate-limits. Sentences are batched to `/analyze` with a `source` lang field for per-token POS + morphology + stress, and rendered in the overlay.

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

4. Open a YouTube video that has a Russian or Ukrainian caption track. Click YouTube's **CC** button and select that track (gear icon → Subtitles/CC → Russian / Ukrainian). The extension intercepts the caption fetch, hides the native caption window, and renders the dual overlay.

Full walkthrough in [DEVELOPMENT.md](DEVELOPMENT.md).

## Configuration

Right-click the extension's toolbar icon → **Manage Extension → Options**:

- **Translated language** — what the bottom subtitle line shows. Tries YouTube auto-translate; falls back to backend `/translate` (Google Translate) if YouTube returns 429 / empty.
- **Backend URL** — defaults to `http://localhost:8765`.
- Toggles for stress marks, POS colors, and hover tooltips.

## Why must the user click YouTube's CC button?

YouTube's caption endpoint now requires a session-bound proof-of-origin token (`pot`) the extension cannot generate. The native player produces a valid signed URL only when the user enables CC through its own UI. Our extension monkey-patches `fetch`/`XMLHttpRequest` in the page's MAIN world so it can read the response body once the native player has triggered the request. Translation tracks are then derived from the captured URL by appending the `tlang` parameter.

## Status

Proof of concept. Personal use only — not on AMO or the Chrome Web Store. Tested on Firefox 115+ and Chromium 111+ on macOS.

## Stack

- TypeScript, esbuild, `webextension-polyfill`, web-ext (extension)
- Python 3.9+, FastAPI, SpaCy `ru_core_news_sm` + `uk_core_news_sm`, ruaccent `1.5.8.3` (tiny mode), `ukrainian-word-stress` `1.1.1` (dictionary-based, U+0301 combining acute), `deep-translator` (Google Translate fallback), `transformers<5` (ruaccent's ONNX models need the legacy tokenizer contract)
