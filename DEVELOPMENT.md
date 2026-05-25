# Development

Two parts: a local Python backend (FastAPI + SpaCy + ruaccent) and an MV3 browser extension (TypeScript, bundled with esbuild) that runs in both Firefox and Chromium-based browsers (Chrome, Edge, Brave).

## 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download ru_core_news_sm
./run.sh
```

The server listens on `http://127.0.0.1:8765`. The ruaccent tiny model loads on startup (one-time download). Smoke test:

```bash
curl -s -X POST http://localhost:8765/analyze \
  -H 'Content-Type: application/json' \
  -d '{"sentences":["Привет, как дела?"]}' | jq
```

Each token should carry `surface`, `accented` (with `U+0301` after the stressed vowel), `lemma`, `pos`, `morph`, and `color_class`.

## 2. Extension

```bash
cd extension
npm install
npm run build              # builds both dist-firefox/ and dist-chrome/
npm run build:firefox      # only Firefox build
npm run build:chrome       # only Chrome build
npm run watch              # esbuild watch (Firefox)
npm run dev                # esbuild watch + web-ext run (spawns Firefox profile)
npm run lint               # tsc --noEmit
```

Two manifests live at the repo root of the extension: `manifest.firefox.json` (uses `background.scripts` + `browser_specific_settings.gecko`) and `manifest.chrome.json` (uses `background.service_worker`). The build script copies the right one into `dist-firefox/` or `dist-chrome/` alongside the bundled JS, HTML, CSS, and the MAIN-world bridge (`page-fetch.js`).

All extension code uses `webextension-polyfill`, so `browser.*` returns Promises in both browsers without per-browser shims.

### Load in Firefox

1. `about:debugging#/runtime/this-firefox`
2. **Load Temporary Add-on…** → pick `extension/dist-firefox/manifest.json`
3. The add-on is removed when Firefox restarts; just reload it.

### Load in Chrome (or Edge / Brave)

1. `chrome://extensions` → toggle **Developer mode** on (top-right).
2. **Load unpacked** → pick the `extension/dist-chrome/` directory.
3. The extension persists across restarts but disappears if you remove it manually.

Chrome 111+ is required (for `world: "MAIN"` content scripts).

After editing TypeScript or any file under `extension/public/`, run `npm run build` (or the per-browser variant), click **Reload** next to the add-on in `about:debugging` / `chrome://extensions`, then reload the YouTube tab.

## 3. End-to-end smoke test

1. Backend running on `:8765`.
2. Extension loaded.
3. Open a YouTube video that has a Russian caption track.
4. Click YouTube's **CC** button (the bottom-right of the player). If the auto-picked track isn't Russian, open the gear icon → **Subtitles/CC** → pick the Russian track.
5. The native caption window is hidden by injected CSS; the extension's overlay appears just above the controls — Russian on top (with stress marks like `приве́т` and POS-coloring), translated text below.
6. Hover any Russian word → tooltip showing lemma + morph fields (Case, Number, Gender, Tense, Person, Aspect, Mood, etc).
7. Open extension options to change target language. Reload the YouTube tab for the new language to take effect.

If the backend is unreachable a red banner appears in the top-right with instructions; the Russian line still renders (without stress/colors) so the rest of the page is not broken.

## 4. How the caption capture works

YouTube now requires a session-bound `pot` (proof-of-origin token) on `api/timedtext` requests. The token is only generated when the user enables CC through the native UI. So the extension does **not** synthesize caption URLs.

Two content scripts run on `youtube.com/*`:

- `page-fetch.js` runs at `document_start` with `world: "MAIN"`. It monkey-patches `window.fetch` and `XMLHttpRequest`. When the patched code sees a request matching `/api/timedtext`, it clones the response, reads the body, and stores `{ body, url }` keyed by `lang|tlang`.
- `content.js` (ISOLATED world) parses `ytInitialPlayerResponse` to confirm a Russian track exists, then `postMessage`s a `sr-captions-req` to the bridge. The bridge waits for the native request to land (user-triggered CC click), then for the translation, builds it by appending `&tlang=<target>` to the captured Russian URL (preserving the `pot`), and fetches that directly.

This is also why the user must enable CC manually — we can't sign URLs ourselves.

## 5. Project layout reference

```
extension/
├── manifest.firefox.json    Firefox build manifest (background.scripts + gecko)
├── manifest.chrome.json     Chrome build manifest (background.service_worker)
├── package.json             esbuild, typescript, web-ext, webextension-polyfill
├── build.mjs                --target=firefox|chrome → dist-<target>/
├── tsconfig.json
├── src/
│   ├── types.ts             Settings, Cue, Token, AnalyzedSentence, RuntimeMessage
│   ├── background.ts        service worker, settings storage + message router
│   ├── content.ts           entrypoint on youtube.com, drives setup/teardown across SPA navigations
│   ├── captions.ts          findRussianTrack(), requestCues() bridge wrapper, JSON3/XML parsing
│   ├── api.ts               POST /analyze, in-memory cache by sentence text
│   ├── overlay.ts           dual-line overlay, rAF sync to video.currentTime, prefetch next N cues
│   ├── tooltip.ts           shared morph tooltip on hover
│   ├── options.ts           options page logic
│   └── popup.ts             toolbar popup (enable toggle)
└── public/
    ├── options.html
    ├── popup.html
    ├── page-fetch.js        MAIN-world bridge (raw JS, copied as-is into dist)
    └── styles.css           overlay, POS colors, tooltip, hides native YT caption window

backend/
├── app.py                   FastAPI app, /health + /analyze
├── analyzer.py              SpaCy + ruaccent wrapper, alignment, lru_cache
├── requirements.txt         fastapi, uvicorn, pydantic, spacy, ruaccent
├── run.sh                   uvicorn convenience launcher
└── README.md
```

## 6. Known limitations

- YouTube DOM is not contractual. `#movie_player`, `ytInitialPlayerResponse`, and the subtitles button class can change without notice.
- Auto-generated (ASR) Russian captions have no punctuation, weakening SpaCy's analysis.
- ruaccent's tiny model picks one default reading for homographs; rare ambiguous stresses may be wrong.
- The extension's overlay is anchored to `#movie_player`. Fullscreen mode works; some experimental YouTube layouts may not.
- Translation track is only available if YouTube's auto-translate supports the target language for that video.
