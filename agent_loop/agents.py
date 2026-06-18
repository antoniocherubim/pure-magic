"""Planner, Executor, and Reviewer agents for the autonomous coding loop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from agent_loop.models import (
    ExecutionContext,
    ExecutorRequest,
    ExecutorResponseError,
    PlannerResponseError,
    PlannerResult,
    ReviewerDecision,
    ReviewerResponseError,
    ReviewerResult,
)
from agent_loop.openai_client import ChatCompletionClient
from agent_loop.prompts import (
    format_executor_prompt,
    format_planner_prompt,
    format_reviewer_prompt,
    parse_executor_response,
    parse_planner_response,
    parse_reviewer_response,
    validate_executor_response,
    validate_planner_response,
    validate_reviewer_response,
)


PlannerResponder = Callable[[ExecutionContext], dict[str, Any]]
ReviewerResponder = Callable[[ExecutionContext, dict[str, Any]], dict[str, Any]]
ExecutorProvider = Callable[[ExecutorRequest], dict[str, Any]]


@dataclass(slots=True)
class PlannerAgent:
    """Builds the planner prompt and optionally calls a model client."""

    responder: PlannerResponder | None = None
    client: ChatCompletionClient | None = None

    def build_prompt(self, context: ExecutionContext) -> str:
        return format_planner_prompt(
            context.contract.to_dict(),
            previous_iteration=context.previous_iteration,
        )

    def run(self, context: ExecutionContext) -> PlannerResult:
        if self.responder is not None:
            payload = self.responder(context)
            return self._to_result(payload)

        if self.client is not None:
            prompt = self.build_prompt(context)
            try:
                raw = self.client.complete(prompt=prompt)
            except Exception as exc:
                raise PlannerResponseError(f"Planner API call failed: {exc}") from exc
            payload = parse_planner_response(raw)
            errors = validate_planner_response(payload)
            if errors:
                raise PlannerResponseError("; ".join(errors))
            return self._to_result(payload)

        return PlannerResult(
            summary="Break the objective into the smallest safe implementation step.",
            tasks=[
                "Read the contract and constraints",
                "Prepare the smallest code or file update",
                "Run the requested verification commands",
            ],
        )

    @staticmethod
    def _to_result(payload: dict[str, Any]) -> PlannerResult:
        return PlannerResult(
            summary=str(payload.get("summary", "")),
            tasks=[str(item) for item in payload.get("tasks", [])],
        )


@dataclass(slots=True)
class ReviewerAgent:
    """Builds the reviewer prompt and optionally calls a model client."""

    responder: ReviewerResponder | None = None
    client: ChatCompletionClient | None = None

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
        if self.responder is not None:
            payload = self.responder(
                context,
                {
                    "planner": planner.to_dict(),
                    "executor_summary": executor_summary,
                    "diff": diff,
                    "command_results": command_results,
                },
            )
            return self._to_result(payload, strict=False)

        if self.client is not None:
            prompt = self.build_prompt(
                context,
                planner=planner,
                executor_summary=executor_summary,
                diff=diff,
                command_results=command_results,
            )
            try:
                raw = self.client.complete(prompt=prompt)
            except Exception as exc:
                raise ReviewerResponseError(f"Reviewer API call failed: {exc}") from exc
            payload = parse_reviewer_response(raw)
            errors = validate_reviewer_response(payload)
            if errors:
                raise ReviewerResponseError("; ".join(errors))
            return self._to_result(payload, strict=True)

        if all(item["returncode"] == 0 for item in command_results):
            return ReviewerResult(
                decision=ReviewerDecision.OBJECTIVE_COMPLETE,
                reason="All requested checks passed in the current iteration.",
            )
        return ReviewerResult(
            decision=ReviewerDecision.REVISE,
            reason="At least one check failed and the plan should be revised.",
        )

    @staticmethod
    def _to_result(payload: dict[str, Any], *, strict: bool) -> ReviewerResult:
        decision_text = str(payload.get("decision", ReviewerDecision.CONTINUE.value))
        try:
            decision = ReviewerDecision(decision_text.strip().upper())
        except ValueError:
            if strict:
                raise ReviewerResponseError(
                    "decision must be one of CONTINUE, REVISE, OBJECTIVE_COMPLETE"
                )
            decision = ReviewerDecision.CONTINUE
        return ReviewerResult(decision=decision, reason=str(payload.get("reason", "")))


@dataclass(slots=True)
class ExecutorAgent:
    """Builds the executor prompt and optionally calls a model client."""

    provider: ExecutorProvider | None = None
    client: ChatCompletionClient | None = None

    def build_prompt(self, context: ExecutionContext, planner: PlannerResult) -> str:
        return format_executor_prompt(
            objective=context.contract.objective,
            plan=planner.to_dict(),
            constraints=context.contract.constraints,
            previous_iteration=context.previous_iteration,
        )

    def build_request(
        self,
        context: ExecutionContext,
        planner: PlannerResult,
    ) -> ExecutorRequest:
        prompt = self.build_prompt(context, planner)
        return ExecutorRequest(
            objective=context.contract.objective,
            plan=planner,
            constraints=list(context.contract.constraints),
            allowed_commands=list(context.contract.checks),
            branch=context.branch,
            iteration=context.iteration,
            repo_path=str(context.repo_path),
            executor_prompt=prompt,
            previous_iteration=context.previous_iteration,
        )

    def run(self, context: ExecutionContext, planner: PlannerResult) -> dict[str, Any]:
        request = self.build_request(context, planner)

        if self.provider is not None:
            payload = dict(self.provider(request))
            return self._finalize_payload(payload, request)

        if self.client is not None:
            try:
                raw = self.client.complete(prompt=request.executor_prompt)
            except Exception as exc:
                raise ExecutorResponseError(f"Executor API call failed: {exc}") from exc
            payload = parse_executor_response(raw)
            return self._finalize_payload(payload, request)

        return self._finalize_payload(
            {
                "operations": [],
                "commands": list(request.allowed_commands),
                "summary": "No external executor was configured, so no file changes were proposed.",
            },
            request,
        )

    @staticmethod
    def _finalize_payload(payload: dict[str, Any], request: ExecutorRequest) -> dict[str, Any]:
        payload.setdefault("executor_request", request.to_dict())
        errors = validate_executor_response(payload, allowed_commands=request.allowed_commands)
        if errors:
            raise ExecutorResponseError("; ".join(errors))
        return payload


def dumps_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)
