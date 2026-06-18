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

    @property
    def task_name(self) -> str:
        return self.contract.task_name


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "plan": self.plan.to_dict(),
            "constraints": list(self.constraints),
            "allowed_commands": list(self.allowed_commands),
            "branch": self.branch,
            "iteration": self.iteration,
            "repo_path": self.repo_path,
            "executor_prompt": self.executor_prompt,
        }


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
