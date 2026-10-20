# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust server migrate plan|prepare|check`` (checklist H-30).

The commands are clients of the one migration manager, like the console's API. What matters at
this level: ``plan`` changes nothing and exits 1 when the move cannot start, ``prepare`` shows
what it will copy and asks, the passphrase never comes from an argument and never appears in the
output, and ``check`` exits 1 exactly when an application is down.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import server_migrate
from noust.core import audit
from noust.core.app_state import AppState
from noust.core.exceptions import BackupError
from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.managers.server_migration import ServerMigration
from tests.test_server_migration import (
    HOST,
    FakeSelf,
    Recorder,
    add_app,
    checker,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    yield opened
    opened.close()
    NoustStore.reset_instance()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture(autouse=True)
def quiet_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def wire(
    monkeypatch: pytest.MonkeyPatch,
    store: NoustStore,
    recorder: Recorder,
    **fakes: Any,
) -> None:
    """Make the commands build a migration over the recorders."""

    def build(logger: Logger | None = None) -> ServerMigration:
        migration = checker(store, recorder, **fakes)
        migration._databases = lambda engine, database, destination: f"{database}.sql.gz"
        return migration

    monkeypatch.setattr(server_migrate, "ServerMigration", build)
    monkeypatch.setattr(server_migrate, "SelfBackupManager", lambda logger=None: FakeSelf(recorder))


def run(*args: str, input: str | None = None, env: dict[str, str] | None = None):
    return CliRunner().invoke(root_cli, ["server", "migrate", *args], input=input, env=env)


class TestPlan:
    def test_it_lists_what_would_move_and_changes_nothing(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        log: list[str],
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.targets["shop.example.com"] = ["uploads"]
        wire(monkeypatch, store, recorder)

        result = run("plan", "--to", "nas")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "shop.example.com" in text and "nas" in text and HOST in text
        assert recorder.calls == []

    def test_a_move_that_cannot_start_exits_1_and_says_why(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        log: list[str],
    ) -> None:
        recorder.destination_exists = False
        wire(monkeypatch, store, recorder)

        result = run("plan", "--to", "nas")

        assert result.exit_code == 1
        assert "does not exist" in "\n".join(log)

    def test_json_carries_the_plan(
        self, store: NoustStore, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_app(store, "shop.example.com")
        wire(monkeypatch, store, recorder)

        body = json.loads(run("plan", "--to", "nas", "--json").output)

        assert body["applications"][0]["domain"] == "shop.example.com"
        assert body["blockers"] == []


class TestPrepare:
    def test_it_shows_what_it_will_copy_and_asks_first(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        log: list[str],
    ) -> None:
        add_app(store, "shop.example.com")
        wire(monkeypatch, store, recorder)

        result = run("prepare", "--to", "nas", input="n\n")

        assert result.exit_code != 0
        assert recorder.calls == []

    def test_yes_copies_everything_and_prints_the_commands_for_the_new_server(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        log: list[str],
        tmp_path: Path,
    ) -> None:
        add_app(store, "shop.example.com")
        wire(monkeypatch, store, recorder)
        secret = tmp_path / "passphrase"
        secret.write_text("correct horse battery staple\n")
        secret.chmod(0o600)

        result = run("prepare", "--to", "nas", "--yes", "--passphrase-file", str(secret))

        assert result.exit_code == 0, result.output
        assert ("seal", "correct horse battery staple") in recorder.calls
        assert (
            f"noust restore-server --install --from-destination nas --name noust-self-{HOST}"
            in result.output
        )
        assert "noust server migrate check" in result.output
        assert "correct horse" not in result.output + "\n".join(log)

    def test_a_piece_that_failed_exits_1_and_says_to_run_it_again(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        log: list[str],
        tmp_path: Path,
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.fail[("application", "shop.example.com")] = BackupError("no space left")
        wire(monkeypatch, store, recorder)
        secret = tmp_path / "passphrase"
        secret.write_text("x\n")
        secret.chmod(0o600)

        result = run("prepare", "--to", "nas", "--yes", "--passphrase-file", str(secret))

        assert result.exit_code == 1
        assert "no space left" in "\n".join(log)
        assert "safe to repeat" in "\n".join(log)

    def test_the_passphrase_can_come_from_the_environment(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_app(store, "shop.example.com")
        wire(monkeypatch, store, recorder)

        result = run(
            "prepare",
            "--to",
            "nas",
            "--yes",
            env={"NOUST_SELF_BACKUP_PASSPHRASE": "from the environment"},
        )

        assert result.exit_code == 0, result.output
        assert ("seal", "from the environment") in recorder.calls

    def test_the_stored_passphrase_is_used_on_request_and_refused_when_there_is_none(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_app(store, "shop.example.com")
        wire(monkeypatch, store, recorder)

        missing = run("prepare", "--to", "nas", "--yes", "--stored-passphrase")
        recorder.passphrase = "the one the schedule keeps"
        used = run("prepare", "--to", "nas", "--yes", "--stored-passphrase")

        assert missing.exit_code != 0 and "No passphrase is stored" in missing.output
        assert used.exit_code == 0, used.output
        assert ("seal", "the one the schedule keeps") in recorder.calls

    def test_a_destination_that_cannot_be_used_stops_it_before_the_passphrase_is_asked(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder.reachable = False
        wire(monkeypatch, store, recorder)

        result = run("prepare", "--to", "nas", "--yes")

        assert result.exit_code != 0 and "cannot start" in result.output
        assert recorder.calls == []

    def test_only_the_named_applications_are_copied(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_app(store, "shop.example.com")
        add_app(store, "blog.example.com")
        wire(monkeypatch, store, recorder)

        run(
            "prepare",
            "--to",
            "nas",
            "--yes",
            "--app",
            "blog.example.com",
            env={"NOUST_SELF_BACKUP_PASSPHRASE": "x"},
        )

        assert ("backup", "blog.example.com") in recorder.calls
        assert ("backup", "shop.example.com") not in recorder.calls

    def test_no_data_leaves_the_data_out(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.targets["shop.example.com"] = ["uploads"]
        wire(monkeypatch, store, recorder)

        run(
            "prepare",
            "--to",
            "nas",
            "--yes",
            "--no-data",
            env={"NOUST_SELF_BACKUP_PASSPHRASE": "x"},
        )

        assert not any(kind == "data" for kind, _name in recorder.calls)


class TestCheck:
    def test_a_server_that_answers_exits_0_and_lists_the_names_still_elsewhere(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        log: list[str],
    ) -> None:
        add_app(store, "shop.example.com")
        wire(monkeypatch, store, recorder, dns={"shop.example.com": ["198.51.100.7"]})

        result = run("check")

        assert result.exit_code == 0, result.output
        assert "shop.example.com -> 198.51.100.7" in result.output
        assert "1 need attention" in "\n".join(log) or "ok" in "\n".join(log)

    def test_an_application_that_is_down_exits_1(
        self,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        log: list[str],
    ) -> None:
        add_app(store, "shop.example.com")
        wire(
            monkeypatch,
            store,
            recorder,
            states={"shop.example.com": AppState("Stopped", False, "The unit is inactive")},
        )

        result = run("check")

        assert result.exit_code == 1
        text = "\n".join(log)
        assert "DOWN" in text or "1 down" in text
        assert "The unit is inactive" in text

    def test_json_is_the_report(
        self, store: NoustStore, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_app(store, "shop.example.com")
        wire(monkeypatch, store, recorder)

        body = json.loads(run("check", "--json").output)

        assert body["host"] == HOST and body["applications"][0]["domain"] == "shop.example.com"
