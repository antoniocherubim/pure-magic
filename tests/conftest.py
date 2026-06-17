"""Fixtures compartilhadas para testes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_EXAMPLE = PROJECT_ROOT / "agent_contract.example.md"


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Repositório git temporário com contrato e commit inicial."""
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    shutil.copy(CONTRACT_EXAMPLE, repo / "agent_contract.md")
    (repo / "README.md").write_text("# Test repo\n", encoding="utf-8")
    (repo / ".gitignore").write_text("work/\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    return repo
