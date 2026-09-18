# Bug sweep — 2026-09-18

Full read of every source file (`backend/app.py`, `backend/analyzer.py`, all of `extension/src`, `extension/public/page-fetch.js`, both manifests). Suspicions were checked empirically where possible: the real Russian analyzer was run in `backend/.venv`, the installed `deep_translator` source was inspected, spaCy tokenization was exercised on the problem cases, the caption bridge's waiter logic was replicated in Node, and browser support was read from MDN compat data.

`npx tsc --noEmit` passes, so nothing below is a type error. All are logic bugs. Fixes are small and local; no new dependencies.

## Findings

### HIGH — user-visible breakage

**H1. Bridge drops a second waiter on the same key** — `extension/public/page-fetch.js:72-86`

`waiters` is `Map<key, resolver>`; a second `waitFor(key)` overwrites the first. The first's timer then deletes the *second's* resolver and resolves the first with `null`; the second never resolves even after `deliver()`.

Evidence — Node replication of `waitFor`/`deliver` with two overlapping waiters on one key:

```
+301ms FIRST resolved: null
+401ms deliver K
+802ms end (anything not printed above never resolved)
```

Trigger: any re-`setup()` for the same video while a request is pending (settings save → `heavyChanged` → `tick()`, or navigating away and back). The content side then times out at 125s and shows "No captions captured".

Fix: `waiters: Map<key, Set<fn>>`; `deliver` resolves all; each timer removes only its own entry.

**H2. Stale `setup()` is never cancelled** — `extension/src/content.ts:39-98, 198-207`

`setup(A)` awaits `requestCues` for up to 125s. An SPA navigation starts `setup(B)` concurrently. When A's promise settles it either shows a wrong banner or mounts an overlay holding A's cues on B's player. The module-level `overlay` is overwritten, so the first leaks along with its `requestAnimationFrame` loop.

Fix: generation counter (`let gen = 0`; `const my = ++gen` at the top of setup; after every `await`, `if (my !== gen) return`). Pass `my` into `backfillTranslations` and stop its workers when stale.

**H3. Track detection reads the first page's player response after SPA navigation** — `extension/src/captions.ts:18-33`

`window.ytInitialPlayerResponse` is a page global, and content scripts run in an isolated world (Chrome) or behind Xray wrappers (Firefox), so that branch is unreachable. The regex fallback reads the inline server-rendered `<script>`, which YouTube does not rewrite on SPA navigation. After a navigation `findSourceTrack` returns the previous video's track: wrong language, or a language the new video lacks, which means a 120s wait and a misleading banner.

Fix (recommended, and it removes the fragile regex entirely): make the bridge language-agnostic. The `sr-captions-req` message carries `langs: ["ru","uk"]`; the bridge resolves on the first captured timedtext for that `videoId` whose `lang` is in the list and returns `{src, tr, lang}`. `content.ts` takes `sourceLang` from the response. The banner becomes "Enable CC and pick the Russian or Ukrainian track". `readPlayerResponse` and `findSourceTrack` are deleted.

**H4. Homograph stress is wrong across a batch** — `backend/analyzer.py:253-259`

`accented_by_surface` is a union over every sentence in the batch, first occurrence wins.

Evidence, from a real run of the analyzer on the batch `["Я вижу замок на двери", "Старый замок стоит на горе"]`:

```
Я ви́жу замо́к на двери́   tokens: … ('замок', 'замо́к') …
Ста́рый за́мок сто́ит на горе́   tokens: … ('замок', 'замо́к') …
```

The second sentence's own `text` carries the correct `за́мок`, but its token was overwritten with `замо́к` from the first sentence.

Fix: build the map per sentence and, while walking the doc, use the map for the chunk currently being filled. Better still, match positionally: keep a cursor into that sentence's `_split_accented` pieces and consume them in order, falling back to the per-sentence map. That also fixes the same word carrying two different stresses inside one sentence.

**H5. Hyphenated words lose stress and render with spaces** — `backend/analyzer.py:112-127`, `extension/src/overlay.ts:70-85`

Evidence: spaCy splits `кто-то` into three tokens and `интернет-магазин` into three, while `_split_accented("кто́-то")` returns the single piece `кто́-то`. The lookup misses, so no stress is shown. The frontend then inserts spaces around the hyphen because it appears in neither punctuation set, rendering `кто - то`.

Fix: stop treating `-` as a word character in `_split_accented`, emitting it as its own piece. Add a `ws` boolean to `Token` from spaCy's `tok.whitespace_` and have `renderAnalyzed` use it instead of the punctuation heuristics, keeping the heuristics only as a fallback.

**H6. Ukrainian apostrophe words get no stress, no colour, no tooltip** — `backend/analyzer.py:112-127, 281`

Evidence: spaCy's Ukrainian model keeps `п'ять` and `ім’я` as single tokens with `is_alpha` false, while the splitter shreds them:

```
"п'я́ть хлопців і ім’я́" -> ['п', "'", 'я́ть', 'хлопців', 'і', 'ім', '’', 'я́']
```

So the surface never matches and `is_word` is false.

Fix: treat `'` and `’` as word characters when between letters, and use `isalnum()` for the base check so digits stay whole (`в 2024 году́` currently splits into four separate digits). Set `is_word` from a regex allowing internal apostrophes rather than `tok.is_alpha` alone.

Note: `backend/.venv` has no `ukrainian_word_stress` installed, so any `source=uk` request raises `ModuleNotFoundError` and returns 500 in this environment. Run `pip install -r requirements.txt` before testing Ukrainian end to end. Not a code bug.

**H7. `zh-Hans` breaks backend translation** — `backend/app.py:73-80`, `extension/src/types.ts:75`

The language list offers `zh-Hans`, which is a YouTube code. Evidence: constructing `GoogleTranslator(source="ru", target="zh-Hans")` raises `LanguageNotSupportedException`, while `zh-CN` succeeds. The blanket `except` in `translate` swallows it, so every chunk silently returns empty strings.

Fix: map YouTube target codes to Google codes in `app.py` (`zh-Hans` to `zh-CN`, `zh-Hant` to `zh-TW`), validate the target once up front, and return HTTP 400 with the message instead of blanks.

**H8. One bad cue blanks a whole 50-cue chunk, and `translate_batch` is per-text anyway** — `backend/app.py:73-114`

Evidence: the installed `deep_translator` 1.9.1 implements `_translate_batch` as a plain loop calling `self.translate(text)` once per item, one HTTP request each. The code comment claiming it "joins with newlines internally" is wrong. Any exception, such as a length error or a rate limit, discards all 50 entries.

Fix: translate per text inside a try/except in `_translate_chunk` so only the failing entry blanks, and drop the misleading comment. Optionally use a small thread pool across texts to recover the latency the loop costs.

### MEDIUM

**M1. Firefox minimum version is too low for `world: "MAIN"`** — `extension/manifest.firefox.json:9`

MDN compat data gives `content_scripts.world` as Firefox 128, Chrome 111. The manifest declares `115.0`. On Firefox 115 through 127 the bridge runs in the isolated world, patches nothing, and the extension silently never sees captions.

Fix: set `"strict_min_version": "128.0"` and update `DEVELOPMENT.md`.

**M2. The caption wait is one-shot** — `extension/src/captions.ts:127-145`

If the user enables CC more than about two minutes after page load, nothing happens until a reload. The bridge has the body cached, but the content script never asks again.

Fix: with the generation token from H2, loop `requestCues` with a short bridge timeout, around 20s, until cues arrive or the generation goes stale.

**M3. YouTube auto-translate users get nothing** — `extension/public/page-fetch.js:124`

If the user's YouTube caption setting auto-translates, the only timedtext request carries both `lang` and `tlang`, keyed `v|ru|en`. The bridge waits on `v|ru|` forever.

Fix, folding into H3's redesign: accept keys that carry a `tlang`, refetch the source with `tlang` stripped using the saved original fetch so the proof-of-origin token is preserved, and reuse the captured body as the translation when its `tlang` matches the request.

**M4. The banner covers the gear button** — `extension/src/content.ts:186`, `extension/public/styles.css:93-109`

Both sit at `top:12px; right:12px` with effectively the same stacking, and the banner is appended later, so settings are unreachable whenever a banner shows.

Fix: move the banner to `right: 56px`, or below the gear.

**M5. The banner is never hidden on several paths** — `extension/src/content.ts`

Disabling the extension in settings returns from `setup` before any `hideBanner()`. The "Translation unavailable" banner is never cleared. A stale banner survives navigation.

Fix: call `hideBanner()` at the top of `setup()` and on the disabled path, and clear the translation banner when a later chunk succeeds.

**M6. The tooltip lingers after the hovered token is re-rendered** — `extension/src/tooltip.ts:99-103`

`mouseout` never fires for a removed node, so on a cue change the tooltip stays visible until the next hover.

Fix: export a `hideTooltip()` and call it from `renderAnalyzed` and wherever the source line is cleared.

**M7. The lazy Ukrainian pipeline can be built twice** — `backend/analyzer.py:190-195`

FastAPI runs sync endpoints in a thread pool, and the extension fires an analyze and a prefetch concurrently. Two threads can both miss the cache and build a pipeline, which means loading Stanza twice.

Fix: a `threading.Lock` around the build in `_pipeline`.

### LOW

- **L1.** If `analyzeBatch` returns fewer sentences than requested, `undefined` is stored in `analyzed` and that cue is stuck plain forever, because `analyzed.has` blocks any retry. Guard the set in `extension/src/overlay.ts:116` and throw on a length mismatch in `extension/src/api.ts:70`.
- **L2.** In the XHR patch, `xhr.responseText` throws for a non-text `responseType` inside the load listener. The fetch patch also misses `URL` object inputs. Wrap the read in a try and normalise the input with `input instanceof Request ? input.url : String(input)`.
- **L3.** `translateCache` in `extension/src/api.ts` is unbounded across a long session. Cap it the way the backend caps its own.
- **L4.** `@app.on_event("startup")` is deprecated in FastAPI 0.115. Move to the `lifespan` handler.
- **L5.** `videoIdFromUrl` only handles `?v=`, so `/shorts/` and `/live/` are ignored. Noted only, out of scope unless wanted.
- **L6.** `readme.md` and `DEVELOPMENT.md` say to reload the tab for a new target language, but `storage.onChanged` already re-runs setup. Documentation nit.

## Implementation order

1. **Backend** (H4, H5a, H6, H7, H8, M7, L4) — `backend/analyzer.py`, `backend/app.py`. Adds a `ws` field to `Token`, mirrored in `extension/src/types.ts`.
2. **Bridge and caption capture** (H1, H3, M2, M3, L2) — `extension/public/page-fetch.js`, `extension/src/captions.ts`.
3. **Content lifecycle** (H2, M4, M5) — `extension/src/content.ts`.
4. **Overlay and tooltip** (H5b, M6, L1, L3) — `extension/src/overlay.ts`, `tooltip.ts`, `api.ts`.
5. **Manifest and docs** (M1, L6) — `extension/manifest.firefox.json`, `DEVELOPMENT.md`.

## Verification

Backend, no server needed:

```bash
cd backend && .venv/bin/python - <<'EOF'
from analyzer import Analyzer, _split_accented
a = Analyzer()
r = a.analyze_many(["Я вижу замок на двери", "Старый замок стоит на горе", "интернет-магазин открыт"], "ru")
for s in r:
    print(s.text, [(t.surface, t.accented, t.ws) for t in s.tokens])
# expect: sentence 2 token 'за́мок'; 'интерне́т', '-', 'магази́н' all stressed; ws False around '-'
print(_split_accented("п'я́ть ім’я́ 2024"))  # expect ["п'я́ть", "ім’я́", "2024"]
EOF
```

Translation, after `pip install -r requirements.txt` and `./run.sh`:

```bash
curl -s -X POST localhost:8765/translate -H 'Content-Type: application/json' \
  -d '{"texts":["привет"],"target":"zh-Hans"}'
```

Expect a non-empty translation, and HTTP 400 for a genuinely unsupported target.

Bridge: re-run the two-waiter replication against the rewritten `waitFor` and `deliver`. Both waiters must resolve with the delivered body.

Extension: `npm run lint && npm run build`, load `dist-firefox`, then walk these cases.

1. Open a Russian video, wait more than two minutes, then enable CC. The overlay should appear (M2).
2. Change the target language in the gear panel before clicking CC, then click CC. The overlay should appear without a 125s stall (H1).
3. Navigate from video A to video B, where B has only Ukrainian captions, through in-page navigation. The correct language should be detected with no stale cues or banners (H2, H3).
4. Turn on YouTube's own auto-translate. Captions should still be captured (M3).
5. Hover a word and let the cue change. The tooltip should disappear (M6), and the gear should stay clickable while a banner is visible (M4).
