"""Wiktionary word glosses.

Translating an isolated word with a machine-translation engine gives one sense
with no indication of which reading it picked. Wiktionary instead returns real
dictionary senses tagged by part of speech, and we already know each token's POS
from SpaCy — so we can show the senses for the reading actually used in the
sentence and drop the rest. For "и" that is the difference between "the tenth
letter of the Russian alphabet" and "and".

Only English is supported: the REST definition endpoint exists on the English
Wiktionary, while other language wikis answer HTTP 501. Callers fall back to
machine translation for other target languages and for words with no entry.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import requests

_API = "https://en.wiktionary.org/api/rest_v1/page/definition/"
_UA = "stressful-russian/0.1 (local language-learning tool; personal use)"
_TIMEOUT = 8

# SpaCy UPOS -> the Wiktionary partOfSpeech labels that count as a match. A few
# map to several because Wiktionary splits Russian pronouns/determiners and
# subordinating/coordinating conjunctions differently than UD does.
_POS_MAP: Dict[str, tuple] = {
    "NOUN": ("Noun",),
    "PROPN": ("Proper noun", "Noun"),
    "VERB": ("Verb",),
    "AUX": ("Verb",),
    "ADJ": ("Adjective",),
    "ADV": ("Adverb",),
    "PRON": ("Pronoun", "Determiner"),
    "DET": ("Determiner", "Pronoun", "Adjective"),
    "ADP": ("Preposition",),
    "CCONJ": ("Conjunction",),
    "SCONJ": ("Conjunction",),
    "PART": ("Particle",),
    "NUM": ("Numeral", "Numeral symbol"),
    "INTJ": ("Interjection",),
}

# Senses to keep, and how much of each. Raise both for fuller entries; the
# tooltip wraps and grows to fit, and it re-anchors after the gloss lands.
_MAX_SENSES = 3
_MAX_SENSE_CHARS = 90

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub("", html)).strip()


def _fetch(word: str, lang: str) -> Optional[List[dict]]:
    try:
        resp = requests.get(
            _API + requests.utils.quote(word, safe=""),
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 - network shapes vary
        print(f"[gloss] request failed for {word!r}: {type(e).__name__}: {e}", flush=True)
        return None
    if resp.status_code == 404:
        return None  # no entry; caller falls back
    if resp.status_code != 200:
        print(f"[gloss] HTTP {resp.status_code} for {word!r}", flush=True)
        return None
    try:
        return resp.json().get(lang) or None
    except ValueError:
        return None


def lookup(word: str, pos: str, source: str = "ru", target: str = "en") -> Optional[str]:
    """Return a compact gloss for `word`, preferring senses whose part of speech
    matches `pos`. Returns None when there's no usable entry, so the caller can
    fall back to machine translation."""
    if target != "en" or not word.strip():
        return None

    entries = _fetch(word, source)
    if not entries:
        return None

    wanted = _POS_MAP.get(pos.upper(), ())
    matching = [e for e in entries if e.get("partOfSpeech") in wanted]
    # No entry for that reading (SpaCy and Wiktionary disagree, or the POS wasn't
    # supplied) — fall back to every sense rather than showing nothing.
    if not matching:
        matching = entries

    senses: List[str] = []
    for entry in matching:
        for d in entry.get("definitions", []):
            text = _clean(d.get("definition", ""))
            if not text:
                continue
            if len(text) > _MAX_SENSE_CHARS:
                text = text[:_MAX_SENSE_CHARS].rstrip(" ,;") + "…"
            if text not in senses:
                senses.append(text)
            if len(senses) >= _MAX_SENSES:
                break
        if len(senses) >= _MAX_SENSES:
            break

    return "; ".join(senses) or None
