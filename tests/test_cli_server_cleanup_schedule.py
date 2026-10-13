"""
``noust server cleanup-schedule``: the policy, its timer, running it, and the history.

The managers are tested on their own (``test_storage_policy.py``); this pins the
translation: the options that become the policy, what is kept when only some are
given, the timer's own command (``--scheduled``) respecting the threshold while a
person's ``run`` does not, root for what changes, and a rehearsal that writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.managers.backup_scheduler import BackupScheduler
from tests.server_support import no_package_manager_running  # noqa: F401 - registers itself
from tests.test_cli_server import machine, store  # noqa: F401 - the fixtures the server tests share

pytestmark = pytest.mark.usefixtures("no_package_manager_running")


@pytest.fixture(autouse=True)
def units_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "systemd"
    directory.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", directory)
    return directory


def run(args: list[str]) -> Result:
    return CliRunner().invoke(root_cli, ["server", "cleanup-schedule", *args])


def policy_of(machine) -> dict:  # noqa: F811
    return machine.ctx.storage_schedule.policy().to_dict()


def test_with_no_policy_it_says_how_to_switch_one_on(machine) -> None:  # noqa: F811
    result = run([])

    assert result.exit_code == 0, result.output
    assert "does not clean its disk by itself" in result.output
    assert "cleanup-schedule set --enable" in result.output


def test_setting_the_policy_keeps_the_options_given_and_writes_the_timer(
    machine,  # noqa: F811
    units_directory: Path,
) -> None:
    result = run(
        [
            "set",
            "--enable",
            "--schedule",
            "weekly",
            "--threshold",
            "70",
            "--action",
            "journal",
            "--action",
            "pkg-cache",
            "--journal-mb",
            "300",
        ]
    )

    assert result.exit_code == 0, result.output
    assert policy_of(machine) == {
        "enabled": True,
        "schedule": "weekly",
        "threshold_percent": 70,
        "actions": ["journal", "pkg-cache"],
        "journal_mb": 300,
        "allow_docker": False,
    }
    assert (units_directory / "noust-storage-clean.timer").is_file()


def test_what_is_not_given_stays_as_it_was(machine) -> None:  # noqa: F811
    run(["set", "--enable", "--threshold", "70"])

    run(["set", "--schedule", "monthly"])

    assert policy_of(machine)["threshold_percent"] == 70
    assert policy_of(machine)["schedule"] == "monthly"
    assert policy_of(machine)["enabled"] is True


def test_switching_it_off_removes_the_timer(machine, units_directory: Path) -> None:  # noqa: F811
    run(["set", "--enable"])

    run(["set", "--disable"])

    assert policy_of(machine)["enabled"] is False
    assert not (units_directory / "noust-storage-clean.timer").exists()


def test_a_docker_action_needs_the_policy_to_accept_what_it_deletes(machine) -> None:  # noqa: F811
    refused = run(["set", "--enable", "--action", "docker-build-cache"])
    accepted = run(["set", "--enable", "--action", "docker-build-cache", "--allow-docker"])

    assert refused.exit_code != 0
    assert "delete something you may want back" in str(refused.exception)
    assert accepted.exit_code == 0, accepted.output


def test_removing_one_image_is_not_an_option() -> None:
    result = run(["set", "--action", "docker-image"])

    assert result.exit_code == 2


def test_the_timers_own_command_waits_for_the_threshold_and_a_persons_run_does_not(machine) -> None:  # noqa: F811
    run(["set", "--enable", "--threshold", "99", "--action", "pkg-cache"])

    scheduled = run(["run", "--scheduled", "--json"])
    by_hand = run(["run", "--json"])

    assert json.loads(scheduled.output)["status"] == "skipped"
    assert json.loads(by_hand.output)["status"] == "ok"
    assert json.loads(by_hand.output)["trigger"] == "policy"


def test_the_history_lists_the_runs(machine) -> None:  # noqa: F811
    run(["set", "--enable", "--threshold", "99", "--action", "pkg-cache"])
    run(["run", "--scheduled"])
    run(["run"])

    result = run(["history"])
    as_json = run(["history", "--json"])

    assert result.exit_code == 0, result.output
    assert "schedule" in result.output and "policy" in result.output
    assert [entry["trigger"] for entry in json.loads(as_json.output)] == ["policy", "schedule"]


def test_under_the_ens_profile_reading_needs_no_reason_and_changing_still_does(
    machine,  # noqa: F811
    units_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.cli import audit_policy

    monkeypatch.setattr(audit_policy, "security_profile", lambda: "ens-medium")

    for reading in (["show"], ["history"]):
        result = run(reading)
        assert result.exit_code == 0, result.output
        assert "--reason" not in result.output
    for changing in (["set", "--enable"], ["run"]):
        result = run(changing)
        assert result.exit_code == 2, result.output
        assert "--reason" in result.output
    assert policy_of(machine)["enabled"] is False
    assert not (units_directory / "noust-storage-clean.timer").exists()
