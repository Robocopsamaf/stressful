# TODO

State as of 2026-09-19.

## Done — bug sweep implemented on `main`

All findings in [BUG-SWEEP.md](BUG-SWEEP.md) are fixed except **L5** (`/shorts/`,
`/live/` URLs), which the sweep itself marked out of scope.

One deliberate omission: **H8's optional thread pool**. The extension already
issues 4 concurrent requests × 15 cues, and `deep_translator` makes one HTTP
call per text, so a server-side pool would multiply that straight into the
rate limiting that H8 exists to contain. Per-text sequential translation stays.

Each fix was reproduced before and verified after:

| Finding | Evidence |
| --- | --- |
| H1 bridge waiters | sweep's two-waiter replication: second waiter never resolved before, both resolve now |
| H2 stale `setup()` | generation counter checked after every `await`, threaded into `backfillTranslations` |
| H3 track detection | bridge matches by predicate (videoId + language list) and reports the language back |
| H4 homograph | `Ста́рый за́мок сто́ит на горе́` — was `замо́к` before |
| H5 hyphens | `интерне́т`/`-`/`магази́н` all stressed, `ws:false`; pre-fix overlay rendered `кто - то` |
| H6 apostrophes/digits | `["п'я́ть", 'ім’я́', '2024']` stay whole |
| H7 `zh-Hans` | maps to `zh-CN`; unsupported target now returns HTTP 400 |
| H8 chunk blanking | one failing text → `['TR:один','TR:два','','TR:три','TR:четыре']` |
| M7 pipeline lock | 8 concurrent threads → 1 build |
| L4 lifespan | `on_event` warns on FastAPI 0.115.4; app now starts with zero FastAPI deprecations |
| L1 / L3 | short `/analyze` response throws; translate cache evicts oldest, keeps recent |

`npx tsc --noEmit` clean, both extension targets build.

Not verifiable in this environment, both environmental and both already noted
in the sweep:

- Google Translate answers `TooManyRequests` from here, so no live `zh-Hans`
  round-trip. The code path is proven; the network call is not.
- `ukrainian_word_stress` is missing from `backend/.venv`, so `source=uk`
  still returns 500 until `pip install -r requirements.txt`.

### Verification harnesses (not preserved)

Three Node harnesses drove the real code and lived in the session scratchpad;
they are gone. Worth rebuilding as real tests if this gets touched again:

1. **Bridge** — loads `extension/public/page-fetch.js` verbatim with a stubbed
   `window`/`fetch`/`XMLHttpRequest` and drives it over the `sr-captions-req`
   protocol. Covered H1 (overlapping and concurrent waiters), H3 (uk track
   reported, stale other-video capture refused), M3 (tlang stripped, `pot`
   preserved, captured body reused), L2 (throwing `responseText`).
2. **Overlay** — esbuild-bundles `src/overlay.ts` against a minimal DOM stub and
   feeds it real analyzer output. Covered H5b spacing, H4 per-cue stress, L1
   retry-after-short-response. Bundling the pre-fix `overlay.ts` from git gave
   the before/after contrast.
3. **API** — bundles `src/api.ts` with a stubbed `fetch`. Covered L1 throw and
   L3 cache bound.

## Next — port the fixes to `single-line-hover-translation`

That branch is the one actually in use (single Russian line, POS-matched hover
glosses). It forked at `a718f15`, **before** the sweep, and independently
rewrote `overlay.ts`, `content.ts`, `api.ts`, `page-fetch.js` and `app.py` —
the same files the fixes touch. Most swept bugs are therefore still live there:

| Bug | On the hover branch |
| --- | --- |
| H1 single-slot waiters | still present (`page-fetch.js`) |
| H3 player-response scrape | still present (`captions.ts`, `content.ts`) |
| H5a hyphen in splitter | still present (`analyzer.py`) |
| H7 `zh-Hans` | still present (`types.ts`) |
| L4 deprecated `on_event` | still present (`app.py`) |
| H8, M7 | the branch already rewrote those spots — re-check rather than re-apply |

Plan, in order of preference:

1. Merge `main` into the branch and hand-resolve. The backend and bridge fixes
   (H1, H3, H4, H5, H6, H7, L4) should merge close to clean; `overlay.ts` and
   `content.ts` will conflict properly and need real resolution, because both
   sides rewrote the same functions.
2. Failing that, port fix by fix onto the branch's rewritten structure.

Note the branch's `backend/glossary.py` is new and untouched by the sweep — it
was never reviewed. Worth a read when merging.

## Also open

- **L5** — `videoIdFromUrl` only handles `?v=`, so `/shorts/` and `/live/` are
  ignored. Deferred by the sweep, not by accident.
- `backend/__pycache__/glossary.cpython-312.pyc` is a stale artifact on `main`
  from branch work; `glossary.py` does not exist here.
