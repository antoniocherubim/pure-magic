from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "runner.py"

MINIMAL_CONTRACT = """---
objective: CLI smoke test
checks:
  - python -m pytest
constraints:
  - Never run sudo
max_iterations: 1
task_name: cli-smoke
allow_overwrite: false
dry_run: true
---
"""


def _run_cli(*args: str, repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--repo", str(repo), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


from agent_loop.runner import _success_artifacts_path


def test_success_artifacts_path_prefers_latest_iteration(tmp_path: Path) -> None:
    work = tmp_path / "work" / "iterations"
    (work / "1").mkdir(parents=True)
    (work / "2").mkdir(parents=True)
    (work / "3").mkdir(parents=True)

    assert _success_artifacts_path(tmp_path / "work") == work / "3"


def test_success_artifacts_path_falls_back_to_iterations_dir(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()

    assert _success_artifacts_path(work) == work / "iterations"


def test_cli_smoke_dry_run_stub_mode(temp_repo: Path) -> None:
    (temp_repo / "agent_contract.md").write_text(MINIMAL_CONTRACT, encoding="utf-8")

    result = _run_cli("--dry-run", repo=temp_repo)

    assert result.returncode == 0, result.stderr
    assert "agent mode:     stub" in result.stdout
    assert "dry_run:        True" in result.stdout
    assert "Harness finished successfully" in result.stdout
    assert f"artifacts: {temp_repo / 'work' / 'iterations' / '1'}" in result.stdout
    assert (temp_repo / "work" / "agent_log.md").exists()
    assert (temp_repo / "work" / "iterations" / "1" / "meta.json").exists()


def test_cli_missing_contract_prints_actionable_error(temp_repo: Path) -> None:
    result = _run_cli("--dry-run", repo=temp_repo)

    assert result.returncode == 1
    assert "Contract not found" in result.stderr
    assert "Hint: verify the contract file" in result.stderr
    assert "agent_contract.md" in result.stderr
