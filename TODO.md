# TODO

State as of 2026-09-19.

## Done — bug sweep implemented on `main`

All findings in [BUG-SWEEP.md](BUG-SWEEP.md) are fixed, L5 included (see below).

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
| L5 `/shorts/`, `/live/` | 12 URL shapes through the real `videoIdFromUrl`; `#shorts-player` accepted as a player root |

`npx tsc --noEmit` clean, both extension targets build.

Not verifiable in this environment, both environmental and both already noted
in the sweep:

- Google Translate answers `TooManyRequests` from here, so no live `zh-Hans`
  round-trip. The code path is proven; the network call is not.
- `ukrainian_word_stress` is missing from `backend/.venv`, so `source=uk`
  still returns 500 until `pip install -r requirements.txt`.

## Done — fixes ported to `single-line-hover-translation`

Merged `main` into the branch (merge commit on the branch, not on `main`).
`analyzer.py`, `page-fetch.js` and `captions.ts` were taken from `main` whole;
`app.py`, `content.ts`, `overlay.ts`, `tooltip.ts` and `api.ts` were resolved
by hand, because both sides had rewritten them. Two resolutions are decisions
rather than mechanics:

- **No `backfillTranslations`.** The branch renders one source line and glosses
  each word on hover, so there is no second time-aligned line to fill. It asks
  the bridge with an empty `tlang` and ignores the `tr` field.
- **The translator is still built per text** in `app.py`, keeping the branch's
  retry and Wiktionary gloss, with `main`'s YouTube-to-Google target aliases
  applied inside it. `deep_translator` mutates its instance's url params on a
  retry, so a shared instance is not safe.

`backend/glossary.py` is new on the branch and was never part of the sweep. It
is still unreviewed; the merge only confirmed that its `target != "en"` guard
makes the `zh-Hans` alias irrelevant to it.

### Verification harnesses (not preserved)

Rebuilt to check the merge and left in the session scratchpad, which is gone.
Worth rebuilding as real tests if this gets touched again — the repo has no
test runner, so nothing here is wired into `npm test`.

1. **Bridge** — loads `extension/public/page-fetch.js` verbatim into a `vm`
   context with a stubbed `window`/`fetch`/`XMLHttpRequest` and drives it over
   the `sr-captions-req` protocol. Covered H1 (both overlapping waiters
   resolve), H3 (capture for another video refused, captured language reported
   back), M3 (auto-translated capture refetched with `tlang` stripped and the
   `pot` preserved), L2 (throwing `responseText`), and the empty-body guard.
   Note `fetchVariant` sniffs the body: a refetch stub must return something
   caption-shaped or it is discarded as a failure.
2. **Overlay** — esbuild-bundles `src/overlay.ts` against a minimal DOM stub
   and feeds it real analyzer output. Covered H5b spacing, H4 per-cue stress,
   M6 (tooltip hidden on cue change and on destroy), L1 retry-after-short-
   response.
3. **Content** — bundles `src/content.ts` with stubs for the polyfill, the
   bridge and the overlay. Covered H2 (a setup that lost the race to an SPA
   navigation mounts nothing), M2 (an empty caption answer is retried), and the
   request shape (`langs: ["ru","uk"]`, empty `tlang`, 20s wait).

## Also open

- Shorts are now recognised (`/shorts/<id>` parses, `#shorts-player` is
  accepted as a player root), but nothing has been run against a real Shorts
  page: the caption bridge there is unproven, and Shorts rarely carry a
  timedtext track at all.
