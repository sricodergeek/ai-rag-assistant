from pathlib import Path
import runpy
import sys
from types import ModuleType
from unittest.mock import Mock

import dotenv
import pytest
import redis


APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize("url", [None, "redis://redis.internal:6379", "rediss://redis.internal:6380"])
@pytest.mark.parametrize("module_name", ["session_service", "oauth_state"])
def test_redis_clients_use_central_configuration(monkeypatch, url, module_name):
    # Isolate configuration from local secrets and other tests' imported modules.
    monkeypatch.setattr(dotenv, "load_dotenv", Mock())
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-unused-key")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.delenv("RAG_MAX_DISTANCE", raising=False)
    monkeypatch.delenv("RAG_MAX_HISTORY_MESSAGES", raising=False)
    if url is None:
        monkeypatch.delenv("REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("REDIS_URL", url)

    settings = runpy.run_path(str(APP_DIR / "config.py"))
    expected_url = url if url is not None else "redis://localhost:6379"
    assert settings["REDIS_URL"] == expected_url

    config = ModuleType("backend.app.config")
    config.REDIS_URL = settings["REDIS_URL"]
    monkeypatch.setitem(sys.modules, "backend.app.config", config)
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(redis.Redis, "from_url", factory)

    module = runpy.run_path(str(APP_DIR / f"{module_name}.py"))

    factory.assert_called_once_with(expected_url, decode_responses=True)
    assert module["_redis"] is client
