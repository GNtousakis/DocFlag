from docflag.compare.diffing import ADDED, CHANGED, EQUAL, REMOVED, diff_texts, diff_words
from docflag.compare.normalize import CompareOptions


def kinds(segs, kind):
    return "".join(s.text for s in segs if s.kind == kind).strip()


def test_identical_ignoring_whitespace_case_punct_accents():
    r = diff_words("Το γρήγορο, καφέ σκυλί.", "το  ΓΡΗΓΟΡΟ\nκαφε σκυλι")
    assert r.stats.total == 0
    assert all(s.kind == EQUAL for s in r.left + r.right)


def test_display_text_preserved():
    r = diff_words("Το ΣΚΥΛΊ, εδώ", "το σκυλί εδώ")
    assert "".join(s.text for s in r.left) == "Το ΣΚΥΛΊ, εδώ"
    assert "".join(s.text for s in r.right) == "το σκυλί εδώ"


def test_added_and_removed():
    r = diff_words("a b c", "a b x c")
    assert (r.stats.added, r.stats.removed, r.stats.changed) == (1, 0, 0)
    assert kinds(r.right, ADDED) == "x"
    r = diff_words("a b x c", "a b c")
    assert (r.stats.added, r.stats.removed) == (0, 1)
    assert kinds(r.left, REMOVED) == "x"


def test_changed_is_yellow_on_both_sides():
    r = diff_words("η τιμή είναι 10 ευρώ", "η τιμή είναι 12 ευρώ")
    assert r.stats.changed == 1 and r.stats.added == 0 and r.stats.removed == 0
    assert kinds(r.left, CHANGED) == "10"
    assert kinds(r.right, CHANGED) == "12"


def test_case_sensitive_option():
    opts = CompareOptions(ignore_case=False)
    assert diff_words("Hello", "hello", opts).stats.changed == 1
    assert diff_words("Hello", "hello").stats.total == 0


def test_punctuation_option():
    opts = CompareOptions(ignore_punctuation=False)
    assert diff_words("end.", "end", opts).stats.changed == 1


def test_standalone_punctuation_never_flagged():
    assert diff_words("a — b", "a b").stats.total == 0


def test_empty_sides():
    assert diff_words("", "").stats.total == 0
    r = diff_words("", "α β")
    assert r.stats.added == 1


TABLE_A = "Είδος | Ποσότητα | Τιμή\nΜήλα | 10 | 1,20\nΑχλάδια | 5 | 2,00"
TABLE_B = "Είδος | Ποσότητα | Τιμή\nΜήλα | 12 | 1,20\nΑχλάδια | 5 | 2,00"


def test_table_changed_cell_highlighted_in_row():
    r = diff_texts(TABLE_A, TABLE_B)
    assert r.structured
    assert (r.stats.changed, r.stats.added, r.stats.removed) == (1, 0, 0)
    assert kinds(r.left, CHANGED) == "10" and kinds(r.right, CHANGED) == "12"
    assert "".join(s.text for s in r.left) == TABLE_A


def test_table_added_row():
    b = TABLE_B + "\nΚεράσια | 3 | 9,00"
    r = diff_texts(TABLE_A, b)
    assert r.stats.added == 1
    assert kinds(r.right, ADDED) == "Κεράσια | 3 | 9,00"


def test_table_removed_row_in_middle_keeps_alignment():
    a = "Είδος | Τιμή\nΜήλα | 1\nΑχλάδια | 2\nΚεράσια | 3"
    b = "Είδος | Τιμή\nΜήλα | 1\nΚεράσια | 3"
    r = diff_texts(a, b)
    assert r.stats.removed == 1 and r.stats.total == 1
    assert kinds(r.left, REMOVED) == "Αχλάδια | 2"


def test_table_changed_row_when_counts_differ():
    a = "Είδος | Τιμή\nΜήλα | 1\nΑχλάδια | 2"
    b = "Είδος | Τιμή\nΜήλα | 1\nΑχλάδια | 3\nΠεπόνι | 4"
    r = diff_texts(a, b)
    assert r.stats.changed == 1 and r.stats.added == 1
    assert kinds(r.left, CHANGED) == "2" and kinds(r.right, CHANGED) == "3"


def test_table_vs_plain_falls_back_to_word_diff():
    r = diff_texts(TABLE_A, "Είδος Ποσότητα Τιμή Μήλα 10 1,20 Αχλάδια 5 2,00")
    assert not r.structured and r.stats.total == 0


def test_table_cell_text_reflow_in_paragraph_lines():
    a = "Εισαγωγή\nΤο κείμενο εδώ\nΕίδος | Τιμή\nΜήλα | 1"
    b = "Εισαγωγή\nΤο κείμενο  εδώ\nΕίδος | Τιμή\nΜήλα | 1"
    assert diff_texts(a, b).stats.total == 0


def test_similarity_identical_and_empty():
    assert diff_texts("a b c", "a b c").stats.similarity == 100.0
    assert diff_texts("", "").stats.similarity == 100.0
    assert diff_texts("a b c", "").stats.similarity == 0.0


def test_similarity_one_word_changed():
    s = diff_texts("one two three four", "one two three five").stats.similarity
    assert s == 75.0


def test_similarity_table_rows():
    a = "Name | Qty | Price\nApple | 3 | 1.20\nPear | 2 | 0.90"
    b = "Name | Qty | Price\nApple | 3 | 1.50\nPear | 2 | 0.90"
    s = diff_texts(a, b).stats.similarity
    assert 80 < s < 100
