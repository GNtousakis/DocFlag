import pytest
from PIL import Image, ImageDraw, ImageFont

from docflag.compare.imagediff import ImageDiffOptions, compare_images, render_overlay

LINES = [
    "Quarterly report invoice total 1250",
    "Payment delivery agreement signed",
    "Customer reference number 88231",
    "Shipping address warehouse north",
    "Approved by finance department",
    "Review scheduled for next month",
]


def doc(lines=LINES, size=(900, 420), offset=(0, 0)):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=28)
    for i, line in enumerate(lines):
        d.text((40 + offset[0], 30 + offset[1] + i * 55), line, fill="black", font=font)
    return img


def test_identical_is_100_and_has_no_boxes():
    r = compare_images(doc(), doc())
    assert r.similarity == pytest.approx(100.0, abs=0.5)
    assert r.boxes == []
    assert r.aligned


def test_shift_is_not_a_change():
    r = compare_images(doc(), doc(offset=(7, 5)))
    assert r.aligned
    assert r.similarity > 95
    assert r.boxes == []


def test_scale_is_not_a_change():
    b = doc()
    b = b.resize((int(b.width * 0.9), int(b.height * 0.9)), Image.LANCZOS)
    r = compare_images(doc(), b)
    assert r.aligned
    assert r.similarity > 90
    assert len(r.boxes) <= 1


def test_edited_word_is_boxed_and_lowers_score():
    lines = list(LINES)
    lines[2] = "Customer reference number 99999"
    r = compare_images(doc(), doc(lines))
    assert 1 <= len(r.boxes) <= 2
    x, y, w, h = r.boxes[0]
    assert 40 + 55 * 2 - 15 < y < 40 + 55 * 2 + 40  # on the third line
    assert r.similarity < 100
    assert r.similarity > compare_images(doc(), doc(["Something else entirely"] * 6)).similarity


def test_different_content_scores_low():
    other = ["Zebra quartz fjord waltz nymph", "Glyph jinx vex bold", "Mighty crypt dwarf", "Lorem ipsum", "Dolor sit", "Amet"]
    assert compare_images(doc(), doc(other)).similarity < 60


def test_aspect_mismatch_falls_back_with_warning():
    r = compare_images(Image.new("RGB", (400, 100), "white"), Image.new("RGB", (100, 400), "white"))
    assert not r.aligned
    assert r.warning == "aspect"


def test_blank_images_do_not_crash():
    r = compare_images(Image.new("RGB", (300, 200), "white"), Image.new("RGB", (300, 200), "white"))
    assert r.similarity == 100.0
    assert r.boxes == []


def test_dark_theme_documents():
    inv = lambda im: Image.eval(im, lambda v: 255 - v)
    r = compare_images(inv(doc()), inv(doc()))
    assert r.similarity > 99


def test_sensitivity_presets_and_overlay():
    assert ImageDiffOptions.from_sensitivity(0).threshold > ImageDiffOptions.from_sensitivity(2).threshold
    lines = list(LINES)
    lines[0] = "Quarterly report invoice total 9999"
    r = compare_images(doc(), doc(lines))
    out = render_overlay(r.b, r.boxes, r.heat)
    assert out.size == r.a.size and out.mode == "RGB"
