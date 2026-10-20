# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The installer's onboarding: the administrator's address, automatic updates, the size of the machine
and where the console is (checklist N-02, N-04; decision R1-5).

``tests/test_installer_unattended.py`` pins the administrator and the JSON answer. This file pins what
was added for a server nobody is sitting at: an address for the administrator, the choice to let the
server install Noust's own releases, a look at memory, processors, disk and the web ports before
anything is installed, and the real address of the console with the link that opens its sign-in on the
access token. The real script runs in its dry-run mode against a machine described to it
(``NOUST_MEMORY_MB`` and the rest are how the platform table is tested without those machines).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "packaging" / "install.sh"

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def machine(tmp_path: Path) -> dict[str, str]:
    """An Ubuntu 24.04 amd64 server of a comfortable size, nothing on its web ports."""
    release = tmp_path / "os-release"
    release.write_text('ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n')
    # The size and the ports are described too: a test must not depend on the laptop it runs on.
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
    path.write_text("correct-horse-battery-staple-4711\n")
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


def document(done: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    loaded = json.loads(done.stdout)
    assert isinstance(loaded, dict)
    return loaded


# ---------------------------------------------------------------------------
# The administrator's address
# ---------------------------------------------------------------------------


def test_the_administrators_address_is_planned_after_the_account(
    machine: dict[str, str], password_file: Path
) -> None:
    done = install(
        machine,
        "--json",
        "--admin-user",
        "ops",
        "--admin-password-file",
        str(password_file),
        "--admin-email",
        "ops@example.com",
    )

    assert done.returncode == 0, done.stderr
    assert document(done)["admin"] == {
        "username": "ops",
        "state": "would-create",
        "email": "ops@example.com",
        "email_state": "would-set",
    }
    assert "noust user set-email ops ops@example.com" in done.stderr


def test_the_address_variable_does_what_the_option_does(
    machine: dict[str, str], password_file: Path
) -> None:
    done = install(
        machine,
        "--json",
        env={
            "NOUST_ADMIN_USER": "ops",
            "NOUST_ADMIN_PASSWORD_FILE": str(password_file),
            "NOUST_ADMIN_EMAIL": "ops@example.com",
        },
    )

    assert document(done)["admin"]["email"] == "ops@example.com"


def test_an_account_without_an_address_keeps_the_document_it_always_had(
    machine: dict[str, str], password_file: Path
) -> None:
    done = install(
        machine, "--json", "--admin-user", "ops", "--admin-password-file", str(password_file)
    )

    assert document(done)["admin"] == {"username": "ops", "state": "would-create"}


@pytest.mark.parametrize(
    "address",
    [
        "ops",
        "ops@",
        "@example.com",
        "ops@example",
        "a b@example.com",
        "ops@@example.com",
        "o;id@x.io",
        "ops@exa..mple.com",
    ],
)
def test_something_that_is_not_an_address_is_a_usage_error(
    machine: dict[str, str], password_file: Path, address: str
) -> None:
    done = install(
        machine,
        "--admin-user",
        "ops",
        "--admin-password-file",
        str(password_file),
        "--admin-email",
        address,
    )

    assert done.returncode == 2
    assert "--admin-email is an address" in done.stderr


def test_an_address_with_no_account_to_give_it_to_is_a_usage_error(
    machine: dict[str, str],
) -> None:
    done = install(machine, "--admin-email", "ops@example.com")

    assert done.returncode == 2
    assert "no --admin-user to give it to" in done.stderr


# ---------------------------------------------------------------------------
# Automatic updates
# ---------------------------------------------------------------------------


def test_automatic_updates_are_off_unless_asked_for(machine: dict[str, str]) -> None:
    done = install(machine, "--json")

    assert document(done)["auto_update"] == {"state": "off"}
    assert "self-update" not in done.stderr


def test_the_flag_alone_means_patch_releases(machine: dict[str, str]) -> None:
    done = install(machine, "--json", "--auto-update")

    assert done.returncode == 0, done.stderr
    assert document(done)["auto_update"] == {
        "state": "would-enable",
        "channel": "patch",
        "window": "03:00-05:00",
        "days": "daily",
    }
    assert "noust self-update set --enable --channel patch" in done.stderr


def test_the_channel_the_hours_and_the_days_reach_the_command(machine: dict[str, str]) -> None:
    done = install(
        machine,
        "--json",
        "--auto-update=security",
        "--auto-update-window",
        "01:00-02:30",
        "--auto-update-days",
        "mon,thu",
    )

    assert document(done)["auto_update"]["channel"] == "security"
    assert (
        "noust self-update set --enable --channel security --window 01:00-02:30 --days mon,thu"
        in done.stderr
    )


@pytest.mark.parametrize(
    ("value", "channel"),
    [("1", "patch"), ("true", "patch"), ("stable", "stable"), ("security", "security")],
)
def test_the_variable_names_a_channel_or_says_yes(
    machine: dict[str, str], value: str, channel: str
) -> None:
    done = install(machine, "--json", env={"NOUST_AUTO_UPDATE": value})

    assert document(done)["auto_update"]["channel"] == channel


@pytest.mark.parametrize("value", ["0", "false", "off", ""])
def test_the_variable_can_say_no(machine: dict[str, str], value: str) -> None:
    done = install(machine, "--json", env={"NOUST_AUTO_UPDATE": value})

    assert document(done)["auto_update"] == {"state": "off"}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--auto-update=nightly"], "security, patch or stable"),
        (["--auto-update", "--auto-update-window", "25:00-26:00"], "hours such as 03:00-05:00"),
        (["--auto-update", "--auto-update-window", "3-5"], "hours such as 03:00-05:00"),
        (["--auto-update", "--auto-update-days", "mon,funday"], "daily or days such as mon,thu"),
        (["--auto-update", "--auto-update-days", "mon;id"], "daily or days such as mon,thu"),
        (["--auto-update-window", "03:00-05:00"], "go with --auto-update"),
        (["--auto-update-days", "daily"], "go with --auto-update"),
    ],
)
def test_a_wrong_automatic_update_is_a_usage_error_before_anything_is_done(
    machine: dict[str, str], args: list[str], message: str
) -> None:
    done = install(machine, *args)

    assert done.returncode == 2
    assert message in done.stderr
    assert "Noust installer:" not in done.stdout


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------


def test_a_comfortable_machine_is_reported_and_asks_nothing(machine: dict[str, str]) -> None:
    done = install(machine, "--json")

    assert done.returncode == 0, done.stderr
    assert document(done)["resources"] == {
        "memory_mb": 4096,
        "cpus": 2,
        "disk_free_mb": 51200,
        "ports": {
            "80": {"state": "free", "holders": None},
            "443": {"state": "free", "holders": None},
        },
        "warnings": [],
    }
    assert "Machine: memory 4 GB, 2 processors, free disk 50 GB" in done.stderr
    assert "warning:" not in done.stderr


def test_memory_under_the_floor_refuses_with_a_json_document(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_MEMORY_MB": "256"}, "--json")

    assert done.returncode == 3
    refused = document(done)
    assert refused["status"] == "refused"
    assert "smaller than Noust can run on" in refused["error"]
    assert refused["resources"]["warnings"] == [
        "Memory is 256 MB, under the 512 MB Noust needs to run. "
        "Builds are what need it; a swap file helps (noust server swap create)."
    ]
    assert "--force" in done.stderr


def test_force_goes_on_past_the_floor_and_says_so(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_MEMORY_MB": "256"}, "--json", "--force")

    assert done.returncode == 0, done.stderr
    assert "Going on because of --force" in done.stderr
    assert document(done)["status"] == "ok"


def test_memory_between_the_floor_and_the_minimum_warns_and_goes_on_with_yes(
    machine: dict[str, str],
) -> None:
    done = install({**machine, "NOUST_MEMORY_MB": "768"}, "--json", "--yes")

    assert done.returncode == 0, done.stderr
    assert "warning: Memory is 768 MB, under the 1 GB a server is sized for." in done.stderr
    assert len(document(done)["resources"]["warnings"]) == 1


def test_a_gigabyte_is_inside_the_minimum(machine: dict[str, str]) -> None:
    """The claim made in the README: it fits in one gigabyte."""
    done = install({**machine, "NOUST_MEMORY_MB": "1024"}, "--json")

    assert done.returncode == 0
    assert document(done)["resources"]["warnings"] == []


def test_disk_under_the_floor_refuses(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_DISK_FREE_MB": "2048"}, "--json")

    assert done.returncode == 3
    assert "Free disk is 2 GB, under the 3 GB Noust needs to run" in done.stderr


def test_disk_under_the_minimum_only_warns(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_DISK_FREE_MB": "6144"}, "--json", "--yes")

    assert done.returncode == 0
    assert "Free disk is 6 GB, under the 10 GB a server is sized for." in done.stderr


def test_a_machine_that_cannot_be_measured_is_not_refused(machine: dict[str, str]) -> None:
    unknown = {**machine}
    for key in ("NOUST_MEMORY_MB", "NOUST_CPUS", "NOUST_DISK_FREE_MB"):
        unknown[key] = "unknown"

    done = install(unknown, "--json")

    assert done.returncode == 0, done.stderr
    assert document(done)["resources"]["memory_mb"] is None
    assert "memory unknown" in done.stderr


def test_the_web_server_holding_the_ports_is_not_in_the_way(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_PORT_HOLDERS": "80:nginx 443:nginx"}, "--json")

    assert done.returncode == 0
    assert document(done)["resources"]["ports"]["80"] == {
        "state": "web-server",
        "holders": "nginx",
    }
    assert "warning:" not in done.stderr


def test_another_proxy_on_a_web_port_is_named_with_what_stops_it(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_PORT_HOLDERS": "80:caddy 443:caddy"}, "--json", "--yes")

    assert done.returncode == 0
    assert "Port 80 is held by caddy, not by the web server" in done.stderr
    assert "systemctl disable --now caddy" in done.stderr
    assert document(done)["resources"]["ports"]["443"] == {"state": "other", "holders": "caddy"}


def test_a_container_publishing_a_web_port_is_told_apart(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_PORT_HOLDERS": "80:docker-proxy"}, "--json", "--yes")

    assert "Port 80 is published by a container (docker-proxy)" in done.stderr
    assert "docker ps --filter publish=80" in done.stderr
    assert document(done)["resources"]["ports"]["80"]["state"] == "container"


def test_nothing_on_a_comfortable_machine_names_a_port(machine: dict[str, str]) -> None:
    done = install(machine)

    assert done.returncode == 0
    assert "Port 80" not in done.stdout + done.stderr


# ---------------------------------------------------------------------------
# Where the console is (decision R1-5)
# ---------------------------------------------------------------------------


def test_the_console_is_given_with_the_servers_own_address_and_the_sign_in_link(
    machine: dict[str, str],
) -> None:
    done = install(machine, "--json")

    assert document(done)["console"] == {
        "state": "skipped",
        "address": "127.0.0.1:8080",
        "url": "http://127.0.0.1:8080/login?with=token",
        "tunnel": "ssh -N -L 8080:127.0.0.1:8080 root@203.0.113.7",
    }
    assert "ssh -N -L 8080:127.0.0.1:8080 root@203.0.113.7" in done.stderr
    assert "http://127.0.0.1:8080/login?with=token" in done.stderr


def test_the_address_sshd_recorded_is_the_one_printed(machine: dict[str, str]) -> None:
    without = {key: value for key, value in machine.items() if key != "NOUST_SERVER_ADDRESS"}

    done = install(without, "--json", env={"SSH_CONNECTION": "198.51.100.9 51234 192.0.2.44 22"})

    assert document(done)["console"]["tunnel"] == "ssh -N -L 8080:127.0.0.1:8080 root@192.0.2.44"


def test_an_address_with_a_shell_in_it_is_not_printed(machine: dict[str, str]) -> None:
    done = install({**machine, "NOUST_SERVER_ADDRESS": "1.2.3.4;reboot"}, "--json")

    assert "reboot" not in document(done)["console"]["tunnel"]


def test_a_server_that_gets_no_console_has_no_link(machine: dict[str, str]) -> None:
    done = install(machine, "--json", "--no-console")

    assert document(done)["console"] == {
        "state": "skipped",
        "address": None,
        "url": None,
        "tunnel": None,
    }


def test_the_help_lists_the_onboarding_options(machine: dict[str, str]) -> None:
    done = install(machine, "--help")

    for text in (
        "--admin-email",
        "--auto-update",
        "--auto-update-window",
        "--auto-update-days",
        "NOUST_ADMIN_EMAIL",
        "NOUST_AUTO_UPDATE",
        "NOUST_MEMORY_MB",
        "docs/requirements.md",
    ):
        assert text in done.stdout
