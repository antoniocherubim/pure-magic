"""Configurações, limites e regras de segurança centralizadas."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_MAX_ITERATIONS = 5
DEFAULT_COST_LIMIT = float("inf")
DEFAULT_BRANCH_PREFIX = "agent/"
COMMAND_TIMEOUT_SEC = 120
ESTIMATED_COST_PER_ITERATION = 0.01

REQUIRED_CONTRACT_FIELDS = ("objective", "checks", "constraints", "max_iterations")

PROTECTED_PATHS = (".env", ".git/")

DANGEROUS_COMMAND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    re.compile(r"\bgit\s+checkout\s+main\b", re.IGNORECASE),
    re.compile(r"\bgit\s+checkout\s+master\b", re.IGNORECASE),
    re.compile(r"\bgit\s+switch\s+main\b", re.IGNORECASE),
    re.compile(r"\bgit\s+switch\s+master\b", re.IGNORECASE),
    re.compile(r"\.env\b"),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r">\s*/dev/", re.IGNORECASE),
]

SUPPORTED_OPERATION_TYPES = ("write_file",)


def load_limits(contract: dict[str, Any]) -> dict[str, Any]:
    """Mescla limites do contrato com defaults."""
    max_iterations = contract.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    cost_limit = contract.get("cost_limit", DEFAULT_COST_LIMIT)

    try:
        max_iterations = int(max_iterations)
    except (TypeError, ValueError):
        max_iterations = DEFAULT_MAX_ITERATIONS

    try:
        cost_limit = float(cost_limit)
    except (TypeError, ValueError):
        cost_limit = DEFAULT_COST_LIMIT

    return {
        "max_iterations": max_iterations,
        "cost_limit": cost_limit,
        "allowed_installs": contract.get("allowed_installs", []),
        "allow_overwrite": bool(contract.get("allow_overwrite", False)),
    }
