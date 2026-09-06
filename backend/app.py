from __future__ import annotations

import time
from typing import Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import glossary
from analyzer import AnalyzedSentence, Analyzer

app = FastAPI(title="Stressful Analyzer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

_analyzer: Analyzer | None = None


def get_analyzer() -> Analyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = Analyzer()
    return _analyzer


@app.on_event("startup")
def _warm_up() -> None:
    get_analyzer().warm("ru")


class AnalyzeRequest(BaseModel):
    sentences: List[str]
    source: str = "ru"


class AnalyzeResponse(BaseModel):
    sentences: List[AnalyzedSentence]


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    analyzer = get_analyzer()
    try:
        sentences = analyzer.analyze_many(req.sentences, req.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AnalyzeResponse(sentences=sentences)


class TranslateRequest(BaseModel):
    texts: List[str]
    target: str
    source: str = "ru"
    # Parallel to `texts`: the SpaCy UPOS tag of each word, when the caller knows
    # it. Used to pick the right Wiktionary sense; may be shorter or empty.
    pos: List[str] = []


class TranslateResponse(BaseModel):
    translations: List[str]


# Keyed by (source, target, pos, text): the same word glossed under two parts of
# speech is two different answers.
_TRANSLATE_CACHE: Dict[Tuple[str, str, str, str], str] = {}
_TRANSLATE_CACHE_LIMIT = 16384


# Google's free endpoint flaps: it intermittently serves a page without the
# result container, which deep-translator surfaces as TranslationNotFound.
# Measured on single words, roughly a third of first attempts fail while a short
# retry clears nearly all of them — worth it here because every hover is one
# word, so a bare failure is a visible "—" in the tooltip.
_RETRY_DELAYS = [0.4, 0.9, 1.6]


def _translate_one(source: str, target: str, text: str) -> str:
    """Translate a single string, retrying the transient empty-page responses.
    Returns "" if it ultimately failed — the caller leaves the word unglossed
    rather than failing the whole request."""
    from deep_translator import GoogleTranslator

    last: Exception | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            r = GoogleTranslator(source=source, target=target).translate(text)
            if isinstance(r, str) and r:
                return r
        except Exception as e:  # noqa: BLE001 - deep_translator raises various types
            last = e
        if attempt < len(_RETRY_DELAYS):
            time.sleep(_RETRY_DELAYS[attempt])
    if last is not None:
        print(f"[translate] failed for {text!r} ({type(last).__name__}): {last}", flush=True)
    return ""


def _resolve_one(source: str, target: str, text: str, pos: str) -> str:
    """A dictionary gloss when we can get one, machine translation otherwise."""
    # Only single words have Wiktionary entries, and only when we know the
    # reading to select; anything else goes straight to machine translation.
    if pos and " " not in text.strip():
        gloss = glossary.lookup(text, pos, source=source, target=target)
        if gloss:
            return gloss
    return _translate_one(source, target, text)


def _translate_chunk(texts: List[str], source: str, target: str, pos: List[str]) -> List[str]:
    # Strictly sequential, one request per text. Two faster-looking shapes were
    # measured and both are worse: joining the list with newlines into a single
    # request makes Google's /m endpoint return no result container at all, and
    # issuing the requests concurrently trips its rate limiter, which comes back
    # as blank translations for a third of the batch. Spaced-out single requests
    # answer in ~0.15s each and succeed. Each text retries on its own, so one
    # word Google refuses can't blank or misalign the words around it.
    return [
        _resolve_one(source, target, t, pos[i] if i < len(pos) else "")
        for i, t in enumerate(texts)
    ]


@app.post("/translate", response_model=TranslateResponse)
def translate(req: TranslateRequest) -> TranslateResponse:
    if not req.texts:
        return TranslateResponse(translations=[])
    if not req.target:
        raise HTTPException(status_code=400, detail="target language required")

    out: List[str] = [""] * len(req.texts)
    misses: List[int] = []
    miss_texts: List[str] = []
    miss_pos: List[str] = []
    for i, t in enumerate(req.texts):
        p = req.pos[i] if i < len(req.pos) else ""
        key = (req.source, req.target, p, t)
        cached = _TRANSLATE_CACHE.get(key)
        if cached is not None:
            out[i] = cached
        elif not t.strip():
            out[i] = ""
        else:
            misses.append(i)
            miss_texts.append(t)
            miss_pos.append(p)

    if miss_texts:
        # Google rejects very large batches; chunk to be safe.
        translated: List[str] = []
        CHUNK = 50
        for start in range(0, len(miss_texts), CHUNK):
            piece = miss_texts[start : start + CHUNK]
            piece_pos = miss_pos[start : start + CHUNK]
            try:
                translated.extend(_translate_chunk(piece, req.source, req.target, piece_pos))
            except Exception as e:
                print(f"[translate] chunk failed: {type(e).__name__}: {e}", flush=True)
                translated.extend([""] * len(piece))
        for idx, src, p, tr in zip(misses, miss_texts, miss_pos, translated):
            out[idx] = tr
            # Only cache successful (non-empty) translations, so a word blanked
            # by a rate-limit gets retried on the next hover rather than being
            # permanently stuck empty.
            if tr:
                _TRANSLATE_CACHE[(req.source, req.target, p, src)] = tr
        if len(_TRANSLATE_CACHE) > _TRANSLATE_CACHE_LIMIT:
            drop = len(_TRANSLATE_CACHE) // 10
            for k in list(_TRANSLATE_CACHE.keys())[:drop]:
                _TRANSLATE_CACHE.pop(k, None)

    return TranslateResponse(translations=out)
