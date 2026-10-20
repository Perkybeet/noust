# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust self-backup`` and ``noust restore-server`` at the terminal (3.4, H-27 and H-28).

The passphrase of a bundle is never an argument: these tests hold the commands
to that (a prompt, a file only root can read, the environment, one line of
standard input) and to what an operator reads: the exit status, the report,
and what each command needs an operator's reason for.
"""

from __future__ import annotations

import io
import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import is_read_only
from noust.cli.commands import restore_server as restore_cli
from noust.cli.commands import self_backup as self_cli
from noust.core.ens import envelope
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers import self_backup
from noust.managers.restore_server import AppOutcome, RestoreServer, ServerRestoreReport
from noust.managers.self_backup import SelfBackupManager
from tests.test_self_backup import PASSPHRASE, FakeTimers, Runner, Server, origin, server

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[Runner]:
    fake = Runner(tmp_path / "remote")
    fake.script(("systemctl", "is-active", "--quiet", "noust-web.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-monitor.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-previews.service"), exit_code=3)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


def point_at(
    monkeypatch: pytest.MonkeyPatch, made: Server, timers: FakeTimers | None = None
) -> None:
    """Make the CLI's manager look at a server's directories."""
    monkeypatch.setattr(
        SelfBackupManager, "_labels", lambda self: {"config": made.config, "state": made.state}
    )
    monkeypatch.setattr(SelfBackupManager, "directory", property(lambda self: made.bundles))
    monkeypatch.setattr(self_backup, "SYSTEMD_DIR", made.units)
    monkeypatch.setattr("noust.managers.cert_manager.CertManager.LETSENCRYPT_DIR", made.letsencrypt)
    if timers is not None:
        monkeypatch.setattr(SelfBackupManager, "scheduler", property(lambda self: timers))


@pytest.fixture
def old(tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch) -> Iterator[Server]:
    made = origin(tmp_path)
    point_at(monkeypatch, made, FakeTimers())
    monkeypatch.setenv("NOUST_SELF_BACKUP_PASSPHRASE", PASSPHRASE)
    yield made
    for connection in made.held:
        connection.close()


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buffer = io.StringIO()

    def factory(**kwargs: Any) -> Logger:
        return Logger(stream=buffer, **kwargs)

    monkeypatch.setattr(cli_app, "Logger", factory)
    return buffer


def invoke(*args: str, **kwargs: Any) -> Any:
    return CliRunner().invoke(cli_app.cli, list(args), **kwargs)


class TestPassphrase:
    def test_it_comes_from_the_environment_for_a_script(
        self, old: Server, logged: io.StringIO
    ) -> None:
        result = invoke("self-backup", "create", "--json")

        assert result.exit_code == 0, result.output + logged.getvalue()
        assert json.loads(result.output)["name"].startswith("noust-self-")

    def test_it_comes_from_a_file_only_its_owner_can_read(
        self, old: Server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logged: io.StringIO
    ) -> None:
        monkeypatch.delenv("NOUST_SELF_BACKUP_PASSPHRASE")
        file = tmp_path / "pass"
        file.write_text(PASSPHRASE + "\n")
        file.chmod(0o644)

        refused = invoke("self-backup", "create", "--passphrase-file", str(file))
        file.chmod(0o600)
        accepted = invoke("self-backup", "create", "--passphrase-file", str(file), "--json")

        assert refused.exit_code == 1 and "only its owner can read" in logged.getvalue()
        assert accepted.exit_code == 0, accepted.output
        assert not list(old.bundles.glob("*.enc")) or len(list(old.bundles.glob("*.enc"))) == 1

    def test_it_comes_from_one_line_of_standard_input(
        self, old: Server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOUST_SELF_BACKUP_PASSPHRASE")

        result = invoke("self-backup", "create", "--json", input=PASSPHRASE + "\n")

        assert result.exit_code == 0, result.output

    def test_without_one_it_says_where_it_can_be_given(
        self, old: Server, monkeypatch: pytest.MonkeyPatch, logged: io.StringIO
    ) -> None:
        monkeypatch.delenv("NOUST_SELF_BACKUP_PASSPHRASE")

        result = invoke("self-backup", "create", input="")

        assert result.exit_code == 1
        assert "--passphrase-file" in logged.getvalue()

    def test_a_passphrase_is_not_an_option_of_any_command(self) -> None:
        for command in self_cli.cli.commands.values():
            options = {opt for param in command.params for opt in getattr(param, "opts", [])}
            assert "--passphrase" not in options


class TestCommands:
    def test_create_says_what_it_holds_and_warns_about_the_passphrase(
        self, old: Server, logged: io.StringIO
    ) -> None:
        result = invoke("self-backup", "create")

        assert result.exit_code == 0, result.output + logged.getvalue()
        text = logged.getvalue()
        assert "Backed up Noust's own state" in text and "Keep the passphrase" in text
        assert PASSPHRASE not in text + result.output

    def test_list_and_verify(self, old: Server, logged: io.StringIO) -> None:
        name = json.loads(invoke("self-backup", "create", "--json").output)["name"]

        listed = json.loads(invoke("self-backup", "list", "--json").output)
        verified = invoke("self-backup", "verify", name, "--json")

        assert [entry["name"] for entry in listed] == [name]
        assert verified.exit_code == 0, verified.output
        assert json.loads(verified.output)["ok"] is True

    def test_a_wrong_passphrase_is_exit_status_1_with_the_reason(
        self, old: Server, monkeypatch: pytest.MonkeyPatch, logged: io.StringIO
    ) -> None:
        name = json.loads(invoke("self-backup", "create", "--json").output)["name"]
        monkeypatch.setenv("NOUST_SELF_BACKUP_PASSPHRASE", "another passphrase")

        result = invoke("self-backup", "verify", name)

        assert result.exit_code == 1
        assert "does not open" in logged.getvalue()

    def test_a_damaged_bundle_fails_with_every_problem(
        self, old: Server, logged: io.StringIO
    ) -> None:
        name = json.loads(invoke("self-backup", "create", "--json").output)["name"]
        path = old.bundles / name
        data = bytearray(path.read_bytes())
        data[-5] ^= 0xFF
        path.write_bytes(bytes(data))

        result = invoke("self-backup", "verify", name)

        assert result.exit_code == 1 and "integrity" in logged.getvalue()

    def test_restore_puts_the_state_back_on_this_server_and_asks_first(
        self,
        old: Server,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        runner: Runner,
        logged: io.StringIO,
    ) -> None:
        bundle = json.loads(invoke("self-backup", "create", "--json").output)["path"]
        new = server(tmp_path, "new")
        point_at(monkeypatch, new)

        declined = invoke("self-backup", "restore", bundle, input="n\n")
        nothing_yet = list(new.config.iterdir())
        restored = invoke("self-backup", "restore", bundle, "--yes", "--json")

        assert declined.exit_code == 0 and nothing_yet == []
        assert restored.exit_code == 0, restored.output + logged.getvalue()
        assert json.loads(restored.output)["files"] > 0
        assert (new.config / "config.yaml").read_text() == "web:\n  port: 8443\n"

    def test_a_restore_over_state_without_force_is_refused(
        self,
        old: Server,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        logged: io.StringIO,
    ) -> None:
        bundle = json.loads(invoke("self-backup", "create", "--json").output)["path"]

        result = invoke("self-backup", "restore", bundle, "--yes")

        assert result.exit_code == 1 and "already holds Noust state" in logged.getvalue()

    def test_delete_asks_and_removes(self, old: Server) -> None:
        name = json.loads(invoke("self-backup", "create", "--json").output)["name"]

        kept = invoke("self-backup", "delete", name, input="n\n")
        gone = invoke("self-backup", "delete", name, "--force")

        assert kept.exit_code == 0 and gone.exit_code == 0
        assert json.loads(invoke("self-backup", "list", "--json").output) == []

    def test_what_only_reads_needs_no_reason(self) -> None:
        for path, command in (
            ("self-backup list", self_cli.cli.commands["list"]),
            ("self-backup remote", self_cli.cli.commands["remote"]),
            ("self-backup schedule show", self_cli.schedule.commands["show"]),
        ):
            assert is_read_only(command, path)
        for path, command in (
            ("self-backup create", self_cli.cli.commands["create"]),
            ("self-backup restore", self_cli.cli.commands["restore"]),
            ("self-backup run", self_cli.cli.commands["run"]),
        ):
            assert not is_read_only(command, path)


class TestSchedule:
    def test_it_is_set_shown_and_removed(self, old: Server, logged: io.StringIO) -> None:
        set_it = invoke(
            "self-backup", "schedule", "set", "--schedule", "weekly", "--keep", "4", "--json"
        )
        shown = json.loads(invoke("self-backup", "schedule", "show", "--json").output)
        removed = invoke("self-backup", "schedule", "remove", "--force")

        assert set_it.exit_code == 0, set_it.output + logged.getvalue()
        assert shown["configured"] is True and shown["retention_count"] == 4
        assert shown["passphrase_stored"] is True
        assert PASSPHRASE not in set_it.output
        assert removed.exit_code == 0
        assert json.loads(invoke("self-backup", "schedule", "show", "--json").output) == {
            "configured": False,
            "timer": {},
            "passphrase_stored": True,
        }

    def test_the_stored_passphrase_can_be_kept(self, old: Server) -> None:
        invoke("self-backup", "schedule", "set")

        result = invoke(
            "self-backup", "schedule", "set", "--schedule", "hourly", "--keep-passphrase"
        )

        assert result.exit_code == 0, result.output

    def test_a_destination_that_does_not_exist_is_refused(
        self, old: Server, logged: io.StringIO
    ) -> None:
        result = invoke("self-backup", "schedule", "set", "--to", "nowhere:3")

        assert result.exit_code == 1 and "does not exist" in logged.getvalue()

    def test_run_takes_a_bundle_the_way_the_timer_does(
        self, old: Server, logged: io.StringIO
    ) -> None:
        invoke("self-backup", "schedule", "set")

        result = invoke("self-backup", "run", "--json")

        assert result.exit_code == 0, result.output + logged.getvalue()
        data = json.loads(result.output)
        assert data["verification"]["ok"] is True
        listed = json.loads(invoke("self-backup", "list", "--json").output)
        assert listed[0]["scheduled"] is True and listed[0]["name"] == data["file"]


class TestRestoreServer:
    def report(self) -> ServerRestoreReport:
        return ServerRestoreReport(
            apps=[
                AppOutcome(
                    "shop.example.com",
                    "restored",
                    "nas",
                    "x_20260101_030000",
                    gate="passed",
                    healthy=True,
                ),
                AppOutcome(
                    "blog.example.com", "failed", error="Cannot restore", evidence="line 1\nline 2"
                ),
                AppOutcome("wiki.example.com", "no_backup", notes=["Deploy it again."]),
            ],
            engines_missing=[{"engine": "postgresql", "command": "noust db install postgresql"}],
        )

    def test_the_final_report_names_each_application_and_exits_1_when_one_is_not_back(
        self, old: Server, monkeypatch: pytest.MonkeyPatch, logged: io.StringIO
    ) -> None:
        monkeypatch.setattr(RestoreServer, "run", lambda self, *a, **k: self.report_to_use)
        monkeypatch.setattr(RestoreServer, "report_to_use", self.report(), raising=False)

        result = invoke("restore-server", "--from", "x.tar.gz.enc", "--yes")

        text = result.output + logged.getvalue()
        assert result.exit_code == 1
        assert "shop.example.com" in text and "health gate passed" in text
        assert "blog.example.com" in text and "FAILED" in text and "line 2" in text
        assert "wiki.example.com" in text and "NO BACKUP" in text
        assert "noust db install postgresql" in text
        assert "--skip-state" in text

    def test_json_is_the_report(self, old: Server, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RestoreServer, "run", lambda self, *a, **k: self.report_to_use)
        monkeypatch.setattr(RestoreServer, "report_to_use", self.report(), raising=False)

        result = invoke("restore-server", "--from", "x", "--yes", "--json")

        data = json.loads(result.output)
        assert data["ok"] is False and data["counts"] == {
            "failed": 1,
            "no_backup": 1,
            "restored": 1,
        }

    def test_an_all_good_report_exits_0(self, old: Server, monkeypatch: pytest.MonkeyPatch) -> None:
        good = ServerRestoreReport(
            apps=[
                AppOutcome(
                    "shop.example.com", "restored", "local", "x", gate="passed", healthy=True
                )
            ]
        )
        monkeypatch.setattr(RestoreServer, "run", lambda self, *a, **k: good)

        assert invoke("restore-server", "--from", "x", "--yes").exit_code == 0

    def test_it_asks_before_it_stops_anything(
        self, old: Server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Any] = []
        monkeypatch.setattr(RestoreServer, "run", lambda self, *a, **k: calls.append(a))

        result = invoke("restore-server", "--from", "x", input="n\n")

        assert result.exit_code == 0 and calls == []

    def test_the_options_reach_the_flow(self, old: Server, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def run(self: RestoreServer, bundle: str, passphrase: str, **kwargs: Any) -> Any:
            seen.update(kwargs, bundle=bundle, passphrase=passphrase)
            return ServerRestoreReport()

        monkeypatch.setattr(RestoreServer, "run", run)

        invoke(
            "restore-server",
            "--from",
            "b",
            "--yes",
            "--app",
            "a.example.com",
            "--skip",
            "z.example.com",
            "--skip-state",
            "--force",
            "--accept-schema-changes",
        )

        assert seen["only"] == ("a.example.com",) and seen["skip"] == ("z.example.com",)
        assert seen["skip_state"] is True and seen["force"] is True
        assert seen["accept_schema_changes"] is True and seen["passphrase"] == PASSPHRASE

    def test_install_and_the_databases_choice_reach_the_flow(
        self, old: Server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def run(self: RestoreServer, bundle: str, passphrase: str, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return ServerRestoreReport()

        monkeypatch.setattr(RestoreServer, "run", run)

        invoke("restore-server", "--from", "b", "--yes")
        assert seen["install"] is False and seen["databases"] is True

        invoke("restore-server", "--from", "b", "--yes", "--install", "--no-databases")
        assert seen["install"] is True and seen["databases"] is False

    def test_the_report_says_what_was_installed_what_failed_and_what_became_of_each_database(
        self, old: Server, monkeypatch: pytest.MonkeyPatch, logged: io.StringIO
    ) -> None:
        from noust.managers.restore_databases import DatabaseOutcome

        report = ServerRestoreReport(
            requirements=[
                {"name": "nginx", "state": "installed", "detail": "", "command": ""},
                {"name": "docker", "state": "failed", "detail": "no repository", "command": ""},
                {
                    "name": "apache",
                    "state": "missing",
                    "detail": "",
                    "command": "apt-get install -y apache2",
                },
            ],
            roles=[
                {
                    "engine": "postgresql",
                    "state": "created",
                    "created": ["shopper"],
                    "present": [],
                    "detail": "",
                },
                {
                    "engine": "mysql",
                    "state": "waiting",
                    "created": [],
                    "present": [],
                    "detail": "not installed yet",
                },
            ],
            databases=[
                DatabaseOutcome("postgresql", "shared", "restored", source="nas", dump="s.dump"),
                DatabaseOutcome("postgresql", "lost", "no_dump", error="No dump of it was found"),
            ],
        )
        monkeypatch.setattr(RestoreServer, "run", lambda self, *a, **k: report)

        result = invoke("restore-server", "--from", "x", "--yes")

        text = result.output + logged.getvalue()
        assert "nginx was installed" in text
        assert "docker could not be installed: no repository" in text
        assert "apt-get install -y apache2" in text and "--install" in text
        assert "created shopper" in text
        assert "postgresql/shared" in text and "back from nas: s.dump" in text
        assert "NO DUMP" in text and "No dump of it was found" in text
        assert result.exit_code == 1

    def test_the_bundle_is_given_one_way_or_the_other_not_both_nor_neither(
        self, old: Server, logged: io.StringIO
    ) -> None:
        assert invoke("restore-server").exit_code == 1
        assert invoke("restore-server", "--from", "a", "--from-destination", "nas").exit_code == 1
        assert invoke("restore-server", "--from-destination", "nas").exit_code == 1
        assert "--name" in logged.getvalue()

    def test_a_plan_reads_the_bundle_and_changes_nothing(
        self, old: Server, monkeypatch: pytest.MonkeyPatch, logged: io.StringIO
    ) -> None:
        monkeypatch.setattr(
            RestoreServer,
            "plan",
            lambda self, bundle, passphrase: {
                "bundle": "b",
                "created_at": "2026-10-06",
                "host": "old",
                "noust_version": "3.4.0",
                "apps": [
                    {
                        "domain": "shop.example.com",
                        "type": "nodejs",
                        "layout": "releases",
                        "destinations": ["nas"],
                        "data_policy": True,
                    }
                ],
                "engines": ["postgresql"],
            },
        )

        result = invoke("restore-server", "--from", "b", "--plan")

        text = result.output + logged.getvalue()
        assert result.exit_code == 0
        assert "shop.example.com" in text and "backed up to nas" in text and "postgresql" in text
        assert is_read_only(restore_cli.cli, "restore-server", {"plan": True})
        assert not is_read_only(restore_cli.cli, "restore-server", {"plan": False})

    def test_the_passphrase_file_must_be_private(
        self, old: Server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logged: io.StringIO
    ) -> None:
        monkeypatch.delenv("NOUST_SELF_BACKUP_PASSPHRASE")
        file = tmp_path / "pass"
        file.write_text(PASSPHRASE)
        file.chmod(0o664)

        result = invoke("restore-server", "--from", "b", "--yes", "--passphrase-file", str(file))

        assert result.exit_code == 1 and "only its owner can read" in logged.getvalue()
        assert stat.S_IMODE(file.stat().st_mode) == 0o664
