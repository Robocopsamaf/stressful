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

   **macOS / Linux**

   ```bash
   cd backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python -m spacy download ru_core_news_sm
   python -m spacy download uk_core_news_sm
   python -c "from ukrainian_word_stress import Stressifier; Stressifier()('привіт')"   # warms ~500MB Stanza data
   ./run.sh                       # http://127.0.0.1:8765
   ```

   **Windows (PowerShell)** — run it natively, not under WSL. See [Windows notes](#windows-notes).

   ```powershell
   cd backend
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m spacy download ru_core_news_sm
   python -m spacy download uk_core_news_sm
   python -c "from ukrainian_word_stress import Stressifier; Stressifier()('привіт')"   # warms ~500MB Stanza data
   python -m uvicorn app:app --host 127.0.0.1 --port 8765
   ```

2. **Extension** (another terminal) — identical on every platform:

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

## Windows notes

Run everything **natively on Windows** — don't use WSL. The browser is on Windows, and the
extension talks to `http://localhost:8765`; putting the backend inside WSL turns that into a
cross-boundary call that needs `--host 0.0.0.0` and often the WSL IP in the extension's
Backend URL. Nothing in the backend is Unix-specific, so WSL buys nothing here.

Differences from the macOS / Linux instructions, all of them in step 1:

- **Use Python 3.12, not 3.13.** `requirements.txt` pins `spacy==3.7.5`, which has no 3.13
  wheels. `py -3.12 -m venv .venv` picks the right one if you have several installed.
- **Activate is `.venv\Scripts\Activate.ps1`**, not `source .venv/bin/activate`. If PowerShell
  refuses with a script-execution error, either allow it for that terminal only —
  `Set-ExecutionPolicy -Scope Process RemoteSigned` — or use `cmd` and run
  `.venv\Scripts\activate.bat`.
- **Start the server with `python -m uvicorn app:app --host 127.0.0.1 --port 8765`.** `run.sh`
  is a bash script and won't run in PowerShell; the module form does the same thing and needs
  no extra tooling.

Everything else is unchanged. `npm install`, `npm run build`, `npm run lint` and `npm run
watch` are all cross-platform, and loading the built extension works exactly as described
above.

If the `ukrainian_word_stress` warm-up line errors on the Cyrillic argument, you're on
Windows PowerShell 5.1, which doesn't pass non-ASCII arguments through cleanly. Use
PowerShell 7 (`pwsh`), or run the two statements inside an interactive `python` prompt
instead. Russian works regardless — this step only warms the Ukrainian stress data.

Two rough edges worth knowing before you file a bug:

- `npm run dev` does **not** work on Windows — it shells out to `npx`, which is `npx.cmd`
  there. Use `npm run watch` and reload the extension by hand.
- `npm run package:chrome` needs the Unix `zip` command. Packaging only; the normal
  build/load flow doesn't touch it.

Not yet verified on Windows — if you hit something not listed here, that's worth reporting.

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
