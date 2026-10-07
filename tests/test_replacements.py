from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docflag import replacements
from docflag.paths import data_dir


def test_first_load_returns_defaults_without_writing():
    assert replacements.load() == replacements.defaults()
    assert ("τηλ.", "τηλέφωνο") in replacements.load()
    assert not (data_dir() / "replacements.json").exists()


def test_adding_a_known_text_updates_its_replacement():
    replacements.save([("τηλ.", "τηλέφωνο")])
    assert replacements.add([("ΤΗΛ.", "τηλ"), ("κ.", "κύριος")]) == [
        ("τηλ.", "τηλ"), ("κ.", "κύριος"),
    ]
    assert replacements.load() == [("τηλ.", "τηλ"), ("κ.", "κύριος")]
    assert replacements.remove("Κ.") == [("τηλ.", "τηλ")]
    assert replacements.reset() == replacements.defaults()


def test_spaces_are_kept():
    assert replacements.save([(" .", "."), ("a  b", " c  "), ("  ", "x")]) == [
        (" .", "."), ("a b", " c  "),
    ]


def test_parse_lines():
    pairs, skipped = replacements.parse_lines(
        "# comment\n"
        "τηλ. => τηλέφωνο\n"
        '" ." => "."\n'
        "a\tb\n"
        "TODO =>\n"
        "οδ. → οδός\n"
        "no separator\n"
        " => nothing to replace\n"
    )
    assert pairs == [
        ("τηλ.", "τηλέφωνο"), (" .", "."), ("a", "b"), ("TODO", ""), ("οδ.", "οδός"),
    ]
    assert skipped == 2


def test_format_lines_round_trips():
    pairs = [(" .", "."), ("TODO", ""), ("a=>b", "c"), ("τηλ.", "τηλέφωνο")]
    text = replacements.format_lines(pairs)
    assert text.splitlines()[0] == '" ." => .'
    assert replacements.parse_lines(text) == (pairs, 0)
