# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The deployer's part of zero downtime by default: when the step runs.

The decision of who gets the mode is :func:`noust.deployers.bluegreen.adopt_for_new_app`
(tested in ``test_zero_downtime_default``); this is the wiring that asks it,
once, as the last step of the first deployment of an application, and never
for what deployed before, previews, Apache or a server that turned it off.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import delivery
from noust.core.exceptions import DeploymentError
from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.deployers import base as base_module
from noust.deployers import bluegreen
from noust.deployers.nodejs import NodeJSDeployer
from tests.test_release_pipeline import (
    DOMAIN,
    GIT_URL,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (git, machine, root, store)


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


def adoptions(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Record what the deployer asks :func:`adopt_for_new_app` for.

    Args:
        monkeypatch: Patching helper.

    Returns:
        The calls, in order.
    """
    calls: list[dict[str, Any]] = []

    def record(domain: str, **kwargs: Any) -> None:
        calls.append({"domain": domain, **kwargs})

    monkeypatch.setattr(base_module, "adopt_for_new_app", record)
    return calls


def configured(
    machine: SimpleNamespace, root: Path, steps: list[str] | None = None, **options: Any
) -> NodeJSDeployer:
    """
    A Node deployer wired to the fakes, optionally recording its steps' titles.

    Args:
        machine: The fakes.
        root: The application directory.
        steps: Collects the title of each step as it starts.
        **options: More options for ``configure``.

    Returns:
        The deployer.
    """
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    if steps is not None:
        deployer.logger.step = lambda i, n, title, icon="": steps.append(title)
    deployer.configure(
        DOMAIN, GIT_URL, port=3100, ssl=False, app_path=root, layout="releases", **options
    )
    return deployer


def test_a_new_application_converts_as_the_last_step_of_its_first_deploy(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the first release answered, and never before: the gate judged the plain unit."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    calls = adoptions(monkeypatch)
    steps: list[str] = []

    assert configured(machine, root, steps).deploy() is True

    assert steps[-2:] == ["Activating release", "Enabling updates without downtime"]
    assert [call["domain"] for call in calls] == [DOMAIN]


def test_the_engine_the_deployer_builds_does_not_wait_for_a_drain_nobody_needs(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody used the unit yet but the health gate: stopping it needs no ten seconds."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    calls = adoptions(monkeypatch)
    deployer = configured(machine, root)
    deployer.deploy()
    row = store.get_app(DOMAIN)
    assert row is not None

    waited: list[float] = []
    monkeypatch.setattr("time.sleep", waited.append)
    converting = calls[0]["engine"](row, Logger())
    updating = deployer._blue_green(row)

    converting._sleep(300)
    assert waited == []
    # An update's own activation, by contrast, lets the old instance drain.
    updating._sleep(3)
    assert waited == [3]


def test_a_redeploy_of_an_existing_application_never_converts_it(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing applications keep how they update, until the operator says otherwise."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    asking[0] = delivery.DeliverySettings(zero_downtime=False)
    deploy_new(root, machine)
    asking[0] = delivery.DeliverySettings(zero_downtime=True)
    calls = adoptions(monkeypatch)
    machine.git.publish(node_tree(tmp_path / "v2", server="// v2\n"))
    steps: list[str] = []

    assert configured(machine, root, steps).deploy() is True

    assert calls == []
    assert "Enabling updates without downtime" not in steps


def test_the_server_turning_it_off_leaves_the_pipeline_as_it_was(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not even a skipped step shows: line for line what deploys did before 3.4."""
    asking[0] = delivery.DeliverySettings(zero_downtime=False)
    machine.git.publish(node_tree(tmp_path / "v1"))
    calls = adoptions(monkeypatch)
    steps: list[str] = []

    configured(machine, root, steps).deploy()

    assert calls == []
    assert steps[-1] == "Activating release"


@pytest.mark.parametrize(
    "options", [{"webserver": "apache"}, {"preview_parent": "main.example.com"}]
)
def test_apache_and_previews_do_not_get_the_step(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    asking: list[delivery.DeliverySettings],
    options: dict[str, Any],
) -> None:
    """The upstream is nginx's; and a preview is thrown away."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    deployer = configured(machine, root, **options)
    deployer._is_new_deployment = True

    titles = [step.title for step in deployer._release_pipeline()]

    assert "Enabling updates without downtime" not in titles


def test_the_step_never_fails_a_deployment_that_answered(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    asking: list[delivery.DeliverySettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conversion that fails is undone by the engine; the deployment is still a success."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)

    def refuse(self: bluegreen.BlueGreen, *, drain_seconds: int | None) -> None:
        raise DeploymentError("Release did not answer on the green instance", details="boom")

    monkeypatch.setattr(bluegreen.BlueGreen, "enable", refuse)
    warnings: list[str] = []
    deployer = configured(machine, root)
    deployer.logger.warning = warnings.append

    assert deployer.deploy() is True

    app = store.get_app(DOMAIN)
    assert app is not None and app.zero_downtime is False
    assert len(warnings) == 1
    assert "could not start in zero-downtime mode" in warnings[0]
    assert f"noust app zero-downtime {DOMAIN} on" in warnings[0]
