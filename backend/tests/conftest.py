import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _no_ambient_ai_credentials(monkeypatch, request):
    """Unit tests must never reach a real model because a developer's shell happens to hold a key.
    Tests marked ``live`` opt out and use the real environment."""
    if "live" not in request.keywords:
        monkeypatch.delenv("GEMMA_API_KEY", raising=False)
        monkeypatch.delenv("GEMMA_ENDPOINT", raising=False)
