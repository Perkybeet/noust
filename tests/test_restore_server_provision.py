# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust restore-server`` installs what the old server ran on and brings back the databases no application owns (checklist H-28, H-30).

The flow used to name the web server, Docker and the engines a new machine lacked and stop there, to
restore the applications' own backups and leave the shared databases behind. What is pinned here:

- ``--install`` installs the web server, Docker (only when an application or an instance needs it)
  and the database engines, and a failure to install one is a line of the report, not the end of the
  flow;
- without it nothing is installed, and the report still says what is missing and the command;
- the roles of the PostgreSQL servers the bundle carries are created once their engine is there, and
  only then (a file kept until its engine is installed);
- the databases no application owns are restored next, from the newest dump wherever it is, and a
  database with no dump anywhere makes the report not ok.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DatabaseEngineError, DependencyError
from noust.core.store import NoustStore
from noust.managers.restore_databases import DatabaseOutcome
from noust.managers.server_provision import ProvisionStep
from tests.test_restore_server import (  # noqa: F401
    Flow,
    _reset_store,
    add_state,
    audited,
    cheap_scrypt,
    meta,
    old,
    outcome,
    runner,
)

pytestmark = pytest.mark.allow_subprocess


@dataclass
class Provisioner:
    """What the new machine has, and what installing was asked to do."""

    present: set[str] = field(default_factory=lambda: {"nginx", "docker"})
    installed: list[str] = field(default_factory=list)
    refuse: dict[str, str] = field(default_factory=dict)

    def _step(self, name: str, ensure: bool) -> ProvisionStep:
        if name in self.present:
            return ProvisionStep(name, "present", f"{name} is installed")
        if not ensure:
            return ProvisionStep(name, "missing", f"{name} is not installed", f"install {name}")
        if name in self.refuse:
            raise DependencyError(f"Could not install {name}", details=self.refuse[name])
        self.installed.append(name)
        self.present.add(name)
        return ProvisionStep(name, "installed", f"{name} was installed")

    def check_webserver(self, name: str) -> ProvisionStep:
        return self._step(name, False)

    def ensure_webserver(self, name: str) -> ProvisionStep:
        return self._step(name, True)

    def check_docker(self) -> ProvisionStep:
        return self._step("docker", False)

    def ensure_docker(self) -> ProvisionStep:
        return self._step("docker", True)


@dataclass
class Orphans:
    """The databases no application owns, as the flow restores them."""

    outcomes: list[DatabaseOutcome] = field(default_factory=list)
    asked: list[str | None] = field(default_factory=list)

    def restore(self, store: NoustStore, *, from_destination: str | None = None) -> list[Any]:
        self.asked.append(from_destination)
        return list(self.outcomes)


@dataclass
class Roles:
    """The roles of a PostgreSQL server, applied to the engine of that name."""

    applied: list[tuple[str, str]] = field(default_factory=list)
    installed: set[str] = field(default_factory=lambda: {"postgresql"})
    fail: str = ""

    def __call__(self, engine: str, path: Path) -> Any:
        if engine not in self.installed:
            return None
        if self.fail:
            raise DatabaseEngineError(self.fail)
        self.applied.append((engine, path.read_text()))
        return SimpleNamespace(created=("shopper",), present=("postgres",), left_out=(), errors=())


@dataclass
class Setup:
    flow: Flow
    provisioner: Provisioner
    orphans: Orphans
    roles: Roles
    engines_installed: list[str] = field(default_factory=list)


@pytest.fixture
def setup(old: Any, runner: Any, tmp_path: Path) -> Setup:
    add_state(old)
    flow = Flow(tmp_path, runner, old)
    provisioner, orphans, roles = Provisioner(), Orphans(), Roles()
    made = Setup(flow, provisioner, orphans, roles)

    def install_engine(engine: str) -> str:
        made.engines_installed.append(engine)
        flow.installed.add(engine)
        return f"{engine} was installed"

    flow.flow._provisioner = provisioner  # type: ignore[assignment]
    flow.flow._orphans = orphans  # type: ignore[assignment]
    flow.flow._apply_roles = roles
    flow.flow._install_engine = install_engine
    flow.flow._webserver_name = lambda: "nginx"
    flow.backups.local["shop.example.com"] = [
        meta("shop-example-com_20260101_030000", "shop.example.com", "2026-01-01T03:00:00+00:00")
    ]
    return made


def put_roles_file(flow: Flow, name: str = "postgresql.sql") -> Path:
    """
    Leave a file of roles where a bundle's restore puts them, after the state is back.

    Args:
        flow: The flow, which brings the state back first so that a later run can skip it.
        name: The file.

    Returns:
        The file.
    """
    flow.run()
    directory = flow.manager.restore_targets()["pg-globals"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("CREATE ROLE shopper;\n")
    return path


class TestWhatTheServerNeeds:
    def test_without_install_nothing_is_installed_and_the_report_says_what_is_missing(
        self, setup: Setup
    ) -> None:
        setup.provisioner.present.clear()

        report = setup.flow.run()

        assert setup.provisioner.installed == []
        [step] = [r for r in report.requirements if r["name"] == "nginx"]
        assert step["state"] == "missing" and step["command"] == "install nginx"
        assert report.ok is False

    def test_install_puts_the_web_server_in_before_the_applications(self, setup: Setup) -> None:
        setup.provisioner.present.clear()

        report = setup.flow.run(install=True)

        assert setup.provisioner.installed == ["nginx"]
        assert [r["state"] for r in report.requirements if r["name"] == "nginx"] == ["installed"]
        assert report.ok is True

    def test_docker_is_only_asked_for_when_an_application_runs_in_a_container(
        self, setup: Setup
    ) -> None:
        setup.provisioner.present = {"nginx"}

        plain = setup.flow.run(install=True)

        assert "docker" not in [r["name"] for r in plain.requirements]
        assert setup.provisioner.installed == []

    def test_a_compose_application_makes_docker_a_requirement(
        self, old: Any, runner: Any, tmp_path: Path
    ) -> None:
        add_state(old, apps=("stack.example.com",))
        connection = sqlite3.connect(old.state / "noust.db")
        connection.execute(
            "UPDATE apps SET app_type = 'docker-compose' WHERE domain = 'stack.example.com'"
        )
        connection.commit()
        connection.close()
        flow = Flow(tmp_path, runner, old)
        provisioner = Provisioner(present={"nginx"})
        flow.flow._provisioner = provisioner  # type: ignore[assignment]
        flow.flow._webserver_name = lambda: "nginx"
        flow.flow._orphans = Orphans()  # type: ignore[assignment]
        flow.flow._apply_roles = Roles()

        report = flow.run(install=True)

        assert "docker" in [r["name"] for r in report.requirements]
        assert provisioner.installed == ["docker"]

    def test_a_failed_install_is_a_line_of_the_report_and_the_flow_goes_on(
        self, setup: Setup
    ) -> None:
        setup.provisioner.present.clear()
        setup.provisioner.refuse["nginx"] = "E: Unable to locate package nginx"

        report = setup.flow.run(install=True)

        [step] = [r for r in report.requirements if r["name"] == "nginx"]
        assert step["state"] == "failed"
        assert "Unable to locate package nginx" in step["detail"]
        assert outcome(report, "shop.example.com").status == "restored"
        assert report.ok is False

    def test_the_web_server_is_the_one_the_restored_configuration_names(self, setup: Setup) -> None:
        setup.flow.flow._webserver_name = lambda: "apache"
        setup.provisioner.present = set()

        report = setup.flow.run(install=True)

        assert setup.provisioner.installed == ["apache"]
        assert [r["name"] for r in report.requirements] == ["apache"]


class TestTheEngines:
    def test_install_brings_the_missing_engines_in_and_they_are_no_longer_missing(
        self, setup: Setup
    ) -> None:
        setup.flow.installed.clear()

        report = setup.flow.run(install=True)

        assert setup.engines_installed == ["postgresql"]
        assert report.engines_missing == []
        assert any(
            r["name"] == "postgresql" and r["state"] == "installed" for r in report.requirements
        )

    def test_an_engine_that_will_not_install_stays_missing_with_the_reason(
        self, setup: Setup
    ) -> None:
        setup.flow.installed.clear()

        def refuse(engine: str) -> str:
            raise DatabaseEngineError(f"apt could not install {engine}")

        setup.flow.flow._install_engine = refuse

        report = setup.flow.run(install=True)

        assert [m["engine"] for m in report.engines_missing] == ["postgresql"]
        failed = [r for r in report.requirements if r["name"] == "postgresql"]
        assert failed[0]["state"] == "failed" and "apt could not install" in failed[0]["detail"]


class TestTheRoles:
    def test_the_roles_of_the_bundle_are_created_once_the_engine_is_there(
        self, setup: Setup
    ) -> None:
        put_roles_file(setup.flow)

        report = setup.flow.run(skip_state=True)

        assert setup.roles.applied == [("postgresql", "CREATE ROLE shopper;\n")]
        assert report.roles[0]["engine"] == "postgresql"
        assert report.roles[0]["created"] == ["shopper"]

    def test_the_file_is_removed_once_it_has_been_applied_because_it_holds_password_hashes(
        self, setup: Setup
    ) -> None:
        path = put_roles_file(setup.flow)

        setup.flow.run(skip_state=True)

        assert not path.exists()

    def test_an_engine_that_is_not_installed_keeps_its_file_for_the_next_run(
        self, setup: Setup
    ) -> None:
        setup.roles.installed = set()
        path = put_roles_file(setup.flow)

        report = setup.flow.run(skip_state=True)

        assert path.exists()
        assert report.roles[0]["state"] == "waiting"

    def test_roles_the_server_refuses_are_said_and_do_not_stop_the_flow(self, setup: Setup) -> None:
        setup.roles.fail = "could not list the roles"
        path = put_roles_file(setup.flow)

        report = setup.flow.run(skip_state=True)

        assert path.exists()
        assert report.roles[0]["state"] == "failed"
        assert "could not list the roles" in report.roles[0]["detail"]
        assert outcome(report, "shop.example.com").status == "restored"

    def test_roles_of_a_database_instance_are_left_for_the_instance(self, setup: Setup) -> None:
        path = put_roles_file(setup.flow, "postgresql-pg16.db.sql")
        setup.roles.installed = set()

        report = setup.flow.run(skip_state=True)

        assert path.exists()
        assert "instance" in report.roles[0]["detail"]


class TestTheDatabasesNoApplicationOwns:
    def test_they_are_restored_and_reported(self, setup: Setup) -> None:
        setup.orphans.outcomes = [
            DatabaseOutcome("postgresql", "shared", "restored", source="local", dump="s.dump")
        ]

        report = setup.flow.run(from_destination="cloud")

        assert [(d.name, d.status) for d in report.databases] == [("shared", "restored")]
        assert setup.orphans.asked == ["cloud"]
        assert report.ok is True

    def test_a_database_with_no_dump_anywhere_makes_the_report_not_ok(self, setup: Setup) -> None:
        setup.orphans.outcomes = [DatabaseOutcome("postgresql", "lost", "no_dump", error="none")]

        report = setup.flow.run()

        assert report.ok is False

    def test_one_that_was_already_there_or_waits_for_an_instance_is_not_a_failure(
        self, setup: Setup
    ) -> None:
        setup.orphans.outcomes = [
            DatabaseOutcome("postgresql", "there", "present"),
            DatabaseOutcome("postgresql@pg16.db", "inst", "skipped", error="make the instance"),
        ]

        report = setup.flow.run()

        assert report.ok is True

    def test_they_can_be_left_out(self, setup: Setup) -> None:
        setup.orphans.outcomes = [DatabaseOutcome("postgresql", "shared", "restored")]

        report = setup.flow.run(databases=False)

        assert report.databases == []
        assert setup.orphans.asked == []

    def test_they_come_back_before_the_applications(self, setup: Setup) -> None:
        order: list[str] = []
        original = setup.orphans.restore

        def restore(store: NoustStore, *, from_destination: str | None = None) -> list[Any]:
            order.append("databases")
            return original(store, from_destination=from_destination)

        setup.orphans.restore = restore  # type: ignore[method-assign]
        setup.flow.flow._refresh_site = lambda app: order.append("site")

        setup.flow.run()

        assert order == ["databases", "site"]


def test_the_report_carries_all_of_it_as_data(setup: Setup) -> None:
    setup.orphans.outcomes = [DatabaseOutcome("postgresql", "shared", "restored", source="local")]

    data = setup.flow.run().to_dict()

    assert {"requirements", "roles", "databases", "engines_missing"} <= set(data)
    assert data["databases"][0]["name"] == "shared"
