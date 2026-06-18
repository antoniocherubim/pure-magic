from __future__ import annotations

import pytest

from agent_loop.models import CheckStatus, PreviousIterationSummary
from agent_loop.prompts import (
    EXECUTOR_CORRECTION_GUIDANCE,
    PLANNER_CORRECTION_GUIDANCE,
    format_executor_correction_guidance,
    format_executor_prompt,
    format_planner_correction_guidance,
    format_planner_prompt,
)

_CONTRACT = {
    "objective": "Build feature",
    "checks": ["pytest"],
    "constraints": ["Never use sudo"],
    "max_iterations": 2,
    "task_name": "prompt-correction",
}
_PLAN = {"summary": "Current plan", "tasks": ["Do work"]}


def _failed_summary(
    failed_stage: str,
    *,
    planner_summary: str | None = "Previous plan",
    executor_summary: str | None = "Previous exec",
    checks: list[CheckStatus] | None = None,
) -> PreviousIterationSummary:
    return PreviousIterationSummary(
        iteration=1,
        status="failed",
        artifact_dir="iterations/1",
        planner_summary=planner_summary,
        executor_summary=executor_summary,
        failed_stage=failed_stage,
        error=f"Simulated {failed_stage} failure",
        checks=checks or [],
    )


PLANNER_STAGE_MARKERS = {
    "planner": "same task decomposition",
    "executor": "valid JSON aligned with the contract",
    "apply_operations": "protected paths",
    "checks": "failed or timed-out check",
    "diff": "allows a diff to be collected",
}

EXECUTOR_STAGE_MARKERS = {
    "planner": "failed before execution",
    "executor": "same operations, commands, or summary",
    "apply_operations": "same write_file operations",
    "checks": "same commands from the previous iteration",
    "diff": "prevented diff collection",
}


@pytest.mark.parametrize("failed_stage", list(PLANNER_STAGE_MARKERS))
def test_planner_prompt_includes_stage_specific_correction(failed_stage: str) -> None:
    summary = _failed_summary(failed_stage)
    prompt = format_planner_prompt(_CONTRACT, previous_iteration=summary)

    assert "Correction strategy:" in prompt
    assert f"Failed stage: {failed_stage}" in prompt
    assert PLANNER_STAGE_MARKERS[failed_stage] in prompt
    assert PLANNER_CORRECTION_GUIDANCE[failed_stage] in prompt


@pytest.mark.parametrize("failed_stage", list(EXECUTOR_STAGE_MARKERS))
def test_executor_prompt_includes_stage_specific_correction(failed_stage: str) -> None:
    summary = _failed_summary(failed_stage)
    prompt = format_executor_prompt(
        objective="Build feature",
        plan=_PLAN,
        constraints=["Never use sudo"],
        previous_iteration=summary,
    )

    assert "Correction strategy:" in prompt
    assert f"Failed stage: {failed_stage}" in prompt
    assert EXECUTOR_STAGE_MARKERS[failed_stage] in prompt
    assert EXECUTOR_CORRECTION_GUIDANCE[failed_stage] in prompt


def test_correction_guidance_differs_across_failed_stages() -> None:
    planner_texts = {
        stage: format_planner_correction_guidance(_failed_summary(stage))
        for stage in PLANNER_STAGE_MARKERS
    }
    executor_texts = {
        stage: format_executor_correction_guidance(_failed_summary(stage))
        for stage in EXECUTOR_STAGE_MARKERS
    }

    assert len(set(planner_texts.values())) == len(PLANNER_STAGE_MARKERS)
    assert len(set(executor_texts.values())) == len(EXECUTOR_STAGE_MARKERS)


@pytest.mark.parametrize(
    ("previous_iteration", "expected"),
    [
        (None, "(none - first iteration)"),
        (
            PreviousIterationSummary(
                iteration=1,
                status="completed",
                artifact_dir="iterations/1",
                reviewer_decision="REVISE",
            ),
            "(none - previous iteration did not fail)",
        ),
    ],
)
def test_planner_correction_guidance_neutral_without_failure(
    previous_iteration: PreviousIterationSummary | None,
    expected: str,
) -> None:
    assert format_planner_correction_guidance(previous_iteration) == expected

    prompt = format_planner_prompt(_CONTRACT, previous_iteration=previous_iteration)
    assert expected in prompt
    assert "Failed stage:" not in prompt


def test_executor_correction_guidance_references_previous_checks() -> None:
    summary = _failed_summary(
        "checks",
        checks=[CheckStatus(command="pytest", status="error", returncode=None)],
    )

    guidance = format_executor_correction_guidance(summary)

    assert "Reference previous checks: pytest (error)" in guidance
    assert EXECUTOR_CORRECTION_GUIDANCE["checks"] in guidance
