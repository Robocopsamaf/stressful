from __future__ import annotations

from typing import List

from fastapi import FastAPI
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
