from __future__ import annotations

import pytest

from agent_loop.agents import PlannerAgent
from agent_loop.config import build_limits
from agent_loop.models import Contract, ExecutionContext, PlannerResponseError


def _make_context(temp_repo, objective: str = "Build a tiny feature") -> ExecutionContext:
    contract = Contract(
        objective=objective,
        checks=["pytest"],
        constraints=["Never use sudo"],
        max_iterations=2,
        task_name="planner-test",
    )
    return ExecutionContext(
        repo_path=temp_repo,
        work_dir=temp_repo / "work",
        branch="agent/planner-test",
        contract=contract,
        limits=build_limits(contract.to_dict()),
        dry_run=True,
        iteration=1,
    )


def test_planner_agent_stub_mode_returns_default_plan(temp_repo) -> None:
    context = _make_context(temp_repo)
    result = PlannerAgent().run(context)

    assert result.summary == "Break the objective into the smallest safe implementation step."
    assert result.tasks == [
        "Read the contract and constraints",
        "Prepare the smallest code or file update",
        "Run the requested verification commands",
    ]


def test_planner_agent_uses_fake_client(temp_repo) -> None:
    captured: dict[str, str] = {}

    class FakeClient:
        def complete(self, *, prompt: str) -> str:
            captured["prompt"] = prompt
            return (
                '{"summary": "Create one file", '
                '"tasks": ["Write generated.txt", "Run pytest"]}'
            )

    context = _make_context(temp_repo, objective="Add generated.txt")
    result = PlannerAgent(client=FakeClient()).run(context)

    assert result.summary == "Create one file"
    assert result.tasks == ["Write generated.txt", "Run pytest"]
    assert "Add generated.txt" in captured["prompt"]


def test_planner_agent_raises_on_invalid_client_response(temp_repo) -> None:
    class BrokenClient:
        def complete(self, *, prompt: str) -> str:
            return "not json"

    with pytest.raises(PlannerResponseError, match="not valid JSON"):
        PlannerAgent(client=BrokenClient()).run(_make_context(temp_repo))


def test_planner_agent_prefers_responder_over_client(temp_repo) -> None:
    class ShouldNotBeCalled:
        def complete(self, *, prompt: str) -> str:
            raise AssertionError("client should not be called when responder is set")

    planner = PlannerAgent(
        responder=lambda context: {
            "summary": "Responder wins",
            "tasks": ["Only responder task"],
        },
        client=ShouldNotBeCalled(),
    )

    result = planner.run(_make_context(temp_repo))

    assert result.summary == "Responder wins"
    assert result.tasks == ["Only responder task"]


def test_planner_agent_wraps_client_errors_as_planner_response_error(temp_repo) -> None:
    class FailingClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("transient API failure")

    with pytest.raises(PlannerResponseError, match="Planner API call failed"):
        PlannerAgent(client=FailingClient()).run(_make_context(temp_repo))
