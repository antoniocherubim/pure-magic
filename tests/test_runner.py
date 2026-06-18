from __future__ import annotations

import pytest

from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.models import ReviewerDecision
from agent_loop.runner import run_loop


def test_run_loop_dry_run_writes_log(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Validate the loop
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 2
task_name: dry-run-test
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    exit_code = run_loop(repo_path=temp_repo, dry_run=True)

    assert exit_code == 0
    assert (temp_repo / "work" / "agent_log.md").exists()
    assert (temp_repo / "work" / "iterations" / "1" / "reviewer_response.json").exists()


def test_run_loop_applies_write_file_and_completes(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Add a generated file
checks:
  - python -m pytest
constraints:
  - Never run sudo
max_iterations: 2
task_name: apply-write-file
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    planner = PlannerAgent(
        responder=lambda context: {
            "summary": "Create one file and run checks",
            "tasks": ["Write a new file", "Run pytest"],
        }
    )
    reviewer = ReviewerAgent(
        responder=lambda context, payload: {
            "decision": ReviewerDecision.OBJECTIVE_COMPLETE.value,
            "reason": "The file was created and checks passed.",
        }
    )
    executor = ExecutorAgent(
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

    exit_code = run_loop(
        repo_path=temp_repo,
        dry_run=False,
        planner=planner,
        reviewer=reviewer,
        executor=executor,
    )

    assert exit_code == 0
    assert (temp_repo / "generated.txt").read_text(encoding="utf-8") == "hello\n"


def _loop_agents_for_reviewer_tests() -> tuple[PlannerAgent, ExecutorAgent]:
    planner = PlannerAgent(
        responder=lambda context: {
            "summary": "Create one file",
            "tasks": ["Write a file"],
        }
    )
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [],
            "commands": ["pytest"],
            "summary": "No file changes",
        }
    )
    return planner, executor


def test_run_loop_retries_after_transient_planner_api_failure(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Retry planner failures
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 5
failure_limit: 3
task_name: planner-retry
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("transient API failure")
            return '{"summary": "Plan", "tasks": ["Do work"]}'

    client = FlakyClient()
    exit_code = run_loop(
        repo_path=temp_repo,
        dry_run=True,
        planner=PlannerAgent(client=client),
    )

    assert exit_code == 0
    assert client.calls == 2


def test_run_loop_aborts_after_repeated_planner_api_failures(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Fail planner repeatedly
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 5
failure_limit: 2
task_name: planner-fail-limit
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    class AlwaysFailingClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("persistent API failure")

    with pytest.raises(RuntimeError, match="Planner API call failed"):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=PlannerAgent(client=AlwaysFailingClient()),
        )


def test_run_loop_supports_contract_outside_repo(temp_repo, tmp_path) -> None:
    contract_file = tmp_path / "external_contract.md"
    contract_file.write_text(
        """---
objective: Add a generated file
checks:
  - python -m pytest
constraints:
  - Never run sudo
max_iterations: 2
task_name: external-contract
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    planner = PlannerAgent(
        responder=lambda context: {
            "summary": "Create one file and run checks",
            "tasks": ["Write a new file", "Run pytest"],
        }
    )
    reviewer = ReviewerAgent(
        responder=lambda context, payload: {
            "decision": ReviewerDecision.OBJECTIVE_COMPLETE.value,
            "reason": "The file was created and checks passed.",
        }
    )
    executor = ExecutorAgent(
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

    exit_code = run_loop(
        repo_path=temp_repo,
        contract_path=contract_file,
        dry_run=False,
        planner=planner,
        reviewer=reviewer,
        executor=executor,
    )

    assert exit_code == 0
    assert (temp_repo / "generated.txt").read_text(encoding="utf-8") == "hello\n"


def test_run_loop_retries_after_transient_reviewer_api_failure(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Retry reviewer failures
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 5
failure_limit: 3
task_name: reviewer-retry
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    class FlakyReviewerClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("transient API failure")
            return '{"decision": "CONTINUE", "reason": "Proceed to next iteration."}'

    planner, executor = _loop_agents_for_reviewer_tests()
    client = FlakyReviewerClient()
    exit_code = run_loop(
        repo_path=temp_repo,
        dry_run=True,
        planner=planner,
        reviewer=ReviewerAgent(client=client),
        executor=executor,
    )

    assert exit_code == 0
    assert client.calls == 2


def test_run_loop_aborts_after_repeated_reviewer_api_failures(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Fail reviewer repeatedly
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 5
failure_limit: 2
task_name: reviewer-fail-limit
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    class AlwaysFailingReviewerClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("persistent API failure")

    planner, executor = _loop_agents_for_reviewer_tests()

    with pytest.raises(RuntimeError, match="Reviewer API call failed"):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            reviewer=ReviewerAgent(client=AlwaysFailingReviewerClient()),
            executor=executor,
        )


def test_run_loop_retries_after_transient_executor_api_failure(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Retry executor failures
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 5
failure_limit: 3
task_name: executor-retry
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    class FlakyExecutorClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("transient API failure")
            return (
                '{"operations": [], "commands": ["pytest"], '
                '"summary": "No file changes"}'
            )

    planner = PlannerAgent(
        responder=lambda context: {
            "summary": "Create one file",
            "tasks": ["Write a file"],
        }
    )
    client = FlakyExecutorClient()
    exit_code = run_loop(
        repo_path=temp_repo,
        dry_run=True,
        planner=planner,
        executor=ExecutorAgent(client=client),
    )

    assert exit_code == 0
    assert client.calls == 2
