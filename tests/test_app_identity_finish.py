# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Finishing the move to an account of each application's own (checklist B-59).

3.2 gave a new application its own account and let an operator move an old one,
one at a time, except an application in zero-downtime mode. Pinned here:

- a zero-downtime application moves too: its template is rewritten and the idle
  instance starts under the new account, so the instance that serves is never
  restarted and a failure leaves it exactly as it was;
- every application that can have its own account moves with one command, and
  one that fails does not stop the others;
- ``noust health`` warns, with the command that fixes it, while any application
  that could have its own account still shares one.
"""

# The fixtures of the single-application tests are reused, not copied.
# ruff: noqa: F811

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import DeploymentError
from noust.core.logger import Logger
from noust.core.store import App, Service
from noust.managers import app_identity
from noust.managers import health as health_module
from tests.test_app_identity import (  # noqa: F401 - fixtures used by name
    UNIT,
    Gate,
    Units,
    as_root,
    existing,
)

ACCOUNT = "noust-app-shop-example-com"


@pytest.fixture
def blue_green(existing: SimpleNamespace) -> SimpleNamespace:
    """The existing application, in zero-downtime mode: its template and two instances."""
    existing.store.set_zero_downtime("shop.example.com", True)
    existing.units = Units("shop-example-com@blue")
    return existing


def migrate_zero_downtime(world: SimpleNamespace, gate: Gate) -> app_identity.IdentityMigration:
    return app_identity.migrate(
        "shop.example.com",
        actor="alice",
        logger=Logger(verbose=False),
        runner=world.runner,
        store=world.store,
        services=world.units,
        gate_for=lambda app, store, log: gate,  # type: ignore[arg-type,return-value]
    )


def test_a_zero_downtime_application_moves_without_restarting_the_instance_that_serves(
    blue_green: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    switches: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "noust.deployers.lifecycle.restart_blue_green",
        lambda app, store, log, put_back: switches.append((app.domain, app.identity)),
    )
    gate = Gate()

    done = migrate_zero_downtime(blue_green, gate)

    assert done.account == ACCOUNT
    assert switches == [("shop.example.com", ACCOUNT)], "the idle instance starts under the account"
    assert gate.restarts == 0, "the serving instance is never restarted"
    assert f"User={ACCOUNT}\n" in blue_green.units.bodies["shop-example-com@blue"]
    app = blue_green.store.get_app("shop.example.com")
    assert app is not None and app.identity == ACCOUNT
    service = blue_green.store.get_service("shop-example-com")
    assert service is not None and (service.user, service.group) == (ACCOUNT, ACCOUNT)


def test_a_zero_downtime_application_that_does_not_answer_is_put_back_and_never_cut(
    blue_green: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuses(app: App, store: Any, log: Any, put_back: Any) -> None:
        raise DeploymentError("the green instance did not answer", details="EACCES: .env")

    monkeypatch.setattr("noust.deployers.lifecycle.restart_blue_green", refuses)
    gate = Gate()

    with pytest.raises(DeploymentError) as raised:
        migrate_zero_downtime(blue_green, gate)

    assert "was not restarted" in raised.value.message, "nothing that serves was touched"
    assert gate.restarts == 0
    assert blue_green.units.bodies["shop-example-com@blue"] == UNIT
    app = blue_green.store.get_app("shop.example.com")
    assert app is not None and app.identity is None
    service = blue_green.store.get_service("shop-example-com")
    assert service is not None and (service.user, service.group) == ("www-data", "www-data")


def test_the_applications_that_could_have_their_own_account_and_do_not_are_listed(
    existing: SimpleNamespace,
) -> None:
    existing.store.create_app(App(domain="own.example.com", app_type="nodejs", app_path="/srv/o"))
    existing.store.set_app_identity("own.example.com", "noust-app-own-example-com")
    existing.store.create_app(
        App(domain="stack.example.com", app_type="docker-compose", app_path="/srv/s")
    )
    existing.store.create_app(
        App(domain="site.example.com", app_type="static", is_static=True, app_path="/srv/w")
    )

    listed = app_identity.shared_account_apps(existing.store)

    assert [app.domain for app in listed] == ["shop.example.com"]


def test_migrating_all_moves_each_and_carries_on_past_one_that_fails(
    existing: SimpleNamespace,
) -> None:
    existing.store.create_app(App(domain="b.example.com", app_type="nodejs", app_path="/srv/b"))
    existing.store.create_service(Service(name="b-example-com", user="www-data", group="www-data"))
    existing.store.create_app(App(domain="c.example.com", app_type="nodejs", app_path="/srv/c"))
    existing.store.create_service(Service(name="c-example-com", user="www-data", group="www-data"))

    class Fleet(Units):
        """Every application's unit; the one of b cannot be rewritten."""

        def __init__(self) -> None:
            self.bodies = {
                "shop-example-com": UNIT,
                "b-example-com": UNIT,
                "c-example-com": UNIT,
            }

        def serving_units(self, app: App) -> list[str]:
            return [app.app_path.rsplit("/", 1)[-1] if app.app_path else "x"]

        def set_unit_account(self, name: str, user: str, group: str) -> str:
            if name == "b":
                raise DeploymentError("The unit of b is not Noust's")
            return super().set_unit_account(name, user, group)

    fleet = Fleet()
    fleet.bodies = {
        "shop-example-com": UNIT,
        "b": UNIT,
        "c": UNIT,
    }
    gate = Gate()

    outcome = app_identity.migrate_all(
        actor="alice",
        logger=Logger(verbose=False),
        runner=existing.runner,
        store=existing.store,
        services=fleet,  # type: ignore[arg-type]
        gate_for=lambda app, store, log: gate,  # type: ignore[arg-type,return-value]
    )

    assert sorted(done.domain for done in outcome.moved) == ["c.example.com", "shop.example.com"]
    assert [domain for domain, _ in outcome.failed] == ["b.example.com"]
    b = existing.store.get_app("b.example.com")
    assert b is not None and b.identity is None
    assert outcome.to_dict()["failed"][0]["domain"] == "b.example.com"


def test_the_command_refuses_a_domain_and_all_together_and_neither(
    existing: SimpleNamespace,
) -> None:
    both = CliRunner().invoke(root_cli, ["app", "identity", "migrate", "a.example.com", "--all"])
    neither = CliRunner().invoke(root_cli, ["app", "identity", "migrate"])

    assert both.exit_code == 2
    assert neither.exit_code == 2


def test_the_command_moves_everyone_with_all(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_all(**kwargs: Any) -> app_identity.BulkMigration:
        calls.append(kwargs["actor"])
        return app_identity.BulkMigration(
            moved=(app_identity.IdentityMigration("shop.example.com", ACCOUNT, ("www-data",) * 2),)
        )

    monkeypatch.setattr(app_identity, "migrate_all", fake_all)
    monkeypatch.setattr(app_identity, "shared_account_apps", lambda store=None: [object()])

    result = CliRunner().invoke(root_cli, ["--json", "app", "identity", "migrate", "--all"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert '"moved"' in result.output


def test_the_command_fails_loudly_when_one_was_not_moved(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app_identity,
        "migrate_all",
        lambda **kw: app_identity.BulkMigration(failed=(("b.example.com", "no"),)),
    )
    monkeypatch.setattr(app_identity, "shared_account_apps", lambda store=None: [object()])

    result = CliRunner().invoke(root_cli, ["--json", "app", "identity", "migrate", "--all"])

    assert result.exit_code != 0
    assert "b.example.com" in result.output


# ---------------------------------------------------------------------------
# The warning in the health report
# ---------------------------------------------------------------------------


def test_health_warns_while_an_application_shares_the_account(existing: SimpleNamespace) -> None:
    warnings: list[str] = []

    check = health_module._check_accounts(warnings)

    assert check is not None and check.status == "warning"
    assert "1 application(s) share one system account" in check.value
    [warning] = warnings
    assert "shop.example.com" in warning
    assert "noust app identity migrate --all" in warning


def test_health_says_nothing_once_every_application_has_its_own(
    existing: SimpleNamespace,
) -> None:
    existing.store.set_app_identity("shop.example.com", ACCOUNT)
    warnings: list[str] = []

    assert health_module._check_accounts(warnings) is None
    assert warnings == []


def test_health_names_three_and_counts_the_rest(existing: SimpleNamespace) -> None:
    for name in "abcd":
        existing.store.create_app(
            App(domain=f"{name}.example.com", app_type="nodejs", app_path=f"/srv/{name}")
        )
    warnings: list[str] = []

    health_module._check_accounts(warnings)

    assert "and 2 more" in warnings[0]


def test_the_report_carries_the_check(existing: SimpleNamespace) -> None:
    warnings: list[str] = []
    check = health_module._check_accounts(warnings)
    report = health_module.HealthReport(
        disk=None,
        nginx=health_module.HealthCheck("Nginx", "Running", "ok"),
        apache=health_module.HealthCheck("Apache", "Not installed", "info"),
        applications=health_module.HealthCheck("Applications", "1/1 serving", "ok"),
        certificates=health_module.HealthCheck("SSL Certificates", "None configured", "info"),
        memory=health_module.HealthCheck("Memory", "50%", "ok"),
        accounts=check,
        warnings=warnings,
    )

    assert "Application accounts" in [item.name for item in report.checks]
    assert report.verdict == "warning"
