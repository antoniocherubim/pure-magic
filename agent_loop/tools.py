"""Safe file, git, process, and log helpers."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_loop.config import (
    DANGEROUS_COMMAND_PATTERNS,
    DEFAULT_BRANCH_PREFIX,
    PROTECTED_PATHS,
)
from agent_loop.models import (
    CommandResult,
    Contract,
    FileOperation,
    IterationAudit,
    RepositoryContext,
)
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


REPO_CONTEXT_IGNORED_DIRS = frozenset({
    ".git",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "work",
    "node_modules",
    ".agents",
    ".codex",
})
REPO_CONTEXT_CONFIG_FILENAMES = frozenset({
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Makefile",
    "pytest.ini",
    "setup.cfg",
})
REPO_CONTEXT_BINARY_EXTENSIONS = frozenset({
    ".pyc",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".woff",
    ".woff2",
})
MAX_TOP_LEVEL_DIRS = 20
MAX_ROOT_FILES = 20
MAX_DOCUMENTATION_FILES = 25
MAX_CONFIG_FILES = 20
MAX_TEST_FILES = 25
MAX_SNIPPET_FILES = 5
MAX_SNIPPET_LINES = 20
MAX_SNIPPET_BYTES = 4096
SNIPPET_EXCLUDED_DOCS = frozenset({"agent_contract.md"})


def build_repository_context(
    repo_path: Path,
    *,
    contract_path: Path | None = None,
) -> RepositoryContext | None:
    """Build a compact repository summary for the Planner.

    Returns None when the repo root is missing or unreadable.
    Individual file errors produce a partial context instead of failing.
    """
    try:
        resolved = repo_path.resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    try:
        root_entries = list(resolved.iterdir())
    except OSError:
        return None

    repo_name = resolved.name
    top_level_dirs = _collect_top_level_dirs(root_entries)
    root_files = _collect_root_files(root_entries)
    documentation_files = _collect_documentation_files(resolved, contract_path)
    config_files = _collect_config_files(resolved, root_entries)
    test_files = _collect_test_files(resolved)
    snippets = _collect_snippets(resolved, documentation_files, config_files)
    summary = _build_repo_summary(
        repo_name,
        top_level_dirs,
        documentation_files,
        config_files,
        test_files,
    )
    return RepositoryContext(
        repo_name=repo_name,
        repo_path=str(resolved),
        top_level_dirs=top_level_dirs,
        root_files=root_files,
        documentation_files=documentation_files,
        config_files=config_files,
        test_files=test_files,
        snippets=snippets,
        summary=summary,
    )


def _path_has_ignored_segment(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        return True
    return any(part in REPO_CONTEXT_IGNORED_DIRS for part in relative.parts)


def _collect_top_level_dirs(root_entries: list[Path]) -> list[str]:
    names = sorted(
        entry.name
        for entry in root_entries
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name not in REPO_CONTEXT_IGNORED_DIRS
    )
    return names[:MAX_TOP_LEVEL_DIRS]


def _collect_root_files(root_entries: list[Path]) -> list[str]:
    names = sorted(
        entry.name
        for entry in root_entries
        if entry.is_file() and not entry.name.startswith(".")
    )
    return names[:MAX_ROOT_FILES]


def _collect_documentation_files(
    repo_root: Path,
    contract_path: Path | None,
) -> list[str]:
    found: set[str] = set()
    readme = repo_root / "README.md"
    if readme.is_file():
        found.add("README.md")
    roadmap = repo_root / "ROADMAP.md"
    if roadmap.is_file():
        found.add("ROADMAP.md")
    contract = repo_root / "agent_contract.md"
    if contract.is_file():
        found.add("agent_contract.md")
    if contract_path is not None:
        try:
            relative = contract_path.resolve().relative_to(repo_root.resolve()).as_posix()
            if (repo_root / relative).is_file():
                found.add(relative)
        except (OSError, ValueError):
            pass
    try:
        for path in repo_root.rglob("*"):
            if not path.is_file() or _path_has_ignored_segment(path, repo_root):
                continue
            if "roadmap" in path.name.lower() and path.suffix.lower() == ".md":
                found.add(path.relative_to(repo_root).as_posix())
    except OSError:
        pass
    docs_dir = repo_root / "docs"
    if docs_dir.is_dir():
        try:
            for path in docs_dir.rglob("*.md"):
                if path.is_file() and not _path_has_ignored_segment(path, repo_root):
                    found.add(path.relative_to(repo_root).as_posix())
        except OSError:
            pass
    return sorted(found)[:MAX_DOCUMENTATION_FILES]


def _collect_config_files(repo_root: Path, root_entries: list[Path]) -> list[str]:
    found: set[str] = set()
    for entry in root_entries:
        if entry.is_file() and entry.name in REPO_CONTEXT_CONFIG_FILENAMES:
            found.add(entry.name)
    try:
        for path in repo_root.rglob("*"):
            if not path.is_file() or _path_has_ignored_segment(path, repo_root):
                continue
            if path.name in REPO_CONTEXT_CONFIG_FILENAMES:
                found.add(path.relative_to(repo_root).as_posix())
    except OSError:
        pass
    return sorted(found)[:MAX_CONFIG_FILES]


def _collect_test_files(repo_root: Path) -> list[str]:
    found: set[str] = set()
    tests_dir = repo_root / "tests"
    if tests_dir.is_dir():
        try:
            for path in tests_dir.rglob("*.py"):
                if path.is_file() and not _path_has_ignored_segment(path, repo_root):
                    found.add(path.relative_to(repo_root).as_posix())
        except OSError:
            pass
    try:
        for path in repo_root.rglob("test_*.py"):
            if path.is_file() and not _path_has_ignored_segment(path, repo_root):
                found.add(path.relative_to(repo_root).as_posix())
    except OSError:
        pass
    return sorted(found)[:MAX_TEST_FILES]


def _snippet_candidate_paths(
    documentation_files: list[str],
    config_files: list[str],
) -> list[str]:
    candidates: list[str] = []
    if "README.md" in documentation_files:
        candidates.append("README.md")
    roadmap_docs = sorted(
        path
        for path in documentation_files
        if "roadmap" in path.lower()
        and path not in SNIPPET_EXCLUDED_DOCS
        and path != "README.md"
    )
    candidates.extend(roadmap_docs)
    other_docs = sorted(
        path
        for path in documentation_files
        if path not in candidates and path not in SNIPPET_EXCLUDED_DOCS
    )
    candidates.extend(other_docs)
    candidates.extend(sorted(config_files))
    seen: set[str] = set()
    unique: list[str] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique[:MAX_SNIPPET_FILES]


def _collect_snippets(
    repo_root: Path,
    documentation_files: list[str],
    config_files: list[str],
) -> list[dict[str, str]]:
    snippets: list[dict[str, str]] = []
    for relative_path in _snippet_candidate_paths(documentation_files, config_files):
        excerpt = _read_snippet(repo_root / relative_path)
        if excerpt is not None:
            snippets.append({"path": relative_path, "excerpt": excerpt})
    return snippets


def _read_snippet(path: Path) -> str | None:
    if path.suffix.lower() in REPO_CONTEXT_BINARY_EXTENSIONS:
        return None
    try:
        if path.stat().st_size > MAX_SNIPPET_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()[:MAX_SNIPPET_LINES]
    return "\n".join(lines)


def _build_repo_summary(
    repo_name: str,
    top_level_dirs: list[str],
    documentation_files: list[str],
    config_files: list[str],
    test_files: list[str],
) -> str:
    dirs_text = ", ".join(top_level_dirs[:5]) if top_level_dirs else "none"
    key_docs = [
        path
        for path in documentation_files
        if path == "README.md" or "roadmap" in path.lower()
    ][:3]
    if not key_docs:
        key_docs = documentation_files[:2]
    docs_text = " and ".join(key_docs) if key_docs else "none"
    config_text = ", ".join(config_files[:3]) if config_files else "none"
    test_count = len(test_files)
    test_label = "1 test file" if test_count == 1 else f"{test_count} test files"
    return (
        f"Repository {repo_name} with top-level dirs {dirs_text}; "
        f"key docs {docs_text}; config {config_text}; {test_label}."
    )


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


def iteration_artifact_dir(work_dir: Path, iteration: int) -> Path:
    return work_dir / "iterations" / str(iteration)


def write_iteration_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_iteration_json(path: Path, data: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


@dataclass(slots=True)
class IterationArtifactWriter:
    work_dir: Path
    iteration: int
    _written: list[str] = field(default_factory=list)
    iteration_dir: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.iteration_dir = iteration_artifact_dir(self.work_dir, self.iteration)
        self.iteration_dir.mkdir(parents=True, exist_ok=True)

    @property
    def artifact_dir_rel(self) -> str:
        return self.iteration_dir.relative_to(self.work_dir).as_posix()

    def artifact_files(self) -> tuple[str, ...]:
        return tuple(self._written)

    def save_planner_prompt(self, text: str) -> Path:
        return self._write_text("planner_prompt.txt", text)

    def save_repository_context(self, data: dict[str, Any]) -> Path:
        return self._write_json("repository_context.json", data)

    def save_planner_response(self, data: dict[str, Any]) -> Path:
        return self._write_json("planner_response.json", data)

    def save_planner_error(self, error: str) -> Path:
        return self.save_planner_response({"error": error})

    def save_executor_request(self, data: dict[str, Any]) -> Path:
        return self._write_json("executor_request.json", data)

    def save_executor_response(self, data: dict[str, Any]) -> Path:
        return self._write_json("executor_response.json", data)

    def save_executor_error(self, error: str) -> Path:
        return self.save_executor_response({"error": error})

    def save_reviewer_prompt(self, text: str) -> Path:
        return self._write_text("reviewer_prompt.txt", text)

    def save_reviewer_response(self, data: dict[str, Any]) -> Path:
        return self._write_json("reviewer_response.json", data)

    def save_reviewer_error(self, error: str) -> Path:
        return self.save_reviewer_response({"error": error})

    def save_apply_operations_error(self, error: str) -> Path:
        return self._write_json(
            "apply_operations_error.json",
            {"stage": "apply_operations", "error": error},
        )

    def save_checks_error(self, error: str) -> Path:
        return self._write_json(
            "checks_error.json",
            {"stage": "checks", "error": error},
        )

    def save_diff_error(self, error: str) -> Path:
        return self._write_json(
            "diff_error.json",
            {"stage": "diff", "error": error},
        )

    def save_commands(self, commands: list[dict[str, Any]]) -> Path:
        return self._write_json("commands.json", commands)

    def save_diff(self, diff_text: str) -> Path:
        return self._write_text("diff.patch", diff_text)

    def save_repeat_signal(self, data: dict[str, Any]) -> Path:
        return self._write_json("repeat_signal.json", data)

    def save_meta(
        self,
        *,
        status: str,
        failed_stage: str | None = None,
        error: str | None = None,
    ) -> Path:
        return self._write_json(
            "meta.json",
            {
                "iteration": self.iteration,
                "status": status,
                "failed_stage": failed_stage,
                "error": error,
                "artifact_dir": self.artifact_dir_rel,
                "files": list(self.artifact_files()),
            },
        )

    def _write_text(self, filename: str, content: str) -> Path:
        path = self.iteration_dir / filename
        write_iteration_text(path, content)
        self._track(filename)
        return path

    def _write_json(self, filename: str, data: dict[str, Any] | list[Any]) -> Path:
        path = self.iteration_dir / filename
        write_iteration_json(path, data)
        self._track(filename)
        return path

    def _track(self, filename: str) -> None:
        if filename not in self._written:
            self._written.append(filename)


def append_iteration_log(work_dir: Path, audit: IterationAudit) -> None:
    log_path = work_dir / "agent_log.md"
    header = ""
    if not log_path.exists():
        header = "# Agent Log\n\n"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(header)
        handle.write(audit.to_markdown())


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
