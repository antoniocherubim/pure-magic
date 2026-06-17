"""Prompt builders and schema validation."""

from __future__ import annotations

import json
import re
from typing import Any

from agent_loop.config import REQUIRED_CONTRACT_FIELDS, SUPPORTED_OPERATION_TYPES
from agent_loop.models import ReviewerDecision

PLANNER_PROMPT = """You are the Planner agent for a local autonomous coding loop.

Read the contract and return the smallest safe implementation plan.

Contract:
{contract}

Return JSON only with this shape:
{{
  "summary": "short summary",
  "tasks": ["atomic task 1", "atomic task 2"]
}}
"""

EXECUTOR_PROMPT = """You are the Executor agent.

Follow the plan exactly, respect all constraints, and return JSON only.

Objective:
{objective}

Plan:
{plan}

Constraints:
{constraints}

Return JSON with this shape:
{{
  "operations": [
    {{
      "type": "write_file",
      "path": "relative/path.py",
      "content": "full file content"
    }}
  ],
  "commands": ["pytest"],
  "summary": "what changed"
}}

Supported operation types: write_file only.
"""

REVIEWER_PROMPT = """You are the Reviewer agent.

Objective:
{objective}

Planner summary:
{planner_summary}

Executor summary:
{executor_summary}

Diff:
{diff}

Command results:
{command_results}

Decide one of CONTINUE, REVISE, OBJECTIVE_COMPLETE.
Return JSON only with this shape:
{{
  "decision": "CONTINUE",
  "reason": "brief reason"
}}
"""


def format_planner_prompt(contract: dict[str, Any]) -> str:
    return PLANNER_PROMPT.format(contract=json.dumps(contract, indent=2, ensure_ascii=False))


def format_executor_prompt(
    objective: str,
    plan: dict[str, Any],
    constraints: list[str],
) -> str:
    return EXECUTOR_PROMPT.format(
        objective=objective,
        plan=json.dumps(plan, indent=2, ensure_ascii=False),
        constraints=json.dumps(constraints, indent=2, ensure_ascii=False),
    )


def format_reviewer_prompt(
    objective: str,
    planner_summary: str,
    executor_summary: str,
    diff: str,
    command_results: list[dict[str, Any]],
) -> str:
    return REVIEWER_PROMPT.format(
        objective=objective,
        planner_summary=planner_summary or "(none)",
        executor_summary=executor_summary or "(none)",
        diff=diff or "(empty)",
        command_results=json.dumps(command_results, indent=2, ensure_ascii=False),
    )


def parse_contract_md(text: str) -> dict[str, Any]:
    """Parse simple YAML-like frontmatter or markdown sections."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end_index = _find_frontmatter_end(lines)
        if end_index is not None:
            parsed = _parse_frontmatter(lines[1:end_index])
            if parsed:
                return parsed
    return _parse_markdown_sections(lines)


def validate_contract(raw_contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field_name in REQUIRED_CONTRACT_FIELDS:
        if field_name not in raw_contract:
            errors.append(f"Missing required field: {field_name}")

    checks = raw_contract.get("checks")
    if checks is not None and not isinstance(checks, list):
        errors.append("checks must be a list")

    constraints = raw_contract.get("constraints")
    if constraints is not None and not isinstance(constraints, list):
        errors.append("constraints must be a list")

    try:
        int(raw_contract.get("max_iterations"))
    except (TypeError, ValueError):
        errors.append("max_iterations must be an integer")

    return errors


def validate_executor_response(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["Executor payload must be a JSON object"]

    operations = payload.get("operations")
    if not isinstance(operations, list):
        errors.append("operations must be a list")
    else:
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                errors.append(f"operations[{index}] must be an object")
                continue
            operation_type = operation.get("type")
            if operation_type not in SUPPORTED_OPERATION_TYPES:
                errors.append(
                    f"operations[{index}].type must be one of {SUPPORTED_OPERATION_TYPES}"
                )
            if not isinstance(operation.get("path"), str) or not operation["path"].strip():
                errors.append(f"operations[{index}].path must be a non-empty string")
            if operation_type == "write_file" and not isinstance(
                operation.get("content"), str
            ):
                errors.append(
                    f"operations[{index}].content must be a string for write_file"
                )

    commands = payload.get("commands")
    if not isinstance(commands, list):
        errors.append("commands must be a list")
    else:
        for index, command in enumerate(commands):
            if not isinstance(command, str) or not command.strip():
                errors.append(f"commands[{index}] must be a non-empty string")

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary must be a non-empty string")

    return errors


def parse_reviewer_decision(text: str) -> ReviewerDecision:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict) and "decision" in data:
        try:
            return ReviewerDecision(str(data["decision"]).upper())
        except ValueError:
            return ReviewerDecision.CONTINUE

    upper_text = text.upper()
    for decision in ReviewerDecision:
        if decision.value in upper_text:
            return decision
    return ReviewerDecision.CONTINUE


def _find_frontmatter_end(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index
    return None


def _parse_frontmatter(lines: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        list_item = re.match(r"^\s*-\s+(.*)$", line)
        if list_item and current_list_key:
            parsed.setdefault(current_list_key, []).append(_coerce_scalar(list_item.group(1)))
            continue

        key_match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not key_match:
            continue

        key, value = key_match.groups()
        if value == "":
            parsed[key] = []
            current_list_key = key
            continue

        parsed[key] = _coerce_scalar(value)
        current_list_key = None

    return parsed


def _parse_markdown_sections(lines: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    current_key: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if current_key is None:
            return
        cleaned = [item for item in buffer if item.strip()]
        if not cleaned:
            parsed[current_key] = ""
        elif all(item.lstrip().startswith("- ") for item in cleaned):
            parsed[current_key] = [item.lstrip()[2:].strip() for item in cleaned]
        else:
            parsed[current_key] = _coerce_scalar("\n".join(cleaned).strip())
        buffer = []

    for line in lines:
        header = re.match(r"^##\s+([A-Za-z0-9_]+)\s*$", line.strip())
        if header:
            flush()
            current_key = header.group(1)
            continue
        if current_key is not None:
            buffer.append(line)

    flush()
    return parsed


def _coerce_scalar(value: str) -> Any:
    text = value.strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text == "[]":
        return []
    return text
