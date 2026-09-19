"""Wiktionary word glosses.

Translating an isolated word with a machine-translation engine gives one sense
with no indication of which reading it picked. Wiktionary instead returns real
dictionary senses tagged by part of speech, and we already know each token's POS
from SpaCy — so we can show the senses for the reading actually used in the
sentence and drop the rest. For "и" that is the difference between "the tenth
letter of the Russian alphabet" and "and".

Only English is supported: the definitions live on the English Wiktionary, and
callers fall back to machine translation for other target languages and for
words with no entry.

Definitions come from the MediaWiki Action API, as raw wikitext, rather than
from the prettier REST `page/definition/` endpoint — because the REST endpoint
takes one word per request and Wikimedia enforces a per-IP request quota over a
rolling window, not a concurrency cap. Measured: after a 90s rest, 8 sequential
REST lookups succeed; every shape after that is refused the same way
(4 in parallel, 3, 2, and strictly sequential all returned 6-7 of 8 as HTTP
429). Making the requests faster or slower cannot help when the limit counts
requests. The Action API takes up to 50 titles at once, so a whole subtitle cue
costs one request instead of one per word: 12 titles came back in 0.49s.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Dict, List, Optional, Sequence, Tuple

import requests

_API = "https://en.wiktionary.org/w/api.php"
# The wikitext language section to read, per caption source language.
_SECTION = {"ru": "Russian", "uk": "Ukrainian"}
# The Action API's own ceiling for anonymous callers.
_BATCH_LIMIT = 50
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

# What costs time here is latency, not the rate cap: an uncached definition takes
# Wikimedia roughly 0.7-1.1s to render, so asking one word at a time made a cue
# take as long as its word count. Measured on 8 uncached words:
#
#   sequential          11.1s
#   4 in parallel        3.6s   ok=8  429=0
#   8 in parallel        1.0s   ok=2  429=6   <- over the line
#
# So the throttle is on concurrency, and 4 is the shelf. That number is adaptive
# anyway: a 429 halves it, a clean run walks it back up.
_PARALLEL_START = 4
_PARALLEL_CEIL = 4
_RELAX_AFTER = 20  # consecutive clean answers before widening again

# The budget is tight enough that re-learning the same words after every restart
# is the single most expensive thing this module can do, so the cache outlives
# the process. Entries are dictionary facts, not user data, and a corrupt or
# missing file simply means starting cold.
_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gloss-cache.json")
_FLUSH_INTERVAL = 5.0

_lock = threading.Lock()
_cooldown_until = 0.0
_clean_streak = 0
_dirty = False
_last_flush = 0.0

# Connection reuse: every lookup is the same host, and a fresh TLS handshake per
# word was ~0.07s of the round trip.
_session = requests.Session()

# Concurrency gate. A Condition rather than a Semaphore because the ceiling
# moves: `_max_parallel` shrinks on a 429 and grows back on a clean run, which a
# fixed-size semaphore cannot express.
_slots = threading.Condition()
_in_flight = 0
_max_parallel = _PARALLEL_START


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
    "NOUN": ("Noun", "Noun form"),
    "PROPN": ("Proper noun", "Proper noun form", "Noun"),
    "VERB": ("Verb", "Verb form", "Participle"),
    "AUX": ("Verb", "Verb form"),
    "ADJ": ("Adjective", "Adjective form", "Participle"),
    "ADV": ("Adverb",),
    "PRON": ("Pronoun", "Pronoun form", "Determiner"),
    "DET": ("Determiner", "Pronoun", "Adjective"),
    "ADP": ("Preposition",),
    "CCONJ": ("Conjunction",),
    "SCONJ": ("Conjunction",),
    "PART": ("Particle",),
    "NUM": ("Numeral", "Numeral form", "Numeral symbol"),
    "INTJ": ("Interjection",),
}

# Wiktionary's own spellings of the "form of" templates, which abbreviate.
_FORM_OF_ALIASES = {
    "infl of": "inflection of",
    "alt form": "alternative form of",
    "alt sp": "alternative spelling of",
    "abbr of": "abbreviation of",
    "syn of": "synonym of",
    "dim of": "diminutive of",
}

# {{+obj|ru|ins}} marks which case a preposition or verb governs.
_CASE_NAMES = {
    "nom": "nominative",
    "gen": "genitive",
    "dat": "dative",
    "acc": "accusative",
    "ins": "instrumental",
    "pre": "prepositional",
    "loc": "locative",
}

# Senses to keep, and how much of each. Raise both for fuller entries; the
# tooltip wraps and grows to fit, and it re-anchors after the gloss lands.
_MAX_SENSES = 3
_MAX_SENSE_CHARS = 90

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.S)

# --- wikitext -------------------------------------------------------------
# A level-2 heading names a language section; POS headings sit at level 3 or 4
# beneath it (level 4 when the entry splits by etymology). A definition is a
# line opening with a single "#"; "#:" is an example and "#*" a citation.
_L2_RE = re.compile(r"^==\s*([^=]+?)\s*==\s*$", re.M)
_HEAD_RE = re.compile(r"^(={3,6})\s*([^=]+?)\s*\1\s*$", re.M)
# Both top-level senses and one level of sub-sense: some entries (prepositions
# especially) put a case marker on the "#" line and the actual meanings under
# "##". "#:" is an example and "#*" a citation, so both are excluded.
_DEF_RE = re.compile(r"^#{1,2}(?![:*#])\s*(.+)$", re.M)

_INNER_TEMPLATE_RE = re.compile(r"\{\{([^{}]*)\}\}")
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")

# Every POS heading any entry in _POS_MAP can point at.
_POS_HEADINGS = {name for names in _POS_MAP.values() for name in names}


def _expand_template(body: str) -> str:
    """Render the few templates that carry meaning; drop the rest.

    Definitions are mostly plain text with link and label templates sprinkled
    through. Anything unrecognised is decoration — inflection tables,
    pronunciation, references — and reads better gone than half-rendered.
    """
    parts = [p.strip() for p in body.split("|")]
    name = parts[0].lower()
    args = [p for p in parts[1:] if "=" not in p]
    if name in ("lb", "label", "lbl"):
        # First arg is the language code; the rest are the labels. "_" is a
        # joiner the template uses for punctuation, not a label.
        labels = [a for a in args[1:] if a and a != "_"]
        return f"({', '.join(labels)})" if labels else ""
    # The "form of" family — {{inflection of|ru|весь||ins|m//n|s}},
    # {{diminutive of|ru|форма}}, {{plural of|...}} and friends. Dropping these
    # blanks the whole definition of every inflected form, which is most of what
    # a subtitle actually contains. Render the headword; the grammar tags that
    # follow are template-specific and not worth reconstructing, since the
    # tooltip already shows SpaCy's morphology beside the gloss.
    if name in ("+obj", "obj"):
        cases = [_CASE_NAMES.get(a, a) for a in args[1:] if a]
        return f"(+ {', '.join(cases)})" if cases else ""
    name = _FORM_OF_ALIASES.get(name, name)
    if name.endswith(" of") or name.endswith("_of"):
        label = name.replace("_", " ")
        # Some are language-prefixed: "ru-participle of" -> "participle of".
        if label.startswith(("ru-", "uk-")):
            label = label[3:]
        term = args[1] if len(args) > 1 and args[1] else ""
        return f"{label} {term}".strip() if term else label
    if name in ("l", "m", "ll", "link", "mention"):
        # {{l|ru|word}}, or {{l|ru|word|display}}.
        return args[2] if len(args) > 2 else args[1] if len(args) > 1 else ""
    if name in ("gloss", "gl", "q", "qual", "qualifier", "i"):
        return f"({args[0]})" if args else ""
    if name in ("n-g", "ng", "non-gloss definition", "non gloss definition"):
        return args[0] if args else ""
    if name in ("w", "pedlink"):
        # {{w|Page title}} or {{w|Page title|display}} — no language argument
        # here, unlike {{l}}, so the display text is the last argument.
        return _readable_target(args[-1]) if args else ""
    return ""


def _readable_target(target: str) -> str:
    """A bare wiki link target as prose: no section anchor, no namespace, no
    underscores. Leaked targets are how a gloss ends up reading
    'lustre (American_and_British_English_spelling_differences#-re,_-er)'."""
    target = target.split("#", 1)[0]
    if ":" in target:
        head, tail = target.split(":", 1)
        # A namespace prefix is short and wordless; a colon inside prose is not.
        if tail and " " not in head and len(head) <= 12:
            target = tail
    return target.replace("_", " ").strip()


def _clean(text: str) -> str:
    """Wikitext down to a plain gloss."""
    text = _REF_RE.sub("", text)
    # Innermost first, so nested templates collapse from the inside out.
    for _ in range(6):
        text, n = _INNER_TEMPLATE_RE.subn(lambda m: _expand_template(m.group(1)), text)
        if not n:
            break
    text = _LINK_RE.sub(
        lambda m: m.group(2) if m.group(2) else _readable_target(m.group(1)), text
    )
    text = text.replace("'''", "").replace("''", "")
    text = _TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    # Tidy what template removal leaves behind.
    text = re.sub(r"\s+([,;.])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = _WS_RE.sub(" ", text)
    return text.strip(" ,;:")


def _parse_entries(wikitext: str, section: str) -> List[dict]:
    """Rebuild the shape the REST endpoint used to return —
    [{partOfSpeech, definitions: [{definition}]}] — from a page's wikitext,
    for one language section."""
    if not section:
        return []
    bounds = [(m.start(), m.end(), m.group(1)) for m in _L2_RE.finditer(wikitext)]
    body = ""
    for i, (_start, end, name) in enumerate(bounds):
        if name.strip().lower() != section.lower():
            continue
        stop = bounds[i + 1][0] if i + 1 < len(bounds) else len(wikitext)
        body = wikitext[end:stop]
        break
    if not body:
        return []

    heads = list(_HEAD_RE.finditer(body))
    entries: List[dict] = []
    for i, h in enumerate(heads):
        pos = h.group(2).strip()
        if pos not in _POS_HEADINGS:
            continue
        stop = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        defs = []
        for d in _DEF_RE.finditer(body[h.end():stop]):
            cleaned = _clean(d.group(1))
            if cleaned:
                defs.append({"definition": cleaned})
        if defs:
            entries.append({"partOfSpeech": pos, "definitions": defs})
    return entries


# _fetch_batch outcomes. "ok" means the API answered: a word it has no entry for
# comes back with an empty entry list, which is a durable fact and gets cached.
# "unavailable" means we never got an answer, so nothing is learned or cached.
_OK = "ok"
_UNAVAILABLE = "unavailable"


def _enter_cooldown(seconds: float, why: str) -> None:
    global _cooldown_until, _max_parallel, _clean_streak
    seconds = max(1.0, min(seconds, _MAX_COOLDOWN))
    with _lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)
        _clean_streak = 0
    with _slots:
        _max_parallel = max(1, _max_parallel // 2)
        width = _max_parallel
    print(f"[gloss] backing off for {seconds:.0f}s, {width} in flight max ({why})", flush=True)


def _note_success() -> None:
    global _max_parallel, _clean_streak
    with _lock:
        _clean_streak += 1
        relax = _clean_streak >= _RELAX_AFTER
        if relax:
            _clean_streak = 0
    if not relax:
        return
    with _slots:
        if _max_parallel < _PARALLEL_CEIL:
            _max_parallel += 1
            _slots.notify()


@contextmanager
def _slot():
    """Hold one of the concurrency gate's permits for the duration of a request."""
    global _in_flight
    with _slots:
        while _in_flight >= _max_parallel:
            _slots.wait()
        _in_flight += 1
    try:
        yield
    finally:
        with _slots:
            _in_flight -= 1
            _slots.notify()


def _fetch_batch(words: Sequence[str], source: str) -> Tuple[str, Dict[str, List[dict]]]:
    """Look up many words in one request.

    Returns the outcome and, on success, an entry list per requested word. A
    word the API answered about but has no entry for maps to [] — that is a
    durable fact and the caller caches it. A word missing from the mapping was
    never answered for.
    """
    if not words:
        return _OK, {}
    if cooldown_remaining() > 0:
        return _UNAVAILABLE, {}
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "redirects": "1",
        "titles": "|".join(words),
    }
    try:
        with _slot():
            resp = _session.get(
                _API, params=params, headers={"User-Agent": _UA}, timeout=_TIMEOUT
            )
    except Exception as e:  # noqa: BLE001 - network shapes vary
        print(f"[gloss] request failed ({len(words)} words): {type(e).__name__}: {e}", flush=True)
        return _UNAVAILABLE, {}
    if resp.status_code == 429:
        try:
            after = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            after = _DEFAULT_COOLDOWN
        _enter_cooldown(after, f"HTTP 429 on a batch of {len(words)}")
        return _UNAVAILABLE, {}
    if resp.status_code != 200:
        print(f"[gloss] HTTP {resp.status_code} for a batch of {len(words)}", flush=True)
        return _UNAVAILABLE, {}
    try:
        query = resp.json().get("query", {})
    except ValueError:
        return _UNAVAILABLE, {}

    _note_success()

    # MediaWiki answers under the canonical title, so walk the normalisation and
    # redirect maps back to the spelling we asked for.
    alias: Dict[str, str] = {}
    for n in query.get("normalized", []):
        alias[n["to"]] = n["from"]
    for r in query.get("redirects", []):
        alias[r["to"]] = alias.get(r["from"], r["from"])

    section = _SECTION.get(source, "")
    out: Dict[str, List[dict]] = {}
    for page in query.get("pages", []):
        title = page.get("title", "")
        asked = alias.get(title, title)
        revisions = page.get("revisions") or []
        if not revisions:
            out[asked] = []  # no page at all
            continue
        content = revisions[0].get("slots", {}).get("main", {}).get("content", "")
        out[asked] = _parse_entries(content, section)
    return _OK, out


def _select(entries: List[dict], pos: str) -> Optional[str]:
    """Pick and trim the senses for `pos`, as a single compact line."""
    if not entries:
        return None
    wanted = _POS_MAP.get(pos.upper(), ())
    matching = [e for e in entries if e.get("partOfSpeech") in wanted]
    # No entry for that reading (SpaCy and Wiktionary disagree, or the POS was
    # not supplied) — fall back to every sense rather than showing nothing.
    if not matching:
        matching = entries

    senses: List[str] = []
    for entry in matching:
        for d in entry.get("definitions", []):
            text = d.get("definition", "").strip()
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


def lookup(word: str, pos: str, source: str = "ru", target: str = "en") -> Optional[str]:
    """Return a compact gloss for `word`, preferring senses whose part of speech
    matches `pos`. Returns None when there's no usable entry, so the caller can
    fall back to machine translation."""
    return lookup_many([word], [pos], source=source, target=target)[0]


def lookup_many(
    words: Sequence[str], poss: Sequence[str], source: str = "ru", target: str = "en"
) -> List[Optional[str]]:
    """`lookup` for a whole batch, in as few requests as the API allows.

    This is the shape that matters: Wikimedia's per-IP limit counts requests
    over a window, so a cue costs one request here instead of one per word.
    """
    out: List[Optional[str]] = [None] * len(words)
    if target != "en" or not words:
        return out

    todo: Dict[str, List[int]] = {}
    for i, raw in enumerate(words):
        word = raw.strip()
        if not word:
            continue
        key = (source, poss[i].upper() if i < len(poss) else "", word)
        if key in _CACHE:
            out[i] = _CACHE[key]
        else:
            todo.setdefault(word, []).append(i)
    if not todo:
        return out

    pending = list(todo)
    for start in range(0, len(pending), _BATCH_LIMIT):
        chunk = pending[start : start + _BATCH_LIMIT]
        status, entries_by_word = _fetch_batch(chunk, source)
        if status == _UNAVAILABLE:
            continue  # nothing learned; not cached, so a later hover retries
        for word in chunk:
            if word not in entries_by_word:
                continue
            entries = entries_by_word[word]
            for i in todo[word]:
                pos = poss[i].upper() if i < len(poss) else ""
                gloss = _select(entries, pos)
                out[i] = gloss
                _remember((source, pos, word), gloss)
    return out


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
