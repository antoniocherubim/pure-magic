"""Templates de prompt e parsing/validação de respostas dos agentes."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from agent_loop.config import REQUIRED_CONTRACT_FIELDS, SUPPORTED_OPERATION_TYPES
from agent_loop.models import ReviewerDecision

PLANNER_PROMPT = """You are the Planner agent.

Contract:
{contract}

Transform the contract into a minimal plan with atomic tasks.
Return a JSON object with keys: summary (string), tasks (list of strings).
"""

EXECUTOR_PROMPT = """You are the Executor agent.

Plan:
{plan}

Contract constraints:
{constraints}

Return ONLY valid JSON with this schema:
{{
  "operations": [{{"type": "write_file", "path": "...", "content": "..."}}],
  "commands": ["pytest"],
  "summary": "..."
}}

Supported operation types: write_file only.
"""

REVIEWER_PROMPT = """You are the Reviewer agent.

Objective:
{objective}

Diff:
{diff}

Test output:
{test_results}

Log summary:
{log_summary}

Decide one of: CONTINUE, OBJECTIVE_COMPLETE, REVISE.
Return JSON: {{"decision": "...", "reason": "..."}}
"""

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_contract_md(text: str) -> dict[str, Any]:
    """Extrai contrato de frontmatter YAML ou seções markdown."""
    match = _FRONTMATTER_RE.match(text)
    if match:
        data = yaml.safe_load(match.group(1))
        if isinstance(data, dict):
            return data

    contract: dict[str, Any] = {}
    current_key: str | None = None
    list_buffer: list[str] = []

    for line in text.splitlines():
        header = re.match(r"^##\s+(\w+)\s*$", line)
        if header:
            if current_key and list_buffer:
                contract[current_key] = list_buffer
                list_buffer = []
            current_key = header.group(1)
            continue

        if current_key is None:
            continue

        item = re.match(r"^-\s+(.+)$", line.strip())
        if item:
            list_buffer.append(item.group(1).strip())
            continue

        if line.strip() and not line.strip().startswith("#"):
            value = line.strip()
            if current_key in ("max_iterations",):
                try:
                    contract[current_key] = int(value)
                except ValueError:
                    contract[current_key] = value
            elif current_key in ("allow_overwrite",):
                contract[current_key] = value.lower() in ("true", "yes", "1")
            else:
                contract[current_key] = value

    if current_key and list_buffer:
        contract[current_key] = list_buffer

    return contract


def validate_contract(contract: dict[str, Any]) -> list[str]:
    """Valida campos obrigatórios do contrato."""
    errors: list[str] = []
    for field_name in REQUIRED_CONTRACT_FIELDS:
        if field_name not in contract:
            errors.append(f"Missing required field: {field_name}")
    if "max_iterations" in contract:
        try:
            int(contract["max_iterations"])
        except (TypeError, ValueError):
            errors.append("max_iterations must be an integer")
    return errors


def validate_executor_response(data: dict[str, Any]) -> list[str]:
    """
    Valida schema do Executor.

    Schema esperado:
    {
      "operations": [{"type": "write_file", "path": "...", "content": "..."}],
      "commands": ["pytest"],
      "summary": "..."
    }
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["Response must be a JSON object"]

    if "operations" not in data:
        errors.append("Missing required field: operations")
    elif not isinstance(data["operations"], list):
        errors.append("operations must be a list")
    else:
        for i, op in enumerate(data["operations"]):
            if not isinstance(op, dict):
                errors.append(f"operations[{i}] must be an object")
                continue
            op_type = op.get("type")
            if op_type not in SUPPORTED_OPERATION_TYPES:
                errors.append(
                    f"operations[{i}].type must be one of {SUPPORTED_OPERATION_TYPES}"
                )
            if not op.get("path"):
                errors.append(f"operations[{i}].path is required")
            if op_type == "write_file" and "content" not in op:
                errors.append(f"operations[{i}].content is required for write_file")

    if "commands" not in data:
        errors.append("Missing required field: commands")
    elif not isinstance(data["commands"], list):
        errors.append("commands must be a list")
    else:
        for i, cmd in enumerate(data["commands"]):
            if not isinstance(cmd, str) or not cmd.strip():
                errors.append(f"commands[{i}] must be a non-empty string")

    if "summary" not in data:
        errors.append("Missing required field: summary")
    elif not isinstance(data["summary"], str):
        errors.append("summary must be a string")

    return errors


def parse_reviewer_decision(text: str) -> ReviewerDecision:
    """Extrai decisão do Reviewer de texto ou JSON."""
    stripped = text.strip()

    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and "decision" in data:
            return ReviewerDecision(data["decision"].upper())
    except json.JSONDecodeError:
        pass

    upper = stripped.upper()
    for decision in ReviewerDecision:
        if decision.value in upper:
            return decision

    return ReviewerDecision.CONTINUE


def format_planner_prompt(contract: dict[str, Any]) -> str:
    return PLANNER_PROMPT.format(contract=json.dumps(contract, indent=2))


def format_executor_prompt(plan: dict[str, Any], constraints: Any) -> str:
    return EXECUTOR_PROMPT.format(
        plan=json.dumps(plan, indent=2),
        constraints=json.dumps(constraints, indent=2),
    )


def format_reviewer_prompt(
    objective: str,
    diff: str,
    test_results: str,
    log_summary: str,
) -> str:
    return REVIEWER_PROMPT.format(
        objective=objective,
        diff=diff or "(empty)",
        test_results=test_results or "(none)",
        log_summary=log_summary or "(none)",
    )
