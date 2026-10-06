# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust health`` and ``noust diagnose`` say about zero downtime.

An application deployed before 3.4 keeps restarting in place on every update
until its operator turns the mode on; the server says which ones could, and
how, without ever counting it as a problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import delivery
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import bluegreen
from noust.deployers.bluegreen import BlueGreen, adopt_for_new_app
from noust.managers.diagnose import diagnose
from noust.managers.health import _check_updates
from tests.test_bluegreen import (
    DOMAIN,
    Machine,
    app,
    machine,
    nobody_can_name_listeners,
    root,
    store,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (app, machine, nobody_can_name_listeners, root, store)


def test_an_application_that_restarts_in_place_but_could_run_twice_is_offered_the_mode(
    root: Path, store: NoustStore, app: App
) -> None:
    assert bluegreen.could_update_without_downtime(app, store=store) is True


@pytest.mark.parametrize(
    "changes",
    [
        {"zero_downtime": True, "active_color": "green"},
        {"layout": "inplace"},
        {"webserver": "apache"},
        {"app_type": "docker-compose"},
        {"app_type": "static", "is_static": True},
        {"preview_parent": "main.example.com"},
        {"port": None},
    ],
)
def test_what_is_in_the_mode_or_cannot_be_is_not_offered_it(
    root: Path, store: NoustStore, app: App, changes: dict[str, Any]
) -> None:
    for field, value in changes.items():
        setattr(app, field, value)

    assert bluegreen.could_update_without_downtime(app, store=store) is False


def test_health_says_which_applications_could_update_without_a_cut(
    root: Path, store: NoustStore, app: App
) -> None:
    check = _check_updates()

    assert check is not None
    assert check.name == "Updates"
    assert check.status == "info", "a suggestion is never a warning"
    assert DOMAIN in check.value
    assert f"noust app zero-downtime {DOMAIN} on" in check.value


def test_health_says_nothing_when_nothing_could_use_the_mode(store: NoustStore) -> None:
    store.create_app(App(domain="site.example.com", app_type="static", is_static=True))

    assert _check_updates() is None


def test_health_counts_the_applications_that_already_update_without_a_cut(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)
    monkeypatch.setattr(
        delivery, "current", lambda **_k: delivery.DeliverySettings(zero_downtime=True)
    )
    change = adopt_for_new_app(
        DOMAIN,
        logger=Logger(),
        store=store,
        engine=lambda row, _logger: BlueGreen(
            row,
            logger=Logger(),
            store=store,
            services=machine,  # type: ignore[arg-type]
            web=machine,  # type: ignore[arg-type]
            probe=machine.probe,
            sleep=machine.sleep,
            refresh_site=machine.refresh_site,
            port_free=lambda port: True,
            listeners=machine.listeners,
            unit_facts=machine.unit_facts,
        ),
    )
    assert change is not None

    check = _check_updates()

    assert check is not None
    assert (check.status, check.value) == ("ok", "1 application(s) update without downtime")


def test_diagnose_names_the_mode_as_something_to_turn_on_without_blaming_it(
    root: Path, store: NoustStore, app: App
) -> None:
    diagnosis = diagnose(
        DOMAIN,
        runner=FakeRunner(),
        store=store,
        http_get=lambda url, headers: (200, None),
        disk_usage=lambda path: type("Usage", (), {"total": 100, "used": 10, "free": 90})(),
    )

    offer = [check for check in diagnosis.checks if check.name == "zero_downtime"]
    assert len(offer) == 1
    assert offer[0].status == "skip", "a skipped check decides nothing about the verdict"
    assert f"noust app zero-downtime {DOMAIN} on" in offer[0].evidence
