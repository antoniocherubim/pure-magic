from __future__ import annotations

import pytest

from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.config import AGENT_DRY_RUN_ENV, HarnessOverrides
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


def test_run_loop_persists_repository_context_and_planner_prompt_block(temp_repo) -> None:
    (temp_repo / "README.md").write_text("# Repo\n", encoding="utf-8")
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Validate repository context
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 1
task_name: repo-context-test
allow_overwrite: false
---
""",
        encoding="utf-8",
    )

    exit_code = run_loop(repo_path=temp_repo, dry_run=True)

    assert exit_code == 0
    context_path = temp_repo / "work" / "iterations" / "1" / "repository_context.json"
    prompt_path = temp_repo / "work" / "iterations" / "1" / "planner_prompt.txt"
    assert context_path.exists()
    assert prompt_path.exists()
    prompt_text = prompt_path.read_text(encoding="utf-8")
    assert "Repository context:" in prompt_text
    assert '"repo_name"' in prompt_text


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


def _programmatic_loop_agents() -> tuple[PlannerAgent, ExecutorAgent, ReviewerAgent]:
    planner = PlannerAgent(
        responder=lambda context: {
            "summary": "Programmatic config test",
            "tasks": ["Run checks"],
        }
    )
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [],
            "commands": [],
            "summary": "No file changes",
        }
    )
    reviewer = ReviewerAgent(
        responder=lambda context, payload: {
            "decision": ReviewerDecision.OBJECTIVE_COMPLETE.value,
            "reason": "Done.",
        }
    )
    return planner, executor, reviewer


def test_run_loop_without_overrides_respects_contract_dry_run(temp_repo, monkeypatch) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Respect contract dry_run
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 1
task_name: contract-dry-run
allow_overwrite: false
dry_run: false
---
""",
        encoding="utf-8",
    )
    calls = {"count": 0}

    def track_safe_start(*args, **kwargs):
        calls["count"] += 1

    monkeypatch.setattr("agent_loop.runner.ensure_safe_start", track_safe_start)
    planner, executor, reviewer = _programmatic_loop_agents()

    exit_code = run_loop(
        repo_path=temp_repo,
        planner=planner,
        executor=executor,
        reviewer=reviewer,
    )

    assert exit_code == 0
    assert calls["count"] == 1


def test_run_loop_without_overrides_respects_env_dry_run(temp_repo, monkeypatch) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Respect env dry_run
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 1
task_name: env-dry-run
allow_overwrite: false
---
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(AGENT_DRY_RUN_ENV, "false")
    calls = {"count": 0}
    monkeypatch.setattr(
        "agent_loop.runner.ensure_safe_start",
        lambda *args, **kwargs: calls.__setitem__("count", calls["count"] + 1),
    )
    planner, executor, reviewer = _programmatic_loop_agents()

    exit_code = run_loop(
        repo_path=temp_repo,
        planner=planner,
        executor=executor,
        reviewer=reviewer,
    )

    assert exit_code == 0
    assert calls["count"] == 1


def test_run_loop_overrides_dry_run_beats_contract_and_env(temp_repo, monkeypatch) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Override dry_run
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 1
task_name: override-dry-run
allow_overwrite: false
dry_run: true
---
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(AGENT_DRY_RUN_ENV, "true")
    calls = {"count": 0}
    monkeypatch.setattr(
        "agent_loop.runner.ensure_safe_start",
        lambda *args, **kwargs: calls.__setitem__("count", calls["count"] + 1),
    )
    planner, executor, reviewer = _programmatic_loop_agents()

    exit_code = run_loop(
        repo_path=temp_repo,
        planner=planner,
        executor=executor,
        reviewer=reviewer,
        overrides=HarnessOverrides(dry_run=False),
    )

    assert exit_code == 0
    assert calls["count"] == 1


def test_run_loop_without_overrides_defaults_to_dry_run(temp_repo, monkeypatch) -> None:
    (temp_repo / "agent_contract.md").write_text(
        """---
objective: Default dry_run
checks:
  - pytest
constraints:
  - Never run sudo
max_iterations: 1
task_name: default-dry-run
allow_overwrite: false
---
""",
        encoding="utf-8",
    )
    monkeypatch.delenv(AGENT_DRY_RUN_ENV, raising=False)
    calls = {"count": 0}
    monkeypatch.setattr(
        "agent_loop.runner.ensure_safe_start",
        lambda *args, **kwargs: calls.__setitem__("count", calls["count"] + 1),
    )

    exit_code = run_loop(repo_path=temp_repo)

    assert exit_code == 0
    assert calls["count"] == 0
