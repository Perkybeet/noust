# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The installer without a person at it (checklist N-02): an administrator and a JSON answer.

cloud-init and configuration management run ``install.sh`` with nobody to type a password or read a
banner. These tests run the real script in its dry-run mode against a described machine
(``NOUST_OS_RELEASE`` and ``NOUST_ARCH``, how the platform table is tested without those machines)
and pin: the administrator is a plain name whose password comes from a file or the environment and
is on no command line and in no output; ``--json`` puts one document on standard output and
everything else on standard error; a wrong combination is a usage error before anything is done.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "packaging" / "install.sh"
PASSWORD = "correct-horse-battery-staple-4711"

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def machine(tmp_path: Path) -> dict[str, str]:
    """An Ubuntu 24.04 amd64 server, described to the script."""
    release = tmp_path / "os-release"
    release.write_text('ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n')
    # The size of the machine and what listens on its web ports are described too: a test must not
    # depend on the memory of the laptop it runs on or on a web server it happens to have.
    return {
        "NOUST_OS_RELEASE": str(release),
        "NOUST_ARCH": "x86_64",
        "NOUST_MEMORY_MB": "4096",
        "NOUST_CPUS": "2",
        "NOUST_DISK_FREE_MB": "51200",
        "NOUST_PORT_HOLDERS": "",
        "NOUST_SERVER_ADDRESS": "203.0.113.7",
    }


@pytest.fixture
def password_file(tmp_path: Path) -> Path:
    path = tmp_path / "admin-password"
    path.write_text(PASSWORD + "\n")
    path.chmod(0o600)
    return path


def install(
    machine: dict[str, str], *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the installer in its dry-run mode with a clean environment."""
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("no sh on this machine")
    environment = {"PATH": os.environ["PATH"], **machine, **(env or {})}
    return subprocess.run(
        [shell, str(INSTALLER), "--dry-run", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        check=False,
    )


def test_json_puts_one_document_on_stdout_and_everything_else_on_stderr(
    machine: dict[str, str],
) -> None:
    done = install(machine, "--json")

    assert done.returncode == 0, done.stderr
    document = json.loads(done.stdout)
    assert done.stdout.count("\n") == 1
    assert document["status"] == "ok"
    assert document["dry_run"] is True
    assert document["method"] == "package"
    assert document["platform"] == {
        "id": "ubuntu",
        "version_id": "24.04",
        "arch": "amd64",
        "support": "supported",
    }
    assert document["admin"] is None
    assert "Noust installer: Ubuntu 24.04 LTS" in done.stderr
    assert "[dry-run]" in done.stderr


def test_the_variable_form_of_json_is_the_same(machine: dict[str, str]) -> None:
    done = install(machine, env={"NOUST_JSON": "1"})

    assert json.loads(done.stdout)["status"] == "ok"


def test_without_json_nothing_changes_about_where_the_text_goes(machine: dict[str, str]) -> None:
    done = install(machine)

    assert done.returncode == 0
    assert "Noust installer: Ubuntu 24.04 LTS" in done.stdout
    assert done.stderr == ""


def test_an_administrator_is_planned_with_its_password_read_from_a_file(
    machine: dict[str, str], password_file: Path
) -> None:
    done = install(
        machine, "--json", "--admin-user", "ops", "--admin-password-file", str(password_file)
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["admin"] == {"username": "ops", "state": "would-create"}
    assert "noust user create ops --role admin --stdin" in done.stderr


def test_the_password_in_the_environment_is_never_printed(machine: dict[str, str]) -> None:
    done = install(
        machine,
        "--json",
        "--admin-user=ops",
        env={"NOUST_ADMIN_PASSWORD": PASSWORD},
    )

    assert done.returncode == 0, done.stderr
    assert PASSWORD not in done.stdout + done.stderr


def test_the_password_file_is_never_printed_either(
    machine: dict[str, str], password_file: Path
) -> None:
    done = install(machine, "--admin-user", "ops", "--admin-password-file", str(password_file))

    assert PASSWORD not in done.stdout + done.stderr


def test_the_variables_do_what_the_options_do(machine: dict[str, str], password_file: Path) -> None:
    done = install(
        machine,
        "--json",
        env={"NOUST_ADMIN_USER": "ops", "NOUST_ADMIN_PASSWORD_FILE": str(password_file)},
    )

    assert json.loads(done.stdout)["admin"]["username"] == "ops"


@pytest.mark.parametrize(
    ("args", "env", "message"),
    [
        (["--admin-user", "ops"], {}, "needs its password"),
        (["--admin-user", "ops;id"], {"NOUST_ADMIN_PASSWORD": "x" * 20}, "letters, digits"),
        (["--admin-user", "-rf"], {"NOUST_ADMIN_PASSWORD": "x" * 20}, "letters, digits"),
        (["--admin-user", "a" * 65], {"NOUST_ADMIN_PASSWORD": "x" * 20}, "at most 64"),
        ([], {"NOUST_ADMIN_PASSWORD": "x" * 20}, "no --admin-user to give it to"),
        (
            ["--admin-user", "ops", "--admin-password-file", "/nonexistent/pw"],
            {},
            "cannot be read",
        ),
        (
            ["--admin-user", "ops"],
            {"NOUST_ADMIN_PASSWORD": "x" * 20, "NOUST_ADMIN_PASSWORD_FILE": "/etc/hostname"},
            "not both",
        ),
    ],
)
def test_a_wrong_combination_is_a_usage_error_before_anything_is_done(
    machine: dict[str, str], args: list[str], env: dict[str, str], message: str
) -> None:
    done = install(machine, *args, env=env)

    assert done.returncode == 2
    assert message in done.stderr
    assert "Noust installer:" not in done.stdout


def test_an_empty_password_file_is_refused(machine: dict[str, str], tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("")

    done = install(machine, "--admin-user", "ops", "--admin-password-file", str(empty))

    assert done.returncode == 2
    assert "is empty" in done.stderr


def test_a_platform_the_table_refuses_is_a_json_document_too(
    tmp_path: Path, machine: dict[str, str]
) -> None:
    release = tmp_path / "arch-release"
    release.write_text('ID=arch\nVERSION_ID="rolling"\nPRETTY_NAME="Arch Linux"\n')

    done = install({**machine, "NOUST_OS_RELEASE": str(release)}, "--json")

    assert done.returncode == 3
    document = json.loads(done.stdout)
    assert document["status"] == "refused"
    assert "Arch Linux" in document["error"]
    assert document["platform"]["id"] == "arch"


def test_the_help_lists_the_new_options(machine: dict[str, str]) -> None:
    done = install(machine, "--help")

    for text in ("--admin-user", "--admin-password-file", "--json", "NOUST_ADMIN_PASSWORD"):
        assert text in done.stdout
