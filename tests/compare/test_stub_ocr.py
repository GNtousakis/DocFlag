from PIL import Image

from docflag.compare.ocr import StubOcrEngine, create_engine
from docflag.compare.ocr.base import OcrEngine, OcrResult
from docflag.compare.ocr.stub import load_fixture


def test_stub_returns_fixture_with_boxes():
    eng = StubOcrEngine("table_v1")
    assert isinstance(eng, OcrEngine) and not eng.is_real
    res = eng.recognize(Image.new("L", (10, 10)))
    assert isinstance(res, OcrResult)
    assert "Ποσότητα" in res.text
    assert all(w.width > 0 and w.height > 0 for w in res.words)
    assert eng.calls == 1


def test_stub_cycles_and_accepts_results():
    v2 = load_fixture("table_v2")
    eng = StubOcrEngine(["table_v1", v2])
    first = eng.recognize(None)
    second = eng.recognize(None)
    third = eng.recognize(None)
    assert first.text != second.text and first.text == third.text


def test_create_engine_forced_stub():
    assert not create_engine(force_stub=True).is_real


def test_all_fixtures_load():
    for name in ["greek", "english", "mixed", "table_v1", "table_v2", "mixed_page"]:
        assert load_fixture(name).words
