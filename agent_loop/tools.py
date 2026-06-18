"""Safe file, git, process, and log helpers."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from agent_loop.config import (
    DANGEROUS_COMMAND_PATTERNS,
    DEFAULT_BRANCH_PREFIX,
    PROTECTED_PATHS,
)
from agent_loop.models import CommandResult, Contract, FileOperation, IterationRecord
from agent_loop.prompts import parse_contract_md, validate_contract


class SecurityError(Exception):
    """Raised when a dangerous action is requested."""


class ContractError(Exception):
    """Raised when contract parsing or validation fails."""


def read_contract(path: Path) -> Contract:
    if not path.exists():
        raise ContractError(f"Contract not found: {path}")
    raw_contract = parse_contract_md(path.read_text(encoding="utf-8"))
    errors = validate_contract(raw_contract)
    if errors:
        raise ContractError("; ".join(errors))
    return Contract.from_dict(raw_contract)


def git_status_porcelain(repo_path: Path) -> str:
    result = _run(["git", "status", "--porcelain"], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    return result.stdout


def ensure_safe_start(
    repo_path: Path,
    *,
    allowed_dirty_paths: tuple[str, ...] = ("agent_contract.md",),
) -> None:
    status_output = git_status_porcelain(repo_path).strip()
    if not status_output:
        return

    allowed = {_normalize_repo_path(path) for path in allowed_dirty_paths}
    violations: list[str] = []
    for line in status_output.splitlines():
        if not line.strip():
            continue
        paths = _parse_porcelain_paths(line)
        if paths and all(_normalize_repo_path(path) in allowed for path in paths):
            continue
        violations.append(line)

    if violations:
        raise SecurityError(
            "Repository must be clean before agent changes are applied.\n"
            f"Current git status:\n" + "\n".join(violations)
        )


def contract_dirty_allowance(repo_path: Path, contract_file: Path) -> tuple[str, ...]:
    """Return repo-relative contract path when the contract lives inside the repo."""
    resolved_repo = repo_path.resolve()
    resolved_contract = contract_file.resolve()
    try:
        contract_rel = resolved_contract.relative_to(resolved_repo)
    except ValueError:
        return ()
    return (_normalize_repo_path(str(contract_rel)),)


def _parse_porcelain_paths(line: str) -> list[str]:
    if len(line) < 4:
        return []

    payload = line[3:]
    if " -> " in payload:
        old_part, new_part = payload.split(" -> ", 1)
        return [
            _unquote_porcelain_path(old_part.strip()),
            _unquote_porcelain_path(new_part.strip()),
        ]
    return [_unquote_porcelain_path(payload.strip())]


def _unquote_porcelain_path(path: str) -> str:
    if path.startswith('"') and path.endswith('"'):
        path = bytes(path[1:-1], "utf-8").decode("unicode_escape")
    return path


def _normalize_repo_path(path: str) -> str:
    return Path(path).as_posix().lstrip("./")


def ensure_agent_branch(branch_name: str) -> None:
    if not branch_name.startswith(DEFAULT_BRANCH_PREFIX):
        raise SecurityError(
            f"Branch must start with {DEFAULT_BRANCH_PREFIX!r}: {branch_name!r}"
        )


def create_or_switch_branch(repo_path: Path, branch_name: str, dry_run: bool) -> str:
    ensure_agent_branch(branch_name)
    if dry_run:
        return branch_name

    exists = _run(["git", "rev-parse", "--verify", branch_name], cwd=repo_path)
    if exists.returncode == 0:
        result = _run(["git", "checkout", branch_name], cwd=repo_path)
    else:
        result = _run(["git", "checkout", "-b", branch_name], cwd=repo_path)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git checkout failed")
    return branch_name


def validate_command(command: str) -> None:
    if not command.strip():
        raise SecurityError("Empty command is not allowed")
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            raise SecurityError(f"Dangerous command blocked: {command}")


def run_command(
    command: str,
    cwd: Path,
    dry_run: bool,
    timeout_sec: int,
) -> CommandResult:
    validate_command(command)
    if dry_run:
        return CommandResult(
            command=command,
            returncode=0,
            stdout=f"[dry-run] skipped `{command}`",
            stderr="",
        )

    result = _run(shlex.split(command), cwd=cwd, timeout=timeout_sec)
    return CommandResult(
        command=command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def apply_operations(
    repo_path: Path,
    operations: list[FileOperation],
    dry_run: bool,
    allow_overwrite: bool,
) -> list[str]:
    written_paths: list[str] = []
    for operation in operations:
        validate_operation(repo_path, operation, allow_overwrite=allow_overwrite)

    for operation in operations:
        if operation.type == "write_file":
            written_paths.append(
                write_file(
                    repo_path,
                    operation.path,
                    operation.content or "",
                    dry_run=dry_run,
                )
            )
    return written_paths


def validate_operation(
    repo_path: Path,
    operation: FileOperation,
    allow_overwrite: bool,
) -> None:
    validate_path(repo_path, operation.path, allow_overwrite=allow_overwrite)
    if operation.type != "write_file":
        raise SecurityError(f"Unsupported operation type: {operation.type}")


def validate_path(repo_path: Path, relative_path: str, allow_overwrite: bool) -> Path:
    normalized = relative_path.replace("\\", "/")
    for protected_path in PROTECTED_PATHS:
        if normalized == protected_path.rstrip("/") or normalized.startswith(protected_path):
            raise SecurityError(f"Protected path blocked: {relative_path}")

    target_path = (repo_path / relative_path).resolve()
    repo_root = repo_path.resolve()
    if repo_root not in target_path.parents and target_path != repo_root:
        raise SecurityError(f"Path escapes repository: {relative_path}")

    if ".." in Path(relative_path).parts:
        raise SecurityError(f"Path traversal blocked: {relative_path}")

    if target_path.exists() and not allow_overwrite:
        raise SecurityError(f"Overwrite not allowed: {relative_path}")

    return target_path


def write_file(repo_path: Path, relative_path: str, content: str, dry_run: bool) -> str:
    target_path = (repo_path / relative_path).resolve()
    if dry_run:
        return relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return relative_path


def collect_diff(repo_path: Path, dry_run: bool) -> str:
    if dry_run:
        return ""
    result = _run(["git", "diff", "--", "."], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.stdout


def save_iteration_artifacts(
    work_dir: Path,
    record: IterationRecord,
    diff_text: str,
) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    if diff_text:
        diff_path = work_dir / f"diff_iter_{record.iteration}.patch"
        diff_path.write_text(diff_text, encoding="utf-8")

    reviewer_path = work_dir / f"reviewer_iter_{record.iteration}.json"
    reviewer_path.write_text(
        json.dumps(record.reviewer.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    executor_path = work_dir / f"executor_iter_{record.iteration}.json"
    executor_path.write_text(
        json.dumps(record.executor.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    append_log(work_dir / "agent_log.md", record)


def append_log(log_path: Path, record: IterationRecord) -> None:
    header = ""
    if not log_path.exists():
        header = "# Agent Log\n\n"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(header)
        handle.write(record.to_markdown())


def _run(
    args: list[str],
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
