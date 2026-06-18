"""Main orchestration loop."""

from __future__ import annotations

import argparse
from pathlib import Path
from agent_loop.agents import ExternalExecutorBridge, PlannerAgent, ReviewerAgent
from agent_loop.config import DEFAULT_BRANCH_PREFIX, build_limits
from agent_loop.models import ExecutionContext, ExecutorResult, IterationRecord, PlannerResponseError
from agent_loop.openai_client import create_chat_client_from_env
from agent_loop.prompts import validate_executor_response
from agent_loop.tools import (
    ContractError,
    SecurityError,
    apply_operations,
    collect_diff,
    create_or_switch_branch,
    ensure_safe_start,
    read_contract,
    run_command,
    save_iteration_artifacts,
)

def run_loop(
    repo_path: str | Path,
    contract_path: str | Path | None = None,
    dry_run: bool = True,
    planner: PlannerAgent | None = None,
    reviewer: ReviewerAgent | None = None,
    executor_bridge: ExternalExecutorBridge | None = None,
) -> int:
    repo = Path(repo_path).resolve()
    contract_file = Path(contract_path) if contract_path else repo / "agent_contract.md"
    if not contract_file.is_absolute():
        contract_file = repo / contract_file

    contract = read_contract(contract_file)
    limits = build_limits(contract.to_dict())
    branch_name = f"{DEFAULT_BRANCH_PREFIX}{contract.task_name}"

    if not dry_run:
        ensure_safe_start(repo)

    create_or_switch_branch(repo, branch_name, dry_run=dry_run)

    context = ExecutionContext(
        repo_path=repo,
        work_dir=repo / "work",
        branch=branch_name,
        contract=contract,
        limits=limits,
        dry_run=dry_run,
    )

    planner_agent = planner or PlannerAgent(client=create_chat_client_from_env())
    reviewer_agent = reviewer or ReviewerAgent()
    bridge = executor_bridge or ExternalExecutorBridge()

    while context.iteration < limits.max_iterations:
        context.iteration += 1

        try:
            planner_result = planner_agent.run(context)
        except PlannerResponseError as exc:
            context.failure_count += 1
            if context.failure_count >= limits.failure_limit:
                raise RuntimeError(str(exc)) from exc
            continue

        executor_payload = bridge.run(context, planner_result)
        validation_errors = validate_executor_response(executor_payload)
        if validation_errors:
            context.failure_count += 1
            if context.failure_count >= limits.failure_limit:
                raise RuntimeError("; ".join(validation_errors))
            continue

        executor_result = ExecutorResult.from_dict(executor_payload)
        apply_operations(
            repo,
            executor_result.operations,
            dry_run=dry_run,
            allow_overwrite=limits.allow_overwrite,
        )

        command_results = [
            run_command(
                command,
                cwd=repo,
                dry_run=dry_run,
                timeout_sec=limits.command_timeout_sec,
            )
            for command in executor_result.commands
        ]

        diff_text = collect_diff(repo, dry_run=dry_run)
        context.last_diff = diff_text

        reviewer_result = reviewer_agent.run(
            context,
            planner=planner_result,
            executor_summary=executor_result.summary,
            diff=diff_text,
            command_results=[item.to_dict() for item in command_results],
        )

        context.cumulative_cost += limits.estimated_cost_per_iteration
        record = IterationRecord(
            iteration=context.iteration,
            planner=planner_result,
            executor=executor_result,
            commands=command_results,
            reviewer=reviewer_result,
            estimated_cost=limits.estimated_cost_per_iteration,
            diff_path=str(context.work_dir / f"diff_iter_{context.iteration}.patch")
            if diff_text
            else "",
        )
        save_iteration_artifacts(context.work_dir, record, diff_text)

        if context.cumulative_cost > limits.cost_limit:
            raise RuntimeError("Cost limit exceeded")

        if dry_run:
            return 0

        if reviewer_result.decision.value == "OBJECTIVE_COMPLETE":
            return 0

        if reviewer_result.decision.value == "REVISE":
            context.failure_count += 1
            if context.failure_count >= limits.failure_limit:
                raise RuntimeError("Failure limit exceeded")

    raise RuntimeError("Max iterations reached")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Local Autonomous Coding Loop against a repository.",
    )
    parser.add_argument("--repo", default=".", help="Target repository path")
    parser.add_argument(
        "--contract",
        default="agent_contract.md",
        help="Contract path relative to the repository",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Simulate a single iteration without mutating the repository",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Allow real file and git changes",
    )
    args = parser.parse_args(argv)

    try:
        return run_loop(
            repo_path=args.repo,
            contract_path=args.contract,
            dry_run=args.dry_run,
        )
    except (ContractError, SecurityError, RuntimeError, PlannerResponseError) as exc:
        print(f"error: {exc}")
        return 1
