"""
What the console server's modelled machine answers about databases (3.3).

``scripts/console_server.py`` is the backend the console is developed and tested
against; its fake runner has to answer what the Databases area asks, or the pages
meet an error no real server would give. What is defended:

- **An engine's settings** are read from the engine's own answers (pg_settings, SHOW
  GLOBAL VARIABLES) over the files Noust writes, so a change made through the API is
  what the next read reports.
- **A database container** is found by the real discovery (``docker ps``, ``docker
  inspect``), belongs to the application its Compose project names, and its client
  runs inside it over its own databases.
- **A use Noust did not link** is found in an application's ``.env`` the way a real
  one is.
- Docker stays absent for every other caller: the adoption wizard and the dependency
  checks rely on it.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DatabaseError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App
from noust.managers.database import flavours
from noust.managers.database.detect_links import (
    AppReferences,
    Endpoint,
    detect,
    read_environment,
    references_in,
)
from noust.managers.database.instances import assign_apps, discover
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.settings import MySQLSettings, PostgresSettings, Resources
from noust.managers.server.host import HostPaths

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "console_server.py"

#: The modelled server's memory and processors, as the recommendations see them.
SERVER = Resources(memory_bytes=2 * 1024**3, cpus=2)


@pytest.fixture(scope="module")
def console() -> ModuleType:
    """
    Load the script as a module: its heavy imports are lazy, so this starts nothing.

    Returns:
        The module.
    """
    spec = importlib.util.spec_from_file_location("console_server_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def machine(console: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """
    The modelled machine: its runner over a host tree below ``tmp_path``.

    Args:
        console: The script.
        tmp_path: The test's directory, which stands in for ``/``.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The runner, installed as the process-wide one.
    """
    host = SimpleNamespace(root=tmp_path / "host", answer=lambda args: None)
    cluster = host.root / "etc" / "postgresql" / "16" / "main"
    (cluster / "conf.d").mkdir(parents=True)
    (cluster / "postgresql.conf").write_text("include_dir = 'conf.d'\n", encoding="utf-8")
    (host.root / "etc" / "mysql" / "mysql.conf.d").mkdir(parents=True)
    monkeypatch.setattr(console, "_SERVER_HOST", host)
    monkeypatch.setattr(flavours, "HOST", HostPaths(root=host.root))
    units = {
        "postgresql": console.Unit(active="active", managed=False),
        "mysql": console.Unit(active="active", managed=False),
    }
    runner = console.make_runner(units, {}, {}, [], tmp_path / "systemd")
    set_runner(runner)
    try:
        yield runner
    finally:
        set_runner(None)


class TestSettings:
    """The Settings page reads the engine and writes Noust's file."""

    def test_postgresql_reports_the_cluster_and_what_it_runs(self, machine: FakeRunner) -> None:
        report = PostgresSettings(PostgresManager(), SERVER).report()

        by_key = {view.spec.key: view for view in report.settings}
        assert report.running
        assert by_key["max_connections"].current == "100"
        assert by_key["shared_buffers"].current == "128MB"
        assert by_key["listen_addresses"].current == "localhost"
        assert by_key["shared_buffers"].recommended == "512MB"
        assert by_key["max_connections"].restart is True
        assert by_key["work_mem"].restart is False

    def test_a_postgresql_change_is_written_checked_restarted_and_read_back(
        self, machine: FakeRunner
    ) -> None:
        settings = PostgresSettings(PostgresManager(), SERVER)

        outcome = settings.apply({"max_connections": "150"})

        assert outcome.changed == ["max_connections"]
        assert outcome.action == "restart"
        assert "max_connections = 150" in settings.settings_file().read_text(encoding="utf-8")
        assert ("systemctl", "restart", "postgresql@16-main") in machine.calls
        assert any(call[-2:] == ("-C", "max_connections") for call in machine.calls), (
            "postgres -C reads the configuration before the restart"
        )
        row = {v.spec.key: v for v in settings.report().settings}["max_connections"]
        assert (row.current, row.configured) == ("150", "150")
        assert row.source is not None and row.source.endswith("conf.d/90-noust.conf")

    def test_mysql_reports_its_variables_and_applies_a_change_at_runtime(
        self, machine: FakeRunner
    ) -> None:
        settings = MySQLSettings(MySQLManager(), SERVER)
        before = {v.spec.key: v for v in settings.report().settings}

        outcome = settings.apply({"max_connections": "200", "innodb_buffer_pool_size": "256MB"})

        after = {v.spec.key: v for v in settings.report().settings}
        assert before["max_connections"].current == "151"
        assert before["innodb_buffer_pool_size"].current == "128MB"
        assert outcome.action == "runtime"
        assert (after["max_connections"].current, after["max_connections"].configured) == (
            "200",
            "200",
        )
        assert after["innodb_buffer_pool_size"].current == "256MB"


class TestDatabaseContainer:
    """One Compose service of a seeded application runs PostgreSQL."""

    def test_discovery_finds_it_and_the_application_its_project_names(
        self, console: ModuleType, machine: FakeRunner
    ) -> None:
        found = discover(console._DockerInstalled(machine))
        owned = assign_apps(
            found, [SimpleNamespace(domain="catalogo.example.org", compose_project=None)]
        )

        (instance,) = owned
        assert instance.key == "postgresql@catalogo-example-org.postgres"
        assert instance.container == "catalogo-example-org-postgres-1"
        assert instance.image == "postgres:16-alpine"
        assert instance.running
        assert instance.port == console.DB_CONTAINER_HOST_PORT
        assert instance.app == "catalogo.example.org"

    def test_docker_stays_absent_for_everything_that_does_not_ask_discovery(
        self, machine: FakeRunner
    ) -> None:
        assert not machine.exists("docker")
        assert discover(machine) == []

    def test_its_client_runs_inside_it_over_its_own_databases(
        self, console: ModuleType, machine: FakeRunner
    ) -> None:
        (instance,) = discover(console._DockerInstalled(machine))
        manager = PostgresManager().bind(instance)

        names = [entry.name for entry in manager.list_databases()]

        assert names == [console.DB_CONTAINER_DATABASE]
        assert (manager.get_version() or "").startswith("16")
        executed = [call for call in machine.calls if call[:2] == ("docker", "exec")]
        assert executed, "the client ran through docker exec, never on the host"
        assert not any(call[0] == "psql" for call in machine.calls)

    def test_stopping_it_is_what_the_next_discovery_reports_and_its_client_refuses(
        self, console: ModuleType, machine: FakeRunner
    ) -> None:
        (instance,) = discover(console._DockerInstalled(machine))
        PostgresManager().bind(instance).stop()

        (stopped,) = discover(console._DockerInstalled(machine))

        assert stopped.state == "exited"
        with pytest.raises(DatabaseError):
            PostgresManager().bind(stopped).list_databases()


class TestDetectedUse:
    """An application's .env names a database Noust did not link."""

    def test_the_seeded_env_resolves_to_the_hosts_postgresql(
        self, console: ModuleType, tmp_path: Path
    ) -> None:
        root = tmp_path / "docs"
        root.mkdir()
        (root / ".env").write_text(
            "NODE_ENV=production\nDATABASE_URL=postgres://docs:old@127.0.0.1:5432/docs\n",
            encoding="utf-8",
        )
        app = App(domain=console.DETECTED_APP, app_path=str(root), layout="inplace")
        console.seed_detected_use(SimpleNamespace(get_app=lambda domain: app))

        references = references_in(read_environment(app))
        (found,) = detect(
            [AppReferences(app=app, references=references)],
            [Endpoint("postgresql", "postgresql", frozenset({5432}))],
        )

        assert found.reference.database == console.DETECTED_DATABASE
        assert found.engine == "postgresql"
        assert (root / ".env").read_text(encoding="utf-8").startswith("NODE_ENV=production\n")
        assert (root / ".env").stat().st_mode & 0o777 == 0o600
