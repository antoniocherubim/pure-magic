from __future__ import annotations

import json
from subprocess import TimeoutExpired

import pytest

from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.models import ReviewerDecision
from agent_loop.runner import run_loop


def _write_contract(
    temp_repo,
    task_name: str,
    *,
    failure_limit: int = 3,
    max_iterations: int = 5,
    checks: list[str] | None = None,
) -> None:
    check_lines = "\n".join(f"  - {item}" for item in (checks or ["pytest"]))
    (temp_repo / "agent_contract.md").write_text(
        f"""---
objective: Artifact audit test
checks:
{check_lines}
constraints:
  - Never run sudo
max_iterations: {max_iterations}
failure_limit: {failure_limit}
task_name: {task_name}
allow_overwrite: false
---
""",
        encoding="utf-8",
    )


def _iteration_dir(temp_repo):
    return temp_repo / "work" / "iterations" / "1"


def _loop_agents_success() -> tuple[PlannerAgent, ExecutorAgent, ReviewerAgent]:
    return (
        PlannerAgent(
            responder=lambda context: {
                "summary": "Create one file",
                "tasks": ["Write a file"],
            }
        ),
        ExecutorAgent(
            provider=lambda request: {
                "operations": [],
                "commands": ["pytest"],
                "summary": "No file changes",
            }
        ),
        ReviewerAgent(
            responder=lambda context, payload: {
                "decision": ReviewerDecision.CONTINUE.value,
                "reason": "Proceed.",
            }
        ),
    )


def test_successful_iteration_writes_all_artifacts(temp_repo) -> None:
    _write_contract(temp_repo, "artifact-success")
    planner, executor, reviewer = _loop_agents_success()

    exit_code = run_loop(
        repo_path=temp_repo,
        dry_run=True,
        planner=planner,
        executor=executor,
        reviewer=reviewer,
    )

    assert exit_code == 0
    iteration_dir = _iteration_dir(temp_repo)
    expected_files = {
        "meta.json",
        "planner_prompt.txt",
        "planner_response.json",
        "executor_request.json",
        "executor_response.json",
        "reviewer_prompt.txt",
        "reviewer_response.json",
        "commands.json",
    }
    assert expected_files.issubset({path.name for path in iteration_dir.iterdir()})

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert meta["failed_stage"] is None

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "iterations/1/planner_prompt.txt" in log_text
    assert "iterations/1/reviewer_response.json" in log_text


def test_planner_failure_writes_partial_artifacts(temp_repo) -> None:
    _write_contract(temp_repo, "artifact-planner-fail", max_iterations=1)

    class AlwaysFailingPlannerClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("planner API failure")

    with pytest.raises(RuntimeError, match="Max iterations reached; last failure at planner"):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=PlannerAgent(client=AlwaysFailingPlannerClient()),
        )
    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "planner_prompt.txt").exists()
    planner_response = json.loads(
        (iteration_dir / "planner_response.json").read_text(encoding="utf-8")
    )
    assert "error" in planner_response
    assert not (iteration_dir / "executor_request.json").exists()

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_stage"] == "planner"

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "Failed stage: `planner`" in log_text


def test_executor_failure_writes_partial_artifacts(temp_repo) -> None:
    _write_contract(temp_repo, "artifact-executor-fail", max_iterations=1)

    class AlwaysFailingExecutorClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("executor API failure")

    planner, _, reviewer = _loop_agents_success()
    with pytest.raises(RuntimeError, match="Max iterations reached; last failure at executor"):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            executor=ExecutorAgent(client=AlwaysFailingExecutorClient()),
            reviewer=reviewer,
        )
    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "planner_response.json").exists()
    assert (iteration_dir / "executor_request.json").exists()
    executor_response = json.loads(
        (iteration_dir / "executor_response.json").read_text(encoding="utf-8")
    )
    assert "error" in executor_response
    assert not (iteration_dir / "reviewer_prompt.txt").exists()

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["failed_stage"] == "executor"


def test_reviewer_failure_writes_partial_artifacts(temp_repo) -> None:
    _write_contract(temp_repo, "artifact-reviewer-fail", max_iterations=1)

    class AlwaysFailingReviewerClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("reviewer API failure")

    planner, executor, _ = _loop_agents_success()
    with pytest.raises(RuntimeError, match="Max iterations reached; last failure at reviewer"):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            executor=executor,
            reviewer=ReviewerAgent(client=AlwaysFailingReviewerClient()),
        )
    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "commands.json").exists()
    assert (iteration_dir / "reviewer_prompt.txt").exists()
    reviewer_response = json.loads(
        (iteration_dir / "reviewer_response.json").read_text(encoding="utf-8")
    )
    assert "error" in reviewer_response

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["failed_stage"] == "reviewer"

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "Failed stage: `reviewer`" in log_text


def test_apply_operations_failure_writes_partial_artifacts(temp_repo) -> None:
    _write_contract(temp_repo, "artifact-apply-ops-fail", max_iterations=1)

    planner, _, reviewer = _loop_agents_success()
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [
                {"type": "write_file", "path": ".env", "content": "leaked"},
            ],
            "commands": [],
            "summary": "Attempt protected write",
        }
    )

    with pytest.raises(
        RuntimeError,
        match="Max iterations reached; last failure at apply_operations",
    ):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "planner_response.json").exists()
    assert (iteration_dir / "executor_response.json").exists()
    assert not (iteration_dir / "commands.json").exists()

    error_payload = json.loads(
        (iteration_dir / "apply_operations_error.json").read_text(encoding="utf-8")
    )
    assert error_payload["stage"] == "apply_operations"
    assert "Protected path blocked" in error_payload["error"]

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_stage"] == "apply_operations"

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "Failed stage: `apply_operations`" in log_text


def test_apply_operations_os_error_writes_partial_artifacts(temp_repo, monkeypatch) -> None:
    _write_contract(temp_repo, "artifact-apply-ops-oserror", max_iterations=1)

    def failing_write_file(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("agent_loop.tools.write_file", failing_write_file)

    planner, _, reviewer = _loop_agents_success()
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [
                {"type": "write_file", "path": "src/module.py", "content": "x = 1\n"},
            ],
            "commands": [],
            "summary": "Write module file",
        }
    )

    with pytest.raises(
        RuntimeError,
        match="Max iterations reached; last failure at apply_operations",
    ):
        run_loop(
            repo_path=temp_repo,
            dry_run=False,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "planner_response.json").exists()
    assert (iteration_dir / "executor_response.json").exists()
    assert not (iteration_dir / "commands.json").exists()

    error_payload = json.loads(
        (iteration_dir / "apply_operations_error.json").read_text(encoding="utf-8")
    )
    assert error_payload["stage"] == "apply_operations"
    assert "Permission denied" in error_payload["error"]

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_stage"] == "apply_operations"

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "Failed stage: `apply_operations`" in log_text


def test_max_iterations_preserves_apply_operations_os_error_context(temp_repo, monkeypatch) -> None:
    _write_contract(
        temp_repo,
        "artifact-max-iter-apply-ops",
        max_iterations=2,
        failure_limit=10,
    )

    def failing_write_file(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("agent_loop.tools.write_file", failing_write_file)

    planner, executor, reviewer = _loop_agents_success()
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [
                {"type": "write_file", "path": "src/module.py", "content": "x = 1\n"},
            ],
            "commands": ["pytest"],
            "summary": "Write module file",
        }
    )

    with pytest.raises(RuntimeError) as exc_info:
        run_loop(
            repo_path=temp_repo,
            dry_run=False,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

    message = str(exc_info.value)
    assert "Max iterations reached" in message
    assert "last failure at apply_operations" in message
    assert "Permission denied" in message

    meta = json.loads(
        (temp_repo / "work" / "iterations" / "2" / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["failed_stage"] == "apply_operations"


def test_checks_failure_writes_partial_artifacts(temp_repo) -> None:
    blocked_command = "sudo apt install pytest"
    _write_contract(
        temp_repo,
        "artifact-checks-fail",
        max_iterations=1,
        checks=[blocked_command],
    )

    planner, _, reviewer = _loop_agents_success()
    executor = ExecutorAgent(
        provider=lambda request: {
            "operations": [],
            "commands": [blocked_command],
            "summary": "Run blocked check",
        }
    )

    with pytest.raises(
        RuntimeError,
        match="Max iterations reached; last failure at checks",
    ):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "executor_response.json").exists()
    assert (iteration_dir / "commands.json").exists()

    error_payload = json.loads(
        (iteration_dir / "checks_error.json").read_text(encoding="utf-8")
    )
    assert error_payload["stage"] == "checks"
    assert "Dangerous command blocked" in error_payload["error"]

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["failed_stage"] == "checks"

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "Failed stage: `checks`" in log_text


def test_max_iterations_preserves_last_failure_context(temp_repo) -> None:
    _write_contract(
        temp_repo,
        "artifact-max-iter-context",
        max_iterations=2,
        failure_limit=10,
    )

    class AlwaysFailingPlannerClient:
        def complete(self, *, prompt: str) -> str:
            raise ConnectionError("planner API failure")

    with pytest.raises(RuntimeError) as exc_info:
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=PlannerAgent(client=AlwaysFailingPlannerClient()),
        )

    message = str(exc_info.value)
    assert "Max iterations reached" in message
    assert "last failure at planner" in message
    assert "planner API failure" in message

    assert (temp_repo / "work" / "iterations" / "1" / "meta.json").exists()
    assert (temp_repo / "work" / "iterations" / "2" / "meta.json").exists()


def test_checks_timeout_writes_partial_artifacts(temp_repo, monkeypatch) -> None:
    _write_contract(temp_repo, "artifact-checks-timeout", max_iterations=1)

    def timeout_run_command(command, *, cwd, dry_run, timeout_sec):
        raise TimeoutExpired(cmd=command, timeout=timeout_sec)

    monkeypatch.setattr("agent_loop.runner.run_command", timeout_run_command)

    planner, executor, reviewer = _loop_agents_success()

    with pytest.raises(
        RuntimeError,
        match="Max iterations reached; last failure at checks",
    ):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "executor_response.json").exists()
    assert (iteration_dir / "commands.json").exists()
    assert not (iteration_dir / "reviewer_prompt.txt").exists()

    error_payload = json.loads(
        (iteration_dir / "checks_error.json").read_text(encoding="utf-8")
    )
    assert error_payload["stage"] == "checks"
    assert "timed out" in error_payload["error"].lower()

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["failed_stage"] == "checks"

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "Failed stage: `checks`" in log_text


def test_diff_failure_writes_partial_artifacts(temp_repo, monkeypatch) -> None:
    _write_contract(temp_repo, "artifact-diff-fail", max_iterations=1)

    def failing_collect_diff(repo, dry_run):
        raise RuntimeError("git diff failed")

    monkeypatch.setattr("agent_loop.runner.collect_diff", failing_collect_diff)

    planner, executor, reviewer = _loop_agents_success()

    with pytest.raises(
        RuntimeError,
        match="Max iterations reached; last failure at diff",
    ):
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

    iteration_dir = _iteration_dir(temp_repo)
    assert (iteration_dir / "commands.json").exists()
    assert not (iteration_dir / "diff.patch").exists()
    assert not (iteration_dir / "reviewer_prompt.txt").exists()

    error_payload = json.loads(
        (iteration_dir / "diff_error.json").read_text(encoding="utf-8")
    )
    assert error_payload["stage"] == "diff"
    assert "git diff failed" in error_payload["error"]

    meta = json.loads((iteration_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_stage"] == "diff"

    log_text = (temp_repo / "work" / "agent_log.md").read_text(encoding="utf-8")
    assert "Failed stage: `diff`" in log_text


def test_max_iterations_preserves_diff_failure_context(temp_repo, monkeypatch) -> None:
    _write_contract(
        temp_repo,
        "artifact-max-iter-diff",
        max_iterations=2,
        failure_limit=10,
    )

    def failing_collect_diff(repo, dry_run):
        raise RuntimeError("git diff failed")

    monkeypatch.setattr("agent_loop.runner.collect_diff", failing_collect_diff)

    planner, executor, reviewer = _loop_agents_success()

    with pytest.raises(RuntimeError) as exc_info:
        run_loop(
            repo_path=temp_repo,
            dry_run=True,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

    message = str(exc_info.value)
    assert "Max iterations reached" in message
    assert "last failure at diff" in message
    assert "git diff failed" in message

    assert (temp_repo / "work" / "iterations" / "1" / "meta.json").exists()
    assert (temp_repo / "work" / "iterations" / "2" / "meta.json").exists()
    meta = json.loads(
        (temp_repo / "work" / "iterations" / "2" / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["failed_stage"] == "diff"
