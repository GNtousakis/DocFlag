import pytest

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep the shared word list out of the repo while testing."""
    monkeypatch.setenv("DOCFLAG_DATA_DIR", str(tmp_path / "data"))
