# Extension — Stressful Russian

Firefox MV3 extension, written in TypeScript and bundled with esbuild. Pairs with the local FastAPI backend in `../backend/`.

## Build

```bash
npm install
npm run build              # builds both: dist-firefox/ and dist-chrome/
npm run build:firefox      # only Firefox build
npm run build:chrome       # only Chrome build
npm run watch              # esbuild watch (Firefox target)
npm run dev                # esbuild watch + web-ext run (temporary Firefox profile)
npm run lint               # tsc --noEmit
npm run package:firefox    # zip dist-firefox/ → web-ext-artifacts/
npm run package:chrome     # zip dist-chrome/ → web-ext-artifacts/
```

Each build copies the right manifest (`manifest.firefox.json` or `manifest.chrome.json`) into the target dist directory along with the bundled JS, HTML, CSS, and the MAIN-world bridge (`page-fetch.js`).

## Load into Firefox

1. `npm run build:firefox` (or `npm run build`)
2. `about:debugging#/runtime/this-firefox`
3. **Load Temporary Add-on…** → pick **`dist-firefox/manifest.json`**
4. After any rebuild click **Reload** next to the add-on, then reload the YouTube tab.

## Load into Chrome

1. `npm run build:chrome` (or `npm run build`)
2. `chrome://extensions` → enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** → pick the `dist-chrome/` directory
4. After any rebuild click the reload icon on the extension card, then reload the YouTube tab.

Chrome 111+ is required (for `world: "MAIN"` content scripts). Brave, Edge, and other Chromium browsers based on the same version work too.

## How a YouTube tab is wired

Two content scripts inject on `https://www.youtube.com/*`:

| Script | World | When | Purpose |
| --- | --- | --- | --- |
| `page-fetch.js` | MAIN | `document_start` | Monkey-patches `fetch` + `XMLHttpRequest`. Captures bodies of `api/timedtext` requests (the only way to get a valid `pot`-signed caption URL). Listens for `sr-captions-req` postMessages and fetches translations by appending `tlang` to a captured URL, retrying up to 4 times on HTTP 429. |
| `content.js` | ISOLATED | `document_idle` | Reads `ytInitialPlayerResponse` to verify a Russian track exists, asks the bridge for cues, mounts the overlay, syncs to `video.currentTime`, calls the backend per cue, renders tokens and the morphology tooltip. |

`styles.css` adds the overlay styles, POS colors, tooltip box, and a rule hiding `.ytp-caption-window-container` so the native caption window doesn't overlap ours.

The toolbar popup is `public/popup.html` (enable toggle + link to options); the options page is `public/options.html` (target language, backend URL, display toggles). Settings are stored via `browser.storage.sync`.

## Source layout

```
src/
├── types.ts        Settings, Cue, Token, AnalyzedSentence, RuntimeMessage
├── background.ts   service worker, settings storage + message router
├── content.ts      entry on youtube.com; SPA-aware URL watcher; mounts/destroys overlay
├── captions.ts     findRussianTrack(), requestCues() (postMessage bridge wrapper), JSON3/XML parsing
├── api.ts          POST /analyze + POST /translate, in-memory caches by text
├── overlay.ts      dual-line overlay, rAF sync, prefetch next 5 cues
├── tooltip.ts      shared morph tooltip on hover
├── options.ts      options page UI
└── popup.ts        toolbar popup UI

public/
├── options.html
├── popup.html
├── page-fetch.js   plain JS, MAIN-world bridge (not bundled)
└── styles.css      overlay, POS colors, tooltip, hides native YT captions
```

## User flow

1. Backend is running on `http://localhost:8765`.
2. Open a YouTube video with a Russian caption track.
3. A red banner appears top-right: *"Enable YouTube CC and pick the Russian track to activate Stressful Russian."*
4. Click YouTube's **CC** button. If multiple subtitle tracks exist, open the gear icon → **Subtitles/CC** and select Russian.
5. The bridge captures the caption response (and, if a target language is configured, fetches the auto-translated version using the same signed URL). The banner disappears; the overlay renders.
6. If YouTube's `tlang` returns HTTP 429 for every retry (common on residential IPs that have done a lot of auto-translate requests recently), the overlay mounts immediately with the Russian line only and `content.ts` calls the backend's `POST /translate` in the background, chunked 50 cues at a time, filling the bottom line progressively.

## Common edits

- **Add a target language to the options dropdown**: edit `LANGS` in `src/options.ts`.
- **Change overlay colors**: edit the `.sr-verb`, `.sr-noun`, `.sr-adj`, `.sr-pron`, `.sr-num`, `.sr-adv` rules in `public/styles.css`.
- **Change overlay position**: tweak `#sr-overlay { bottom: 12%; ... }` in `public/styles.css`.
- **Increase caption-wait timeout**: change `timeoutMs` in `requestCues()` in `src/captions.ts` (also propagated to `page-fetch.js`).

## Known limitations

- Requires the user to enable native CC and pick the Russian track. We cannot generate `pot` tokens.
- YouTube DOM selectors are not contractual. Most likely to break: `.ytp-subtitles-button`, `.ytp-caption-window-container`, `#movie_player`, `ytInitialPlayerResponse`.
- ASR (auto-generated) Russian captions have no punctuation; SpaCy's parse is weaker on them.
- The overlay is anchored to the player container; fullscreen works. Mini-player / picture-in-picture has not been tested.

## Tests

There are no automated tests yet. The smoke test is "load it, open a Russian video, see if the overlay and tooltips behave."
