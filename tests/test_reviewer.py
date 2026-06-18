from __future__ import annotations

import pytest

from agent_loop.agents import ReviewerAgent
from agent_loop.config import build_limits
from agent_loop.models import (
    Contract,
    ExecutionContext,
    PlannerResult,
    ReviewerDecision,
    ReviewerResponseError,
)


def _make_context(temp_repo, objective: str = "Build a tiny feature") -> ExecutionContext:
    contract = Contract(
        objective=objective,
        checks=["pytest"],
        constraints=["Never use sudo"],
        max_iterations=2,
        task_name="reviewer-test",
    )
    return ExecutionContext(
        repo_path=temp_repo,
        work_dir=temp_repo / "work",
        branch="agent/reviewer-test",
        contract=contract,
        limits=build_limits(contract.to_dict()),
        dry_run=True,
        iteration=1,
    )


def _run_args(command_returncode: int = 0) -> dict:
    return {
        "planner": PlannerResult(summary="Write one file", tasks=["Create file"]),
        "executor_summary": "Created file",
        "diff": "diff text",
        "command_results": [
            {
                "command": "pytest",
                "returncode": command_returncode,
                "stdout": "",
                "stderr": "",
            }
        ],
    }


def test_reviewer_agent_stub_all_checks_pass(temp_repo) -> None:
    result = ReviewerAgent().run(_make_context(temp_repo), **_run_args(command_returncode=0))

    assert result.decision == ReviewerDecision.OBJECTIVE_COMPLETE
    assert "checks passed" in result.reason


def test_reviewer_agent_stub_check_failure(temp_repo) -> None:
    result = ReviewerAgent().run(_make_context(temp_repo), **_run_args(command_returncode=1))

    assert result.decision == ReviewerDecision.REVISE
    assert "check failed" in result.reason


def test_reviewer_agent_uses_fake_client(temp_repo) -> None:
    captured: dict[str, str] = {}

    class FakeClient:
        def complete(self, *, prompt: str) -> str:
            captured["prompt"] = prompt
            return (
                '{"decision": "OBJECTIVE_COMPLETE", '
                '"reason": "All checks passed."}'
            )

    context = _make_context(temp_repo, objective="Ship the feature")
    result = ReviewerAgent(client=FakeClient()).run(context, **_run_args())

    assert result.decision == ReviewerDecision.OBJECTIVE_COMPLETE
    assert result.reason == "All checks passed."
    assert "Ship the feature" in captured["prompt"]


def test_reviewer_agent_raises_on_invalid_client_response(temp_repo) -> None:
    class BrokenClient:
        def complete(self, *, prompt: str) -> str:
            return "not json"

    with pytest.raises(ReviewerResponseError, match="not valid JSON"):
        ReviewerAgent(client=BrokenClient()).run(_make_context(temp_repo), **_run_args())


def test_reviewer_agent_raises_on_invalid_decision(temp_repo) -> None:
    class InvalidDecisionClient:
        def complete(self, *, prompt: str) -> str:
            return '{"decision": "MAYBE", "reason": "unclear"}'

    with pytest.raises(ReviewerResponseError, match="decision must be one of"):
        ReviewerAgent(client=InvalidDecisionClient()).run(_make_context(temp_repo), **_run_args())


def test_reviewer_agent_prefers_responder_over_client(temp_repo) -> None:
    class ShouldNotBeCalled:
        def complete(self, *, prompt: str) -> str:
            raise AssertionError("client should not be called when responder is set")

    reviewer = ReviewerAgent(
        responder=lambda context, payload: {
            "decision": ReviewerDecision.CONTINUE.value,
            "reason": "Responder wins",
        },
        client=ShouldNotBeCalled(),
    )

    result = reviewer.run(_make_context(temp_repo), **_run_args())

    assert result.decision == ReviewerDecision.CONTINUE
    assert result.reason == "Responder wins"


def test_reviewer_agent_wraps_client_errors_as_reviewer_response_error(temp_repo) -> None:
    class FailingClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("transient API failure")

    with pytest.raises(ReviewerResponseError, match="Reviewer API call failed"):
        ReviewerAgent(client=FailingClient()).run(_make_context(temp_repo), **_run_args())
