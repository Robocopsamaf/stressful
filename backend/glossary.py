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

import json
import os
import re
import tempfile
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests

_API = "https://en.wiktionary.org/api/rest_v1/page/definition/"
_UA = "stressful-russian/0.1 (local language-learning tool; personal use)"
_TIMEOUT = 8

# Wikimedia rate-limits this endpoint per IP (`x-envoy-ratelimited: true`, with a
# `Retry-After` in seconds). A subtitle overlay prefetches every word of every
# cue, which walks straight into that limit and then keeps it alive by retrying.
# Three things keep us under it:
#
#   1. `_CACHE` — a word's dictionary entry does not change, so ask once. This
#      also covers "no entry", which is otherwise re-asked on every hover.
#   2. `_cooldown_until` — once told to back off, stop asking entirely until the
#      window passes, rather than spending a round trip to be refused again.
#   3. `_MIN_INTERVAL` — space requests out, so a burst of cue words cannot trip
#      the limit in the first place.
#
# `_CACHE` holds None for "no entry, don't ask again". A lookup that failed for
# a reason that might not repeat (429, timeout) is not cached at all.
_CACHE: Dict[Tuple[str, str, str], Optional[str]] = {}
_CACHE_LIMIT = 32768
_DEFAULT_COOLDOWN = 60.0
_MAX_COOLDOWN = 300.0

# The spacing is adaptive because the per-IP budget is not published and clearly
# is not the documented headline figure: each 429 doubles the gap, each stretch
# of clean answers relaxes it. Starting gap is small enough to be invisible for
# a cue's worth of words.
_MIN_INTERVAL_FLOOR = 0.25
_MIN_INTERVAL_CEIL = 4.0
_RELAX_AFTER = 20  # consecutive successes before easing the gap back

# The budget is tight enough that re-learning the same words after every restart
# is the single most expensive thing this module can do, so the cache outlives
# the process. Entries are dictionary facts, not user data, and a corrupt or
# missing file simply means starting cold.
_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gloss-cache.json")
_FLUSH_INTERVAL = 5.0

_lock = threading.Lock()
_last_request = 0.0
_cooldown_until = 0.0
_interval = _MIN_INTERVAL_FLOOR
_clean_streak = 0
_dirty = False
_last_flush = 0.0


def _load_cache() -> None:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as e:  # noqa: BLE001 - a bad cache file is not fatal
        print(f"[gloss] ignoring unreadable cache: {type(e).__name__}: {e}", flush=True)
        return
    for k, v in raw.items():
        source, pos, word = k.split("|", 2)
        _CACHE[(source, pos, word)] = v
    print(f"[gloss] loaded {len(_CACHE)} cached entries", flush=True)


def flush_cache() -> None:
    """Write the cache out, atomically, so a crash can't leave a half file."""
    global _dirty, _last_flush
    with _lock:
        if not _dirty:
            return
        snapshot = {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in _CACHE.items()}
        _dirty = False
        _last_flush = time.monotonic()
    try:
        d = os.path.dirname(_CACHE_PATH)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".gloss-cache.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False)
        os.replace(tmp, _CACHE_PATH)
    except Exception as e:  # noqa: BLE001 - persistence is best effort
        print(f"[gloss] cache write failed: {type(e).__name__}: {e}", flush=True)


def _maybe_flush() -> None:
    with _lock:
        due = _dirty and time.monotonic() - _last_flush >= _FLUSH_INTERVAL
    if due:
        flush_cache()


def cooldown_remaining() -> float:
    """Seconds until Wiktionary is worth asking again; 0 when it is available."""
    with _lock:
        return max(0.0, _cooldown_until - time.monotonic())

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


# _fetch outcomes. "none" means Wiktionary answered and has nothing for this
# word — a durable fact, safe to cache. "unavailable" means we never got an
# answer, so nothing is learned and nothing is cached.
_OK = "ok"
_NONE = "none"
_UNAVAILABLE = "unavailable"


def _enter_cooldown(seconds: float, why: str) -> None:
    global _cooldown_until, _interval, _clean_streak
    seconds = max(1.0, min(seconds, _MAX_COOLDOWN))
    with _lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)
        _interval = min(_MIN_INTERVAL_CEIL, max(_MIN_INTERVAL_FLOOR, _interval * 2))
        _clean_streak = 0
        gap = _interval
    print(f"[gloss] backing off for {seconds:.0f}s, gap now {gap:.2f}s ({why})", flush=True)


def _note_success() -> None:
    global _interval, _clean_streak
    with _lock:
        _clean_streak += 1
        if _clean_streak >= _RELAX_AFTER and _interval > _MIN_INTERVAL_FLOOR:
            _interval = max(_MIN_INTERVAL_FLOOR, _interval / 2)
            _clean_streak = 0


def _pace() -> None:
    """Hold the next request back so bursts stay under the per-IP limit."""
    global _last_request
    with _lock:
        wait = _last_request + _interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _fetch(word: str, lang: str) -> Tuple[str, Optional[List[dict]]]:
    if cooldown_remaining() > 0:
        return _UNAVAILABLE, None
    _pace()
    try:
        resp = requests.get(
            _API + requests.utils.quote(word, safe=""),
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 - network shapes vary
        print(f"[gloss] request failed for {word!r}: {type(e).__name__}: {e}", flush=True)
        return _UNAVAILABLE, None
    if resp.status_code == 404:
        _note_success()  # a clean answer, just an empty one
        return _NONE, None  # no entry; caller falls back
    if resp.status_code == 429:
        try:
            after = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            after = _DEFAULT_COOLDOWN
        _enter_cooldown(after, f"HTTP 429 on {word!r}")
        return _UNAVAILABLE, None
    if resp.status_code != 200:
        print(f"[gloss] HTTP {resp.status_code} for {word!r}", flush=True)
        return _UNAVAILABLE, None
    _note_success()
    try:
        return _OK, resp.json().get(lang) or None
    except ValueError:
        return _NONE, None


def lookup(word: str, pos: str, source: str = "ru", target: str = "en") -> Optional[str]:
    """Return a compact gloss for `word`, preferring senses whose part of speech
    matches `pos`. Returns None when there's no usable entry, so the caller can
    fall back to machine translation."""
    if target != "en" or not word.strip():
        return None

    key = (source, pos.upper(), word)
    if key in _CACHE:
        return _CACHE[key]

    status, entries = _fetch(word, source)
    if status == _UNAVAILABLE:
        return None  # not cached: we learned nothing about this word
    if not entries:
        _remember(key, None)
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

    gloss = "; ".join(senses) or None
    _remember(key, gloss)
    return gloss


def _remember(key: Tuple[str, str, str], gloss: Optional[str]) -> None:
    global _dirty
    if len(_CACHE) >= _CACHE_LIMIT:
        for k in list(_CACHE.keys())[: _CACHE_LIMIT // 10]:
            _CACHE.pop(k, None)
    _CACHE[key] = gloss
    with _lock:
        _dirty = True
    _maybe_flush()


_load_cache()
