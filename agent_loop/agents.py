"""Lightweight Planner/Reviewer agents plus external executor bridge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent_loop.models import (
    ExecutionContext,
    PlannerResult,
    ReviewerDecision,
    ReviewerResult,
)
from agent_loop.prompts import (
    format_executor_prompt,
    format_planner_prompt,
    format_reviewer_prompt,
)


class ChatCompletionClient(Protocol):
    def responses(self) -> Any:  # pragma: no cover - protocol only
        raise NotImplementedError


PlannerResponder = Callable[[ExecutionContext], dict[str, Any]]
ReviewerResponder = Callable[[ExecutionContext, dict[str, Any]], dict[str, Any]]
ExecutorProvider = Callable[[ExecutionContext, PlannerResult, str], dict[str, Any]]


@dataclass(slots=True)
class PlannerAgent:
    """Builds the planner prompt and optionally calls a model client."""

    responder: PlannerResponder | None = None

    def build_prompt(self, context: ExecutionContext) -> str:
        return format_planner_prompt(context.contract.to_dict())

    def run(self, context: ExecutionContext) -> PlannerResult:
        if self.responder is None:
            return PlannerResult(
                summary="Break the objective into the smallest safe implementation step.",
                tasks=[
                    "Read the contract and constraints",
                    "Prepare the smallest code or file update",
                    "Run the requested verification commands",
                ],
            )
        payload = self.responder(context)
        return PlannerResult(
            summary=str(payload.get("summary", "")),
            tasks=[str(item) for item in payload.get("tasks", [])],
        )


@dataclass(slots=True)
class ReviewerAgent:
    """Builds the reviewer prompt and optionally calls a model client."""

    responder: ReviewerResponder | None = None

    def build_prompt(
        self,
        context: ExecutionContext,
        planner: PlannerResult,
        executor_summary: str,
        diff: str,
        command_results: list[dict[str, Any]],
    ) -> str:
        return format_reviewer_prompt(
            objective=context.contract.objective,
            planner_summary=planner.summary,
            executor_summary=executor_summary,
            diff=diff,
            command_results=command_results,
        )

    def run(
        self,
        context: ExecutionContext,
        planner: PlannerResult,
        executor_summary: str,
        diff: str,
        command_results: list[dict[str, Any]],
    ) -> ReviewerResult:
        if self.responder is None:
            if all(item["returncode"] == 0 for item in command_results):
                return ReviewerResult(
                    decision=ReviewerDecision.OBJECTIVE_COMPLETE,
                    reason="All requested checks passed in the current iteration.",
                )
            return ReviewerResult(
                decision=ReviewerDecision.REVISE,
                reason="At least one check failed and the plan should be revised.",
            )

        payload = self.responder(
            context,
            {
                "planner": planner.to_dict(),
                "executor_summary": executor_summary,
                "diff": diff,
                "command_results": command_results,
            },
        )
        decision_text = str(payload.get("decision", ReviewerDecision.CONTINUE.value))
        try:
            decision = ReviewerDecision(decision_text)
        except ValueError:
            decision = ReviewerDecision.CONTINUE
        return ReviewerResult(decision=decision, reason=str(payload.get("reason", "")))


@dataclass(slots=True)
class ExternalExecutorBridge:
    """Produces the executor prompt for a separate agent and accepts its JSON response."""

    provider: ExecutorProvider | None = None

    def build_prompt(self, context: ExecutionContext, planner: PlannerResult) -> str:
        return format_executor_prompt(
            objective=context.contract.objective,
            plan=planner.to_dict(),
            constraints=context.contract.constraints,
        )

    def run(self, context: ExecutionContext, planner: PlannerResult) -> dict[str, Any]:
        prompt = self.build_prompt(context, planner)
        if self.provider is None:
            return {
                "operations": [],
                "commands": list(context.contract.checks),
                "summary": "No external executor was configured, so no file changes were proposed.",
                "executor_prompt": prompt,
            }

        payload = dict(self.provider(context, planner, prompt))
        payload.setdefault("executor_prompt", prompt)
        return payload


def dumps_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)
