from __future__ import annotations

from pathlib import Path

import pytest

from agent_loop.models import RepositoryContext
from agent_loop.prompts import format_planner_prompt, format_repository_context
from agent_loop.tools import (
    MAX_SNIPPET_BYTES,
    MAX_SNIPPET_FILES,
    MAX_TEST_FILES,
    build_repository_context,
)


def _seed_repo(repo: Path) -> None:
    (repo / "README.md").write_text("# Demo\n\nProject overview.\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("pytest>=8.0.0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (repo / "agent_contract.md").write_text("---\nobjective: test\n---\n", encoding="utf-8")
    (repo / "roadmap_geometria.md").write_text("# Roadmap\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_alpha.py").write_text("def test_alpha():\n    assert True\n", encoding="utf-8")
    (repo / "tests" / "test_beta.py").write_text("def test_beta():\n    assert True\n", encoding="utf-8")
    (repo / "venv").mkdir()
    (repo / "venv" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    (repo / "work").mkdir()
    (repo / "work" / "agent_log.md").write_text("log\n", encoding="utf-8")


def test_build_repository_context_detects_key_files(tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    _seed_repo(repo)

    context = build_repository_context(repo)

    assert context is not None
    assert context.repo_name == "demo-repo"
    assert "tests" in context.top_level_dirs
    assert "README.md" in context.root_files
    assert "README.md" in context.documentation_files
    assert "agent_contract.md" in context.documentation_files
    assert "roadmap_geometria.md" in context.documentation_files
    assert "docs/guide.md" in context.documentation_files
    assert "requirements.txt" in context.config_files
    assert "pyproject.toml" in context.config_files
    assert "tests/test_alpha.py" in context.test_files
    assert "tests/test_beta.py" in context.test_files
    assert not any("venv" in path for path in context.test_files)
    assert "demo-repo" in context.summary
    assert "README.md" in context.summary


def test_build_repository_context_snippet_priority_and_excludes_contract(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    _seed_repo(repo)

    context = build_repository_context(repo)

    assert context is not None
    snippet_paths = [item["path"] for item in context.snippets]
    assert snippet_paths[0] == "README.md"
    assert "agent_contract.md" not in snippet_paths
    assert len(context.snippets) <= MAX_SNIPPET_FILES


def test_build_repository_context_skips_large_file_snippet(tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    _seed_repo(repo)
    (repo / "README.md").write_text("x" * (MAX_SNIPPET_BYTES + 1), encoding="utf-8")

    context = build_repository_context(repo)

    assert context is not None
    assert "README.md" in context.documentation_files
    assert not any(item["path"] == "README.md" for item in context.snippets)


def test_build_repository_context_truncates_sorted_test_files(tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    for index in range(MAX_TEST_FILES + 5):
        (repo / "tests" / f"test_{index:02d}.py").write_text("pass\n", encoding="utf-8")

    context = build_repository_context(repo)

    assert context is not None
    assert len(context.test_files) == MAX_TEST_FILES
    assert context.test_files == sorted(context.test_files)
    assert context.test_files[0] == "tests/test_00.py"
    assert context.test_files[-1] == f"tests/test_{MAX_TEST_FILES - 1:02d}.py"


def test_build_repository_context_returns_none_for_missing_repo(tmp_path: Path) -> None:
    assert build_repository_context(tmp_path / "missing") is None


def test_build_repository_context_returns_none_for_unreadable_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()

    def fail_iterdir(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    assert build_repository_context(repo) is None


def test_build_repository_context_partial_on_individual_file_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    _seed_repo(repo)
    original_read_text = Path.read_text

    def flaky_read_text(self, *args, **kwargs):
        if self.name == "guide.md":
            raise OSError("read failed")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    context = build_repository_context(repo)

    assert context is not None
    assert "docs/guide.md" in context.documentation_files
    assert not any(item["path"] == "docs/guide.md" for item in context.snippets)
    assert any(item["path"] == "README.md" for item in context.snippets)


def test_format_planner_prompt_includes_repository_context_block() -> None:
    contract = {
        "objective": "Test",
        "checks": ["pytest"],
        "constraints": ["Never use sudo"],
        "max_iterations": 1,
        "task_name": "ctx-test",
    }
    context = RepositoryContext(
        repo_name="demo",
        repo_path="/tmp/demo",
        top_level_dirs=["tests"],
        root_files=["README.md"],
        documentation_files=["README.md"],
        config_files=["requirements.txt"],
        test_files=["tests/test_demo.py"],
        snippets=[{"path": "README.md", "excerpt": "# Demo"}],
        summary="Repository demo with top-level dirs tests; key docs README.md; config requirements.txt; 1 test file.",
    )

    prompt = format_planner_prompt(contract, repository_context=context)

    assert "Repository context:" in prompt
    assert '"repo_name": "demo"' in prompt
    assert "tests/test_demo.py" in prompt


def test_format_planner_prompt_without_context_uses_not_available() -> None:
    contract = {
        "objective": "Test",
        "checks": ["pytest"],
        "constraints": ["Never use sudo"],
        "max_iterations": 1,
        "task_name": "ctx-test",
    }

    prompt = format_planner_prompt(contract)

    assert "Repository context:" in prompt
    assert "(not available)" in prompt


def test_format_repository_context_not_available() -> None:
    assert format_repository_context(None) == "(not available)"
