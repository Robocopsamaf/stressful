from __future__ import annotations

import pytest

from analyzer import ACUTE, _AccentCursor, _split_accented, _strip_accent

A = ACUTE


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", []),
        ("   ", []),
        ("слово", ["слово"]),
        (f"заМо{A}к стои{A}т", [f"заМо{A}к", f"стои{A}т"]),
        ("в 2024 году", ["в", "2024", "году"]),
        ("кто-то", ["кто", "-", "то"]),
        ("да, нет.", ["да", ",", "нет", "."]),
        # Apostrophes stay inside a word only between two letters.
        ("ім'я", ["ім'я"]),
        ("п’ять", ["п’ять"]),
        ("'ім", ["'", "ім"]),
        ("ім'", ["ім", "'"]),
        ("ім' я", ["ім", "'", "я"]),
        ("5'я", ["5", "'", "я"]),
        # Only the character right before the apostrophe must be a letter...
        ("1а'б", ["1а'б"]),
        # ...and only the one right after it.
        ("ім'я,", ["ім'я", ","]),
        ("ім'я5", ["ім'я5"]),
        # The letter before the apostrophe may carry an accent.
        (f"бу{A}'ти", [f"бу{A}'ти"]),
        ("a\tb\nc", ["a", "b", "c"]),
    ],
)
def test_split_accented(text, expected):
    assert _split_accented(text) == expected


def test_strip_accent_removes_only_the_combining_acute():
    assert _strip_accent(f"за{A}мо{A}к") == "замок"
    assert _strip_accent("ёж") == "ёж"


def test_cursor_is_positional_for_repeated_homographs():
    cur = _AccentCursor(f"за{A}мок и замо{A}к")
    assert [cur.take(w) for w in ["замок", "и", "замок"]] == [
        f"за{A}мок",
        "и",
        f"замо{A}к",
    ]


def test_cursor_resyncs_within_the_window():
    # spaCy saw a token the accentizer cut differently; the next match is
    # three pieces ahead, inside the window, so the cursor jumps to it.
    cur = _AccentCursor(f"а б в до{A}м")
    assert cur.take("дом") == f"до{A}м"
    # Having jumped, it does not re-offer earlier pieces positionally.
    assert cur.take("а") == "а"


def test_cursor_window_is_four_pieces():
    cur = _AccentCursor(f"а б в г до{A}м ко{A}т")
    # "кот" is five pieces ahead: out of the window, so this is a surface-map
    # hit and must not move the position.
    assert cur.take("кот") == f"ко{A}т"
    # "дом" is at index 4, also outside the window of 4 from position 0.
    assert cur.take("дом") == f"до{A}м"
    assert cur.pos == 0
    assert cur.take("г") == "г"
    assert cur.pos == 4


def test_cursor_window_reaches_index_three():
    cur = _AccentCursor(f"а б в до{A}м до{A}м")
    assert cur.take("дом") == f"до{A}м"
    assert cur.pos == 4


def test_cursor_surface_fallback_keeps_first_occurrence():
    cur = _AccentCursor(f"x x x x за{A}мок замо{A}к")
    assert cur.by_surface["замок"] == f"за{A}мок"


def test_cursor_passes_unknown_surface_through():
    cur = _AccentCursor("при")
    assert cur.take("кот") == "кот"
    assert cur.pos == 0


def test_cursor_stops_at_end_of_pieces():
    cur = _AccentCursor("а")
    assert cur.take("а") == "а"
    assert cur.take("а") == "а"  # surface fallback after the end
    assert cur.pos == 1
