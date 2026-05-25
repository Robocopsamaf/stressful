# Backend — Stressful Russian Analyzer

Local FastAPI server that runs SpaCy (`ru_core_news_sm`) and ruaccent (tiny mode). The Firefox extension sends batches of Russian sentences and receives per-token stress marks, lemma, POS, morphology, and a CSS color class.

## Requirements

- Python 3.9 or newer (3.9 works; 3.10+ also fine — pin `ruaccent==1.5.8.3` if you stay on 3.9, since newer ruaccent wheels require 3.10+).
- ~1 GB of disk space for the SpaCy model + ruaccent's first-run downloads.

## One-time setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download ru_core_news_sm
```

The first POST to `/analyze` triggers the ruaccent tiny model download (handful of MB) into the user cache directory.

## Run

```bash
./run.sh
# equivalent to:
uvicorn app:app --host 127.0.0.1 --port 8765
```

Default port `8765`. Override with extra args, e.g. `./run.sh --port 9000`.

## Endpoints

- `GET /health` → `{"ok": true}`
- `POST /analyze` — request body `{ "sentences": ["..."] }`, response `{ "sentences": [{ "text": "...", "tokens": [...] }] }`.
- `POST /translate` — request body `{ "texts": ["..."], "target": "sv", "source": "ru" }` (source defaults to `ru`), response `{ "translations": ["..."] }`. Backed by `deep-translator`'s `GoogleTranslator` (Google Translate web). Used as a fallback by the extension when YouTube's `tlang` auto-translate returns HTTP 429.

Each token:

```json
{
  "surface": "иду",
  "accented": "иду́",
  "lemma": "идти",
  "pos": "VERB",
  "morph": {"Aspect": "Imp", "Mood": "Ind", "Tense": "Pres", "Person": "First", "Number": "Sing"},
  "color_class": "verb",
  "is_word": true
}
```

`color_class` is one of `verb`, `noun`, `adj`, `pron`, `num`, `adv`, `other`.

## Smoke test

```bash
curl -s -X POST http://localhost:8765/analyze \
  -H 'Content-Type: application/json' \
  -d '{"sentences":["Я иду в большой магазин."]}' | jq
```

Expect every Russian word to have `accented` containing a `U+0301` combining acute after the stressed vowel.

## Caching

Sentences are memoized in an in-process LRU (`maxsize=4096`). Repeated playback of the same video re-uses cached analyses — no duplicate work. `/translate` keeps a separate in-process cache keyed by `(source, target, text)` (cap 16k entries).

## Tuning

`backend/analyzer.py`:

- `Analyzer.__init__` loads `omograph_model_size="tiny"` and `tiny_mode=True`. Bump to `"turbo"` / `tiny_mode=False` for slower but more accurate homograph disambiguation.
- `POS_COLOR` maps SpaCy POS tags to the extension's CSS classes — edit to add categories (e.g. `PART`, `INTJ`).

## Troubleshooting

- **`ruaccent==1.5.10.4 not found`** — that version requires Python 3.10+. Stay on `1.5.8.3` on Python 3.9 (already pinned in `requirements.txt`).
- **`OSError: [E050] Can't find model 'ru_core_news_sm'`** — run `python -m spacy download ru_core_news_sm`.
- **Port already in use** — pass `--port <N>` to `run.sh` and update the extension's Backend URL in the options page.
- **Ruaccent first request slow** — the tiny model is downloading. Subsequent requests are fast (sub-100 ms per cached sentence).
- **Stress marks missing (every `accented` equals `surface`)** — usually means `transformers>=5` is installed; ruaccent's ONNX models require the legacy tokenizer contract (`token_type_ids` in the default output). `requirements.txt` pins `transformers<5`; `analyzer.py` also monkey-patches `put_accent` / `predict_stress_usage` to inject zero `token_type_ids` as a safety net. Reinstall with `pip install -r requirements.txt` if you see this.
- **`/translate` returns empty strings** — `deep-translator` scrapes Google Translate web and occasionally hits its own rate limit. Failures are logged with `[translate] chunk failed: …`. Retry after a few minutes, or swap to `LibreTranslate`.
