# Backend — Stressful Analyzer

Local FastAPI server. Two source languages today:

- **Russian** — SpaCy `ru_core_news_sm` + ruaccent (tiny mode).
- **Ukrainian** — SpaCy `uk_core_news_sm` + `ukrainian-word-stress` (lang-uk, dictionary-based).

The extension sends batches of sentences (with the detected source language) and receives per-token stress marks, lemma, POS, morphology, and a CSS color class.

## Requirements

- Python 3.9 or newer (3.9 works; 3.10+ also fine — pin `ruaccent==1.5.8.3` if you stay on 3.9, since newer ruaccent wheels require 3.10+).
- ~2 GB of disk space (SpaCy ru + uk models, ruaccent first-run downloads, Stanza data for `ukrainian-word-stress`).

## One-time setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download ru_core_news_sm
python -m spacy download uk_core_news_sm
# Warm ukrainian-word-stress: first call downloads ~500MB of Stanza data
# into ~/stanza_resources. Doing it now avoids a slow first /analyze.
python -c "from ukrainian_word_stress import Stressifier; Stressifier()('привіт')"
```

The first POST to `/analyze` for Russian triggers the ruaccent tiny model download (handful of MB) into the user cache directory.

## Run

```bash
./run.sh
# equivalent to:
uvicorn app:app --host 127.0.0.1 --port 8765
```

Default port `8765`. Override with extra args, e.g. `./run.sh --port 9000`.

## Endpoints

- `GET /health` → `{"ok": true}`
- `POST /analyze` — request body `{ "sentences": ["..."], "source": "ru" }` (source defaults to `ru`; `uk` also supported), response `{ "sentences": [{ "text": "...", "tokens": [...] }] }`.
- `POST /translate` — request body `{ "texts": ["..."], "target": "en", "source": "ru", "pos": ["NOUN"] }` (source defaults to `ru`; `pos` is optional and parallel to `texts`), response `{ "translations": ["..."] }`. Serves the extension's hover glosses, one word per hover plus a prefetch of each cue's words.

  Two sources, in order. When the target is English and a `pos` is supplied, `glossary.py` looks the word up on the **English Wiktionary** and returns the senses whose part of speech matches — so `дело` as a `NOUN` gives "affair, matter, concern; work, business" and not the past-tense verb reading. Anything else — a non-English target, a word with no entry, a multi-word string — falls through to `deep-translator`'s `GoogleTranslator`.

  Only the English Wiktionary is used: the REST definition endpoint answers HTTP 501 on the other language wikis. It carries Ukrainian entries too, so `source: "uk"` works the same way.

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
# Russian
curl -s -X POST http://localhost:8765/analyze \
  -H 'Content-Type: application/json' \
  -d '{"sentences":["Я иду в большой магазин."],"source":"ru"}' | jq

# Ukrainian
curl -s -X POST http://localhost:8765/analyze \
  -H 'Content-Type: application/json' \
  -d '{"sentences":["Я йду до великого магазину."],"source":"uk"}' | jq
```

Expect every Russian/Ukrainian word to have `accented` containing a `U+0301` combining acute after the stressed vowel.

## Caching

Sentences are memoized in an in-process dict keyed by `(source_lang, sentence)` (cap 8k entries). Repeated playback of the same video re-uses cached analyses — no duplicate work. `/translate` keeps a separate in-process cache keyed by `(source, target, pos, text)` (cap 16k entries) — `pos` is part of the key because the same word glossed under two parts of speech is two different answers. Empty results are never cached, so a word blanked by a rate limit is retried on the next request instead of being stuck.

## Tuning

`backend/analyzer.py`:

- `_build_ru_pipeline` loads ruaccent with `omograph_model_size="tiny"` and `tiny_mode=True`. Bump to `"turbo"` / `tiny_mode=False` for slower but more accurate homograph disambiguation.
- `_build_uk_pipeline` uses `Stressifier(on_ambiguity="all")` so homographs get every plausible stress marked. Switch to `"first"` or `"skip"` if you prefer fewer marks.
- `POS_COLOR` maps SpaCy POS tags to the extension's CSS classes — edit to add categories (e.g. `PART`, `INTJ`).
- `SUPPORTED_LANGS` lists the source languages the API accepts.

## Troubleshooting

- **`ruaccent==1.5.10.4 not found`** — that version requires Python 3.10+. Stay on `1.5.8.3` on Python 3.9 (already pinned in `requirements.txt`).
- **`OSError: [E050] Can't find model 'ru_core_news_sm'`** — run `python -m spacy download ru_core_news_sm`. Same for `uk_core_news_sm`.
- **First `/analyze` with `source=uk` is very slow / hangs** — `ukrainian-word-stress` is downloading ~500MB of Stanza data on first use. Run the warm-up command from the one-time setup section instead.
- **Port already in use** — pass `--port <N>` to `run.sh` and update the extension's Backend URL in the options page.
- **Ruaccent first request slow** — the tiny model is downloading. Subsequent requests are fast (sub-100 ms per cached sentence).
- **Stress marks missing (every `accented` equals `surface`)** — usually means `transformers>=5` is installed; ruaccent's ONNX models require the legacy tokenizer contract (`token_type_ids` in the default output). `requirements.txt` pins `transformers<5`; `analyzer.py` also monkey-patches `put_accent` / `predict_stress_usage` to inject zero `token_type_ids` as a safety net. Reinstall with `pip install -r requirements.txt` if you see this.
- **`/translate` returns empty strings** — only reachable on the machine-translation fallback path: `deep-translator` scrapes Google Translate web and is rate-limited by IP. Failures are logged with `[translate] failed for …`. Requests are deliberately sequential and retried with backoff; two faster-looking shapes were measured and are worse, both noted in `_translate_chunk`. Joining the texts with newlines into one request makes Google's `/m` endpoint return no result container at all, and issuing the requests concurrently trips the rate limiter and blanks about a third of a batch. If it persists, wait a few minutes or swap in `LibreTranslate`.
- **Glosses look like machine translation rather than dictionary senses** — the Wiktionary path only runs for an English target with a `pos` supplied. Check the request actually carries `pos`, and look for `[gloss] …` lines in the log.
