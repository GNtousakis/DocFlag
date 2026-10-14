from docflag.compare.normalize import CompareOptions, normalize_text, normalize_word, strip_accents


def test_nfc_equivalence():
    decomposed = "ά"  # alpha + combining acute
    assert normalize_word(decomposed) == normalize_word("ά") == "α"


def test_greek_accents_stripped():
    assert normalize_word("Καλημέρα") == normalize_word("καλημερα")
    assert strip_accents("ΐ") == "ι"
    assert normalize_word("ΆΈΉΊΌΎΏ") == "αεηιουω"


def test_greek_case_and_final_sigma():
    assert normalize_word("ΟΔΟΣ") == normalize_word("οδός") == normalize_word("οδοσ")
    assert normalize_word("λόγος") == normalize_word("ΛΟΓΟΣ")
    assert "ς" not in normalize_word("τέλος")


def test_final_sigma_even_when_case_sensitive():
    opts = CompareOptions(ignore_case=False)
    assert normalize_word("λόγος", opts) == normalize_word("λόγοσ", opts)
    assert normalize_word("Λόγος", opts) != normalize_word("λόγος", opts)


def test_ignore_case_off_keeps_case():
    assert normalize_word("Hello", CompareOptions(ignore_case=False)) == "Hello"


def test_punctuation_ignored_by_default():
    assert normalize_word("(γεια,") == "γεια"
    assert normalize_word("«Λόγος»;") == "λογοσ"
    assert normalize_word("end.") == "end"


def test_punctuation_kept_when_requested():
    opts = CompareOptions(ignore_punctuation=False)
    assert normalize_word("end.", opts) == "end."


def test_decimal_separator_kept_inside_numbers():
    assert normalize_word("1,20") != normalize_word("12,0")
    assert normalize_word("1,20") == "1,20"


def test_pure_punctuation_is_empty():
    assert normalize_word("—") == ""
    assert normalize_word("|") == ""


def test_normalize_text_whitespace():
    assert normalize_text("  Το   Σκυλί\n\tτρέχει. ") == "το σκυλι τρεχει"
