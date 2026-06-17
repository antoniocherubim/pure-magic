"""Definições dos agentes Planner, Executor e Reviewer."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from agent_loop.models import AgentResponse, AgentRole, Context, ReviewerDecision
from agent_loop.prompts import (
    format_executor_prompt,
    format_planner_prompt,
    format_reviewer_prompt,
    parse_reviewer_decision,
)


class BaseAgent(ABC):
    """Interface base para agentes com suporte a stub (sem API) ou OpenAI."""

    role: AgentRole

    def __init__(self, api_client: Any | None = None) -> None:
        self.api_client = api_client

    @abstractmethod
    def call(self, context: Context, **kwargs: Any) -> AgentResponse:
        """Invoca o agente e retorna resposta estruturada."""

    def _stub_or_api(
        self,
        context: Context,
        prompt: str,
        stub_response: AgentResponse,
    ) -> AgentResponse:
        if self.api_client is None or context.dry_run:
            return stub_response
        raise NotImplementedError("OpenAI integration not yet implemented")


class PlannerAgent(BaseAgent):
    role = AgentRole.PLANNER

    def call(self, context: Context, **kwargs: Any) -> AgentResponse:
        prompt = format_planner_prompt(context.contract)
        stub = AgentResponse(
            role=self.role,
            raw_text=json.dumps(
                {
                    "summary": "Stub plan for dry-run",
                    "tasks": ["Analyze contract", "Prepare minimal changes"],
                }
            ),
            parsed={
                "summary": "Stub plan for dry-run",
                "tasks": ["Analyze contract", "Prepare minimal changes"],
            },
            tokens_used_estimate=100,
        )
        return self._stub_or_api(context, prompt, stub)


class ExecutorAgent(BaseAgent):
    role = AgentRole.EXECUTOR

    def call(self, context: Context, **kwargs: Any) -> AgentResponse:
        plan = kwargs.get("plan", {})
        constraints = context.contract.get("constraints", [])
        prompt = format_executor_prompt(plan, constraints)
        stub_parsed = {
            "operations": [],
            "commands": list(context.contract.get("checks", [])),
            "summary": "Stub executor response for dry-run",
        }
        stub = AgentResponse(
            role=self.role,
            raw_text=json.dumps(stub_parsed),
            parsed=stub_parsed,
            tokens_used_estimate=150,
        )
        return self._stub_or_api(context, prompt, stub)


class ReviewerAgent(BaseAgent):
    role = AgentRole.REVIEWER

    def call(self, context: Context, **kwargs: Any) -> AgentResponse:
        objective = context.contract.get("objective", "")
        diff = kwargs.get("diff", context.last_diff)
        test_results = kwargs.get("test_results", "")
        log_summary = kwargs.get("log_summary", "")
        prompt = format_reviewer_prompt(objective, diff, test_results, log_summary)
        decision = ReviewerDecision.CONTINUE
        stub_parsed = {"decision": decision.value, "reason": "Dry-run stub — continue"}
        stub = AgentResponse(
            role=self.role,
            raw_text=json.dumps(stub_parsed),
            parsed=stub_parsed,
            tokens_used_estimate=80,
        )
        response = self._stub_or_api(context, prompt, stub)
        response.parsed["decision"] = parse_reviewer_decision(
            response.raw_text
        ).value
        return response
