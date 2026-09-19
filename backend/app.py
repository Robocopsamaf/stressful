from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from analyzer import AnalyzedSentence, Analyzer


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_analyzer().warm("ru")
    yield


app = FastAPI(title="Stressful Analyzer", version="0.1.0", lifespan=lifespan)

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


class TranslateResponse(BaseModel):
    translations: List[str]


_TRANSLATE_CACHE: Dict[Tuple[str, str, str], str] = {}
_TRANSLATE_CACHE_LIMIT = 16384


# The extension's language list uses YouTube's caption codes; Google Translate
# spells a couple of them differently and rejects the YouTube spelling outright.
_TARGET_ALIASES = {"zh-Hans": "zh-CN", "zh-Hant": "zh-TW"}


def _make_translator(source: str, target: str) -> Any:
    from deep_translator import GoogleTranslator

    return GoogleTranslator(source=source, target=_TARGET_ALIASES.get(target, target))


def _translate_texts(translator: Any, texts: List[str]) -> List[str]:
    """Translate one text at a time.

    deep_translator's `translate_batch` is a plain loop over `translate()`
    (one HTTP request per text) with no error isolation, so doing the loop
    here costs nothing and keeps one bad entry — a length error, a rate
    limit — from blanking every other text in the request.
    """
    out: List[str] = []
    for text in texts:
        try:
            tr = translator.translate(text)
        except Exception as e:
            print(f"[translate] text failed: {type(e).__name__}: {e}", flush=True)
            tr = ""
        out.append(tr if isinstance(tr, str) else "")
    return out


@app.post("/translate", response_model=TranslateResponse)
def translate(req: TranslateRequest) -> TranslateResponse:
    if not req.texts:
        return TranslateResponse(translations=[])
    if not req.target:
        raise HTTPException(status_code=400, detail="target language required")

    # Validate up front so an unsupported code is an explicit 400 rather than a
    # response full of empty strings.
    try:
        translator = _make_translator(req.source, req.target)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported language pair {req.source}->{req.target}: {type(e).__name__}",
        )

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
        translated = _translate_texts(translator, miss_texts)
        for idx, src, tr in zip(misses, miss_texts, translated):
            out[idx] = tr
            # Only cache successful (non-empty) translations, so cues that came
            # back blank from a rate-limit get retried on the next request
            # rather than being permanently stuck empty.
            if tr:
                _TRANSLATE_CACHE[(req.source, req.target, src)] = tr
        if len(_TRANSLATE_CACHE) > _TRANSLATE_CACHE_LIMIT:
            drop = len(_TRANSLATE_CACHE) // 10
            for k in list(_TRANSLATE_CACHE.keys())[:drop]:
                _TRANSLATE_CACHE.pop(k, None)

    return TranslateResponse(translations=out)
