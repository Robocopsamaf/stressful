# Extension — Stressful

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
| `page-fetch.js` | MAIN | `document_start` | Monkey-patches `fetch` + `XMLHttpRequest`. Captures bodies of `api/timedtext` requests (the only way to get a valid `pot`-signed caption URL), keyed by `videoId\|lang\|tlang` so SPA navigation between videos picks up fresh cues instead of replaying the previous video's track. Listens for `sr-captions-req` postMessages and replies with the captured body. No translated track is ever fetched — translation is per hovered word. |
| `content.js` | ISOLATED | `document_idle` | Reads `ytInitialPlayerResponse` to find a supported source-language track (ru or uk), asks the bridge for cues, mounts the overlay, syncs to `video.currentTime`, calls the backend per cue, renders tokens and the hover tooltip (gloss + morphology). |

`styles.css` adds the overlay styles, POS colors, tooltip box, and a rule hiding `.ytp-caption-window-container` so the native caption window doesn't overlap ours.

The toolbar popup is `public/popup.html` (enable toggle + link to options); the options page is `public/options.html` (hover translation language, backend URL, display toggles). Settings are stored via `browser.storage.sync`.

## Source layout

```
src/
├── types.ts        Settings, Cue, Token, AnalyzedSentence, RuntimeMessage
├── background.ts   service worker, settings storage + message router
├── content.ts      entry on youtube.com; SPA-aware URL watcher; mounts/destroys overlay
├── captions.ts     findSourceTrack(prefs), requestCues() (postMessage bridge wrapper), JSON3/XML parsing
├── api.ts          POST /analyze + POST /translate, caches keyed by (source, text) and word+POS, in-flight dedupe
├── overlay.ts      single-line source overlay, rAF sync, prefetch next 30 cues
├── tooltip.ts      hover tooltip: word + async gloss + lemma·POS + morphology
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
2. Open a YouTube video with a Russian or Ukrainian caption track. The extension auto-detects (ru preferred, then uk).
3. A red banner appears top-right: *"Enable YouTube CC and pick the Russian/Ukrainian track to activate Stressful."*
4. Click YouTube's **CC** button. If multiple subtitle tracks exist, open the gear icon → **Subtitles/CC** and select the Russian or Ukrainian track.
5. The bridge captures the caption response. The banner disappears; the single source line renders, stress-marked and color-coded by part of speech.
6. Hovering a word shows the rendered (accented) form, its gloss, its dictionary form and part of speech, and its morphology. `tooltip.ts` calls the `translateWord` callback injected by `content.ts`, passing the token's **lemma** and its SpaCy **POS** to `POST /translate` — the POS picks the right Wiktionary sense, so it's part of the cache key too.
7. The gloss is normally already there, because `overlay.ts` prefetches every word of a cue as that cue renders. `Fetching translation...` only appears on a genuine miss. `api.ts` caches results and dedupes in-flight requests, so a hover landing mid-prefetch joins the pending request rather than issuing a second one, and a response that arrives after the pointer has moved on is discarded.

## Common edits

- **Add a target language to the options dropdown**: edit `LANGS` in `src/types.ts`.
- **Change overlay colors**: edit the `.sr-verb`, `.sr-noun`, `.sr-adj`, `.sr-pron`, `.sr-num`, `.sr-adv` rules in `public/styles.css`.
- **Change overlay position**: it's driven by the `overlayPosition` setting, applied as an inline `top` percentage in `src/overlay.ts`; the base rule is `#sr-overlay` in `public/styles.css`.
- **Restyle the hover box**: edit `.sr-tt-header`, `.sr-tt-translation`, `.sr-tt-meta`, `.sr-tt-body` in `public/styles.css`.
- **Increase caption-wait timeout**: change `timeoutMs` in `requestCues()` in `src/captions.ts` (also propagated to `page-fetch.js`).
- **Translate the surface form instead of the lemma**: change the `word` chosen in `format()` in `src/tooltip.ts`.
- **Change the "Fetching translation..." placeholder**: same function, where `.sr-tt-translation` is first filled.
- **Stop prefetching whole cues**: drop the `prefetchWords` dep passed to `mountOverlay` in `src/content.ts`; hovers then fetch lazily, one word at a time.

## Known limitations

- Requires the user to enable native CC and pick the Russian or Ukrainian track. We cannot generate `pot` tokens.
- YouTube DOM selectors are not contractual. Most likely to break: `.ytp-subtitles-button`, `.ytp-caption-window-container`, `#movie_player`, `ytInitialPlayerResponse`.
- ASR (auto-generated) captions have no punctuation; SpaCy's parse is weaker on them.
- The overlay is anchored to the player container; fullscreen works. Mini-player / picture-in-picture has not been tested.
- Belarusian is not supported (no official spaCy `be` pipeline, no off-the-shelf BE stress library). See `SUPPORTED_SOURCES` in `src/content.ts` for the seam.

## Tests

There are no automated tests yet. The smoke test is "load it, open a ru/uk video, see if the overlay and tooltips behave."
