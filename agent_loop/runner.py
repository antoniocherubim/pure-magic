"""Orquestrador principal do ciclo de agentes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_loop.agents import ExecutorAgent, PlannerAgent, ReviewerAgent
from agent_loop.config import (
    DEFAULT_BRANCH_PREFIX,
    ESTIMATED_COST_PER_ITERATION,
    load_limits,
)
from agent_loop.models import Context, LogEntry, ReviewerDecision
from agent_loop.prompts import validate_executor_response
from agent_loop.tools import (
    append_log,
    create_work_branch,
    get_diff,
    read_contract,
    run_command,
    safe_git_status,
    write_file,
)


def run_loop(
    repo_path: str | Path,
    contract_path: str | Path | None = None,
    dry_run: bool = True,
    task_name: str | None = None,
) -> int:
    """
    Executa o ciclo Planner → Executor → Reviewer.

    Em dry-run: valida contrato, simula 1 iteração, grava log, sem mutações.
    Retorna exit code (0 = sucesso).
    """
    repo = Path(repo_path).resolve()
    contract_file = Path(contract_path) if contract_path else repo / "agent_contract.md"
    if not contract_file.is_absolute():
        contract_file = repo / contract_file

    contract = read_contract(contract_file)
    limits = load_limits(contract)
    name = task_name or contract.get("task_name", "default-task")
    branch = f"{DEFAULT_BRANCH_PREFIX}{name}"
    work_dir = repo / "work"
    log_path = work_dir / "agent_log.md"

    clean, status_output = safe_git_status(repo)
    if not clean and not dry_run:
        print(f"Aborting: unexpected dirty git state:\n{status_output}", file=sys.stderr)
        return 1

    if not dry_run:
        create_work_branch(repo, branch, dry_run=False)

    context = Context(
        task_name=name,
        branch=branch,
        iteration=0,
        contract=contract,
        limits=limits,
        cumulative_cost=0.0,
        work_dir=work_dir,
        dry_run=dry_run,
        repo_path=repo,
    )

    planner = PlannerAgent()
    executor = ExecutorAgent()
    reviewer = ReviewerAgent()

    max_iterations = limits["max_iterations"]
    cost_limit = limits["cost_limit"]

    while context.iteration < max_iterations:
        context.iteration += 1

        planner_response = planner.call(context)
        plan = planner_response.parsed

        executor_response = executor.call(context, plan=plan)
        executor_data = executor_response.parsed
        validation_errors = validate_executor_response(executor_data)
        if validation_errors:
            context.failure_count += 1
            print(
                f"Executor validation failed: {validation_errors}",
                file=sys.stderr,
            )
            if context.failure_count >= 3:
                return 1
            continue

        commands_run: list[dict] = []
        if not context.dry_run:
            allow_overwrite = limits.get("allow_overwrite", False)
            for op in executor_data.get("operations", []):
                if op.get("type") == "write_file":
                    write_file(
                        repo,
                        op["path"],
                        op.get("content", ""),
                        dry_run=False,
                        allow_overwrite=allow_overwrite,
                    )

            for cmd in executor_data.get("commands", []):
                result = run_command(cmd, repo, dry_run=False)
                commands_run.append(result)
        else:
            for cmd in executor_data.get("commands", []):
                commands_run.append(run_command(cmd, repo, dry_run=True))

        diff = get_diff(repo, dry_run=context.dry_run)
        context.last_diff = diff

        test_output = "\n".join(
            r.get("stdout", "") + r.get("stderr", "")
            for r in commands_run
        )

        reviewer_response = reviewer.call(
            context,
            diff=diff,
            test_results=test_output,
            log_summary=plan.get("summary", ""),
        )
        decision_str = reviewer_response.parsed.get("decision", ReviewerDecision.CONTINUE.value)
        try:
            decision = ReviewerDecision(decision_str)
        except ValueError:
            decision = ReviewerDecision.CONTINUE

        iteration_cost = ESTIMATED_COST_PER_ITERATION
        context.cumulative_cost += iteration_cost

        entry = LogEntry.create(
            iteration=context.iteration,
            planner_summary=plan.get("summary", ""),
            executor_ops=executor_data.get("operations", []),
            commands_run=commands_run,
            test_results=test_output,
            reviewer_decision=decision.value,
            diff_path=str(work_dir / f"diff_iter_{context.iteration}.patch") if diff else "",
            estimated_cost=iteration_cost,
        )
        append_log(log_path, entry)

        if context.cumulative_cost > cost_limit:
            print("Cost limit exceeded.", file=sys.stderr)
            return 1

        if decision == ReviewerDecision.OBJECTIVE_COMPLETE:
            return 0

        if dry_run:
            return 0

        if decision == ReviewerDecision.REVISE:
            context.failure_count += 1

    print("Max iterations reached.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local Autonomous Coding Loop — orquestrador de agentes"
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Caminho do repositório alvo (default: .)",
    )
    parser.add_argument(
        "--contract",
        default="agent_contract.md",
        help="Caminho do contrato relativo ao repo (default: agent_contract.md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Modo simulação sem mutações (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Desativa dry-run e aplica mudanças",
    )
    parser.add_argument(
        "--task-name",
        default=None,
        help="Nome da tarefa para branch agent/<task-name>",
    )
    args = parser.parse_args(argv)
    return run_loop(
        repo_path=args.repo,
        contract_path=args.contract,
        dry_run=args.dry_run,
        task_name=args.task_name,
    )


if __name__ == "__main__":
    sys.exit(main())
