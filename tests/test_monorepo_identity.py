# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A monorepo created now runs as an account of its own (checklist L-14).

Until 3.4 a monorepo was the one application that ran several units and kept
them on the shared account, because the account was only ever given to a unit
named like its application. Its workspaces' units now name the account
themselves, so one compromised workspace cannot read another application's
files. The account is one for the whole monorepo: its workspaces are one
application, and one compose of trust.

A monorepo from before keeps the shared account until
``noust app identity migrate`` moves it, as every application from before 3.2
does.
"""

# The sandbox fixture is imported rather than replicated; pytest resolves it
# by name.
# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, Service
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.monorepo import MonorepoDeployer
from noust.managers import app_identity
from noust.managers.service_manager import with_account
from tests.test_build_sandbox import as_root  # noqa: F401 - fixture used by name
from tests.test_monorepo_sandbox import DOMAIN, deployer, registered, workspace_tree

ACCOUNT = "noust-app-mono-example-com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr("noust.deployers.monorepo.get_store", lambda: instance)
    monkeypatch.setattr(build_sandbox, "get_store", lambda: instance)
    monkeypatch.setattr("noust.deployers.helpers.sandbox_trial.get_store", lambda: instance)
    monkeypatch.setattr(app_identity, "running_as_root", lambda: True)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the steps of a deployment out: what is asked is who the deployer says it runs as."""
    monkeypatch.setattr(MonorepoDeployer, "_deploy_steps", lambda self, app, total: True)
    monkeypatch.setattr(MonorepoDeployer, "_pre_flight_check", lambda self: None)


class TestANewMonorepo:
    def test_it_is_given_an_account_of_its_own(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, stubbed: None, as_root
    ) -> None:
        deployer(tmp_path / "mono", runner)._deploy()

        assert store.get_app(DOMAIN).identity == ACCOUNT  # type: ignore[union-attr]
        assert any(call[:1] == ("useradd",) and ACCOUNT in call for call in runner.calls)

    def test_it_says_it_runs_as_that_account(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, stubbed: None, as_root
    ) -> None:
        instance = deployer(tmp_path / "mono", runner)
        instance._deploy()

        assert instance._service_account() == (ACCOUNT, ACCOUNT)

    def test_its_tree_is_handed_to_the_account(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, stubbed: None, as_root
    ) -> None:
        instance = deployer(tmp_path / "mono", runner)
        instance._deploy()

        instance._set_permissions()

        assert ("chown", "-R", f"{ACCOUNT}:{ACCOUNT}", str(tmp_path / "mono")) in runner.calls

    def test_its_workspaces_units_name_the_account(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, stubbed: None, as_root
    ) -> None:
        instance = deployer(tmp_path / "mono", runner)
        instance._deploy()
        workspace_tree(tmp_path / "mono")
        created: list[dict[str, object]] = []
        instance.service_manager.create_service = lambda **kwargs: created.append(kwargs)  # type: ignore[method-assign,assignment]
        instance.service_manager.enable = lambda name: None  # type: ignore[method-assign,assignment]
        instance._discover_workspaces()

        instance._create_services()

        assert created, "no unit was created"
        assert {(unit["user"], unit["group"]) for unit in created} == {(ACCOUNT, ACCOUNT)}
        rows = [store.get_service(f"mono-example-com-{ws.name}") for ws in instance.workspaces]
        assert {(row.user, row.group) for row in rows if row} == {(ACCOUNT, ACCOUNT)}

    def test_the_build_sandbox_runs_as_the_account_too(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, stubbed: None, as_root
    ) -> None:
        instance = deployer(tmp_path / "mono", runner)
        instance._deploy()
        workspace_tree(tmp_path / "mono")

        spec = instance._execution(build_sandbox.BuildPhase.BUILD)

        assert spec is not None and spec.user == ACCOUNT


class TestAMonorepoFromBefore:
    def test_a_redeploy_keeps_the_shared_account(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, stubbed: None, as_root
    ) -> None:
        root = workspace_tree(tmp_path / "mono")
        registered(store, root, sandboxed=False)
        instance = deployer(root, runner)
        instance.replace_existing = True

        instance._deploy()

        assert store.get_app(DOMAIN).identity in (None, "")  # type: ignore[union-attr]
        assert instance._service_account() == (
            instance.config.service_user,
            instance.config.service_group,
        )
        assert not any(call[:1] == ("useradd",) for call in runner.calls)


class TestMigration:
    def test_a_monorepo_can_be_moved_like_any_other_application(self) -> None:
        app = App(domain=DOMAIN, app_type="monorepo", app_path="/var/www/apps/mono-example-com")

        assert app_identity.ineligible(app) is None

    @pytest.mark.parametrize("kind", ["docker-compose", "container"])
    def test_a_stack_of_containers_still_has_no_account_of_its_own(self, kind: str) -> None:
        reason = app_identity.ineligible(App(domain="x.example.com", app_type=kind))

        assert reason is not None and "containers" in reason

    def test_a_static_site_has_nothing_to_run(self) -> None:
        reason = app_identity.ineligible(App(domain="x.example.com", app_type="static"))

        assert reason is not None and "serves its files" in reason

    def test_a_monorepo_in_zero_downtime_mode_is_still_refused(self) -> None:
        app = App(
            domain=DOMAIN,
            app_type="monorepo",
            app_path="/var/www/apps/mono-example-com",
            zero_downtime=True,
        )

        with pytest.raises(ValidationError, match="zero-downtime"):
            app_identity.refuse_migration(app)


class Workspaces:
    """Stands in for ServiceManager over a monorepo's units: one body per workspace."""

    UNIT = "[Service]\nType=simple\nUser=www-data\nGroup=www-data\nExecStart=/usr/bin/node x.js\n"

    def __init__(self, *units: str) -> None:
        self.units = units
        self.bodies = dict.fromkeys(units, self.UNIT)

    def serving_units(self, app: App) -> list[str]:
        return list(self.units)

    def set_unit_account(self, name: str, user: str, group: str) -> str:
        previous = self.bodies[name]
        self.bodies[name] = with_account(previous, user, group)
        return previous

    def update_config(self, name: str, content: str) -> str:
        previous = self.bodies[name]
        self.bodies[name] = content
        return previous


class Gate:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)

    def restart_and_probe(self) -> tuple[bool, str]:
        healthy = self.answers.pop(0) if self.answers else True
        return healthy, "" if healthy else "web did not answer"


@pytest.fixture
def old_monorepo(
    tmp_path: Path, store: NoustStore, as_root: Any
) -> Iterator[tuple[Path, Workspaces, FakeRunner]]:
    root = workspace_tree(tmp_path / "mono-example-com")
    (root / ".env").write_text("A=1\n")
    store.create_app(App(domain=DOMAIN, app_type="monorepo", app_path=str(root)))
    for unit in ("mono-example-com-web", "mono-example-com-api"):
        store.create_service(Service(name=unit, user="www-data", group="www-data"))
    yield root, Workspaces("mono-example-com-web", "mono-example-com-api"), FakeRunner()


def migrate(
    store: NoustStore, units: Workspaces, runner: FakeRunner, gate: Gate
) -> app_identity.IdentityMigration:
    return app_identity.migrate(
        DOMAIN,
        actor="alice",
        logger=Logger(verbose=False),
        runner=runner,
        store=store,
        services=units,  # type: ignore[arg-type]
        gate_for=lambda app, st, log: gate,  # type: ignore[arg-type,return-value]
    )


class TestMovingAMonorepoFromBefore:
    def test_every_workspaces_unit_moves_to_the_one_account(
        self, store: NoustStore, old_monorepo: tuple[Path, Workspaces, FakeRunner]
    ) -> None:
        root, units, runner = old_monorepo
        old = app_identity._owner_spec("www-data", "www-data")

        done = migrate(store, units, runner, Gate(True))

        assert done.account == ACCOUNT
        assert set(done.units) == {"mono-example-com-web", "mono-example-com-api"}
        for body in units.bodies.values():
            assert f"User={ACCOUNT}\n" in body and f"Group={ACCOUNT}\n" in body
        for unit in units.bodies:
            row = store.get_service(unit)
            assert row is not None and (row.user, row.group) == (ACCOUNT, ACCOUNT)
        assert any(call[:3] == ("chown", "-R", f"--from={old}") for call in runner.calls)
        assert store.get_app(DOMAIN).identity == ACCOUNT  # type: ignore[union-attr]

    def test_when_it_does_not_answer_every_unit_is_put_back(
        self, store: NoustStore, old_monorepo: tuple[Path, Workspaces, FakeRunner]
    ) -> None:
        _root, units, runner = old_monorepo

        with pytest.raises(DeploymentError, match="was not moved to its own account"):
            migrate(store, units, runner, Gate(False, True))

        assert set(units.bodies.values()) == {Workspaces.UNIT}
        for unit in units.bodies:
            row = store.get_service(unit)
            assert row is not None and (row.user, row.group) == ("www-data", "www-data")
        assert store.get_app(DOMAIN).identity in (None, "")  # type: ignore[union-attr]
