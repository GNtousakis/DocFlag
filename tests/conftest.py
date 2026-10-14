import pytest

from docflag.compare import service

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep the shared word list out of the repo while testing."""
    monkeypatch.setenv("DOCFLAG_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def stub_ocr(monkeypatch):
    """Canned OCR results (compare/fixtures) instead of Windows OCR, starting
    afresh in every test: table_v1 for the first image, table_v2 for the next."""
    monkeypatch.setenv(service.STUB_ENV, "1")
    monkeypatch.setattr(service, "_engine", None)
