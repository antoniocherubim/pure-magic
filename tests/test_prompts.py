from __future__ import annotations

import pytest

from agent_loop.models import PlannerResponseError
from agent_loop.prompts import (
    parse_contract_md,
    parse_planner_response,
    validate_contract,
    validate_executor_response,
    validate_planner_response,
)


def test_parse_contract_frontmatter() -> None:
    text = """---
objective: Build a tiny feature
checks:
  - pytest
constraints:
  - Never use sudo
max_iterations: 2
task_name: tiny-feature
allow_overwrite: false
---
"""
    parsed = parse_contract_md(text)
    assert parsed["objective"] == "Build a tiny feature"
    assert parsed["checks"] == ["pytest"]
    assert parsed["constraints"] == ["Never use sudo"]
    assert parsed["max_iterations"] == 2
    assert parsed["task_name"] == "tiny-feature"


def test_validate_contract_reports_missing_fields() -> None:
    errors = validate_contract({"objective": "x"})
    assert "Missing required field: checks" in errors
    assert "Missing required field: task_name" in errors


def test_validate_executor_response_accepts_write_file_only() -> None:
    payload = {
        "operations": [
            {
                "type": "write_file",
                "path": "src/app.py",
                "content": "print('ok')\n",
            }
        ],
        "commands": ["pytest"],
        "summary": "Created app.py",
    }
    assert validate_executor_response(payload) == []


def test_validate_executor_response_rejects_modify_file_for_now() -> None:
    payload = {
        "operations": [{"type": "modify_file", "path": "src/app.py"}],
        "commands": ["pytest"],
        "summary": "Try patching",
    }
    errors = validate_executor_response(payload)
    assert any("operations[0].type" in error for error in errors)


def test_validate_planner_response_accepts_valid_payload() -> None:
    payload = {
        "summary": "Create one file",
        "tasks": ["Write generated.txt", "Run pytest"],
    }
    assert validate_planner_response(payload) == []


def test_validate_planner_response_reports_empty_summary() -> None:
    errors = validate_planner_response({"summary": "  ", "tasks": ["Do work"]})
    assert "summary must be a non-empty string" in errors


def test_validate_planner_response_reports_empty_tasks() -> None:
    errors = validate_planner_response({"summary": "Plan", "tasks": []})
    assert "tasks must contain at least one item" in errors


def test_validate_planner_response_reports_invalid_task_items() -> None:
    errors = validate_planner_response({"summary": "Plan", "tasks": ["ok", ""]})
    assert "tasks[1] must be a non-empty string" in errors


def test_parse_planner_response_accepts_raw_json() -> None:
    parsed = parse_planner_response(
        '{"summary": "Plan", "tasks": ["Write file"]}'
    )
    assert parsed == {"summary": "Plan", "tasks": ["Write file"]}


def test_parse_planner_response_accepts_markdown_fence() -> None:
    parsed = parse_planner_response(
        """Here is the plan:
```json
{"summary": "Plan", "tasks": ["Write file"]}
```
"""
    )
    assert parsed == {"summary": "Plan", "tasks": ["Write file"]}


def test_parse_planner_response_raises_on_invalid_json() -> None:
    with pytest.raises(PlannerResponseError, match="not valid JSON"):
        parse_planner_response("not json")
