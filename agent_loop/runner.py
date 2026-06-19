"""Main orchestration loop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.config import (
    DEFAULT_BRANCH_PREFIX,
    HarnessOverrides,
    ResolvedHarnessConfig,
    resolve_harness_config,
)
from agent_loop.models import (
    CommandResult,
    ExecutionContext,
    ExecutorResult,
    ExecutorResponseError,
    IterationAudit,
    IterationRecord,
    PlannerResponseError,
    PreviousIterationSummary,
    ReviewerResponseError,
    build_check_statuses,
    detect_repeat_attempt,
    write_file_paths_from_operations,
)
from agent_loop.openai_client import create_chat_client, create_chat_client_from_env
from agent_loop.tools import (
    ContractError,
    IterationArtifactWriter,
    SecurityError,
    append_iteration_log,
    apply_operations,
    build_repository_context,
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


def _snapshot_repeat_signal(
    context: ExecutionContext,
    writer: IterationArtifactWriter,
    *,
    planner_summary: str | None = None,
    executor_summary: str | None = None,
    commands: list[str] | None = None,
    write_file_paths: list[str] | None = None,
) -> None:
    signal = detect_repeat_attempt(
        planner_summary=planner_summary,
        executor_summary=executor_summary,
        commands=commands,
        write_file_paths=write_file_paths,
        previous=context.previous_iteration,
    )
    context.repeat_signal = signal
    writer.save_repeat_signal(signal.to_dict())


def _remember_previous_iteration(
    context: ExecutionContext,
    writer: IterationArtifactWriter,
    *,
    status: str,
    planner_summary: str | None = None,
    executor_summary: str | None = None,
    reviewer_decision: str | None = None,
    failed_stage: str | None = None,
    error: str | None = None,
    command_results: list[CommandResult] | None = None,
    planned_commands: list[str] | None = None,
    failed_command: str | None = None,
    write_file_paths: list[str] | None = None,
) -> None:
    context.previous_iteration = PreviousIterationSummary(
        iteration=context.iteration,
        status=status,
        artifact_dir=writer.artifact_dir_rel,
        planner_summary=planner_summary,
        executor_summary=executor_summary,
        reviewer_decision=reviewer_decision,
        failed_stage=failed_stage,
        error=error,
        checks=build_check_statuses(
            results=command_results,
            planned_commands=planned_commands,
            failed_command=failed_command,
        ),
        commands=list(planned_commands or []),
        write_file_paths=list(write_file_paths or []),
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
    command_results: list[CommandResult] | None = None,
    planned_commands: list[str] | None = None,
    failed_command: str | None = None,
    write_file_paths: list[str] | None = None,
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
    _snapshot_repeat_signal(
        context,
        writer,
        planner_summary=planner_summary,
        executor_summary=executor_summary,
        commands=planned_commands,
        write_file_paths=write_file_paths,
    )
    _remember_previous_iteration(
        context,
        writer,
        status="failed",
        planner_summary=planner_summary,
        executor_summary=executor_summary,
        failed_stage=stage,
        error=error,
        command_results=command_results,
        planned_commands=planned_commands,
        failed_command=failed_command,
        write_file_paths=write_file_paths,
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


def _effective_overrides(
    overrides: HarnessOverrides | None,
    dry_run: bool | None,
) -> HarnessOverrides:
    if overrides is not None:
        if dry_run is not None and overrides.dry_run is None:
            return HarnessOverrides(
                model=overrides.model,
                max_iterations=overrides.max_iterations,
                command_timeout_sec=overrides.command_timeout_sec,
                cost_limit=overrides.cost_limit,
                dry_run=dry_run,
            )
        return overrides
    if dry_run is not None:
        return HarnessOverrides(dry_run=dry_run)
    return HarnessOverrides()


def run_loop(
    repo_path: str | Path,
    contract_path: str | Path | None = None,
    dry_run: bool | None = None,
    planner: PlannerAgent | None = None,
    reviewer: ReviewerAgent | None = None,
    executor: ExecutorAgent | None = None,
    overrides: HarnessOverrides | None = None,
) -> int:
    repo = Path(repo_path).resolve()
    contract_file = Path(contract_path) if contract_path else repo / "agent_contract.md"
    if not contract_file.is_absolute():
        contract_file = repo / contract_file
    contract_file = contract_file.resolve()

    contract = read_contract(contract_file)
    effective_overrides = _effective_overrides(overrides, dry_run)
    resolved = resolve_harness_config(contract.to_dict(), cli=effective_overrides)
    limits = resolved.limits
    dry_run = resolved.dry_run
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

    chat_client = create_chat_client(resolved.openai_settings) if resolved.openai_settings else None
    planner_agent = planner or PlannerAgent(client=chat_client or create_chat_client_from_env())
    reviewer_agent = reviewer or ReviewerAgent(client=chat_client or create_chat_client_from_env())
    executor_agent = executor or ExecutorAgent(client=chat_client or create_chat_client_from_env())

    while context.iteration < limits.max_iterations:
        context.iteration += 1
        writer = IterationArtifactWriter(context.work_dir, context.iteration)

        context.repository_context = build_repository_context(
            repo,
            contract_path=contract_file,
        )
        if context.repository_context is not None:
            writer.save_repository_context(context.repository_context.to_dict())
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
                planned_commands=executor_result.commands,
                write_file_paths=write_file_paths_from_operations(
                    executor_result.operations
                ),
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
            failed_command = None
            if len(command_results) < len(executor_result.commands):
                failed_command = executor_result.commands[len(command_results)]
            _fail_iteration(
                context,
                writer,
                limits,
                stage="checks",
                error=str(exc),
                planner_summary=planner_result.summary,
                executor_summary=executor_result.summary,
                command_results=command_results,
                planned_commands=executor_result.commands,
                failed_command=failed_command,
                write_file_paths=write_file_paths_from_operations(
                    executor_result.operations
                ),
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
                command_results=command_results,
                planned_commands=executor_result.commands,
                write_file_paths=write_file_paths_from_operations(
                    executor_result.operations
                ),
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
                command_results=command_results,
                planned_commands=executor_result.commands,
                write_file_paths=write_file_paths_from_operations(
                    executor_result.operations
                ),
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
        write_paths = write_file_paths_from_operations(record.executor.operations)
        _snapshot_repeat_signal(
            context,
            writer,
            planner_summary=record.planner.summary,
            executor_summary=record.executor.summary,
            commands=record.executor.commands,
            write_file_paths=write_paths,
        )
        _remember_previous_iteration(
            context,
            writer,
            status="completed",
            planner_summary=record.planner.summary,
            executor_summary=record.executor.summary,
            reviewer_decision=record.reviewer.decision.value,
            command_results=record.commands,
            planned_commands=record.executor.commands,
            write_file_paths=write_paths,
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


CLI_EPILOG = """
Configuration precedence (highest to lowest):
  1. CLI flags (--model, --max-iterations, --command-timeout-sec, --cost-limit, --dry-run)
  2. agent_contract.md fields
  3. Environment variables
  4. Built-in defaults

Environment variables:
  OPENAI_API_KEY            API key for Planner, Executor, and Reviewer (required for API mode)
  OPENAI_MODEL              OpenAI model name
  AGENT_MAX_ITERATIONS      Loop iteration limit
  AGENT_COMMAND_TIMEOUT_SEC Subprocess timeout for checks
  AGENT_COST_LIMIT          Maximum estimated cumulative cost
  AGENT_DRY_RUN             true/false/1/0 for simulated vs real execution
"""


def _print_run_banner(
    resolved: ResolvedHarnessConfig,
    repo: Path,
    contract: Path,
    task_name: str,
    agent_mode: str,
) -> None:
    branch = f"{DEFAULT_BRANCH_PREFIX}{task_name}"
    print("Local Autonomous Coding Loop")
    print(f"  repo:           {repo}")
    print(f"  contract:       {contract}")
    print(f"  agent mode:     {agent_mode}")
    if agent_mode == "openai" and resolved.openai_settings:
        print(f"  model:          {resolved.openai_settings.model}")
    print(f"  task / branch:  {task_name} -> {branch}")
    print(f"  dry_run:        {resolved.dry_run}")
    print(f"  max_iterations: {resolved.limits.max_iterations}")


def _success_artifacts_path(work: Path) -> Path:
    iterations_dir = work / "iterations"
    if not iterations_dir.is_dir():
        return iterations_dir
    numbers = [
        int(path.name)
        for path in iterations_dir.iterdir()
        if path.is_dir() and path.name.isdigit()
    ]
    if not numbers:
        return iterations_dir
    return iterations_dir / str(max(numbers))


def _print_run_success(repo: Path, dry_run: bool) -> None:
    work = repo / "work"
    mode = "dry-run" if dry_run else "live"
    artifacts = _success_artifacts_path(work)
    print(f"Harness finished successfully ({mode} mode).")
    print(f"  log:       {work / 'agent_log.md'}")
    print(f"  artifacts: {artifacts}")


def _format_cli_error(exc: Exception, *, repo: Path, contract: Path) -> str:
    work = repo / "work"
    lines = [f"Harness failed: {exc}"]

    if isinstance(exc, ContractError):
        lines.append(f"Hint: verify the contract file at {contract}")
    elif isinstance(exc, SecurityError):
        lines.append(
            "Hint: ensure the repository is clean before running with --no-dry-run."
        )
        lines.append(
            "      Only allowed dirty paths (e.g. agent_contract.md) are permitted."
        )
    elif isinstance(exc, PlannerResponseError):
        lines.append(
            "Hint: see work/iterations/<n>/planner_response.json for details."
        )
    elif isinstance(exc, ExecutorResponseError):
        lines.append(
            "Hint: see work/iterations/<n>/executor_response.json for details."
        )
    elif isinstance(exc, ReviewerResponseError):
        lines.append(
            "Hint: see work/iterations/<n>/reviewer_response.json for details."
        )
    elif isinstance(exc, RuntimeError):
        lines.append(
            f"Hint: see {work / 'iterations'}/*/meta.json for failed_stage and error."
        )
        if "Max iterations reached" in str(exc):
            lines.append(
                f"      Also check {work / 'agent_log.md'} for the full iteration history."
            )

    lines.append(f"Logs: {work / 'agent_log.md'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Local Autonomous Coding Loop (Planner, Executor, Reviewer) "
            "against a repository with safe git and subprocess guards."
        ),
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Target repository path (default: current directory)",
    )
    parser.add_argument(
        "--contract",
        default="agent_contract.md",
        help="Contract path relative to the repository (default: agent_contract.md)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenAI model override; omit to use OPENAI_MODEL, then default",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum loop iterations; omit to use contract, AGENT_MAX_ITERATIONS, or default",
    )
    parser.add_argument(
        "--command-timeout-sec",
        type=int,
        default=None,
        help="Check command timeout in seconds; omit to use contract, env, or default",
    )
    parser.add_argument(
        "--cost-limit",
        type=float,
        default=None,
        help="Estimated cumulative cost limit; omit to use contract, AGENT_COST_LIMIT, or default",
    )
    dry_run_group = parser.add_mutually_exclusive_group()
    dry_run_group.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        default=None,
        help="Simulate without mutating the repository (default when unset: contract/env/default)",
    )
    dry_run_group.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Apply real file, git, and subprocess changes",
    )
    args = parser.parse_args(argv)

    overrides = HarnessOverrides(
        model=args.model,
        max_iterations=args.max_iterations,
        command_timeout_sec=args.command_timeout_sec,
        cost_limit=args.cost_limit,
        dry_run=args.dry_run,
    )

    repo = Path(args.repo).resolve()
    contract_file = Path(args.contract)
    if not contract_file.is_absolute():
        contract_file = repo / contract_file
    contract_file = contract_file.resolve()

    try:
        contract = read_contract(contract_file)
        resolved = resolve_harness_config(contract.to_dict(), cli=overrides)
        agent_mode = "openai" if resolved.openai_settings else "stub"
        _print_run_banner(
            resolved,
            repo,
            contract_file,
            contract.task_name,
            agent_mode,
        )

        exit_code = run_loop(
            repo_path=repo,
            contract_path=contract_file,
            overrides=overrides,
        )
        if exit_code == 0:
            _print_run_success(repo, resolved.dry_run)
        return exit_code
    except (
        ContractError,
        SecurityError,
        RuntimeError,
        PlannerResponseError,
        ReviewerResponseError,
        ExecutorResponseError,
    ) as exc:
        print(_format_cli_error(exc, repo=repo, contract=contract_file), file=sys.stderr)
        return 1
