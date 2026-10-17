"""Shared fixtures. Nothing here touches the network or loads a model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pytest

import analyzer
import glossary


@pytest.fixture(autouse=True)
def isolated_glossary(tmp_path, monkeypatch):
    """Point the gloss cache at a scratch file and reset the throttle state.

    `glossary` loads the real on-disk cache at import time, so every test starts
    by emptying it; otherwise a developer's warm cache would mask misses.
    """
    monkeypatch.setattr(glossary, "_CACHE_PATH", str(tmp_path / "gloss-cache.json"))
    monkeypatch.setattr(glossary, "_CACHE", {})
    monkeypatch.setattr(glossary, "_cooldown_until", 0.0)
    monkeypatch.setattr(glossary, "_clean_streak", 0)
    monkeypatch.setattr(glossary, "_max_parallel", glossary._PARALLEL_START)
    monkeypatch.setattr(glossary, "_in_flight", 0)
    monkeypatch.setattr(glossary, "_dirty", False)
    monkeypatch.setattr(glossary, "_last_flush", 0.0)


@pytest.fixture(autouse=True)
def isolated_analyzer(monkeypatch):
    monkeypatch.setattr(analyzer, "_CACHE", {})


# --- a stand-in for spaCy ----------------------------------------------------


@dataclass
class FakeMorph:
    feats: Dict[str, str]

    def to_dict(self) -> Dict[str, str]:
        return dict(self.feats)

    def __bool__(self) -> bool:
        return bool(self.feats)


@dataclass
class FakeTok:
    text: str
    pos_: str = "X"
    lemma_: str = ""
    whitespace_: str = ""
    morph: Optional[FakeMorph] = None


@dataclass
class FakeNlp:
    """Splits on whitespace and peels punctuation off, like a tiny tokenizer.

    `pos` maps a surface form to its UPOS; unknown words are NOUN, punctuation
    is PUNCT. `calls` records every text the pipeline was asked to parse.
    """

    pos: Dict[str, str] = field(default_factory=dict)
    morph: Dict[str, Dict[str, str]] = field(default_factory=dict)
    calls: List[str] = field(default_factory=list)

    _PIECE = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]")

    def __call__(self, text: str) -> List[FakeTok]:
        self.calls.append(text)
        toks = []
        for m in self._PIECE.finditer(text):
            surface = m.group(0)
            ws = " " if text[m.end() : m.end() + 1] == " " else ""
            default = "PUNCT" if not surface[0].isalnum() else "NOUN"
            feats = self.morph.get(surface)
            toks.append(
                FakeTok(
                    text=surface,
                    pos_=self.pos.get(surface, default),
                    lemma_=surface.lower(),
                    whitespace_=ws,
                    morph=FakeMorph(feats) if feats is not None else None,
                )
            )
        return toks


@pytest.fixture
def fake_nlp() -> FakeNlp:
    return FakeNlp()


def make_pipeline(nlp: Callable, accent: Callable[[str], str] = lambda s: s):
    return analyzer._LangPipeline(nlp, accent)
