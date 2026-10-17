from __future__ import annotations

import pytest

import analyzer
from analyzer import ACUTE, Analyzer, _analyze_batch_impl, _make_token, _store
from conftest import FakeMorph, FakeNlp, FakeTok, make_pipeline

A = ACUTE


def stress_first_vowel(text: str) -> str:
    """A deterministic accentizer: stress the first vowel of every word."""
    out, seen = [], False
    for ch in text:
        out.append(ch)
        if not ch.isalpha():
            seen = False
        elif ch in "аеиоуыэюяё" and not seen:
            out.append(A)
            seen = True
    return "".join(out)


@pytest.mark.parametrize(
    "pos, color",
    [
        ("VERB", "verb"),
        ("AUX", "verb"),
        ("NOUN", "noun"),
        ("PROPN", "noun"),
        ("ADJ", "adj"),
        ("PRON", "pron"),
        ("DET", "pron"),
        ("NUM", "num"),
        ("ADV", "adv"),
        ("ADP", "other"),
        ("PUNCT", "other"),
    ],
)
def test_make_token_colour_class(pos, color):
    assert _make_token(FakeTok("x", pos_=pos), None).color_class == color


@pytest.mark.parametrize(
    "surface, is_word",
    [
        ("слово", True),
        ("ім'я", True),
        ("п’ять", True),
        ("2024", False),
        ("а1", False),
        (",", False),
        ("-", False),
        ("'я", False),
        ("ім'", False),
        ("snake_case", False),
    ],
)
def test_make_token_is_word(surface, is_word):
    assert _make_token(FakeTok(surface), None).is_word is is_word


def test_make_token_fields_without_cursor():
    tok = FakeTok("Дом", pos_="NOUN", lemma_="дом", whitespace_=" ",
                  morph=FakeMorph({"Case": "Nom", "Number": "Sing"}))
    t = _make_token(tok, None)
    assert t.model_dump() == {
        "surface": "Дом",
        "accented": "Дом",
        "lemma": "дом",
        "pos": "NOUN",
        "morph": {"Case": "Nom", "Number": "Sing"},
        "color_class": "noun",
        "is_word": True,
        "ws": True,
    }


def test_make_token_empty_morph_and_no_whitespace():
    t = _make_token(FakeTok("и", morph=FakeMorph({})), None)
    assert t.morph == {}
    assert t.ws is False


def test_make_token_takes_accent_from_cursor():
    cur = analyzer._AccentCursor(f"до{A}м")
    assert _make_token(FakeTok("дом"), cur).accented == f"до{A}м"


def test_batch_empty():
    assert _analyze_batch_impl(make_pipeline(FakeNlp()), []) == []


def test_batch_one_doc_per_batch_and_per_sentence_accents():
    nlp = FakeNlp()
    out = _analyze_batch_impl(make_pipeline(nlp, stress_first_vowel), ["дом стоит", "кот, да"])
    # One parse for the whole batch, joined on the sentinel.
    assert nlp.calls == ["дом стоит ‖ кот, да"]
    assert [s.text for s in out] == [f"до{A}м сто{A}ит", f"ко{A}т, да{A}"]
    assert [[t.accented for t in s.tokens] for s in out] == [
        [f"до{A}м", f"сто{A}ит"],
        [f"ко{A}т", ",", f"да{A}"],
    ]
    assert [t.ws for t in out[0].tokens] == [True, True]


def test_batch_each_sentence_has_its_own_cursor():
    # The same word in two sentences: each sentence's accents come from its own
    # accentizer output, not from the first sentence's.
    accents = {"замок": f"за{A}мок", "замок!": f"замо{A}к!"}
    out = _analyze_batch_impl(make_pipeline(FakeNlp(), accents.__getitem__), ["замок", "замок!"])
    assert out[0].tokens[0].accented == f"за{A}мок"
    assert out[1].tokens[0].accented == f"замо{A}к"


class SplittingNlp(FakeNlp):
    """Emits an extra sentinel, so the batch splits into too many chunks."""

    def __call__(self, text):
        toks = super().__call__(text)
        if "‖" in text:
            toks.insert(0, FakeTok("‖", pos_="PUNCT"))
        return toks


def test_batch_falls_back_to_single_parses_when_chunks_mismatch():
    nlp = SplittingNlp()
    out = _analyze_batch_impl(make_pipeline(nlp, stress_first_vowel), ["дом", "кот"])
    assert nlp.calls == ["дом ‖ кот", "дом", "кот"]
    assert [[t.accented for t in s.tokens] for s in out] == [[f"до{A}м"], [f"ко{A}т"]]
    assert [s.text for s in out] == [f"до{A}м", f"ко{A}т"]


def test_batch_extra_chunks_beyond_cursors_get_no_accent():
    class ThreeChunks(FakeNlp):
        def __call__(self, text):
            return super().__call__(text) + [FakeTok("‖"), FakeTok("дом")]

    # Parsing must not crash on a chunk with no cursor; fallback then applies.
    out = _analyze_batch_impl(make_pipeline(ThreeChunks(), stress_first_vowel), ["кот"])
    assert len(out) == 1


def test_analyzer_rejects_unsupported_language():
    with pytest.raises(ValueError, match="unsupported source language: be"):
        Analyzer().analyze("привет", "be")


def test_analyzer_builds_each_pipeline_once_and_caches_results(monkeypatch):
    nlp = FakeNlp()
    builds = []

    def build():
        builds.append(1)
        return make_pipeline(nlp, stress_first_vowel)

    monkeypatch.setitem(analyzer._PIPELINE_BUILDERS, "ru", build)
    a = Analyzer()
    a.warm("ru")
    first = a.analyze_many(["дом", "кот"], "ru")
    again = a.analyze_many(["кот", "сад", "дом"], "ru")
    assert len(builds) == 1
    # Only the unseen sentence is parsed the second time.
    assert nlp.calls == ["дом ‖ кот", "сад"]
    assert [s.text for s in again] == [f"ко{A}т", f"са{A}д", f"до{A}м"]
    assert again[0] is first[1]
    assert a.analyze("дом", "ru") is first[0]


def test_analyzer_cache_is_per_language(monkeypatch):
    ru, uk = FakeNlp(), FakeNlp()
    monkeypatch.setitem(analyzer._PIPELINE_BUILDERS, "ru", lambda: make_pipeline(ru))
    monkeypatch.setitem(analyzer._PIPELINE_BUILDERS, "uk", lambda: make_pipeline(uk))
    a = Analyzer()
    a.analyze("так", "ru")
    a.analyze("так", "uk")
    assert ru.calls == ["так"] and uk.calls == ["так"]


def test_store_evicts_oldest_tenth_past_the_limit(monkeypatch):
    monkeypatch.setattr(analyzer, "_CACHE_LIMIT", 20)
    v = analyzer.AnalyzedSentence(text="", tokens=[])
    for i in range(21):
        _store(("ru", str(i)), v)
    assert len(analyzer._CACHE) == 21  # not over the limit yet when inserting #20
    _store(("ru", "new"), v)
    # 21 > 20, so drop 21 // 10 = 2 oldest, then insert.
    assert ("ru", "0") not in analyzer._CACHE
    assert ("ru", "1") not in analyzer._CACHE
    assert ("ru", "2") in analyzer._CACHE
    assert len(analyzer._CACHE) == 20


def test_analyzer_defaults_to_russian(monkeypatch):
    nlp = FakeNlp()
    monkeypatch.setitem(analyzer._PIPELINE_BUILDERS, "ru", lambda: make_pipeline(nlp))
    a = Analyzer()
    a.analyze("дом")
    a.analyze_many(["кот"])
    assert nlp.calls == ["дом", "кот"]
