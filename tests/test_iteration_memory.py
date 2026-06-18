from __future__ import annotations

import json
from subprocess import TimeoutExpired

import pytest

from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.config import build_limits
from agent_loop.models import (
    CheckStatus,
    CommandResult,
    Contract,
    ExecutionContext,
    PreviousIterationSummary,
    ReviewerDecision,
    build_check_statuses,
)
from agent_loop.prompts import format_planner_prompt, format_previous_iteration
from agent_loop.runner import run_loop
from agent_loop import tools as tools_module


def _write_contract(temp_repo, task_name: str, *, max_iterations: int = 2) -> None:
    (temp_repo / "agent_contract.md").write_text(
        f"""---
objective: Iteration memory test
checks:
  - python3 -c pass
constraints:
  - Never run sudo
max_iterations: {max_iterations}
failure_limit: 10
task_name: {task_name}
allow_overwrite: false
---
""",
        encoding="utf-8",
    )


def test_format_previous_iteration_none_on_first_iteration() -> None:
    assert format_previous_iteration(None) == "(none - first iteration)"


def test_format_previous_iteration_serializes_structured_summary() -> None:
    summary = PreviousIterationSummary(
        iteration=1,
        status="completed",
        artifact_dir="iterations/1",
        planner_summary="First plan",
        executor_summary="First exec",
        reviewer_decision="REVISE",
        checks=[CheckStatus(command="python3 -c pass", status="passed", returncode=0)],
    )

    payload = json.loads(format_previous_iteration(summary))

    assert payload["iteration"] == 1
    assert payload["artifact_dir"] == "iterations/1"
    assert payload["reviewer_decision"] == "REVISE"
    assert payload["checks"] == [
        {"command": "python3 -c pass", "status": "passed", "returncode": 0},
    ]


def test_build_check_statuses_marks_error_and_not_run() -> None:
    statuses = build_check_statuses(
        results=[
            CommandResult(command="pytest", returncode=0, stdout="", stderr=""),
        ],
        planned_commands=["pytest", "python -m compileall ."],
        failed_command="python -m compileall .",
    )

    assert [item.to_dict() for item in statuses] == [
        {"command": "pytest", "status": "passed", "returncode": 0},
        {"command": "python -m compileall .", "status": "error", "returncode": None},
    ]


def test_planner_prompt_includes_previous_iteration_summary(temp_repo) -> None:
    contract = Contract(
        objective="Build feature",
        checks=["pytest"],
        constraints=["Never use sudo"],
        max_iterations=2,
        task_name="prompt-memory",
    )
    context = ExecutionContext(
        repo_path=temp_repo,
        work_dir=temp_repo / "work",
        branch="agent/prompt-memory",
        contract=contract,
        limits=build_limits(contract.to_dict()),
        dry_run=True,
        iteration=2,
        previous_iteration=PreviousIterationSummary(
            iteration=1,
            status="completed",
            artifact_dir="iterations/1",
            planner_summary="First plan",
            executor_summary="First exec",
            reviewer_decision="REVISE",
            checks=[CheckStatus(command="python3 -c pass", status="passed", returncode=0)],
        ),
    )

    prompt = PlannerAgent().build_prompt(context)

    assert "Previous iteration (most recent only):" in prompt
    assert "iterations/1" in prompt
    assert "First plan" in prompt
    assert "First exec" in prompt
    assert '"reviewer_decision": "REVISE"' in prompt
    assert '"status": "passed"' in prompt


def test_second_iteration_receives_previous_context_after_revise(temp_repo) -> None:
    _write_contract(temp_repo, "memory-revise")

    reviewer_calls = {"count": 0}

    def reviewer_responder(context, payload):
        reviewer_calls["count"] += 1
        if reviewer_calls["count"] == 1:
            return {
                "decision": ReviewerDecision.REVISE.value,
                "reason": "Needs another pass.",
            }
        return {
            "decision": ReviewerDecision.OBJECTIVE_COMPLETE.value,
            "reason": "Done.",
        }

    planner = PlannerAgent(
        responder=lambda context: {
            "summary": f"Plan for iteration {context.iteration}",
            "tasks": ["Implement step"],
        }
    )
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [],
            "commands": ["python3 -c pass"],
            "summary": f"Exec summary for iteration {request.iteration}",
        }
    )
    reviewer = ReviewerAgent(responder=reviewer_responder)

    exit_code = run_loop(
        repo_path=temp_repo,
        dry_run=False,
        planner=planner,
        executor=executor,
        reviewer=reviewer,
    )

    assert exit_code == 0
    assert reviewer_calls["count"] == 2

    planner_prompt = (
        temp_repo / "work" / "iterations" / "2" / "planner_prompt.txt"
    ).read_text(encoding="utf-8")
    executor_request = json.loads(
        (temp_repo / "work" / "iterations" / "2" / "executor_request.json").read_text(
            encoding="utf-8"
        )
    )

    assert "iterations/1" in planner_prompt
    assert "Plan for iteration 1" in planner_prompt
    assert "Exec summary for iteration 1" in planner_prompt
    assert '"reviewer_decision": "REVISE"' in planner_prompt
    assert '"status": "passed"' in planner_prompt
    assert "Correction strategy:" in planner_prompt
    assert "Reviewer decision: REVISE" in planner_prompt
    assert "do not blindly repeat the previous plan" in planner_prompt

    previous = executor_request["previous_iteration"]
    assert previous["artifact_dir"] == "iterations/1"
    assert previous["planner_summary"] == "Plan for iteration 1"
    assert previous["executor_summary"] == "Exec summary for iteration 1"
    assert previous["reviewer_decision"] == "REVISE"
    assert previous["checks"] == [
        {"command": "python3 -c pass", "status": "passed", "returncode": 0},
    ]
    assert "Previous iteration (most recent only):" in executor_request["executor_prompt"]
    assert "Reviewer decision: REVISE" in executor_request["executor_prompt"]
    assert "Keep commands within the contract allowlist" in executor_request["executor_prompt"]


def test_second_iteration_receives_failed_checks_context(temp_repo, monkeypatch) -> None:
    _write_contract(temp_repo, "memory-checks-fail")

    checks_calls = {"count": 0}
    real_run_command = tools_module.run_command

    def flaky_run_command(command, *, cwd, dry_run, timeout_sec):
        checks_calls["count"] += 1
        if checks_calls["count"] == 1:
            raise TimeoutExpired(cmd=command, timeout=timeout_sec)
        return real_run_command(
            command,
            cwd=cwd,
            dry_run=dry_run,
            timeout_sec=timeout_sec,
        )

    monkeypatch.setattr("agent_loop.runner.run_command", flaky_run_command)

    planner = PlannerAgent(
        responder=lambda context: {
            "summary": f"Plan for iteration {context.iteration}",
            "tasks": ["Implement step"],
        }
    )
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [],
            "commands": ["python3 -c pass"],
            "summary": f"Exec summary for iteration {request.iteration}",
        }
    )
    reviewer = ReviewerAgent(
        responder=lambda context, payload: {
            "decision": ReviewerDecision.OBJECTIVE_COMPLETE.value,
            "reason": "Checks passed.",
        }
    )

    exit_code = run_loop(
        repo_path=temp_repo,
        dry_run=False,
        planner=planner,
        executor=executor,
        reviewer=reviewer,
    )

    assert exit_code == 0
    assert checks_calls["count"] == 2

    planner_prompt = (
        temp_repo / "work" / "iterations" / "2" / "planner_prompt.txt"
    ).read_text(encoding="utf-8")
    executor_request = json.loads(
        (temp_repo / "work" / "iterations" / "2" / "executor_request.json").read_text(
            encoding="utf-8"
        )
    )

    assert '"failed_stage": "checks"' in planner_prompt
    assert "Correction strategy:" in planner_prompt
    assert "Failed stage: checks" in planner_prompt
    assert "failed or timed-out check" in planner_prompt
    assert "timed out" in planner_prompt.lower()
    assert "Exec summary for iteration 1" in planner_prompt
    assert '"status": "error"' in planner_prompt

    previous = executor_request["previous_iteration"]
    assert previous["status"] == "failed"
    assert previous["failed_stage"] == "checks"
    assert "Correction strategy:" in executor_request["executor_prompt"]
    assert "Failed stage: checks" in executor_request["executor_prompt"]
    assert "contract allowlist" in executor_request["executor_prompt"]
    assert "same commands from the previous iteration" not in executor_request["executor_prompt"]
    assert previous["checks"] == [
        {"command": "python3 -c pass", "status": "error", "returncode": None},
    ]
