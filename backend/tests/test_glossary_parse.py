from __future__ import annotations

import pytest

import glossary
from glossary import _parse_entries, _select

PAGE = """\
==English==
===Noun===
# not this one

==Russian==
===Etymology 1===
====Noun====
{{ru-noun+|за́мок}}

# [[castle]]
#: example sentence
#* citation
## {{lb|ru|figurative}} [[stronghold]]
# {{IPA|ru|x}}

====Declension====
# not a definition section

===Verb===
# to [[lock]]
### too deep

===Pronunciation===
# ignored

==Ukrainian==
===Noun===
# [[lock]] (Ukrainian)
"""


def test_parse_entries_reads_only_the_named_section():
    assert _parse_entries(PAGE, "Russian") == [
        {"partOfSpeech": "Noun", "definitions": [
            {"definition": "castle"},
            {"definition": "(figurative) stronghold"},
        ]},
        {"partOfSpeech": "Verb", "definitions": [{"definition": "to lock"}]},
    ]


def test_parse_entries_last_section_runs_to_end_of_page():
    assert _parse_entries(PAGE, "Ukrainian") == [
        {"partOfSpeech": "Noun", "definitions": [{"definition": "lock (Ukrainian)"}]}
    ]


def test_parse_entries_section_match_is_case_insensitive():
    assert _parse_entries(PAGE, "russian") == _parse_entries(PAGE, "Russian")
    assert _parse_entries("== Russian ==\n===Noun===\n# x\n", "RUSSIAN") == [
        {"partOfSpeech": "Noun", "definitions": [{"definition": "x"}]}
    ]


@pytest.mark.parametrize(
    "wikitext, section",
    [
        (PAGE, ""),
        (PAGE, "Belarusian"),
        ("", "Russian"),
        ("==Russian==\n", "Russian"),
        ("==Russian==\n===Noun===\n#: only an example\n", "Russian"),
        ("==Russian==\n===Noun===\n# {{IPA|x}}\n", "Russian"),
        ("==Russian==\n===Etymology===\n# x\n", "Russian"),
    ],
)
def test_parse_entries_nothing_usable(wikitext, section):
    assert _parse_entries(wikitext, section) == []


def test_parse_entries_first_heading_bounded_by_next():
    text = "==Russian==\n===Noun===\n# a\n===Adverb===\n# b\n"
    assert _parse_entries(text, "Russian") == [
        {"partOfSpeech": "Noun", "definitions": [{"definition": "a"}]},
        {"partOfSpeech": "Adverb", "definitions": [{"definition": "b"}]},
    ]


def test_every_pos_map_label_is_a_heading():
    assert glossary._POS_HEADINGS == {
        n for names in glossary._POS_MAP.values() for n in names
    }
    assert "Noun" in glossary._POS_HEADINGS and "Etymology" not in glossary._POS_HEADINGS


def e(pos, *defs):
    return {"partOfSpeech": pos, "definitions": [{"definition": d} for d in defs]}


@pytest.mark.parametrize(
    "entries, pos, expected",
    [
        ([], "NOUN", None),
        ([e("Noun", "castle"), e("Verb", "to lock")], "NOUN", "castle"),
        ([e("Noun", "castle"), e("Verb", "to lock")], "verb", "to lock"),
        ([e("Noun", "castle"), e("Verb", "to lock")], "AUX", "to lock"),
        ([e("Noun form", "of houses")], "NOUN", "of houses"),
        ([e("Participle", "made")], "ADJ", "made"),
        ([e("Determiner", "all")], "PRON", "all"),
        ([e("Conjunction", "and"), e("Letter", "the letter")], "CCONJ", "and"),
        ([e("Conjunction", "if")], "SCONJ", "if"),
        ([e("Proper noun", "Moscow")], "PROPN", "Moscow"),
        ([e("Numeral", "two")], "NUM", "two"),
        # No entry for that reading: fall back to all senses.
        ([e("Noun", "castle"), e("Verb", "to lock")], "ADV", "castle; to lock"),
        ([e("Noun", "castle")], "", "castle"),
        ([e("Noun", "castle")], "UNKNOWN", "castle"),
        # Blank and whitespace-only senses are skipped, others stripped.
        ([e("Noun", "", "  ", " castle ")], "NOUN", "castle"),
        ([e("Noun", "")], "NOUN", None),
        ([{"partOfSpeech": "Noun"}], "NOUN", None),
        ([{"partOfSpeech": "Noun", "definitions": [{}]}], "NOUN", None),
        # Duplicates collapse.
        ([e("Noun", "lock", "lock"), e("Noun form", "lock")], "NOUN", "lock"),
        # At most three senses, counted across entries.
        ([e("Noun", "a", "b", "c", "d")], "NOUN", "a; b; c"),
        ([e("Noun", "a", "b"), e("Noun form", "c", "d")], "NOUN", "a; b; c"),
        ([e("Noun", "a", "b", "c"), e("Noun form", "d")], "NOUN", "a; b; c"),
    ],
)
def test_select(entries, pos, expected):
    assert _select(entries, pos) == expected


def test_select_truncates_long_senses():
    long = "x" * 85 + ", ;   yyyyyyyyy"
    assert len(long) > glossary._MAX_SENSE_CHARS
    out = _select([e("Noun", long)], "NOUN")
    assert out == "x" * 85 + "…"


def test_select_keeps_sense_of_exactly_max_length():
    exact = "y" * glossary._MAX_SENSE_CHARS
    assert _select([e("Noun", exact)], "NOUN") == exact
    assert _select([e("Noun", exact + "z")], "NOUN") == exact + "…"


def test_select_trims_only_separators_before_the_ellipsis():
    sense = "X" * 89 + "XX"
    assert _select([e("Noun", sense)], "NOUN") == "X" * 90 + "…"


def test_select_dedupes_after_truncation():
    a = "z" * 95
    b = "z" * 99
    assert _select([e("Noun", a, b, "q")], "NOUN") == "z" * 90 + "…; q"
