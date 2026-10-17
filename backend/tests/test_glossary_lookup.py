from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

import pytest

import glossary
from glossary import _OK, _UNAVAILABLE, _fetch_batch, lookup, lookup_many


class FakeResp:
    def __init__(self, status: int = 200, body: Any = None, headers: Optional[dict] = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(glossary.time, "monotonic", c)
    return c


def session(monkeypatch, *responses) -> FakeSession:
    s = FakeSession(*responses)
    monkeypatch.setattr(glossary, "_session", s)
    return s


def page(title: str, wikitext: Optional[str]) -> dict:
    if wikitext is None:
        return {"title": title, "missing": True}
    return {"title": title, "revisions": [{"slots": {"main": {"content": wikitext}}}]}


RU_DOM = "==Russian==\n===Noun===\n# [[house]]\n# [[home]]\n"
RU_I = "==Russian==\n===Particle===\n# even\n===Conjunction===\n# [[and]]\n"


def ok(*pages, **query) -> FakeResp:
    return FakeResp(200, {"query": {"pages": list(pages), **query}})


# --- _fetch_batch ------------------------------------------------------------


def test_fetch_empty_batch_asks_nothing(monkeypatch):
    s = session(monkeypatch)
    assert _fetch_batch([], "ru") == (_OK, {})
    assert s.calls == []


def test_fetch_sends_one_request_for_all_titles(monkeypatch, clock):
    s = session(monkeypatch, ok(page("дом", RU_DOM), page("и", RU_I)))
    status, out = _fetch_batch(["дом", "и"], "ru")
    assert status == _OK
    assert out == {
        "дом": [{"partOfSpeech": "Noun", "definitions": [{"definition": "house"}, {"definition": "home"}]}],
        "и": [
            {"partOfSpeech": "Particle", "definitions": [{"definition": "even"}]},
            {"partOfSpeech": "Conjunction", "definitions": [{"definition": "and"}]},
        ],
    }
    [call] = s.calls
    assert call["url"] == "https://en.wiktionary.org/w/api.php"
    assert call["params"] == {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "redirects": "1",
        "titles": "дом|и",
    }
    assert call["headers"] == {"User-Agent": glossary._UA}
    assert call["timeout"] == glossary._TIMEOUT


def test_fetch_reads_the_section_for_the_source_language(monkeypatch, clock):
    text = "==Russian==\n===Noun===\n# ru\n==Ukrainian==\n===Noun===\n# uk\n"
    session(monkeypatch, ok(page("так", text)), ok(page("так", text)), ok(page("так", text)))
    assert _fetch_batch(["так"], "uk")[1]["так"][0]["definitions"] == [{"definition": "uk"}]
    assert _fetch_batch(["так"], "ru")[1]["так"][0]["definitions"] == [{"definition": "ru"}]
    assert _fetch_batch(["так"], "be") == (_OK, {"так": []})


def test_fetch_maps_normalised_and_redirected_titles_back(monkeypatch, clock):
    session(
        monkeypatch,
        ok(
            page("Дом", RU_DOM),
            page("кошка", RU_DOM),
            normalized=[{"from": "дом_", "to": "Дом"}, {"from": "котя", "to": "Котя"}],
            redirects=[{"from": "Котя", "to": "кошка"}],
        ),
    )
    _, out = _fetch_batch(["дом_", "котя"], "ru")
    assert set(out) == {"дом_", "котя"}


def test_fetch_maps_a_redirect_without_normalisation(monkeypatch, clock):
    session(monkeypatch, ok(page("кошка", RU_DOM), redirects=[{"from": "котя", "to": "кошка"}]))
    _, out = _fetch_batch(["котя"], "ru")
    assert set(out) == {"котя"}


def test_fetch_page_without_title(monkeypatch, clock):
    session(monkeypatch, ok({"revisions": []}))
    assert _fetch_batch(["a"], "ru") == (_OK, {"": []})


def test_fetch_page_without_revisions_is_a_known_miss(monkeypatch, clock):
    session(monkeypatch, ok(page("xyz", None), {"title": "abc", "revisions": []}))
    assert _fetch_batch(["xyz", "abc"], "ru") == (_OK, {"xyz": [], "abc": []})


def test_fetch_tolerates_missing_query_and_slots(monkeypatch, clock):
    session(monkeypatch, FakeResp(200, {}), ok({"title": "a", "revisions": [{}]}))
    assert _fetch_batch(["a"], "ru") == (_OK, {})
    assert _fetch_batch(["a"], "ru") == (_OK, {"a": []})


@pytest.mark.parametrize(
    "resp",
    [
        OSError("connection reset"),
        FakeResp(500),
        FakeResp(404),
        FakeResp(200, ValueError("not json")),
    ],
)
def test_fetch_failures_are_unavailable_and_do_not_cool_down(monkeypatch, clock, resp):
    session(monkeypatch, resp)
    assert _fetch_batch(["дом"], "ru") == (_UNAVAILABLE, {})
    assert glossary.cooldown_remaining() == 0.0
    assert glossary._max_parallel == glossary._PARALLEL_START


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"Retry-After": "30"}, 30.0),
        ({}, glossary._DEFAULT_COOLDOWN),
        ({"Retry-After": "soon"}, glossary._DEFAULT_COOLDOWN),
        ({"Retry-After": "0"}, 1.0),
        ({"Retry-After": "9999"}, glossary._MAX_COOLDOWN),
    ],
)
def test_fetch_429_enters_a_clamped_cooldown(monkeypatch, clock, headers, expected):
    session(monkeypatch, FakeResp(429, headers=headers))
    assert _fetch_batch(["дом"], "ru") == (_UNAVAILABLE, {})
    assert glossary.cooldown_remaining() == expected
    assert glossary._max_parallel == glossary._PARALLEL_START // 2
    assert type(glossary._max_parallel) is int


def test_fetch_skips_the_network_while_cooling_down(monkeypatch, clock):
    s = session(monkeypatch, FakeResp(429, headers={"Retry-After": "10"}), ok(page("дом", RU_DOM)))
    _fetch_batch(["дом"], "ru")
    clock.t += 9.5
    assert glossary.cooldown_remaining() == pytest.approx(0.5)
    assert _fetch_batch(["дом"], "ru") == (_UNAVAILABLE, {})
    assert len(s.calls) == 1
    clock.t += 0.5
    assert glossary.cooldown_remaining() == 0.0
    assert _fetch_batch(["дом"], "ru")[0] == _OK
    assert len(s.calls) == 2


def test_cooldown_never_shortens_and_width_floors_at_one(monkeypatch, clock):
    glossary._enter_cooldown(100, "a")
    glossary._enter_cooldown(5, "b")
    assert glossary.cooldown_remaining() == 100.0
    for _ in range(5):
        glossary._enter_cooldown(1, "c")
    assert glossary._max_parallel == 1


def test_clean_streak_widens_the_gate_up_to_the_ceiling(monkeypatch):
    monkeypatch.setattr(glossary, "_max_parallel", 1)
    for _ in range(glossary._RELAX_AFTER - 1):
        glossary._note_success()
    assert glossary._max_parallel == 1
    glossary._note_success()
    assert glossary._max_parallel == 2
    assert glossary._clean_streak == 0
    for _ in range(glossary._RELAX_AFTER * 10):
        glossary._note_success()
    assert glossary._max_parallel == glossary._PARALLEL_CEIL


def test_cooldown_resets_the_clean_streak(monkeypatch, clock):
    for _ in range(glossary._RELAX_AFTER - 1):
        glossary._note_success()
    glossary._enter_cooldown(1, "x")
    assert glossary._clean_streak == 0


def test_slot_blocks_beyond_the_gate_width(monkeypatch):
    monkeypatch.setattr(glossary, "_max_parallel", 1)
    entered = threading.Event()
    with glossary._slot():
        assert glossary._in_flight == 1
        t = threading.Thread(target=lambda: (glossary._slot().__enter__(), entered.set()))
        t.start()
        assert not entered.wait(0.1)
    assert entered.wait(1.0)
    t.join()


def test_slot_releases_on_error():
    with pytest.raises(RuntimeError):
        with glossary._slot():
            raise RuntimeError
    assert glossary._in_flight == 0


# --- lookup_many -------------------------------------------------------------


def test_lookup_non_english_target_and_empty_input_ask_nothing(monkeypatch):
    s = session(monkeypatch)
    assert lookup_many(["дом"], ["NOUN"], target="de") == [None]
    assert lookup_many([], []) == []
    assert lookup_many(["  ", ""], ["NOUN", "NOUN"]) == [None, None]
    assert s.calls == []


def test_lookup_selects_by_pos_and_caches_per_pos(monkeypatch, clock):
    s = session(monkeypatch, ok(page("и", RU_I), page("дом", RU_DOM)))
    out = lookup_many([" и ", "дом", "и"], ["cconj", "NOUN", "X"])
    assert out == ["and", "house; home", "even; and"]
    assert s.calls[0]["params"]["titles"] == "и|дом"
    assert glossary._CACHE == {
        ("ru", "CCONJ", "и"): "and",
        ("ru", "NOUN", "дом"): "house; home",
        ("ru", "X", "и"): "even; and",
    }
    # Fully cached: no second request.
    assert lookup_many(["дом", "и"], ["NOUN", "CCONJ"]) == ["house; home", "and"]
    assert len(s.calls) == 1


def test_lookup_missing_pos_list_entries_mean_no_pos(monkeypatch, clock):
    session(monkeypatch, ok(page("и", RU_I)))
    assert lookup_many(["и", "и"], ["CCONJ"]) == ["and", "even; and"]
    assert ("ru", "", "и") in glossary._CACHE
    # And that key is what a later lookup without a POS hits.
    assert lookup_many(["и"], []) == ["even; and"]


def test_lookup_caches_known_misses_but_not_failures(monkeypatch, clock):
    s = session(monkeypatch, ok(page("xyz", None)), FakeResp(500), ok(page("abc", None)))
    assert lookup_many(["xyz"], ["NOUN"]) == [None]
    assert glossary._CACHE == {("ru", "NOUN", "xyz"): None}
    assert lookup_many(["xyz"], ["NOUN"]) == [None]
    assert len(s.calls) == 1  # the miss was remembered

    assert lookup_many(["abc"], ["NOUN"]) == [None]  # HTTP 500
    assert ("ru", "NOUN", "abc") not in glossary._CACHE
    assert lookup_many(["abc"], ["NOUN"]) == [None]  # retried
    assert len(s.calls) == 3
    assert ("ru", "NOUN", "abc") in glossary._CACHE


def test_lookup_word_absent_from_answer_is_not_cached(monkeypatch, clock):
    session(monkeypatch, ok(page("дом", RU_DOM)))
    assert lookup_many(["кот", "дом"], ["NOUN", "NOUN"]) == [None, "house; home"]
    assert ("ru", "NOUN", "кот") not in glossary._CACHE


def test_lookup_blank_words_do_not_stop_the_batch(monkeypatch, clock):
    session(monkeypatch, ok(page("дом", RU_DOM)))
    assert lookup_many(["", "дом"], ["X", "NOUN"]) == [None, "house; home"]


def test_lookup_defaults_to_russian_into_english(monkeypatch, clock):
    text = "==Russian==\n===Noun===\n# ru\n==Ukrainian==\n===Noun===\n# uk\n"
    s = session(monkeypatch, ok(page("так", text)))
    assert lookup("так", "NOUN") == "ru"
    assert lookup("так", "NOUN", target="de") is None
    assert len(s.calls) == 1


def test_lookup_splits_large_batches(monkeypatch, clock):
    monkeypatch.setattr(glossary, "_BATCH_LIMIT", 2)
    s = session(
        monkeypatch,
        ok(page("a", RU_DOM), page("b", RU_DOM)),
        FakeResp(500),
        ok(page("e", RU_DOM)),
    )
    out = lookup_many(list("abcde"), ["NOUN"] * 5)
    assert [c["params"]["titles"] for c in s.calls] == ["a|b", "c|d", "e"]
    assert out == ["house; home", "house; home", None, None, "house; home"]


def test_lookup_source_is_part_of_the_cache_key(monkeypatch, clock):
    text = "==Russian==\n===Noun===\n# ru\n==Ukrainian==\n===Noun===\n# uk\n"
    s = session(monkeypatch, ok(page("так", text)), ok(page("так", text)))
    assert lookup("так", "NOUN", source="ru") == "ru"
    assert lookup("так", "NOUN", source="uk") == "uk"
    assert len(s.calls) == 2


# --- persistence -------------------------------------------------------------


def test_cache_round_trips_through_disk(monkeypatch, clock):
    session(monkeypatch, ok(page("дом", RU_DOM), page("xyz", None)))
    lookup_many(["дом", "xyz"], ["NOUN", "NOUN"])
    glossary.flush_cache()
    with open(glossary._CACHE_PATH, encoding="utf-8") as fh:
        raw = fh.read()
    assert json.loads(raw) == {"ru|NOUN|дом": "house; home", "ru|NOUN|xyz": None}
    assert "дом" in raw  # stored readable, not \\u-escaped

    monkeypatch.setattr(glossary, "_CACHE", {})
    glossary._load_cache()
    assert glossary._CACHE == {("ru", "NOUN", "дом"): "house; home", ("ru", "NOUN", "xyz"): None}


def test_load_cache_keeps_pipes_in_the_word(tmp_path, monkeypatch):
    with open(glossary._CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump({"ru|NOUN|a|b": "x"}, fh)
    glossary._load_cache()
    assert glossary._CACHE == {("ru", "NOUN", "a|b"): "x"}


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        # Valid JSON of the wrong shape escapes the try block in _load_cache
        # and raises at import time, so the backend fails to start.
        pytest.param("[1, 2]", marks=pytest.mark.xfail(strict=True, reason="bug: _load_cache only guards json.load")),
        pytest.param('{"no-pipes": 1}', marks=pytest.mark.xfail(strict=True, reason="bug: _load_cache only guards json.load")),
    ],
)
def test_load_cache_ignores_a_corrupt_file(content):
    with open(glossary._CACHE_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)
    glossary._load_cache()
    assert glossary._CACHE == {}


def test_load_cache_missing_file_is_a_cold_start():
    glossary._load_cache()
    assert glossary._CACHE == {}


def test_flush_only_writes_when_dirty():
    glossary.flush_cache()
    import os

    assert not os.path.exists(glossary._CACHE_PATH)


def test_flush_survives_an_unwritable_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(glossary, "_CACHE_PATH", str(tmp_path / "missing-dir" / "c.json"))
    monkeypatch.setattr(glossary, "_dirty", True)
    glossary.flush_cache()  # must not raise
    assert glossary._dirty is False


def test_remember_flushes_at_most_every_interval(monkeypatch, clock):
    import os

    clock.t = glossary._FLUSH_INTERVAL  # first write is due straight away
    glossary._remember(("ru", "NOUN", "a"), "x")
    assert os.path.exists(glossary._CACHE_PATH)
    os.remove(glossary._CACHE_PATH)

    clock.t += glossary._FLUSH_INTERVAL - 0.01
    glossary._remember(("ru", "NOUN", "b"), "y")
    assert not os.path.exists(glossary._CACHE_PATH)
    assert glossary._dirty is True

    clock.t += 0.01
    glossary._remember(("ru", "NOUN", "c"), "z")
    with open(glossary._CACHE_PATH, encoding="utf-8") as fh:
        assert set(json.load(fh)) == {"ru|NOUN|a", "ru|NOUN|b", "ru|NOUN|c"}


def test_remember_evicts_oldest_tenth_at_the_limit(monkeypatch, clock):
    monkeypatch.setattr(glossary, "_CACHE_LIMIT", 20)
    for i in range(20):
        glossary._CACHE[("ru", "", str(i))] = None
    glossary._remember(("ru", "", "new"), "g")
    assert len(glossary._CACHE) == 19
    assert ("ru", "", "0") not in glossary._CACHE
    assert ("ru", "", "1") not in glossary._CACHE
    assert ("ru", "", "2") in glossary._CACHE
    assert glossary._CACHE[("ru", "", "new")] == "g"


def test_flush_writes_its_temp_file_beside_the_cache(monkeypatch, tmp_path):
    # os.replace is only atomic within one filesystem, so the temp file must be
    # created in the cache's own directory, not the system temp dir.
    import os
    import tempfile

    target = tmp_path / "sub" / "c.json"
    target.parent.mkdir()
    monkeypatch.setattr(glossary, "_CACHE_PATH", str(target))
    monkeypatch.setattr(glossary, "_dirty", True)
    seen = []
    real = tempfile.mkstemp

    def spy(*args, **kwargs):
        fd, path = real(*args, **kwargs)
        seen.append(os.path.dirname(path))
        return fd, path

    monkeypatch.setattr(glossary.tempfile, "mkstemp", spy)
    glossary.flush_cache()
    assert seen == [str(target.parent)]
    assert target.exists()
    assert os.listdir(target.parent) == ["c.json"]
