"""Executable contracts for PostgreSQL commands published in the M1 runbooks."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WINDOWS_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = WINDOWS_GIT_BASH if WINDOWS_GIT_BASH.is_file() else shutil.which("bash")


@pytest.mark.parametrize(
    ("relative_path", "program"),
    [
        ("docs/runbooks/backup-before-m1-pilot.md", "pg_dump"),
        ("docs/runbooks/backup-before-m1-pilot.md", "pg_restore"),
        ("docs/runbooks/m1-owner-acceptance.md", "psql"),
    ],
)
def test_postgres_runbook_command_uses_container_identity_and_executes(
    relative_path: str, program: str
) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "$env:SUPERBOSS_POSTGRES_USER" not in text
    assert "$env:SUPERBOSS_POSTGRES_DB" not in text
    match = re.search(
        rf"exec -T postgres\s+`?\s*sh -ceu '([^']*\b{program}\b[^']*)'", text, re.DOTALL
    )
    assert match is not None, f"missing container-shell {program} command"
    script = match.group(1)
    assert '"$POSTGRES_USER"' in script
    assert '"$POSTGRES_DB"' in script

    if BASH is None:
        pytest.skip("Git Bash is required for the executable container-shell contract")
    probe = f'{program}() {{ printf "%s\\n" "$@"; }}\n{script}'
    environment = os.environ.copy()
    environment.pop("SUPERBOSS_POSTGRES_USER", None)
    environment.pop("SUPERBOSS_POSTGRES_DB", None)
    environment.update(POSTGRES_USER="acceptance_user", POSTGRES_DB="acceptance_db")
    completed = subprocess.run(
        [str(BASH), "-ceu", probe],
        input="SELECT 1;\n" if program == "psql" else None,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--username\nacceptance_user\n" in completed.stdout
    assert "--dbname\nacceptance_db\n" in completed.stdout
