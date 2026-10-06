# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Zero downtime by default for new applications (checklist B-10).

A new application on the releases layout that runs a process behind nginx
starts in the blue/green mode right after its first deployment answered, so
every update after it starts the new release beside the old one. What is
pinned here is the decision (who gets it, who never does), that it can never
fail the deployment it follows, and that what it leaves behind is the mode an
operator would have turned on: an update that fails the health gate puts
nothing out of service.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import delivery
from noust.core.exceptions import RolledBackError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers import bluegreen
from noust.deployers.bluegreen import BlueGreen, adopt_for_new_app
from tests.test_bluegreen import (  # noqa: F401  (the fixtures are imported by name)
    BASE,
    BROKEN,
    DOMAIN,
    FIRST,
    PORT,
    SECOND,
    Machine,
    app,
    engine,
    machine,
    nobody_can_name_listeners,
    root,
    store,
    write_release,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (app, machine, nobody_can_name_listeners, root, store)


@pytest.fixture
def asking(monkeypatch: pytest.MonkeyPatch) -> list[delivery.DeliverySettings]:
    """
    The server's configuration says what it says by default: zero downtime on.

    Returns:
        The settings handed out, to change them mid-test.
    """
    current = [delivery.DeliverySettings(zero_downtime=True, drain_seconds=7)]
    monkeypatch.setattr(delivery, "current", lambda **_kwargs: current[0])
    return current


def adopt(machine: Machine, store: NoustStore, **kwargs: Any) -> Any:
    """
    Run the default for the application, over the fake machine.

    Args:
        machine: The fake machine.
        store: The store.
        **kwargs: Handed to the engine.

    Returns:
        What :func:`adopt_for_new_app` returned.
    """
    return adopt_for_new_app(
        DOMAIN,
        logger=Logger(),
        store=store,
        engine=lambda row, _logger: _built(machine, store, row, **kwargs),
    )


def _built(machine: Machine, store: NoustStore, row: App, **kwargs: Any) -> BlueGreen:
    """Build the engine for ``row`` over the fakes."""
    return BlueGreen(
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
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Who gets it
# ---------------------------------------------------------------------------


def test_a_new_application_that_can_run_twice_starts_as_blue_and_green(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Green takes the traffic, the unit it was created as retires, and the drain is the server's."""
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)

    change = adopt(machine, store)

    assert change is not None and change.enabled and change.changed
    row = store.get_app(DOMAIN)
    assert row is not None
    assert (row.zero_downtime, row.active_color, row.drain_seconds) == (True, "green", 7)
    assert machine.events[-1] == ("delete_service", BASE, True)
    assert machine.upstream == f"server 127.0.0.1:{PORT + 1};"


def test_the_server_can_turn_it_off(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
) -> None:
    """``deploy.zero_downtime: false`` keeps every new application as one unit."""
    asking[0] = delivery.DeliverySettings(zero_downtime=False)

    assert adopt(machine, store) is None

    row = store.get_app(DOMAIN)
    assert row is not None and row.zero_downtime is False
    assert machine.events == []


def test_an_application_already_in_the_mode_is_left_alone(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked again (a redeploy), it changes nothing: not even the drain."""
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)
    assert adopt(machine, store) is not None
    machine.events.clear()

    assert adopt(machine, store) is None
    assert machine.events == []


def test_a_preview_is_never_converted(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
) -> None:
    """A pull request's preview is thrown away: two instances would only cost."""
    row = store.get_app(DOMAIN)
    assert row is not None
    store.set_preview_parent(DOMAIN, "main.example.com")

    assert adopt(machine, store) is None
    assert machine.events == []


def test_a_compose_stack_is_never_converted_by_default(
    store: NoustStore, asking: list[delivery.DeliverySettings]
) -> None:
    """Its relay is its own opt-in; a new stack keeps recreating its containers in place."""
    store.create_app(
        App(
            domain="stack.example.com",
            app_type="docker-compose",
            app_path="/var/www/apps/stack",
            port=3200,
            layout="releases",
            webserver="nginx",
        )
    )
    calls: list[str] = []

    result = adopt_for_new_app(
        "stack.example.com",
        logger=Logger(),
        store=store,
        engine=lambda row, _logger: calls.append(row.domain) or pytest.fail("built an engine"),  # type: ignore[func-returns-value]
    )

    assert result is None
    assert calls == []
    row = store.get_app("stack.example.com")
    assert row is not None and row.zero_downtime is False


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"webserver": "apache"}, "apache"),
        ({"layout": "inplace"}, "in place"),
        ({"is_static": True}, "static"),
        ({"app_type": "monorepo"}, "monorepo"),
    ],
)
def test_what_cannot_run_twice_stays_as_it_is_and_says_why(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
    changes: dict[str, Any],
    reason: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never an error: the deployment succeeded, and this is only a better way to update."""
    row = store.get_app(DOMAIN)
    assert row is not None
    for field, value in changes.items():
        setattr(row, field, value)
    store.update_app(row)

    assert adopt(machine, store) is None

    assert machine.events == []
    stored = store.get_app(DOMAIN)
    assert stored is not None and stored.zero_downtime is False
    assert reason in capsys.readouterr().out.lower()


def test_a_port_that_is_taken_keeps_the_application_as_one_unit(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Green needs the next port; something else listening on it is not ours to stop."""
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: port != PORT + 1)

    assert adopt(machine, store) is None

    assert machine.events == []
    assert f"{PORT + 1}" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# It never fails the deployment
# ---------------------------------------------------------------------------


def test_a_first_release_that_does_not_answer_on_green_leaves_the_unit_serving(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The conversion is undone whole, the unit never stopped, and the deploy is still a success."""
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)
    (root / "releases" / SECOND / "server.js").write_text(BROKEN)
    machine.running[BASE] = root / "releases" / FIRST

    assert adopt(machine, store) is None

    row = store.get_app(DOMAIN)
    assert row is not None and row.zero_downtime is False and row.active_color is None
    assert BASE in machine.running
    assert machine.upstream is None
    out = capsys.readouterr().out
    assert "zero-downtime mode" in out
    assert f"noust app zero-downtime {DOMAIN} on" in out


def test_an_update_that_fails_the_gate_of_a_default_application_serves_from_the_old_instance(
    root: Path,
    store: NoustStore,
    app: App,
    machine: Machine,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new release never takes traffic: nginx, ``current`` and the old instance are untouched."""
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)
    assert adopt(machine, store) is not None
    third = write_release(root, "20260927-100000-ccccccc", BROKEN)
    machine.events.clear()
    upstream_before = machine.upstream
    row = store.get_app(DOMAIN)
    assert row is not None
    serving = row.active_color

    from noust.deployers.releases import ReleaseManager

    with pytest.raises(RolledBackError):
        BlueGreen(
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
        ).activate(third, ReleaseManager(root))

    assert machine.upstream == upstream_before
    assert os.readlink(root / "current") == f"releases/{SECOND}"
    after = store.get_app(DOMAIN)
    assert after is not None and after.active_color == serving
    assert f"{BASE}@{serving}" in machine.running
    assert not any(event[0] == "sleep" for event in machine.events), "nothing drained"


def test_the_server_s_drain_is_a_valid_drain() -> None:
    """The config's range and the store's are one: a drain the config accepts the store takes."""
    from noust.core.config import DEFAULT_CONFIG
    from noust.core.store import DEFAULT_DRAIN_SECONDS, MAX_DRAIN_SECONDS

    assert DEFAULT_CONFIG["deploy"]["drain_seconds"] == DEFAULT_DRAIN_SECONDS
    assert delivery.DEFAULT_DRAIN_SECONDS == DEFAULT_DRAIN_SECONDS
    settings = delivery.DeliverySettings.read(_config({"deploy.drain_seconds": MAX_DRAIN_SECONDS}))
    assert settings.drain_seconds == MAX_DRAIN_SECONDS
    settings = delivery.DeliverySettings.read(
        _config({"deploy.drain_seconds": MAX_DRAIN_SECONDS + 1})
    )
    assert settings.drain_seconds == DEFAULT_DRAIN_SECONDS


def _config(values: dict[str, Any]) -> Any:
    """
    A configuration that answers ``get`` from a dictionary.

    Args:
        values: Dotted key to value.

    Returns:
        The stand-in.
    """
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def test_the_settings_read_what_the_configuration_says_and_survive_a_typo() -> None:
    settings = delivery.DeliverySettings.read(
        _config(
            {
                "deploy.zero_downtime": "off",
                "deploy.max_concurrent_builds": "4",
                "deploy.queue_limit": 60,
            }
        )
    )
    assert settings == delivery.DeliverySettings(
        zero_downtime=False, drain_seconds=10, max_concurrent_builds=4, queue_limit=60
    )

    broken = delivery.DeliverySettings.read(
        _config(
            {
                "deploy.zero_downtime": "perhaps",
                "deploy.max_concurrent_builds": "many",
                "deploy.queue_limit": -3,
                "deploy.drain_seconds": True,
            }
        )
    )
    assert broken == delivery.DeliverySettings()
