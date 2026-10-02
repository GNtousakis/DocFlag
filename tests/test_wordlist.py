from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docflag import wordlist
from docflag.paths import data_dir


def test_first_load_returns_defaults_without_writing():
    assert wordlist.load() == wordlist.defaults()
    assert "confidential" in wordlist.load()
    assert not (data_dir() / "wordlist.json").exists()


def test_add_persists_and_deduplicates_ignoring_case():
    wordlist.save(["Alpha"])
    assert wordlist.add(["  beta  gamma ", "ALPHA", ""]) == ["Alpha", "beta gamma"]
    assert wordlist.load() == ["Alpha", "beta gamma"]
    assert (data_dir() / "wordlist.json").exists()


def test_remove_and_reset():
    wordlist.save(["Alpha", "Beta"])
    assert wordlist.remove("alpha") == ["Beta"]
    assert wordlist.reset() == wordlist.defaults()


def test_an_emptied_list_stays_empty():
    wordlist.save([])
    assert wordlist.load() == []


def test_parse_lines_skips_comments_and_blanks():
    assert wordlist.parse_lines("# note\n\none\r\n two words \n") == ["one", "two words"]


def test_decode_text_handles_utf8_utf16_and_greek_ansi():
    word = "εμπιστευτικό"
    assert wordlist.decode_text(word.encode("utf-8")) == word
    assert wordlist.decode_text(word.encode("utf-8-sig")) == word
    assert wordlist.decode_text(word.encode("utf-16")) == word
    assert wordlist.decode_text(word.encode("cp1253")) == word
