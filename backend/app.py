from __future__ import annotations

from typing import Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from analyzer import AnalyzedSentence, Analyzer

app = FastAPI(title="Stressful Russian Analyzer", version="0.1.0")

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
    get_analyzer()


class AnalyzeRequest(BaseModel):
    sentences: List[str]


class AnalyzeResponse(BaseModel):
    sentences: List[AnalyzedSentence]


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    analyzer = get_analyzer()
    return AnalyzeResponse(sentences=analyzer.analyze_many(req.sentences))


class TranslateRequest(BaseModel):
    texts: List[str]
    target: str
    source: str = "ru"


class TranslateResponse(BaseModel):
    translations: List[str]


_TRANSLATE_CACHE: Dict[Tuple[str, str, str], str] = {}
_TRANSLATE_CACHE_LIMIT = 16384


def _translate_chunk(texts: List[str], source: str, target: str) -> List[str]:
    from deep_translator import GoogleTranslator

    translator = GoogleTranslator(source=source, target=target)
    # translate_batch joins with newlines internally; some entries may come
    # back as None when Google can't translate (proper nouns, single chars).
    raw = translator.translate_batch(texts)
    return [r if isinstance(r, str) else "" for r in raw]


@app.post("/translate", response_model=TranslateResponse)
def translate(req: TranslateRequest) -> TranslateResponse:
    if not req.texts:
        return TranslateResponse(translations=[])
    if not req.target:
        raise HTTPException(status_code=400, detail="target language required")

    out: List[str] = [""] * len(req.texts)
    misses: List[int] = []
    miss_texts: List[str] = []
    for i, t in enumerate(req.texts):
        key = (req.source, req.target, t)
        cached = _TRANSLATE_CACHE.get(key)
        if cached is not None:
            out[i] = cached
        elif not t.strip():
            out[i] = ""
        else:
            misses.append(i)
            miss_texts.append(t)

    if miss_texts:
        # Google rejects very large batches; chunk to be safe.
        translated: List[str] = []
        CHUNK = 50
        for start in range(0, len(miss_texts), CHUNK):
            piece = miss_texts[start : start + CHUNK]
            try:
                translated.extend(_translate_chunk(piece, req.source, req.target))
            except Exception as e:
                print(f"[translate] chunk failed: {type(e).__name__}: {e}", flush=True)
                translated.extend([""] * len(piece))
        for idx, src, tr in zip(misses, miss_texts, translated):
            out[idx] = tr
            _TRANSLATE_CACHE[(req.source, req.target, src)] = tr
        if len(_TRANSLATE_CACHE) > _TRANSLATE_CACHE_LIMIT:
            drop = len(_TRANSLATE_CACHE) // 10
            for k in list(_TRANSLATE_CACHE.keys())[:drop]:
                _TRANSLATE_CACHE.pop(k, None)

    return TranslateResponse(translations=out)
