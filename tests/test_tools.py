from __future__ import annotations

import subprocess

import pytest

from agent_loop.models import FileOperation
from agent_loop.tools import (
    SecurityError,
    _parse_porcelain_paths,
    contract_dirty_allowance,
    ensure_safe_start,
    validate_command,
    validate_operation,
)


def test_validate_command_blocks_sudo() -> None:
    with pytest.raises(SecurityError):
        validate_command("sudo apt install pytest")


def test_validate_command_blocks_git_push() -> None:
    with pytest.raises(SecurityError):
        validate_command("git push origin main")


def test_validate_operation_blocks_env(temp_repo) -> None:
    operation = FileOperation(type="write_file", path=".env", content="SECRET=x\n")
    with pytest.raises(SecurityError):
        validate_operation(temp_repo, operation, allow_overwrite=False)


def test_ensure_safe_start_allows_untracked_contract(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text("contract\n", encoding="utf-8")
    ensure_safe_start(temp_repo, allowed_dirty_paths=("agent_contract.md",))


def test_ensure_safe_start_blocks_other_untracked_files(temp_repo) -> None:
    (temp_repo / "agent_contract.md").write_text("contract\n", encoding="utf-8")
    (temp_repo / "other.txt").write_text("other\n", encoding="utf-8")
    with pytest.raises(SecurityError):
        ensure_safe_start(temp_repo, allowed_dirty_paths=("agent_contract.md",))


def test_ensure_safe_start_blocks_modified_tracked_file(temp_repo) -> None:
    readme = temp_repo / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    with pytest.raises(SecurityError):
        ensure_safe_start(temp_repo, allowed_dirty_paths=("agent_contract.md",))


def test_parse_porcelain_paths_handles_simple_entry() -> None:
    assert _parse_porcelain_paths("?? agent_contract.md") == ["agent_contract.md"]


def test_parse_porcelain_paths_handles_rename() -> None:
    assert _parse_porcelain_paths("R  old.md -> new.md") == ["old.md", "new.md"]


def test_parse_porcelain_paths_handles_quoted_rename() -> None:
    line = 'R  "old name.md" -> "new name.md"'
    assert _parse_porcelain_paths(line) == ["old name.md", "new name.md"]


def test_parse_porcelain_paths_handles_copy() -> None:
    assert _parse_porcelain_paths("C  src.md -> dst.md") == ["src.md", "dst.md"]


def test_contract_dirty_allowance_returns_empty_for_external_contract(temp_repo, tmp_path) -> None:
    external = tmp_path / "external_contract.md"
    external.write_text("contract\n", encoding="utf-8")
    assert contract_dirty_allowance(temp_repo, external) == ()


def test_contract_dirty_allowance_returns_relative_path_for_in_repo_contract(temp_repo) -> None:
    contract = temp_repo / "configs" / "agent_contract.md"
    contract.parent.mkdir()
    contract.write_text("contract\n", encoding="utf-8")
    assert contract_dirty_allowance(temp_repo, contract) == ("configs/agent_contract.md",)


def test_ensure_safe_start_blocks_rename_of_unallowed_file(temp_repo) -> None:
    foo = temp_repo / "foo.md"
    foo.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "foo.md"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add foo"],
        cwd=temp_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "mv", "foo.md", "bar.md"], cwd=temp_repo, check=True, capture_output=True)

    with pytest.raises(SecurityError):
        ensure_safe_start(temp_repo, allowed_dirty_paths=())


def test_ensure_safe_start_allows_rename_when_both_paths_allowed(temp_repo) -> None:
    foo = temp_repo / "foo.md"
    foo.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "foo.md"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add foo"],
        cwd=temp_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "mv", "foo.md", "bar.md"], cwd=temp_repo, check=True, capture_output=True)

    ensure_safe_start(temp_repo, allowed_dirty_paths=("foo.md", "bar.md"))
