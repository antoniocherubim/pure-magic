from __future__ import annotations

import json

import pytest

from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.models import (
    FileOperation,
    PreviousIterationSummary,
    RepeatSignal,
    ReviewerDecision,
    detect_repeat_attempt,
    write_file_paths_from_operations,
)
from agent_loop.prompts import (
    format_executor_prompt,
    format_planner_prompt,
    format_repeat_warning,
)
from agent_loop.runner import run_loop


def _previous_summary(**overrides) -> PreviousIterationSummary:
    defaults = {
        "iteration": 1,
        "status": "completed",
        "artifact_dir": "iterations/1",
        "planner_summary": "Same plan",
        "executor_summary": "Same exec",
        "commands": ["python3 -c pass"],
        "write_file_paths": ["src/module.py"],
    }
    defaults.update(overrides)
    return PreviousIterationSummary(**defaults)


def test_detect_repeat_attempt_detects_all_matches() -> None:
    previous = _previous_summary()
    signal = detect_repeat_attempt(
        planner_summary="Same plan",
        executor_summary="Same exec",
        commands=["python3 -c pass"],
        write_file_paths=["src/module.py"],
        previous=previous,
    )

    assert signal.detected is True
    assert signal.matches == [
        "planner_summary",
        "executor_summary",
        "commands",
        "write_file_paths",
    ]
    assert signal.compared_with_iteration == 1


def test_detect_repeat_attempt_no_repeat_when_values_differ() -> None:
    previous = _previous_summary()
    signal = detect_repeat_attempt(
        planner_summary="Different plan",
        executor_summary="Different exec",
        commands=["pytest"],
        write_file_paths=["src/other.py"],
        previous=previous,
    )

    assert signal.detected is False
    assert signal.matches == []


def test_detect_repeat_attempt_partial_match_commands_only() -> None:
    previous = _previous_summary()
    signal = detect_repeat_attempt(
        planner_summary="Different plan",
        executor_summary="Different exec",
        commands=["python3 -c pass"],
        write_file_paths=["src/other.py"],
        previous=previous,
    )

    assert signal.detected is True
    assert signal.matches == ["commands"]


def test_detect_repeat_attempt_without_previous_is_neutral() -> None:
    signal = detect_repeat_attempt(
        planner_summary="Any plan",
        executor_summary="Any exec",
        commands=["python3 -c pass"],
        write_file_paths=["src/module.py"],
        previous=None,
    )

    assert signal.detected is False
    assert signal.matches == []
    assert signal.compared_with_iteration is None


def test_write_file_paths_from_operations_returns_sorted_paths() -> None:
    paths = write_file_paths_from_operations(
        [
            FileOperation(type="write_file", path="b.py", content=""),
            FileOperation(type="write_file", path="a.py", content=""),
            FileOperation(type="other", path="ignored.py", content=""),
        ]
    )

    assert paths == ["a.py", "b.py"]


def test_format_repeat_warning_neutral_when_not_detected() -> None:
    assert format_repeat_warning(None) == "(none - no repeated attempt detected)"
    assert (
        format_repeat_warning(RepeatSignal(detected=False, matches=[]))
        == "(none - no repeated attempt detected)"
    )


def test_format_repeat_warning_lists_matches() -> None:
    warning = format_repeat_warning(
        RepeatSignal(
            detected=True,
            matches=["planner_summary", "commands"],
            compared_with_iteration=2,
        )
    )

    assert "Probable repeated attempt detected (compared with iteration 2):" in warning
    assert "- planner_summary" in warning
    assert "- commands" in warning
    assert "do not repeat the same approach blindly" in warning


def test_planner_prompt_includes_repeat_warning() -> None:
    contract = {
        "objective": "Build feature",
        "checks": ["python3 -c pass"],
        "constraints": ["Never use sudo"],
        "max_iterations": 3,
        "task_name": "repeat-prompt",
    }
    signal = RepeatSignal(
        detected=True,
        matches=["executor_summary"],
        compared_with_iteration=2,
    )

    prompt = format_planner_prompt(contract, repeat_signal=signal)

    assert "Repeat warning:" in prompt
    assert "Probable repeated attempt detected" in prompt
    assert "- executor_summary" in prompt


def test_executor_prompt_includes_repeat_warning() -> None:
    signal = RepeatSignal(
        detected=True,
        matches=["write_file_paths"],
        compared_with_iteration=2,
    )

    prompt = format_executor_prompt(
        objective="Build feature",
        plan={"summary": "Plan", "tasks": ["Work"]},
        constraints=["Never use sudo"],
        repeat_signal=signal,
    )

    assert "Repeat warning:" in prompt
    assert "- write_file_paths" in prompt


def test_loop_saves_repeat_signal_and_warns_on_next_iteration(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Repeat detection integration
checks:
  - python3 -c pass
constraints:
  - Never run sudo
max_iterations: 3
failure_limit: 10
task_name: repeat-integration
allow_overwrite: true
---
""",
        encoding="utf-8",
    )

    reviewer_calls = {"count": 0}

    def reviewer_responder(context, payload):
        reviewer_calls["count"] += 1
        if reviewer_calls["count"] < 3:
            return {
                "decision": ReviewerDecision.REVISE.value,
                "reason": "Try again.",
            }
        return {
            "decision": ReviewerDecision.OBJECTIVE_COMPLETE.value,
            "reason": "Done.",
        }

    planner = PlannerAgent(
        responder=lambda context: {
            "summary": "Repeated plan",
            "tasks": ["Implement step"],
        }
    )
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [
                {
                    "type": "write_file",
                    "path": "src/module.py",
                    "content": "x = 1\n",
                }
            ],
            "commands": ["python3 -c pass"],
            "summary": "Repeated exec",
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
    assert reviewer_calls["count"] == 3

    repeat_signal = json.loads(
        (temp_repo / "work" / "iterations" / "2" / "repeat_signal.json").read_text(
            encoding="utf-8"
        )
    )
    assert repeat_signal["detected"] is True
    assert "planner_summary" in repeat_signal["matches"]
    assert "executor_summary" in repeat_signal["matches"]
    assert "commands" in repeat_signal["matches"]
    assert "write_file_paths" in repeat_signal["matches"]

    planner_prompt = (
        temp_repo / "work" / "iterations" / "3" / "planner_prompt.txt"
    ).read_text(encoding="utf-8")
    executor_request = json.loads(
        (temp_repo / "work" / "iterations" / "3" / "executor_request.json").read_text(
            encoding="utf-8"
        )
    )

    assert "Repeat warning:" in planner_prompt
    assert "Probable repeated attempt detected" in planner_prompt
    assert "Repeat warning:" in executor_request["executor_prompt"]
    assert "Probable repeated attempt detected" in executor_request["executor_prompt"]
