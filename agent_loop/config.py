"""Central configuration and safety defaults."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_BRANCH_PREFIX = "agent/"
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_COST_LIMIT = 5.0
DEFAULT_FAILURE_LIMIT = 3
DEFAULT_COMMAND_TIMEOUT_SEC = 120
DEFAULT_ESTIMATED_COST_PER_ITERATION = 0.02
DEFAULT_DRY_RUN = True
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
AGENT_MAX_ITERATIONS_ENV = "AGENT_MAX_ITERATIONS"
AGENT_COMMAND_TIMEOUT_SEC_ENV = "AGENT_COMMAND_TIMEOUT_SEC"
AGENT_COST_LIMIT_ENV = "AGENT_COST_LIMIT"
AGENT_DRY_RUN_ENV = "AGENT_DRY_RUN"
SUPPORTED_OPERATION_TYPES = ("write_file",)
PROTECTED_PATHS = (".env", ".git/")
REQUIRED_CONTRACT_FIELDS = (
    "objective",
    "checks",
    "constraints",
    "max_iterations",
    "task_name",
)

DANGEROUS_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(checkout|switch)\s+(main|master)\b", re.IGNORECASE),
    re.compile(r"\.env\b"),
)


@dataclass(slots=True)
class OpenAISettings:
    api_key: str
    model: str = DEFAULT_OPENAI_MODEL


@dataclass(slots=True)
class HarnessOverrides:
    """Explicit CLI or programmatic overrides; None means use the next layer."""

    model: str | None = None
    max_iterations: int | None = None
    command_timeout_sec: int | None = None
    cost_limit: float | None = None
    dry_run: bool | None = None


@dataclass(slots=True)
class RuntimeLimits:
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    cost_limit: float = DEFAULT_COST_LIMIT
    failure_limit: int = DEFAULT_FAILURE_LIMIT
    command_timeout_sec: int = DEFAULT_COMMAND_TIMEOUT_SEC
    estimated_cost_per_iteration: float = DEFAULT_ESTIMATED_COST_PER_ITERATION
    allow_overwrite: bool = False
    allowed_installs: list[str] | None = None


@dataclass(slots=True)
class ResolvedHarnessConfig:
    limits: RuntimeLimits
    openai_settings: OpenAISettings | None
    dry_run: bool


def load_openai_settings() -> OpenAISettings | None:
    """Load OpenAI settings from environment variables."""
    return resolve_harness_config({}).openai_settings


def build_limits(raw_contract: dict[str, object]) -> RuntimeLimits:
    """Create normalized runtime limits from contract data."""
    return resolve_harness_config(raw_contract).limits


def resolve_harness_config(
    raw_contract: dict[str, object],
    *,
    cli: HarnessOverrides | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedHarnessConfig:
    """Resolve harness settings with precedence: CLI > contract > env > defaults."""
    env = dict(environ if environ is not None else os.environ)
    overrides = cli or HarnessOverrides()

    max_iterations = _resolve_int(
        cli=overrides.max_iterations,
        contract_val=raw_contract.get("max_iterations"),
        env_val=env.get(AGENT_MAX_ITERATIONS_ENV),
        default=DEFAULT_MAX_ITERATIONS,
    )
    command_timeout_sec = _resolve_int(
        cli=overrides.command_timeout_sec,
        contract_val=raw_contract.get("command_timeout_sec"),
        env_val=env.get(AGENT_COMMAND_TIMEOUT_SEC_ENV),
        default=DEFAULT_COMMAND_TIMEOUT_SEC,
    )
    cost_limit = _resolve_float(
        cli=overrides.cost_limit,
        contract_val=raw_contract.get("cost_limit"),
        env_val=env.get(AGENT_COST_LIMIT_ENV),
        default=DEFAULT_COST_LIMIT,
    )
    dry_run = _resolve_bool(
        cli=overrides.dry_run,
        contract_val=raw_contract.get("dry_run"),
        env_val=env.get(AGENT_DRY_RUN_ENV),
        default=DEFAULT_DRY_RUN,
    )
    model = _resolve_str(
        cli=overrides.model,
        contract_val=None,
        env_val=env.get(OPENAI_MODEL_ENV),
        default=DEFAULT_OPENAI_MODEL,
    )

    limits = RuntimeLimits(
        max_iterations=max_iterations,
        cost_limit=cost_limit,
        failure_limit=_as_int(raw_contract.get("failure_limit"), DEFAULT_FAILURE_LIMIT),
        command_timeout_sec=command_timeout_sec,
        estimated_cost_per_iteration=_as_float(
            raw_contract.get("estimated_cost_per_iteration"),
            DEFAULT_ESTIMATED_COST_PER_ITERATION,
        ),
        allow_overwrite=bool(raw_contract.get("allow_overwrite", False)),
        allowed_installs=list(raw_contract.get("allowed_installs", []) or []),
    )

    api_key = env.get(OPENAI_API_KEY_ENV, "").strip()
    openai_settings = (
        OpenAISettings(api_key=api_key, model=model or DEFAULT_OPENAI_MODEL)
        if api_key
        else None
    )

    return ResolvedHarnessConfig(
        limits=limits,
        openai_settings=openai_settings,
        dry_run=dry_run,
    )


def _resolve_str(
    *,
    cli: str | None,
    contract_val: object,
    env_val: str | None,
    default: str,
) -> str:
    if cli is not None and str(cli).strip():
        return str(cli).strip()
    if contract_val is not None and str(contract_val).strip():
        return str(contract_val).strip()
    if env_val is not None and env_val.strip():
        return env_val.strip()
    return default


def _resolve_int(
    *,
    cli: int | None,
    contract_val: object,
    env_val: str | None,
    default: int,
) -> int:
    if cli is not None:
        return cli
    if contract_val is not None:
        return _as_int(contract_val, default)
    if env_val is not None and env_val.strip():
        return _as_int(env_val.strip(), default)
    return default


def _resolve_float(
    *,
    cli: float | None,
    contract_val: object,
    env_val: str | None,
    default: float,
) -> float:
    if cli is not None:
        return cli
    if contract_val is not None:
        return _as_float(contract_val, default)
    if env_val is not None and env_val.strip():
        return _as_float(env_val.strip(), default)
    return default


def _resolve_bool(
    *,
    cli: bool | None,
    contract_val: object,
    env_val: str | None,
    default: bool,
) -> bool:
    if cli is not None:
        return cli
    if contract_val is not None:
        return _as_bool(contract_val, default)
    if env_val is not None and env_val.strip():
        return _parse_env_bool(env_val.strip(), default)
    return default


def _parse_env_bool(value: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _parse_env_bool(value, default)
    return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
