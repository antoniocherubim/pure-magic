"""Main orchestration loop."""

from __future__ import annotations

import argparse
from pathlib import Path
from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.config import DEFAULT_BRANCH_PREFIX, build_limits
from agent_loop.models import (
    ExecutionContext,
    ExecutorResult,
    ExecutorResponseError,
    IterationAudit,
    IterationRecord,
    PlannerResponseError,
    ReviewerResponseError,
)
from agent_loop.openai_client import create_chat_client_from_env
from agent_loop.tools import (
    ContractError,
    IterationArtifactWriter,
    SecurityError,
    append_iteration_log,
    apply_operations,
    collect_diff,
    contract_dirty_allowance,
    create_or_switch_branch,
    ensure_safe_start,
    read_contract,
    run_command,
)


def _finalize_failed_iteration(
    writer: IterationArtifactWriter,
    context: ExecutionContext,
    *,
    stage: str,
    error: str,
    planner_summary: str | None = None,
    executor_summary: str | None = None,
) -> None:
    writer.save_meta(status="failed", failed_stage=stage, error=error)
    append_iteration_log(
        context.work_dir,
        IterationAudit(
            iteration=context.iteration,
            status="failed",
            failed_stage=stage,
            error=error,
            artifact_dir=writer.artifact_dir_rel,
            planner_summary=planner_summary,
            executor_summary=executor_summary,
            artifact_files=writer.artifact_files(),
        ),
    )


def _finalize_completed_iteration(
    writer: IterationArtifactWriter,
    context: ExecutionContext,
    record: IterationRecord,
    estimated_cost: float,
) -> None:
    writer.save_meta(status="completed")
    append_iteration_log(
        context.work_dir,
        IterationAudit(
            iteration=record.iteration,
            status="completed",
            failed_stage=None,
            error=None,
            artifact_dir=writer.artifact_dir_rel,
            planner_summary=record.planner.summary,
            executor_summary=record.executor.summary,
            reviewer_decision=record.reviewer.decision.value,
            estimated_cost=estimated_cost,
            artifact_files=writer.artifact_files(),
        ),
    )


def _fail_iteration(
    context: ExecutionContext,
    writer: IterationArtifactWriter,
    limits,
    *,
    stage: str,
    error: str,
    planner_summary: str | None = None,
    executor_summary: str | None = None,
) -> None:
    context.last_error = error
    context.last_failed_stage = stage
    context.failure_count += 1
    _finalize_failed_iteration(
        writer,
        context,
        stage=stage,
        error=error,
        planner_summary=planner_summary,
        executor_summary=executor_summary,
    )
    if context.failure_count >= limits.failure_limit:
        raise RuntimeError(error)


def _max_iterations_error(context: ExecutionContext) -> RuntimeError:
    if context.last_error and context.last_failed_stage:
        return RuntimeError(
            "Max iterations reached; "
            f"last failure at {context.last_failed_stage}: {context.last_error}"
        )
    return RuntimeError("Max iterations reached")


def run_loop(
    repo_path: str | Path,
    contract_path: str | Path | None = None,
    dry_run: bool = True,
    planner: PlannerAgent | None = None,
    reviewer: ReviewerAgent | None = None,
    executor: ExecutorAgent | None = None,
) -> int:
    repo = Path(repo_path).resolve()
    contract_file = Path(contract_path) if contract_path else repo / "agent_contract.md"
    if not contract_file.is_absolute():
        contract_file = repo / contract_file
    contract_file = contract_file.resolve()

    contract = read_contract(contract_file)
    limits = build_limits(contract.to_dict())
    branch_name = f"{DEFAULT_BRANCH_PREFIX}{contract.task_name}"

    if not dry_run:
        ensure_safe_start(repo, allowed_dirty_paths=contract_dirty_allowance(repo, contract_file))

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
    reviewer_agent = reviewer or ReviewerAgent(client=create_chat_client_from_env())
    executor_agent = executor or ExecutorAgent(client=create_chat_client_from_env())

    while context.iteration < limits.max_iterations:
        context.iteration += 1
        writer = IterationArtifactWriter(context.work_dir, context.iteration)

        writer.save_planner_prompt(planner_agent.build_prompt(context))
        try:
            planner_result = planner_agent.run(context)
        except PlannerResponseError as exc:
            writer.save_planner_error(str(exc))
            _fail_iteration(
                context,
                writer,
                limits,
                stage="planner",
                error=str(exc),
            )
            continue
        writer.save_planner_response(planner_result.to_dict())

        executor_request = executor_agent.build_request(context, planner_result)
        writer.save_executor_request(executor_request.to_dict())

        try:
            executor_payload = executor_agent.run(context, planner_result)
        except ExecutorResponseError as exc:
            writer.save_executor_error(str(exc))
            _fail_iteration(
                context,
                writer,
                limits,
                stage="executor",
                error=str(exc),
                planner_summary=planner_result.summary,
            )
            continue
        writer.save_executor_response(executor_payload)

        executor_result = ExecutorResult.from_dict(executor_payload)
        try:
            apply_operations(
                repo,
                executor_result.operations,
                dry_run=dry_run,
                allow_overwrite=limits.allow_overwrite,
            )
        except Exception as exc:
            writer.save_apply_operations_error(str(exc))
            _fail_iteration(
                context,
                writer,
                limits,
                stage="apply_operations",
                error=str(exc),
                planner_summary=planner_result.summary,
                executor_summary=executor_result.summary,
            )
            continue

        command_results = []
        try:
            for command in executor_result.commands:
                command_results.append(
                    run_command(
                        command,
                        cwd=repo,
                        dry_run=dry_run,
                        timeout_sec=limits.command_timeout_sec,
                    )
                )
        except Exception as exc:
            writer.save_commands([item.to_dict() for item in command_results])
            writer.save_checks_error(str(exc))
            _fail_iteration(
                context,
                writer,
                limits,
                stage="checks",
                error=str(exc),
                planner_summary=planner_result.summary,
                executor_summary=executor_result.summary,
            )
            continue
        writer.save_commands([item.to_dict() for item in command_results])

        try:
            diff_text = collect_diff(repo, dry_run=dry_run)
        except Exception as exc:
            writer.save_diff_error(str(exc))
            _fail_iteration(
                context,
                writer,
                limits,
                stage="diff",
                error=str(exc),
                planner_summary=planner_result.summary,
                executor_summary=executor_result.summary,
            )
            continue
        context.last_diff = diff_text
        if diff_text:
            writer.save_diff(diff_text)

        command_payload = [item.to_dict() for item in command_results]
        writer.save_reviewer_prompt(
            reviewer_agent.build_prompt(
                context,
                planner=planner_result,
                executor_summary=executor_result.summary,
                diff=diff_text,
                command_results=command_payload,
            )
        )

        try:
            reviewer_result = reviewer_agent.run(
                context,
                planner=planner_result,
                executor_summary=executor_result.summary,
                diff=diff_text,
                command_results=command_payload,
            )
        except ReviewerResponseError as exc:
            writer.save_reviewer_error(str(exc))
            _fail_iteration(
                context,
                writer,
                limits,
                stage="reviewer",
                error=str(exc),
                planner_summary=planner_result.summary,
                executor_summary=executor_result.summary,
            )
            continue
        writer.save_reviewer_response(reviewer_result.to_dict())

        context.cumulative_cost += limits.estimated_cost_per_iteration
        diff_path = ""
        if diff_text:
            diff_path = str(writer.iteration_dir / "diff.patch")
        record = IterationRecord(
            iteration=context.iteration,
            planner=planner_result,
            executor=executor_result,
            commands=command_results,
            reviewer=reviewer_result,
            estimated_cost=limits.estimated_cost_per_iteration,
            diff_path=diff_path,
        )
        _finalize_completed_iteration(
            writer,
            context,
            record,
            limits.estimated_cost_per_iteration,
        )

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

    raise _max_iterations_error(context)


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
    except (
        ContractError,
        SecurityError,
        RuntimeError,
        PlannerResponseError,
        ReviewerResponseError,
        ExecutorResponseError,
    ) as exc:
        print(f"error: {exc}")
        return 1
