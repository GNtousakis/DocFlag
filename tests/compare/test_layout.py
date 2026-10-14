from docflag.compare.layout import CELL_SEP, group_rows, reconstruct_text
from docflag.compare.ocr.base import OcrLine, OcrResult, OcrWord
from docflag.compare.ocr.stub import load_fixture
from docflag.compare.ocr.synthetic import combine, paragraph_lines, table

COLS = [20.0, 260.0, 460.0]


def test_paragraph_line_breaks_merged():
    text = reconstruct_text(load_fixture("greek"))
    lines = text.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("Η εταιρεία") and lines[0].endswith("σύμβασης.")
    assert "\n" not in lines[0]


def test_english_and_mixed_fixtures():
    assert reconstruct_text(load_fixture("english")).count("\n") == 1
    mixed = reconstruct_text(load_fixture("mixed"))
    assert "\n" not in mixed and "DocDiff" in mixed and "Word document" in mixed


def test_blank_line_starts_new_paragraph():
    res = paragraph_lines(["first paragraph line one that is long enough to wrap", "continues here and is long too", "second paragraph"],
                          gaps_before=(2,))
    assert reconstruct_text(res).split("\n") == [
        "first paragraph line one that is long enough to wrap continues here and is long too",
        "second paragraph",
    ]


def test_hyphenated_word_rejoined():
    res = paragraph_lines(["this is a long line of text with a split wor-", "ding that continues here and wraps again"])
    assert "wording" in reconstruct_text(res)


def test_table_rows_and_columns():
    rows = [["Είδος", "Ποσότητα", "Τιμή"], ["Μήλα", "10", "1,20"], ["Αχλάδια", "5", "2,00"]]
    text = reconstruct_text(table(rows, COLS))
    assert text.split("\n") == [CELL_SEP.join(r) for r in rows]


def test_table_with_empty_cell_keeps_columns():
    rows = [["Είδος", "Ποσότητα", "Τιμή"], ["Μήλα", "", "1,20"], ["Αχλάδια", "5", "2,00"]]
    lines = reconstruct_text(table(rows, COLS)).split("\n")
    assert lines[1] == "Μήλα |  | 1,20"
    assert lines[2] == "Αχλάδια | 5 | 2,00"


def test_table_multiword_cells():
    rows = [["Περιγραφή είδους", "Ποσότητα", "Τιμή"], ["Φρέσκα μήλα Ελλάδας", "10", "1,20"]]
    lines = reconstruct_text(table(rows, COLS)).split("\n")
    assert lines == ["Περιγραφή είδους | Ποσότητα | Τιμή", "Φρέσκα μήλα Ελλάδας | 10 | 1,20"]


def test_words_out_of_order_and_split_over_ocr_lines():
    # Windows OCR may split a table row into several lines or order them oddly.
    base = table([["a", "b", "c"], ["d", "e", "f"]], COLS)
    shuffled = OcrResult(list(reversed(base.lines)))
    assert reconstruct_text(shuffled) == "a | b | c\nd | e | f"


def test_paragraph_table_paragraph_page():
    text = reconstruct_text(load_fixture("mixed_page"))
    lines = text.split("\n")
    assert len(lines) == 6
    assert lines[1] == "Είδος | Ποσότητα | Τιμή"
    assert lines[5].startswith("The company")


def test_two_column_prose_read_column_by_column():
    left = ["alpha beta gamma delta epsilon zeta eta theta", "iota kappa lambda mu nu xi omicron pi", "rho sigma tau upsilon phi chi psi omega"]
    right = ["one two three four five six seven eight", "nine ten eleven twelve thirteen fourteen", "fifteen sixteen seventeen eighteen nineteen"]
    lines = []
    for i, (a, b) in enumerate(zip(left, right)):
        y = 20 + 28 * i
        for text, x in ((a, 20.0), (b, 700.0)):
            words, cx = [], x
            for tok in text.split():
                words.append(OcrWord(tok, cx, y, 10 * len(tok), 20))
                cx += 10 * len(tok) + 8
            lines.append(OcrLine(text, words))
    text = reconstruct_text(OcrResult(lines))
    assert " | " not in text
    flat = " ".join(text.split())
    assert flat.index("omega") < flat.index("one")
    assert flat.startswith("alpha beta")


def test_group_rows_tolerates_slight_vertical_jitter():
    words = [OcrWord("a", 0, 100, 10, 20), OcrWord("b", 30, 103, 10, 20), OcrWord("c", 0, 130, 10, 20)]
    assert [len(r.words) for r in group_rows(words)] == [2, 1]


def test_no_boxes_falls_back_to_line_text():
    res = OcrResult([OcrLine("one"), OcrLine("two")])
    assert reconstruct_text(res) == "one\ntwo"


def test_empty_result():
    assert reconstruct_text(OcrResult()) == ""


def test_single_spaced_row_with_wide_gap_stays_plain():
    res = combine(paragraph_lines(["Date: 12 Jan"]))
    assert " | " not in reconstruct_text(res)
