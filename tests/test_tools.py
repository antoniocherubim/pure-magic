from __future__ import annotations

import pytest

from agent_loop.models import FileOperation
from agent_loop.tools import SecurityError, validate_command, validate_operation


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
