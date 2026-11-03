# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Removing a database engine is refused by the manager while the engine holds databases (4.2).

The console asked the operator to type the engine's name and then queued a job that ran ``apt-get
purge`` and ``rm -rf`` on its data whatever it held, and only that dialog stood between the
button and the databases. The guard is the manager's (rule 4): the console, the command line and
a job all remove an engine through ``uninstall``, so the refusal is there, and so is the record of
what happened, which the route used to write at the moment it queued the job, before anything
had been done or refused.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import audit
from noust.core.exceptions import DatabaseAccessError, DatabaseEngineError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import Database, get_store
from noust.managers.database import flavours
from noust.managers.database.base import DatabaseInfo
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.server.host import HostPaths

APT_REMOVAL = ("apt-get", "remove", "-y")


@pytest.fixture
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    return recorded


@pytest.fixture
def host(runner: FakeRunner) -> FakeRunner:
    """An apt server with PostgreSQL's client and nothing of the other engines."""
    runner.only_knows("apt-get", "dpkg-query", "psql", "systemctl")
    return runner


def track(name: str, engine: str = "postgresql") -> None:
    get_store().create_database(Database(name=name, engine=engine))


def running_with(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """The engine answers, and lists these databases."""
    monkeypatch.setattr(PostgresManager, "is_running", lambda self: True)
    monkeypatch.setattr(
        PostgresManager,
        "list_databases",
        lambda self: [DatabaseInfo(name=name, engine="postgresql") for name in names],
    )


def removals(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [
        call for call in runner.calls if call[:2] in {("apt-get", "remove"), ("apt-get", "purge")}
    ]


class TestWhatStopsTheRemoval:
    def test_a_database_the_store_tracks_stops_it(self, host: FakeRunner) -> None:
        track("shop")

        with pytest.raises(DatabaseEngineError, match="shop"):
            PostgresManager().uninstall()

        assert not removals(host)

    def test_nothing_is_stopped_or_touched_when_it_is_refused(self, host: FakeRunner) -> None:
        track("shop")

        with pytest.raises(DatabaseEngineError):
            PostgresManager().uninstall(purge=True)

        assert not [c for c in host.calls if c[:2] == ("systemctl", "stop")]
        assert not [c for c in host.calls if c[:2] == ("rm", "-rf")]

    def test_a_database_only_the_engine_knows_stops_it_too(
        self, host: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        running_with(monkeypatch, "orders")

        with pytest.raises(DatabaseEngineError, match="orders"):
            PostgresManager().uninstall()

        assert not removals(host)

    def test_the_error_names_every_database_once_whoever_knows_it(
        self, host: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        track("shop")
        track("blog")
        running_with(monkeypatch, "shop", "orders")

        with pytest.raises(DatabaseEngineError) as raised:
            PostgresManager().uninstall()

        assert raised.value.message == "PostgreSQL still holds 3 databases: blog, orders, shop"

    def test_a_purge_gets_no_other_answer(self, host: FakeRunner) -> None:
        track("shop")

        with pytest.raises(DatabaseEngineError, match="shop"):
            PostgresManager().uninstall(purge=True)

        assert not removals(host)

    def test_the_databases_of_another_engine_do_not_count(self, host: FakeRunner) -> None:
        track("wiki", engine="mysql")

        PostgresManager().uninstall()

        assert removals(host)

    def test_an_engine_that_is_running_and_cannot_be_read_is_not_removed(
        self, host: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Not knowing what it holds is not knowing that it holds nothing.
        monkeypatch.setattr(PostgresManager, "is_running", lambda self: True)

        def refuse(self: PostgresManager) -> list[DatabaseInfo]:
            raise DatabaseAccessError("PostgreSQL does not let Noust sign in", "Set its account.")

        monkeypatch.setattr(PostgresManager, "list_databases", refuse)

        with pytest.raises(DatabaseEngineError, match="cannot say what it holds"):
            PostgresManager().uninstall()

        assert not removals(host)

    def test_an_engine_that_is_stopped_is_judged_by_what_the_store_tracks(
        self, host: FakeRunner
    ) -> None:
        # Nothing can be asked of a stopped engine; the store is what is known.
        PostgresManager().uninstall()

        assert removals(host)

    def test_the_hint_is_a_command_that_exists(self, host: FakeRunner) -> None:
        track("shop")

        with pytest.raises(DatabaseEngineError) as raised:
            PostgresManager().uninstall()

        line = next(
            part.strip()
            for part in raised.value.details.splitlines()
            if part.strip().startswith("noust db drop")
        )
        assert line == "noust db drop shop --engine postgresql"
        # As typed: the words of the command are the command's own.
        arguments = shlex.split(line)[1:]
        assert CliRunner().invoke(root_cli, [*arguments, "--help"]).exit_code == 0


class TestTheRecordIsTheManagers:
    def test_a_removal_that_finished_is_recorded_when_it_finished(
        self, host: FakeRunner, audit_events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        PostgresManager().uninstall(purge=True)

        [(event, fields)] = audit_events
        assert event == "db.uninstall"
        assert fields["target"] == "db:postgresql"
        assert fields["outcome"] == "ok"
        assert fields["details"]["purge"] is True
        # It is recorded after the package manager ran, not when somebody asked.
        assert removals(host)

    def test_a_refusal_is_recorded_with_what_held_it(
        self, host: FakeRunner, audit_events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        track("shop")

        with pytest.raises(DatabaseEngineError):
            PostgresManager().uninstall()

        [(event, fields)] = audit_events
        assert event == "db.uninstall"
        assert fields["outcome"] == "denied"
        assert fields["details"]["databases"] == ["shop"]

    def test_a_removal_that_failed_is_recorded_as_one(
        self, host: FakeRunner, audit_events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        host.script(["apt-get", "remove"], exit_code=100, stderr="E: dpkg was interrupted")

        with pytest.raises(DatabaseEngineError, match="Failed to remove"):
            PostgresManager().uninstall()

        [(event, fields)] = audit_events
        assert (event, fields["outcome"]) == ("db.uninstall", "failure")

    def test_asking_whether_it_may_be_removed_records_a_refusal_once(
        self, host: FakeRunner, audit_events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        track("shop")
        manager = PostgresManager()

        with pytest.raises(DatabaseEngineError):
            manager.refuse_uninstall()

        assert [fields["outcome"] for _event, fields in audit_events] == ["denied"]

    def test_asking_whether_it_may_be_removed_records_nothing_when_it_may(
        self, host: FakeRunner, audit_events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        PostgresManager().refuse_uninstall()

        assert audit_events == []
        assert not removals(host)


class TestTheRepositoryNoustAdded:
    @pytest.fixture
    def apt_host(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = tmp_path / "root"
        for directory in ("etc/apt/sources.list.d", "usr/share/keyrings"):
            (root / directory).mkdir(parents=True)
        monkeypatch.setattr(flavours, "HOST", HostPaths(root))
        return root

    def write_noust_pgdg(self, root: Path) -> tuple[Path, Path]:
        source = root / "etc/apt/sources.list.d/noust-pgdg.list"
        key = root / "usr/share/keyrings/noust-pgdg.gpg"
        source.write_text(
            "deb [signed-by=/usr/share/keyrings/noust-pgdg.gpg] "
            "https://apt.postgresql.org/pub/repos/apt noble-pgdg main\n"
        )
        key.write_bytes(b"key")
        return source, key

    def test_the_source_and_the_key_noust_wrote_go_with_the_engine(
        self, host: FakeRunner, apt_host: Path
    ) -> None:
        source, key = self.write_noust_pgdg(apt_host)
        other = apt_host / "etc/apt/sources.list.d/operators.list"
        other.write_text("deb https://example.org/apt stable main\n")

        PostgresManager().uninstall()

        assert not source.exists() and not key.exists()
        assert other.exists()

    def test_a_removal_that_failed_leaves_the_repository_for_the_next_try(
        self, host: FakeRunner, apt_host: Path
    ) -> None:
        source, key = self.write_noust_pgdg(apt_host)
        host.script(["apt-get", "remove"], exit_code=100, stderr="E: held broken packages")

        with pytest.raises(DatabaseEngineError):
            PostgresManager().uninstall()

        assert source.exists() and key.exists()

    def test_a_file_with_noust_s_name_that_the_operator_wrote_stays(
        self, host: FakeRunner, apt_host: Path
    ) -> None:
        # Only a line signed by the key Noust itself exported is Noust's.
        source = apt_host / "etc/apt/sources.list.d/noust-pgdg.list"
        source.write_text("deb https://mirror.example.org/pgdg noble main\n")

        PostgresManager().uninstall()

        assert source.exists()

    def test_the_repository_of_the_other_flavour_stays(
        self, host: FakeRunner, apt_host: Path
    ) -> None:
        mariadb = apt_host / "etc/apt/sources.list.d/noust-mariadb.list"
        mariadb.write_text(
            "deb [signed-by=/usr/share/keyrings/noust-mariadb.gpg] "
            "https://deb.mariadb.org/11.4/ubuntu noble main\n"
        )
        self.write_noust_pgdg(apt_host)

        PostgresManager().uninstall()

        assert mariadb.exists()

    def test_the_files_go_through_the_filesystem_seam(
        self, host: FakeRunner, apt_host: Path
    ) -> None:
        # So that --dry-run, which swaps the seam, leaves them where they are.
        source, key = self.write_noust_pgdg(apt_host)
        recording = RecordingFileSystem()

        PostgresManager(fs=recording).uninstall()

        assert ("remove", source) in recording.changes
        assert ("remove", key) in recording.changes


class TestAnEngineOfTwoFlavours:
    def test_the_repositories_of_both_flavours_go_with_the_engine(
        self, host: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Its removal takes out the server and client packages of either flavour, so neither
        # repository has anything left to serve.
        root = tmp_path / "root"
        for directory in ("etc/apt/sources.list.d", "usr/share/keyrings"):
            (root / directory).mkdir(parents=True)
        monkeypatch.setattr(flavours, "HOST", HostPaths(root))
        for flavour in ("mariadb", "mysql"):
            (root / f"etc/apt/sources.list.d/noust-{flavour}.list").write_text(
                f"deb [signed-by=/usr/share/keyrings/noust-{flavour}.gpg] https://x.example.org/ n m\n"
            )
            (root / f"usr/share/keyrings/noust-{flavour}.gpg").write_bytes(b"k")
        host.only_knows("apt-get", "dpkg-query", "mysql")
        host.script(["dpkg-query", "-W", "-f", "${Package}\\n"], stdout="mariadb-server\n")

        MySQLManager().uninstall()

        left = sorted(path.name for path in root.rglob("noust-*"))
        assert left == []


class TestTheCommand:
    def test_the_refusal_comes_before_the_question(self, host: FakeRunner) -> None:
        track("shop")

        result = CliRunner().invoke(root_cli, ["db", "uninstall", "postgresql"], input="y\n")

        assert result.exit_code != 0
        assert "shop" in result.output
        assert "Uninstall PostgreSQL from this server?" not in result.output
        assert not removals(host)

    def test_a_removal_nobody_holds_databases_in_goes_through(self, host: FakeRunner) -> None:
        result = CliRunner().invoke(root_cli, ["db", "uninstall", "postgresql", "--force"])

        assert result.exit_code == 0, result.output
        assert removals(host)
