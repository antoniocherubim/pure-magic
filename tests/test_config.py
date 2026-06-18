from __future__ import annotations

import pytest

from agent_loop.config import DEFAULT_OPENAI_MODEL, load_openai_settings


def test_load_openai_settings_returns_none_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert load_openai_settings() is None


def test_load_openai_settings_reads_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")

    settings = load_openai_settings()

    assert settings is not None
    assert settings.api_key == "test-key"
    assert settings.model == "gpt-4.1-mini"


def test_load_openai_settings_uses_default_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    settings = load_openai_settings()

    assert settings is not None
    assert settings.model == DEFAULT_OPENAI_MODEL
