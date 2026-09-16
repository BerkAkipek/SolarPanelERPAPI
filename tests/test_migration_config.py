"""Configuration failures should identify the setting without exposing secrets."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def run_offline_migration(environment):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_missing_database_configuration_names_missing_settings():
    environment = {key: value for key, value in os.environ.items() if key not in ("DB_USER", "DB_PASSWORD", "DB_NAME")}
    result = run_offline_migration(environment)
    assert result.returncode != 0
    assert "Missing database configuration: DB_USER, DB_PASSWORD, DB_NAME" in result.stderr


@pytest.mark.parametrize("port", ["abc", "0", "65536"])
def test_invalid_port_is_understandable_and_does_not_expose_password(port):
    password = "test-secret-never-log-this"
    environment = dict(os.environ, DB_USER="test", DB_NAME="test", DB_PASSWORD=password, DB_PORT=port)
    result = run_offline_migration(environment)
    assert result.returncode != 0
    assert "DB_PORT must be an integer between 1 and 65535" in result.stderr
    assert password not in result.stdout + result.stderr
