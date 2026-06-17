"""Funções utilitárias seguras — git, subprocess, arquivos."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent_loop.config import (
    COMMAND_TIMEOUT_SEC,
    DANGEROUS_COMMAND_PATTERNS,
    DEFAULT_BRANCH_PREFIX,
    PROTECTED_PATHS,
)
from agent_loop.models import LogEntry
from agent_loop.prompts import parse_contract_md, validate_contract


class SecurityError(Exception):
    """Comando ou operação bloqueada pelas regras de segurança."""


class ContractError(Exception):
    """Contrato inválido ou incompleto."""


def validate_command(cmd: str) -> None:
    """Levanta SecurityError se o comando bater padrões perigosos."""
    if not cmd or not cmd.strip():
        raise SecurityError("Empty command is not allowed")
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(cmd):
            raise SecurityError(f"Dangerous command blocked: {cmd!r}")


def validate_path(path: str, allow_overwrite: bool = False) -> None:
    """Valida path de arquivo contra restrições."""
    normalized = path.replace("\\", "/")
    for protected in PROTECTED_PATHS:
        if normalized == protected or normalized.startswith(protected):
            raise SecurityError(f"Protected path blocked: {path!r}")
    if ".." in Path(path).parts:
        raise SecurityError(f"Path traversal blocked: {path!r}")


def _run_subprocess(
    args: list[str],
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def safe_git_status(repo: Path) -> tuple[bool, str]:
    """Retorna (clean, output) do git status."""
    result = _run_subprocess(["git", "status", "--porcelain"], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr.strip()}")
    output = result.stdout.strip()
    return (output == "", output)


def create_work_branch(repo: Path, branch: str, dry_run: bool = False) -> str:
    """Cria branch de trabalho com prefixo agent/."""
    if not branch.startswith(DEFAULT_BRANCH_PREFIX):
        raise SecurityError(
            f"Branch must start with {DEFAULT_BRANCH_PREFIX!r}, got {branch!r}"
        )
    if dry_run:
        return f"[dry-run] would create branch {branch}"
    result = _run_subprocess(["git", "checkout", "-b", branch], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(f"git checkout -b failed: {result.stderr.strip()}")
    return branch


def get_diff(repo: Path, dry_run: bool = False) -> str:
    """Retorna diff do repositório (staged + unstaged)."""
    if dry_run:
        return ""
    staged = _run_subprocess(["git", "diff", "--staged"], cwd=repo)
    unstaged = _run_subprocess(["git", "diff"], cwd=repo)
    parts = [staged.stdout, unstaged.stdout]
    return "\n".join(p for p in parts if p.strip())


def run_command(
    cmd: str,
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT_SEC,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Valida e executa comando via shell=False (split simples)."""
    validate_command(cmd)
    if dry_run:
        return {
            "command": cmd,
            "returncode": 0,
            "stdout": f"[dry-run] skipped: {cmd}",
            "stderr": "",
        }

    result = _run_subprocess(cmd.split(), cwd=cwd, timeout=timeout)
    return {
        "command": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def write_file(
    base_dir: Path,
    path: str,
    content: str,
    dry_run: bool = False,
    allow_overwrite: bool = False,
) -> str:
    """Escreve arquivo relativo ao repositório."""
    validate_path(path)
    target = (base_dir / path).resolve()
    base_resolved = base_dir.resolve()
    if not str(target).startswith(str(base_resolved)):
        raise SecurityError(f"Path outside repo blocked: {path!r}")

    if target.exists() and not allow_overwrite:
        raise SecurityError(f"Overwrite not allowed: {path!r}")

    if dry_run:
        return f"[dry-run] would write {path}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


def read_contract(path: Path) -> dict[str, Any]:
    """Lê e valida contrato do repositório."""
    if not path.exists():
        raise ContractError(f"Contract not found: {path}")
    text = path.read_text(encoding="utf-8")
    contract = parse_contract_md(text)
    errors = validate_contract(contract)
    if errors:
        raise ContractError("; ".join(errors))
    return contract


def append_log(log_path: Path, entry: LogEntry) -> None:
    """Append entrada incremental no log markdown."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if not log_path.exists():
        header = "# Agent Loop Log\n\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(header + entry.to_markdown())
