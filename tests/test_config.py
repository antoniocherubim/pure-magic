from __future__ import annotations

import pytest

from agent_loop.config import (
    AGENT_COST_LIMIT_ENV,
    AGENT_DRY_RUN_ENV,
    AGENT_MAX_ITERATIONS_ENV,
    DEFAULT_COST_LIMIT,
    DEFAULT_DRY_RUN,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_OPENAI_MODEL,
    HarnessOverrides,
    OPENAI_API_KEY_ENV,
    OPENAI_MODEL_ENV,
    load_openai_settings,
    resolve_harness_config,
)


def _base_contract(**overrides) -> dict[str, object]:
    contract = {
        "objective": "Test",
        "checks": ["pytest"],
        "constraints": ["Never use sudo"],
        "max_iterations": 5,
        "task_name": "config-test",
    }
    contract.update(overrides)
    return contract


def test_load_openai_settings_returns_none_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv(OPENAI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(OPENAI_MODEL_ENV, raising=False)

    assert load_openai_settings() is None


def test_load_openai_settings_reads_env_vars(monkeypatch) -> None:
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "test-key")
    monkeypatch.setenv(OPENAI_MODEL_ENV, "gpt-4.1-mini")

    settings = load_openai_settings()

    assert settings is not None
    assert settings.api_key == "test-key"
    assert settings.model == "gpt-4.1-mini"


def test_load_openai_settings_uses_default_model(monkeypatch) -> None:
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "test-key")
    monkeypatch.delenv(OPENAI_MODEL_ENV, raising=False)

    settings = load_openai_settings()

    assert settings is not None
    assert settings.model == DEFAULT_OPENAI_MODEL


def test_resolve_harness_config_cli_overrides_contract_and_env() -> None:
    contract = _base_contract(max_iterations=5, cost_limit=1.0, command_timeout_sec=90)
    env = {
        AGENT_MAX_ITERATIONS_ENV: "7",
        AGENT_COST_LIMIT_ENV: "3.0",
    }
    cli = HarnessOverrides(
        max_iterations=10,
        cost_limit=2.5,
        command_timeout_sec=45,
        dry_run=False,
        model="cli-model",
    )

    resolved = resolve_harness_config(contract, cli=cli, environ=env)

    assert resolved.limits.max_iterations == 10
    assert resolved.limits.cost_limit == 2.5
    assert resolved.limits.command_timeout_sec == 45
    assert resolved.dry_run is False
    assert resolved.openai_settings is None


def test_resolve_harness_config_contract_overrides_env() -> None:
    contract = _base_contract(max_iterations=3, cost_limit=1.5)
    env = {
        AGENT_MAX_ITERATIONS_ENV: "9",
        AGENT_COST_LIMIT_ENV: "4.0",
    }

    resolved = resolve_harness_config(contract, environ=env)

    assert resolved.limits.max_iterations == 3
    assert resolved.limits.cost_limit == 1.5


def test_resolve_harness_config_env_overrides_defaults() -> None:
    contract = _base_contract()
    contract.pop("cost_limit", None)
    env = {AGENT_COST_LIMIT_ENV: "2.0"}

    resolved = resolve_harness_config(contract, environ=env)

    assert resolved.limits.cost_limit == 2.0


def test_resolve_harness_config_defaults_without_contract_or_env() -> None:
    contract = _base_contract()
    contract.pop("cost_limit", None)

    resolved = resolve_harness_config(contract, environ={})

    assert resolved.limits.max_iterations == 5
    assert resolved.limits.cost_limit == DEFAULT_COST_LIMIT
    assert resolved.dry_run is DEFAULT_DRY_RUN


def test_resolve_harness_config_dry_run_precedence() -> None:
    contract = _base_contract(dry_run=True)
    env = {AGENT_DRY_RUN_ENV: "true"}

    cli_resolved = resolve_harness_config(
        contract,
        cli=HarnessOverrides(dry_run=False),
        environ=env,
    )
    contract_resolved = resolve_harness_config(contract, environ=env)
    env_resolved = resolve_harness_config(_base_contract(), environ=env)
    default_resolved = resolve_harness_config(_base_contract(), environ={})

    assert cli_resolved.dry_run is False
    assert contract_resolved.dry_run is True
    assert env_resolved.dry_run is True
    assert default_resolved.dry_run is DEFAULT_DRY_RUN


def test_resolve_harness_config_model_cli_overrides_env() -> None:
    env = {
        OPENAI_API_KEY_ENV: "test-key",
        OPENAI_MODEL_ENV: "env-model",
    }

    resolved = resolve_harness_config(
        _base_contract(),
        cli=HarnessOverrides(model="cli-model"),
        environ=env,
    )

    assert resolved.openai_settings is not None
    assert resolved.openai_settings.model == "cli-model"


def test_resolve_harness_config_openai_settings_none_without_api_key() -> None:
    resolved = resolve_harness_config(
        _base_contract(),
        cli=HarnessOverrides(model="cli-model"),
        environ={},
    )

    assert resolved.openai_settings is None


@pytest.mark.parametrize("value", ["false", "0", "no"])
def test_resolve_harness_config_parses_env_dry_run_false(value: str) -> None:
    resolved = resolve_harness_config(
        _base_contract(),
        environ={AGENT_DRY_RUN_ENV: value},
    )

    assert resolved.dry_run is False
