from __future__ import annotations

import pytest

from glossary import _clean, _expand_template, _readable_target


@pytest.mark.parametrize(
    "body, expected",
    [
        # labels
        ("lb|ru|colloquial", "(colloquial)"),
        ("label|ru|figurative|_|rare", "(figurative, rare)"),
        ("lbl|ru|slang", "(slang)"),
        ("LB|ru|dated", "(dated)"),
        ("lb|ru", ""),
        ("lb|ru|_", ""),
        ("lb|ru||informal", "(informal)"),
        # named args are not labels
        ("lb|ru|sort=x|informal", "(informal)"),
        # case government
        ("+obj|ru|ins", "(+ instrumental)"),
        ("obj|ru|acc|gen", "(+ accusative, genitive)"),
        ("+obj|ru|pre|loc|nom|dat", "(+ prepositional, locative, nominative, dative)"),
        ("+obj|ru|xyz", "(+ xyz)"),
        ("+obj|ru", ""),
        # the "form of" family
        ("inflection of|ru|весь||ins|m//n|s", "inflection of весь"),
        ("infl of|ru|весь||gen", "inflection of весь"),
        ("alt form|ru|ещё", "alternative form of ещё"),
        ("alt sp|ru|ещё", "alternative spelling of ещё"),
        ("abbr of|ru|СССР", "abbreviation of СССР"),
        ("syn of|ru|дом", "synonym of дом"),
        ("dim of|ru|дом", "diminutive of дом"),
        ("plural of|ru|дом", "plural of дом"),
        ("ru-participle of|ru|делать", "participle of делать"),
        ("uk-participle of|uk|робити", "participle of робити"),
        ("ru-PRE-reform_of|ru|дом", "pre-reform of дом"),
        ("feminine_of|ru|кот", "feminine of кот"),
        ("plural of|ru", "plural of"),
        ("plural of|ru|", "plural of"),
        # links and mentions
        ("l|ru|дом", "дом"),
        ("l|ru|дом|house", "house"),
        ("m|ru|кот", "кот"),
        ("ll|ru|сад", "сад"),
        ("link|en|garden", "garden"),
        ("mention|en|x|y", "y"),
        ("l|ru", ""),
        ("l", ""),
        # glosses and qualifiers
        ("gloss|a house", "(a house)"),
        ("gl|x", "(x)"),
        ("q|rare", "(rare)"),
        ("qual|rare", "(rare)"),
        ("qualifier|rare", "(rare)"),
        ("i|rare", "(rare)"),
        ("q", ""),
        # non-gloss definitions
        ("n-g|used to express doubt", "used to express doubt"),
        ("ng|x", "x"),
        ("non-gloss definition|x", "x"),
        ("non gloss definition|x", "x"),
        ("n-g", ""),
        # wikipedia links show their last argument as prose
        ("w|Moscow", "Moscow"),
        ("w|Moscow_Kremlin#History|Kremlin", "Kremlin"),
        ("pedlink|Red_Square", "Red Square"),
        ("w", ""),
        # anything else is decoration
        ("ru-noun-table|a", ""),
        ("IPA|ru|[dom]", ""),
        ("  lb  | ru | rare ", "(rare)"),
    ],
)
def test_expand_template(body, expected):
    assert _expand_template(body) == expected


@pytest.mark.parametrize(
    "target, expected",
    [
        ("house", "house"),
        ("red_square", "red square"),
        ("lustre#English", "lustre"),
        ("American_and_British_English_spelling_differences#-re,_-er", "American and British English spelling differences"),
        ("w:Moscow", "Moscow"),
        ("Wiktionary:About", "About"),
        ("Appendix:Russian_nouns", "Russian nouns"),
        ("abcdefghijkl:x", "x"),  # 12-char namespace still stripped
        ("abcdefghijklm:x", "abcdefghijklm:x"),  # 13 is prose
        ("ratio 1:2", "ratio 1:2"),  # space before the colon: prose
        ("a b:c", "a b:c"),
        ("ns:", "ns:"),  # empty tail is not a namespace split
        (" padded_ ", "padded"),
        ("a:b:c", "b:c"),
        ("a#b#c", "a"),
    ],
)
def test_readable_target(target, expected):
    assert _readable_target(target) == expected


@pytest.mark.parametrize(
    "wikitext, expected",
    [
        ("house", "house"),
        ("a [[house]]", "a house"),
        ("a [[дом|house]]", "a house"),
        ("[[w:Moscow_Kremlin]]", "Moscow Kremlin"),
        ("[[lustre#English|]]", "lustre"),
        ("'''bold''' and ''italic''", "bold and italic"),
        ("x<ref>Some source</ref> y", "x y"),
        ("x<ref name=\"a\"/> y", "x y"),
        ("x<ref name=a>multi\nline</ref>.", "x."),
        ("<span class=\"x\">inner</span>", "inner"),
        ("{{lb|ru|colloquial}} a house", "(colloquial) a house"),
        ("{{l|ru|{{q|rare}}}}", "(rare)"),  # inner first; the outer link then shows "(rare)"
        ("{{gloss|{{l|en|house}}}}", "(house)"),
        ("{{IPA|ru|x}}, house", "house"),
        ("house {{IPA|ru|x}}.", "house."),
        ("house ( ) here", "house here"),
        ("house () here", "house here"),
        ("  ,; house :; ", "house"),
        ("a   b\t\nc", "a b c"),
        ("x , y ; z .", "x, y; z."),
        ("", ""),
        # Only spaces and separators are trimmed from the ends.
        ("X-ray X", "X-ray X"),
    ],
)
def test_clean(wikitext, expected):
    assert _clean(wikitext) == expected


def test_clean_expands_six_levels_of_nesting_but_not_seven():
    def nest(n: int) -> str:
        s = "core"
        for _ in range(n):
            s = "{{gloss|" + s + "}}"
        return s

    assert _clean(nest(6)) == "((((((core))))))"
    # A seventh level is left as raw braces rather than looping forever.
    assert "{{" in _clean(nest(7))
