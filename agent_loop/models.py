"""Core domain models for the orchestrator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from agent_loop.config import RuntimeLimits


class PlannerResponseError(Exception):
    """Raised when the planner response cannot be parsed or validated."""


class ReviewerResponseError(Exception):
    """Raised when the reviewer response cannot be parsed or validated."""


class ExecutorResponseError(Exception):
    """Raised when the executor response cannot be parsed or validated."""


class ReviewerDecision(str, Enum):
    CONTINUE = "CONTINUE"
    REVISE = "REVISE"
    OBJECTIVE_COMPLETE = "OBJECTIVE_COMPLETE"


@dataclass(slots=True)
class Contract:
    objective: str
    checks: list[str]
    constraints: list[str]
    max_iterations: int
    task_name: str
    allowed_installs: list[str] = field(default_factory=list)
    allow_overwrite: bool = False
    cost_limit: float | None = None
    failure_limit: int | None = None
    command_timeout_sec: int | None = None
    estimated_cost_per_iteration: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Contract":
        return cls(
            objective=str(data["objective"]),
            checks=list(data["checks"]),
            constraints=list(data["constraints"]),
            max_iterations=int(data["max_iterations"]),
            task_name=str(data["task_name"]),
            allowed_installs=list(data.get("allowed_installs", []) or []),
            allow_overwrite=bool(data.get("allow_overwrite", False)),
            cost_limit=_optional_float(data.get("cost_limit")),
            failure_limit=_optional_int(data.get("failure_limit")),
            command_timeout_sec=_optional_int(data.get("command_timeout_sec")),
            estimated_cost_per_iteration=_optional_float(
                data.get("estimated_cost_per_iteration")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutionContext:
    repo_path: Path
    work_dir: Path
    branch: str
    contract: Contract
    limits: RuntimeLimits
    dry_run: bool
    iteration: int = 0
    cumulative_cost: float = 0.0
    failure_count: int = 0
    last_diff: str = ""
    last_error: str = ""
    last_failed_stage: str | None = None
    previous_iteration: PreviousIterationSummary | None = None
    repeat_signal: RepeatSignal | None = None

    @property
    def task_name(self) -> str:
        return self.contract.task_name


@dataclass(slots=True)
class RepeatSignal:
    detected: bool
    matches: list[str]
    compared_with_iteration: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CheckStatus:
    command: str
    status: str
    returncode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_command_result(cls, result: "CommandResult") -> "CheckStatus":
        status = "passed" if result.returncode == 0 else "failed"
        return cls(command=result.command, status=status, returncode=result.returncode)

    @classmethod
    def not_run(cls, command: str) -> "CheckStatus":
        return cls(command=command, status="not_run", returncode=None)

    @classmethod
    def error(cls, command: str) -> "CheckStatus":
        return cls(command=command, status="error", returncode=None)


def build_check_statuses(
    *,
    results: list["CommandResult"] | None = None,
    planned_commands: list[str] | None = None,
    failed_command: str | None = None,
) -> list[CheckStatus]:
    statuses: list[CheckStatus] = []
    executed: set[str] = set()

    for result in results or []:
        statuses.append(CheckStatus.from_command_result(result))
        executed.add(result.command)

    if failed_command and failed_command not in executed:
        statuses.append(CheckStatus.error(failed_command))
        executed.add(failed_command)

    for command in planned_commands or []:
        if command not in executed:
            statuses.append(CheckStatus.not_run(command))

    return statuses


def write_file_paths_from_operations(operations: list["FileOperation"]) -> list[str]:
    return sorted(
        operation.path
        for operation in operations
        if operation.type == "write_file" and operation.path.strip()
    )


def _normalized_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def detect_repeat_attempt(
    *,
    planner_summary: str | None,
    executor_summary: str | None,
    commands: list[str] | None,
    write_file_paths: list[str] | None,
    previous: PreviousIterationSummary | None,
) -> RepeatSignal:
    if previous is None:
        return RepeatSignal(detected=False, matches=[], compared_with_iteration=None)

    matches: list[str] = []

    current_planner = _normalized_text(planner_summary)
    previous_planner = _normalized_text(previous.planner_summary)
    if current_planner and previous_planner and current_planner == previous_planner:
        matches.append("planner_summary")

    current_executor = _normalized_text(executor_summary)
    previous_executor = _normalized_text(previous.executor_summary)
    if current_executor and previous_executor and current_executor == previous_executor:
        matches.append("executor_summary")

    current_commands = list(commands or [])
    previous_commands = list(previous.commands or [])
    if current_commands and previous_commands and current_commands == previous_commands:
        matches.append("commands")

    current_paths = sorted(write_file_paths or [])
    previous_paths = sorted(previous.write_file_paths or [])
    if current_paths and previous_paths and current_paths == previous_paths:
        matches.append("write_file_paths")

    return RepeatSignal(
        detected=bool(matches),
        matches=matches,
        compared_with_iteration=previous.iteration,
    )


@dataclass(slots=True)
class PreviousIterationSummary:
    iteration: int
    status: str
    artifact_dir: str
    planner_summary: str | None = None
    executor_summary: str | None = None
    reviewer_decision: str | None = None
    failed_stage: str | None = None
    error: str | None = None
    checks: list[CheckStatus] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    write_file_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "status": self.status,
            "artifact_dir": self.artifact_dir,
            "planner_summary": self.planner_summary,
            "executor_summary": self.executor_summary,
            "reviewer_decision": self.reviewer_decision,
            "failed_stage": self.failed_stage,
            "error": self.error,
            "checks": [check.to_dict() for check in self.checks],
            "commands": list(self.commands),
            "write_file_paths": list(self.write_file_paths),
        }


@dataclass(slots=True)
class FileOperation:
    type: str
    path: str
    content: str | None = None
    instructions: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileOperation":
        return cls(
            type=str(data.get("type", "")),
            path=str(data.get("path", "")),
            content=data.get("content"),
            instructions=data.get("instructions"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutorResult:
    operations: list[FileOperation]
    commands: list[str]
    summary: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutorResult":
        return cls(
            operations=[FileOperation.from_dict(item) for item in data["operations"]],
            commands=[str(item) for item in data["commands"]],
            summary=str(data["summary"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operations": [operation.to_dict() for operation in self.operations],
            "commands": list(self.commands),
            "summary": self.summary,
        }


@dataclass(slots=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PlannerResult:
    summary: str
    tasks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutorRequest:
    objective: str
    plan: PlannerResult
    constraints: list[str]
    allowed_commands: list[str]
    branch: str
    iteration: int
    repo_path: str
    executor_prompt: str
    previous_iteration: PreviousIterationSummary | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "objective": self.objective,
            "plan": self.plan.to_dict(),
            "constraints": list(self.constraints),
            "allowed_commands": list(self.allowed_commands),
            "branch": self.branch,
            "iteration": self.iteration,
            "repo_path": self.repo_path,
            "executor_prompt": self.executor_prompt,
        }
        if self.previous_iteration is not None:
            payload["previous_iteration"] = self.previous_iteration.to_dict()
        return payload


@dataclass(slots=True)
class ReviewerResult:
    decision: ReviewerDecision
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
        }


@dataclass(slots=True)
class IterationAudit:
    iteration: int
    status: str
    failed_stage: str | None
    error: str | None
    artifact_dir: str
    planner_summary: str | None = None
    executor_summary: str | None = None
    reviewer_decision: str | None = None
    estimated_cost: float | None = None
    artifact_files: tuple[str, ...] = ()
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_markdown(self) -> str:
        lines = [
            f"## Iteration {self.iteration}",
            f"- Timestamp: `{self.created_at}`",
            f"- Status: `{self.status}`",
        ]
        if self.estimated_cost is not None:
            lines.append(f"- Estimated cost: `{self.estimated_cost}`")
        if self.failed_stage:
            lines.append(f"- Failed stage: `{self.failed_stage}`")
        if self.error:
            lines.append(f"- Error: {self.error}")
        if self.planner_summary:
            lines.append(f"- Planner summary: {self.planner_summary}")
        if self.executor_summary:
            lines.append(f"- Executor summary: {self.executor_summary}")
        if self.reviewer_decision:
            lines.append(f"- Reviewer decision: `{self.reviewer_decision}`")
        lines.append(f"- Artifact directory: `{self.artifact_dir}`")
        if self.artifact_files:
            lines.append("- Artifacts:")
            for artifact_file in self.artifact_files:
                lines.append(f"  - `{self.artifact_dir}/{artifact_file}`")
        lines.append("")
        return "\n".join(lines)


@dataclass(slots=True)
class IterationRecord:
    iteration: int
    planner: PlannerResult
    executor: ExecutorResult
    commands: list[CommandResult]
    reviewer: ReviewerResult
    estimated_cost: float
    diff_path: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_markdown(self) -> str:
        command_lines = []
        for command in self.commands:
            command_lines.append(
                f"- `{command.command}` -> returncode `{command.returncode}`"
            )

        operation_lines = []
        for operation in self.executor.operations:
            operation_lines.append(f"- `{operation.type}` `{operation.path}`")

        lines = [
            f"## Iteration {self.iteration}",
            f"- Timestamp: `{self.created_at}`",
            f"- Estimated cost: `{self.estimated_cost}`",
            f"- Reviewer decision: `{self.reviewer.decision.value}`",
            f"- Reviewer reason: {self.reviewer.reason}",
            "",
            "### Planner",
            self.planner.summary,
            "",
            "### Planner Tasks",
            *(self.planner.tasks or ["- (none)"]),
            "",
            "### Executor Summary",
            self.executor.summary,
            "",
            "### Operations",
            *(operation_lines or ["- (none)"]),
            "",
            "### Commands",
            *(command_lines or ["- (none)"]),
            "",
            f"### Diff\n`{self.diff_path or '(none)'}`",
            "",
        ]
        return "\n".join(lines)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
