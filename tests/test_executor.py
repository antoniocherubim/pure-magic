from __future__ import annotations

import pytest

from agent_loop.agents import ExecutorAgent
from agent_loop.config import build_limits
from agent_loop.models import (
    Contract,
    ExecutionContext,
    ExecutorResponseError,
    PlannerResult,
)


def _make_context(temp_repo, objective: str = "Build a tiny feature") -> ExecutionContext:
    contract = Contract(
        objective=objective,
        checks=["pytest"],
        constraints=["Never use sudo"],
        max_iterations=2,
        task_name="executor-test",
    )
    return ExecutionContext(
        repo_path=temp_repo,
        work_dir=temp_repo / "work",
        branch="agent/executor-test",
        contract=contract,
        limits=build_limits(contract.to_dict()),
        dry_run=True,
        iteration=1,
    )


def _planner() -> PlannerResult:
    return PlannerResult(summary="Write one file", tasks=["Create file"])


def test_executor_agent_stub_returns_empty_operations(temp_repo) -> None:
    payload = ExecutorAgent().run(_make_context(temp_repo), _planner())

    assert payload["operations"] == []
    assert payload["commands"] == ["pytest"]
    assert "No external executor was configured" in payload["summary"]
    assert payload["executor_request"]["objective"] == "Build a tiny feature"


def test_executor_agent_uses_provider(temp_repo) -> None:
    agent = ExecutorAgent(
        provider=lambda request: {
            "operations": [
                {
                    "type": "write_file",
                    "path": "generated.txt",
                    "content": "hello\n",
                }
            ],
            "commands": [],
            "summary": "Created generated.txt",
        }
    )

    payload = agent.run(_make_context(temp_repo), _planner())

    assert payload["operations"][0]["path"] == "generated.txt"
    assert payload["summary"] == "Created generated.txt"
    assert payload["executor_request"]["plan"]["summary"] == "Write one file"


def test_executor_agent_uses_fake_client(temp_repo) -> None:
    captured: dict[str, str] = {}

    class FakeClient:
        def complete(self, *, prompt: str) -> str:
            captured["prompt"] = prompt
            return (
                '{"operations": [{"type": "write_file", "path": "app.py", '
                '"content": "print(1)\\n"}], "commands": ["pytest"], '
                '"summary": "Created app.py"}'
            )

    context = _make_context(temp_repo, objective="Add app.py")
    payload = ExecutorAgent(client=FakeClient()).run(context, _planner())

    assert payload["summary"] == "Created app.py"
    assert payload["operations"][0]["path"] == "app.py"
    assert "Add app.py" in captured["prompt"]


def test_executor_agent_raises_on_invalid_client_response(temp_repo) -> None:
    class BrokenClient:
        def complete(self, *, prompt: str) -> str:
            return "not json"

    with pytest.raises(ExecutorResponseError, match="not valid JSON"):
        ExecutorAgent(client=BrokenClient()).run(_make_context(temp_repo), _planner())


def test_executor_agent_prefers_provider_over_client(temp_repo) -> None:
    class ShouldNotBeCalled:
        def complete(self, *, prompt: str) -> str:
            raise AssertionError("client should not be called when provider is set")

    agent = ExecutorAgent(
        provider=lambda request: {
            "operations": [],
            "commands": ["pytest"],
            "summary": "Provider wins",
        },
        client=ShouldNotBeCalled(),
    )

    payload = agent.run(_make_context(temp_repo), _planner())

    assert payload["summary"] == "Provider wins"


def test_executor_agent_wraps_client_errors_as_executor_response_error(temp_repo) -> None:
    class FailingClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("transient API failure")

    with pytest.raises(ExecutorResponseError, match="Executor API call failed"):
        ExecutorAgent(client=FailingClient()).run(_make_context(temp_repo), _planner())


def test_executor_agent_rejects_disallowed_command_from_provider(temp_repo) -> None:
    agent = ExecutorAgent(
        provider=lambda request: {
            "operations": [],
            "commands": ["python -m pytest"],
            "summary": "Run checks",
        }
    )

    with pytest.raises(ExecutorResponseError, match="commands\\[0\\]"):
        agent.run(_make_context(temp_repo), _planner())


def test_executor_agent_builds_explicit_request(temp_repo) -> None:
    contract = Contract(
        objective="Create one file",
        checks=["pytest"],
        constraints=["Never use sudo"],
        max_iterations=2,
        task_name="executor-request",
    )
    context = ExecutionContext(
        repo_path=temp_repo,
        work_dir=temp_repo / "work",
        branch="agent/executor-request",
        contract=contract,
        limits=build_limits(contract.to_dict()),
        dry_run=True,
        iteration=1,
    )
    planner = PlannerResult(summary="Write one file", tasks=["Create file"])

    request = ExecutorAgent().build_request(context, planner)

    assert request.objective == "Create one file"
    assert request.allowed_commands == ["pytest"]
    assert request.branch == "agent/executor-request"
    assert request.iteration == 1
    assert request.plan.summary == "Write one file"
