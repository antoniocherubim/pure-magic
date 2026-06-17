"""Testes smoke do esqueleto MVP."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_loop import run_loop
from agent_loop import agents, config, models, prompts, tools
from agent_loop.prompts import parse_contract_md, validate_contract, validate_executor_response
from agent_loop.tools import SecurityError, safe_git_status, validate_command

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_EXAMPLE = PROJECT_ROOT / "agent_contract.example.md"


def test_imports() -> None:
    assert agents.PlannerAgent is not None
    assert config.DEFAULT_MAX_ITERATIONS == 5
    assert models.Context is not None
    assert prompts.PLANNER_PROMPT
    assert tools.validate_command is not None
    assert run_loop is not None


def test_dangerous_command_blocked() -> None:
    with pytest.raises(SecurityError):
        validate_command("sudo rm -rf /")


def test_contract_example_has_required_fields() -> None:
    text = CONTRACT_EXAMPLE.read_text(encoding="utf-8")
    contract = parse_contract_md(text)
    errors = validate_contract(contract)
    assert errors == []
    assert contract["objective"]
    assert isinstance(contract["checks"], list)
    assert isinstance(contract["constraints"], list)
    assert contract["max_iterations"] == 3


def test_executor_schema_valid() -> None:
    valid = {
        "operations": [{"type": "write_file", "path": "foo.py", "content": "pass"}],
        "commands": ["pytest"],
        "summary": "done",
    }
    assert validate_executor_response(valid) == []


def test_executor_schema_invalid() -> None:
    invalid = {"operations": [], "commands": "not-a-list"}
    errors = validate_executor_response(invalid)
    assert any("summary" in e for e in errors)
    assert any("commands" in e for e in errors)


def test_dry_run_loop(tmp_repo: Path) -> None:
    clean_before, _ = safe_git_status(tmp_repo)
    assert clean_before

    code = run_loop(tmp_repo, dry_run=True)
    assert code == 0

    log_path = tmp_repo / "work" / "agent_log.md"
    assert log_path.exists()
    assert "Iteration 1" in log_path.read_text(encoding="utf-8")

    clean_after, _ = safe_git_status(tmp_repo)
    assert clean_after


def test_git_push_blocked() -> None:
    with pytest.raises(SecurityError):
        validate_command("git push origin main")
