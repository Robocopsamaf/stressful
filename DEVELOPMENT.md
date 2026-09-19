# Development

Two parts: a local Python backend (FastAPI + SpaCy + ruaccent + ukrainian-word-stress) and an MV3 browser extension (TypeScript, bundled with esbuild) that runs in both Firefox and Chromium-based browsers (Chrome, Edge, Brave).

## 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download ru_core_news_sm
python -m spacy download uk_core_news_sm
# Warm ukrainian-word-stress: first call downloads ~500MB of Stanza data
# into ~/stanza_resources. Doing it now avoids a slow first /analyze.
python -c "from ukrainian_word_stress import Stressifier; Stressifier()('привіт')"
./run.sh
```

The server listens on `http://127.0.0.1:8765`. The ruaccent tiny model loads on startup (one-time download). `run.sh` auto-discovers the venv's `uvicorn` so activation is optional. Smoke test:

```bash
# Russian
curl -s -X POST http://localhost:8765/analyze \
  -H 'Content-Type: application/json' \
  -d '{"sentences":["Привет, как дела?"],"source":"ru"}' | jq

# Ukrainian
curl -s -X POST http://localhost:8765/analyze \
  -H 'Content-Type: application/json' \
  -d '{"sentences":["Привіт, як справи?"],"source":"uk"}' | jq
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

Firefox 128 or newer is required: the MAIN-world bridge relies on `content_scripts[].world`, which Gecko only supports from 128 (Chromium from 111). On older Firefox the bridge runs in the isolated world, patches nothing, and the extension silently never sees captions — hence the `strict_min_version` in the manifest.

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
3. Open a YouTube video that has a Russian or Ukrainian caption track. The source language is whichever of the two you actually switch on — it is read off the caption request the player makes, not guessed ahead of time.
4. Click YouTube's **CC** button (the bottom-right of the player). If the auto-picked track isn't Russian / Ukrainian, open the gear icon → **Subtitles/CC** → pick the right track.
5. The native caption window is hidden by injected CSS; the extension's overlay appears just above the controls — a source-language line with stress marks like `приве́т` / `приві́т` and POS-coloring.
6. Hover any word → tooltip showing the accented word, its dictionary form + part of speech, and its morph fields (Case, Number, Gender, Tense, Person, Aspect, Mood, etc). In hover mode it also carries the word's gloss, usually already cached because each cue's words are prefetched as it appears; on a miss it briefly reads `Fetching translation...`.
7. Open extension options and switch **Subtitle mode** to *Dual subtitle lines*. A second, italic line appears under the source line, and the tooltip drops its translation row. No tab reload: the storage listener treats a mode change as heavy and re-runs `setup()`, which re-fetches the captions. Switch back and the second line goes away again.
8. Change the **translation language**. In hover mode it takes effect on the next hover, with no re-fetch. In dual mode it re-fetches, because it decides which `tlang` track is asked for and what the whole second line says.

If the backend is unreachable a red banner appears in the top-right with instructions; the source-language line still renders (without stress/colors) so the rest of the page is not broken.

## 4. How the caption capture works

YouTube now requires a session-bound `pot` (proof-of-origin token) on `api/timedtext` requests. The token is only generated when the user enables CC through the native UI. So the extension does **not** synthesize caption URLs.

Two content scripts run on `youtube.com/*`:

- `page-fetch.js` runs at `document_start` with `world: "MAIN"`. It monkey-patches `window.fetch` and `XMLHttpRequest`. When the patched code sees a request matching `/api/timedtext`, it clones the response, reads the body, and appends `{ videoId, lang, tlang, body, url }` to a short list of captures.
- `content.js` (ISOLATED world) `postMessage`s a `sr-captions-req` naming the video id and the languages it accepts (`["ru", "uk"]`). It deliberately does **not** decide the source language itself: `ytInitialPlayerResponse` is unreachable from the isolated world, and the server-rendered copy in the DOM still describes the *first* video after an SPA navigation. The bridge resolves on the first capture for that video in one of the requested languages and reports the language back.
- In hover mode only the source track is wanted, so the request carries an empty `tlang` and the bridge's `tr` field comes back empty: translation is per word, on hover, through the backend. Dual mode passes the target language as `tlang`, and `tr` then carries YouTube's own translated track when it has one. Either way the bridge handles `tlang` on its own account, because YouTube's auto-translate may be on — the single request it makes then carries a `tlang` of its own, so the bridge refetches the same URL (which carries the `pot`) with `tlang` stripped to recover the source track.
- Waits are short (20s) and the content script re-asks until cues arrive, so enabling CC minutes after page load still works. Several concurrent waits on the same track all resolve.

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
│   ├── captions.ts          requestCues() bridge wrapper, JSON3/XML parsing
│   ├── api.ts               POST /analyze + POST /translate, caches by (source, text) and word+POS, in-flight dedupe
│   ├── overlay.ts           single-line source overlay, rAF sync to video.currentTime, prefetch next N cues
│   ├── tooltip.ts           hover tooltip: word + async gloss + lemma·POS + morphology
│   ├── settings-form.ts     shared settings form used by the options page and in-page panel
│   ├── settings-panel.ts    in-page gear button + settings panel
│   ├── options.ts           options page logic
│   └── popup.ts             toolbar popup (enable toggle)
└── public/
    ├── options.html
    ├── popup.html
    ├── page-fetch.js        MAIN-world bridge (raw JS, copied as-is into dist)
    └── styles.css           overlay, POS colors, tooltip, hides native YT caption window

backend/
├── app.py                   FastAPI app, /health + /analyze + /translate
├── glossary.py              English Wiktionary lookup, POS-matched word senses
├── analyzer.py              per-language pipelines (ru: SpaCy + ruaccent; uk: SpaCy + ukrainian-word-stress)
├── requirements.txt         fastapi, uvicorn, pydantic, spacy, ruaccent, ukrainian-word-stress, deep-translator, requests
├── run.sh                   uvicorn launcher (auto-picks venv binary)
└── README.md
```

## 6. Subtitle modes

`Settings.subtitleMode` is `"hover"` (the default) or `"dual"`. The seams:

- `content.ts` passes `settings.targetLang` as the bridge's `tlang` only in dual mode, and only
  then keeps the `tr` track and builds `translatedByIdx`.
- `backfillTranslations()` runs only in dual mode, and only when YouTube served no translated
  track. It orders cues outward from the playhead so the line under what you are watching fills
  first, then runs 4 workers over 15-cue chunks.
- `overlay.ts` creates its second `.sr-tr` line only in dual mode, and refreshes it every frame
  rather than on cue changes, because the backfill lands mid-cue.
- `tooltip.ts` takes a synchronous `glossEnabled` predicate. Dual mode returns false, so the
  translation row is never created — the translated line already says it — and no per-word
  request is made. `prefetchWords` is gated the same way.
- A mode change is a **heavy** settings change in the `storage.onChanged` listener: the overlay
  is torn down and `setup()` re-runs. That is why `applySettings` never has to add or remove the
  second line.

## 7. Word glosses

Hovering in **hover mode** sends the token's **lemma** and its SpaCy POS to `POST /translate`.
Three rules, each of which cost some measuring to arrive at:

- **Gloss the lemma, not the surface form.** Russian inflection is heavy and an isolated
  inflected form gets mis-sensed.
- **Prefer Wiktionary, filtered by part of speech.** `glossary.py` asks the English Wiktionary
  for the word and keeps the senses whose part of speech matches SpaCy's tag. This is why `и`
  glosses as "and" rather than "the tenth letter of the Russian alphabet". English only — the
  REST definition endpoint answers HTTP 501 on the other language wikis. Anything it can't
  serve falls through to `deep-translator`'s `GoogleTranslator`.
- **Keep machine-translation requests sequential.** Google's free endpoint is rate-limited by
  IP. Two faster-looking shapes were measured and are worse: joining the texts with newlines
  into one request makes the `/m` endpoint return no result container at all, and issuing them
  concurrently blanks about a third of a batch. Sequential single words are fine — 26 in a row
  measured clean. `_translate_chunk` in `app.py` carries this note.

Latency is hidden by prefetching: as each cue renders, `overlay.ts` hands `content.ts` the
lemma+POS of every word in it, so by the time the pointer lands the gloss is usually cached.
`api.ts` dedupes in-flight requests, so a hover landing mid-prefetch joins the pending request
instead of issuing a second one. Caches are keyed by `(source, target, pos, word)` on both
sides — the same word under two readings is two different answers — and empty results are
never cached.

Dual mode uses the same endpoint for whole cue sentences, with no `pos`, so `_resolve_one`
skips the Wiktionary lookup and goes straight to machine translation.

## 8. Known limitations

- YouTube DOM is not contractual. `#movie_player`, `ytInitialPlayerResponse`, and the subtitles button class can change without notice.
- Auto-generated (ASR) Russian / Ukrainian captions have no punctuation, weakening SpaCy's analysis.
- ruaccent's tiny model picks one default reading for homographs; rare ambiguous stresses may be wrong.
- `ukrainian-word-stress` is dictionary-based; words not in its 2.7M-form lexicon (rare proper nouns, neologisms) are emitted unaccented. Configured with `on_ambiguity="all"` so homographs show every plausible stress.
- The extension's overlay is anchored to `#movie_player`. Fullscreen mode works; some experimental YouTube layouts may not.
- Wiktionary glosses are English-only. Other target languages fall back to machine translation, which returns a single context-free sense (`в` on its own comes back as "V").
- A word with no Wiktionary entry, or a reading SpaCy and Wiktionary disagree on, falls back to machine translation too. Google's free endpoint is rate-limited by IP, so a gloss can occasionally come back blank; blanks are never cached, so the next hover retries. Wiktionary itself can return HTTP 429 under heavy use, which also falls through to the fallback.
- Belarusian is **not** supported. spaCy has no official `be` pipeline and there is no off-the-shelf BE stress library equivalent to ruaccent. See `SUPPORTED_SOURCES` in `content.ts` and `_PIPELINE_BUILDERS` in `analyzer.py` for the seam.
