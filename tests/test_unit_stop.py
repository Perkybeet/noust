# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long an application's unit has to stop, and with which signal (checklist B-21).

- The unit a deploy writes carries ``TimeoutStopSec=`` and ``KillSignal=`` only
  for what the application set, in the same place in both templates.
- Setting a policy on an existing unit edits it rather than rendering it again,
  and the edit produces exactly what rendering would: one spelling.
- :func:`noust.deployers.lifecycle.set_stop_policy` rewrites every unit of the
  application, records the policy and restarts nothing; a redeploy keeps it.
"""

# The service manager fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, PackageLoader

from noust.core import app_rollout_store
from noust.core.exceptions import DeploymentError, ServiceError, ValidationError
from noust.core.store import App, NoustStore, Service
from noust.deployers import lifecycle
from noust.managers import unit_stop
from noust.managers.service_manager import ServiceManager
from tests.test_service_manager import (  # noqa: F401  (pytest resolves fixtures by name)
    manager,
    store,
    unit_dirs,
)


def render(template: str, *stop: str) -> str:
    """Render an application unit template the way the service manager does."""
    return (
        Environment(  # noqa: S701 - unit files, escaped by their own macros
            loader=PackageLoader("noust", "templates/systemd"), trim_blocks=True, lstrip_blocks=True
        )
        .get_template(template)
        .render(
            name="shop-example-com",
            description="Noust: shop.example.com (nodejs)",
            command="/usr/bin/npm run start",
            working_directory="/var/www/apps/shop-example-com/current",
            colors_directory="/var/www/apps/shop-example-com/colors",
            user="www-data",
            group="www-data",
            environment={"PORT": "3100"},
            stop_policy=list(stop),
        )
    )


class TestWhatTheUnitSays:
    @pytest.mark.parametrize("template", ["app.service.j2", "app@.service.j2"])
    def test_the_directives_follow_the_restart_policy(self, template: str) -> None:
        unit = render(template, "TimeoutStopSec=45", "KillSignal=SIGINT")

        assert "RestartSec=10\nTimeoutStopSec=45\nKillSignal=SIGINT\n\n# Security" in unit

    @pytest.mark.parametrize("template", ["app.service.j2", "app@.service.j2"])
    def test_an_application_that_set_nothing_gets_no_line(self, template: str) -> None:
        unit = render(template)

        assert "TimeoutStopSec" not in unit and "KillSignal" not in unit
        assert "RestartSec=10\n\n# Security" in unit

    def test_only_what_is_set_is_written(self) -> None:
        assert unit_stop.directives(30, None) == ["TimeoutStopSec=30"]
        assert unit_stop.directives(None, "SIGQUIT") == ["KillSignal=SIGQUIT"]
        assert unit_stop.directives(None, None) == []

    @pytest.mark.parametrize("template", ["app.service.j2", "app@.service.j2"])
    def test_editing_a_unit_gives_what_rendering_it_would(self, template: str) -> None:
        plain = render(template)
        both = render(template, "TimeoutStopSec=45", "KillSignal=SIGINT")

        assert unit_stop.with_stop_policy(plain, 45, "SIGINT") == both
        assert unit_stop.with_stop_policy(both, None, None) == plain
        assert unit_stop.with_stop_policy(both, 10, None) == render(template, "TimeoutStopSec=10")

    def test_an_edited_unit_keeps_the_operators_own_lines(self) -> None:
        unit = (
            "[Service]\nExecStart=/bin/app\nRestartSec=5\nNice=5\nTimeoutStopSec=300\n\n[Install]\n"
        )

        edited = unit_stop.with_stop_policy(unit, 20, "SIGHUP")

        assert edited == (
            "[Service]\nExecStart=/bin/app\nRestartSec=5\nTimeoutStopSec=20\nKillSignal=SIGHUP\n"
            "Nice=5\n\n[Install]\n"
        )

    def test_a_unit_without_a_service_section_is_refused(self) -> None:
        with pytest.raises(ServiceError, match=r"\[Service\]"):
            unit_stop.with_stop_policy("[Unit]\nDescription=x\n", 20, None)


class TestTheServiceManager:
    def test_a_policy_is_written_into_a_unit_and_systemd_reloaded(
        self, manager: ServiceManager, unit_dirs: dict[str, Path], runner: Any
    ) -> None:
        manager.create_service(name="shop", command="/usr/bin/true", working_directory="/srv")
        unit = unit_dirs["managed"] / "shop.service"

        previous = manager.set_stop_policy("shop", 45, "SIGINT")

        assert "TimeoutStopSec" not in previous
        assert "RestartSec=10\nTimeoutStopSec=45\nKillSignal=SIGINT\n" in unit.read_text()
        assert runner.calls.count(("systemctl", "daemon-reload")) == 2

    def test_a_new_unit_gets_the_policy_its_application_set(
        self,
        manager: ServiceManager,
        store: Any,
        unit_dirs: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store.apps.append(
            App(
                domain="shop.example.com",
                app_type="nodejs",
                port=3100,
                app_path="/srv/shop",
                id=1,
            )
        )
        monkeypatch.setattr(
            unit_stop,
            "get_rollout",
            lambda domain: app_rollout_store.Rollout(stop_timeout=60, stop_signal="SIGTERM"),
        )

        manager.create_service(name="shop", command="/usr/bin/true", working_directory="/srv")

        body = (unit_dirs["managed"] / "shop.service").read_text()
        assert "TimeoutStopSec=60\nKillSignal=SIGTERM\n" in body


class FakeUnits:
    """Stands in for ServiceManager: records the policy written into each unit."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.policy: dict[str, tuple[int | None, str | None] | str] = {}
        self.fail_on = fail_on
        self.restarts: list[str] = []

    def set_stop_policy(self, name: str, timeout: int | None, signal: str | None) -> str:
        if name == self.fail_on:
            raise ServiceError(f"Could not write {name}")
        previous = self.policy.get(name, "old")
        self.policy[name] = (timeout, signal)
        return previous  # type: ignore[return-value]

    def update_config(self, name: str, content: str) -> str:
        previous = self.policy[name]
        self.policy[name] = content
        return previous  # type: ignore[return-value]

    def app_units(self, app: Any) -> list[str]:
        return [
            f"{app.app_path.rsplit('/', 1)[-1]}@blue",
            f"{app.app_path.rsplit('/', 1)[-1]}@green",
        ]

    def restart(self, name: str) -> None:
        self.restarts.append(name)


@pytest.fixture
def sqlite_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


def deploy(store: NoustStore, *, app_type: str = "nodejs", units: tuple[str, ...] = ()) -> App:
    app = store.create_app(
        App(domain="shop.example.com", app_type=app_type, port=3100, app_path="/srv/shop")
    )
    for name in units:
        store.create_service(Service(app_id=app.id, name=name, command="x", working_directory="/"))
    return app


class TestTheApplicationOperation:
    def test_every_unit_gets_it_and_the_row_keeps_it_and_nothing_restarts(
        self, sqlite_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy(sqlite_store, app_type="monorepo", units=("shop-web", "shop-api"))
        units = FakeUnits()
        monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: units)

        change = lifecycle.set_stop_policy("shop.example.com", timeout=45, signal="sigint")

        assert sorted(change.units) == ["shop-api", "shop-web"]
        assert units.policy == dict.fromkeys(change.units, (45, "SIGINT"))
        assert units.restarts == []
        settings = app_rollout_store.get("shop.example.com", store=sqlite_store)
        assert (settings.stop_timeout, settings.stop_signal) == (45, "SIGINT")

    def test_a_zero_downtime_application_is_written_once_into_its_template(
        self, sqlite_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy(sqlite_store, units=("shop",))
        sqlite_store.set_zero_downtime("shop.example.com", True)
        units = FakeUnits()
        monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: units)

        change = lifecycle.set_stop_policy("shop.example.com", timeout=45, signal=None)

        assert change.units == ("shop@blue",)
        assert units.policy == {"shop@blue": (45, None)}

    def test_a_unit_that_cannot_be_written_puts_the_others_back(
        self, sqlite_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy(sqlite_store, units=("shop-api", "shop-web"))
        order = [s.name for s in sqlite_store.list_services()]
        units = FakeUnits(fail_on=order[-1])
        monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: units)

        with pytest.raises(ServiceError):
            lifecycle.set_stop_policy("shop.example.com", timeout=45, signal=None)

        assert units.policy == {order[0]: "old"}
        assert app_rollout_store.get("shop.example.com", store=sqlite_store).stop_timeout is None

    @pytest.mark.parametrize(
        ("app_type", "is_static", "match"),
        [
            ("docker-compose", False, "stop_grace_period"),
            ("container", False, "stop_grace_period"),
            ("static", True, "static site"),
        ],
    )
    def test_what_has_no_unit_of_its_own_is_refused_with_where_it_belongs(
        self,
        sqlite_store: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
        app_type: str,
        is_static: bool,
        match: str,
    ) -> None:
        app = deploy(sqlite_store, app_type=app_type)
        app.is_static = is_static
        sqlite_store.update_app(app)
        monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: FakeUnits())

        with pytest.raises(DeploymentError) as failure:
            lifecycle.set_stop_policy("shop.example.com", timeout=45, signal=None)

        assert match in f"{failure.value.message} {failure.value.details}"

    def test_a_value_out_of_range_is_refused_before_a_unit_is_touched(
        self, sqlite_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy(sqlite_store, units=("shop",))
        units = FakeUnits()
        monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: units)

        with pytest.raises(ValidationError):
            lifecycle.set_stop_policy("shop.example.com", timeout=99999, signal=None)
        with pytest.raises(ValidationError):
            lifecycle.set_stop_policy("shop.example.com", timeout=None, signal="SIGKILL")

        assert units.policy == {}

    def test_an_unknown_application_is_refused(
        self, sqlite_store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: FakeUnits())

        with pytest.raises(Exception, match="not found"):
            lifecycle.set_stop_policy("gone.example.com", timeout=45, signal=None)
