"""Modelos de dados internos do orquestrador."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class AgentRole(str, Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"


class ReviewerDecision(str, Enum):
    CONTINUE = "CONTINUE"
    OBJECTIVE_COMPLETE = "OBJECTIVE_COMPLETE"
    REVISE = "REVISE"


@dataclass
class Context:
    task_name: str
    branch: str
    iteration: int
    contract: dict[str, Any]
    limits: dict[str, Any]
    cumulative_cost: float
    work_dir: Path
    last_diff: str = ""
    failure_count: int = 0
    dry_run: bool = True
    repo_path: Path = field(default_factory=Path)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["work_dir"] = str(self.work_dir)
        data["repo_path"] = str(self.repo_path)
        return data


@dataclass
class AgentResponse:
    role: AgentRole
    raw_text: str
    parsed: dict[str, Any] = field(default_factory=dict)
    tokens_used_estimate: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "raw_text": self.raw_text,
            "parsed": self.parsed,
            "tokens_used_estimate": self.tokens_used_estimate,
        }


@dataclass
class FileOperation:
    type: str
    path: str
    content: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileOperation:
        return cls(
            type=data.get("type", ""),
            path=data.get("path", ""),
            content=data.get("content", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "path": self.path, "content": self.content}


@dataclass
class LogEntry:
    iteration: int
    timestamp: str
    planner_summary: str
    executor_ops: list[dict[str, Any]]
    commands_run: list[dict[str, Any]]
    test_results: str
    reviewer_decision: str
    diff_path: str = ""
    estimated_cost: float = 0.0

    @classmethod
    def create(
        cls,
        iteration: int,
        planner_summary: str,
        executor_ops: list[dict[str, Any]],
        commands_run: list[dict[str, Any]],
        test_results: str,
        reviewer_decision: str,
        diff_path: str = "",
        estimated_cost: float = 0.0,
    ) -> LogEntry:
        return cls(
            iteration=iteration,
            timestamp=datetime.now(timezone.utc).isoformat(),
            planner_summary=planner_summary,
            executor_ops=executor_ops,
            commands_run=commands_run,
            test_results=test_results,
            reviewer_decision=reviewer_decision,
            diff_path=diff_path,
            estimated_cost=estimated_cost,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            f"## Iteration {self.iteration}",
            f"**Timestamp:** {self.timestamp}",
            f"**Estimated cost:** {self.estimated_cost}",
            "",
            "### Planner",
            self.planner_summary,
            "",
            "### Executor operations",
            str(self.executor_ops),
            "",
            "### Commands run",
            str(self.commands_run),
            "",
            "### Test results",
            self.test_results or "(none)",
            "",
            f"### Reviewer decision: {self.reviewer_decision}",
        ]
        if self.diff_path:
            lines.extend(["", f"**Diff:** {self.diff_path}"])
        lines.append("")
        return "\n".join(lines)
