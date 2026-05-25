# Stressful Russian

A Firefox + Chrome extension that overlays YouTube videos with dual subtitles:

- **Top line:** Russian text with stressed vowels marked (e.g. `приве́т`) and color-coded by part of speech.
- **Bottom line:** translation in a language you choose, sourced from YouTube's auto-translate.

Hovering any Russian word shows its lemma, part of speech, and morphology (case, number, gender, tense, person, aspect, mood).

## Architecture

```
┌───────────────────┐   timedtext (intercepted)   ┌────────────────────┐
│  YouTube watch    │ ──────────────────────────► │  Firefox extension │
│  page (Firefox)   │                             │  (TypeScript, MV3) │
└───────────────────┘                             └─────────┬──────────┘
                                                            │ POST /analyze
                                                            ▼
                                              ┌──────────────────────────┐
                                              │ Local FastAPI backend    │
                                              │ SpaCy + ruaccent (Python)│
                                              │ http://localhost:8765    │
                                              └──────────────────────────┘
```

Browsers cannot run SpaCy or ruaccent, so a small Python backend (FastAPI) runs locally. The extension fetches YouTube's caption text (Russian + auto-translated target), batches sentences to the backend, receives per-token POS + morphology + stress, and renders the overlay.

## Repository layout

```
stressful-russian/
├── readme.md            ← this file
├── DEVELOPMENT.md       ← full dev setup walkthrough
├── backend/             ← FastAPI + SpaCy + ruaccent
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

4. Open a YouTube video that has a Russian caption track. Click YouTube's **CC** button and select the Russian track (gear icon → Subtitles/CC → Russian). The extension intercepts the caption fetch, hides the native caption window, and renders the dual overlay.

Full walkthrough in [DEVELOPMENT.md](DEVELOPMENT.md).

## Configuration

Right-click the extension's toolbar icon → **Manage Extension → Options**:

- **Translated language** — what the bottom subtitle line shows. Uses YouTube auto-translate.
- **Backend URL** — defaults to `http://localhost:8765`.
- Toggles for stress marks, POS colors, and hover tooltips.

## Why must the user click YouTube's CC button?

YouTube's caption endpoint now requires a session-bound proof-of-origin token (`pot`) the extension cannot generate. The native player produces a valid signed URL only when the user enables CC through its own UI. Our extension monkey-patches `fetch`/`XMLHttpRequest` in the page's MAIN world so it can read the response body once the native player has triggered the request. Translation tracks are then derived from the captured URL by appending the `tlang` parameter.

## Status

Proof of concept. Personal use only — not on AMO or the Chrome Web Store. Tested on Firefox 115+ and Chromium 111+ on macOS.

## Stack

- TypeScript, esbuild, `webextension-polyfill`, web-ext (extension)
- Python 3.9+, FastAPI, SpaCy `ru_core_news_sm`, ruaccent `1.5.8.3` (tiny mode)
