"""
conftest.py — shared fixtures for the test suite.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Each test gets its own fresh SQLite database."""
    db_file = str(tmp_path / "test_recipes.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import recipes.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", __import__("pathlib").Path(db_file))
    monkeypatch.setenv("DROPBOX_TOKEN", "fake-token")
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://fake.llm/v1")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    import recipes.tagger as tagger_module

    tagger_module.reset_client()

    import recipes.poller as poller_module

    poller_module.reset_dropbox_client()

    yield db_file


@pytest.fixture()
def client(temp_db):
    """FastAPI test client with a fresh DB and scheduler disabled."""
    # Patch the poller so startup doesn't try to hit Dropbox
    import unittest.mock as mock

    with mock.patch("recipes.main.poll_dropbox"):
        from recipes.main import app

        with TestClient(app) as c:
            yield c
