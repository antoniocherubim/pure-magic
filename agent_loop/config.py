"""Central configuration and safety defaults."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_BRANCH_PREFIX = "agent/"
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_COST_LIMIT = 5.0
DEFAULT_FAILURE_LIMIT = 3
DEFAULT_COMMAND_TIMEOUT_SEC = 120
DEFAULT_ESTIMATED_COST_PER_ITERATION = 0.02
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
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


def load_openai_settings() -> OpenAISettings | None:
    """Load OpenAI settings from environment variables."""
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        return None
    model = os.environ.get(OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL).strip()
    return OpenAISettings(api_key=api_key, model=model or DEFAULT_OPENAI_MODEL)


@dataclass(slots=True)
class RuntimeLimits:
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    cost_limit: float = DEFAULT_COST_LIMIT
    failure_limit: int = DEFAULT_FAILURE_LIMIT
    command_timeout_sec: int = DEFAULT_COMMAND_TIMEOUT_SEC
    estimated_cost_per_iteration: float = DEFAULT_ESTIMATED_COST_PER_ITERATION
    allow_overwrite: bool = False
    allowed_installs: list[str] | None = None


def build_limits(raw_contract: dict[str, object]) -> RuntimeLimits:
    """Create normalized runtime limits from contract data."""
    return RuntimeLimits(
        max_iterations=_as_int(raw_contract.get("max_iterations"), DEFAULT_MAX_ITERATIONS),
        cost_limit=_as_float(raw_contract.get("cost_limit"), DEFAULT_COST_LIMIT),
        failure_limit=_as_int(raw_contract.get("failure_limit"), DEFAULT_FAILURE_LIMIT),
        command_timeout_sec=_as_int(
            raw_contract.get("command_timeout_sec"),
            DEFAULT_COMMAND_TIMEOUT_SEC,
        ),
        estimated_cost_per_iteration=_as_float(
            raw_contract.get("estimated_cost_per_iteration"),
            DEFAULT_ESTIMATED_COST_PER_ITERATION,
        ),
        allow_overwrite=bool(raw_contract.get("allow_overwrite", False)),
        allowed_installs=list(raw_contract.get("allowed_installs", []) or []),
    )


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
