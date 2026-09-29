# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for what may answer the blue/green health gate.

The idle instance's port is free by design, so whatever listens there when
the gate probes it is taken for the release: another application given that
port explicitly, any local process, or the application's own unit left
running by a switch to zero-downtime mode that did not finish. nginx would
then send the domain to it and stop the instance that served. What is pinned:

- Before the idle instance starts, its port must be free. The instance
  itself, left running, is stopped first; anything else is refused, naming
  the process, and nothing is switched.
- After the probe passes, the instance must be active and the process that
  listens must be one of its own; otherwise it is stopped and nothing is
  switched.
- A switch nginx cannot even be asked to make (the upstream cannot be
  written) stops the new instance like any other refused switch.
- A new application cannot be given, explicitly, a port another
  application owns, the idle instance's included.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, NginxError, NoustError, RolledBackError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import bluegreen, lifecycle
from noust.deployers.bluegreen import Listener
from noust.deployers.helpers import preflight
from noust.deployers.releases import ReleaseManager
from tests.test_bluegreen import (
    BASE,
    DOMAIN,
    FIRST,
    PORT,
    SECOND,
    Machine,
    app,
    engine,
    link,
    machine,
    root,
    store,
    switched_on,
)

__all__ = ["app", "machine", "root", "store"]  # fixtures, imported for pytest

STRANGER = Listener(process="python3", pid=999, unit="session-3.scope")


def port_free(machine: Machine) -> Any:
    """A port is free when nothing the fake machine models listens on it."""
    return lambda port: not machine.listeners(port)


# ---------------------------------------------------------------------------
# Before the idle instance starts
# ---------------------------------------------------------------------------


def test_a_stranger_on_the_idle_port_is_refused_and_nothing_is_switched(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    """Something else answers on blue's port: it would have passed the gate."""
    switched_on(machine, store, app)
    machine.strangers[PORT] = [STRANGER]
    before = machine.upstream

    with pytest.raises(DeploymentError, match="nothing was switched") as refused:
        engine(machine, store, port_free=port_free(machine)).activate(
            root / "releases" / FIRST, ReleaseManager(root)
        )

    assert not isinstance(refused.value, RolledBackError)
    assert "python3 (pid 999" in str(refused.value)
    assert f"sport = :{PORT}" in str(refused.value)
    assert "restart" not in machine.names()
    assert "upstream" not in machine.names()
    assert machine.upstream == before
    assert link(root, "current") == f"releases/{SECOND}"
    row = store.get_app(DOMAIN)
    assert row is not None and row.active_color == "green"


def test_the_idle_instance_left_running_is_stopped_before_it_starts_again(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    """An interrupted drain or a crash loop leaves the instance itself on its port."""
    switched_on(machine, store, app)
    machine.running[f"{BASE}@blue"] = root / "releases" / SECOND

    switch = engine(machine, store, port_free=port_free(machine)).activate(
        root / "releases" / FIRST, ReleaseManager(root)
    )

    assert switch.to_color == "blue"
    assert machine.events[0] == ("stop", f"{BASE}@blue")
    assert machine.events[1] == ("restart", f"{BASE}@blue", FIRST)
    assert machine.upstream == f"server 127.0.0.1:{PORT};"


def test_the_unit_an_interrupted_switch_left_running_is_named_and_refused(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    """
    Turning the mode on was interrupted during the drain: the application's own unit
    still runs the old release on blue's port, and would answer blue's gate.
    """
    switched_on(machine, store, app)
    machine.running[BASE] = root / "releases" / SECOND

    with pytest.raises(DeploymentError, match="nothing was switched") as refused:
        engine(machine, store, port_free=port_free(machine)).activate(
            root / "releases" / FIRST, ReleaseManager(root)
        )

    text = str(refused.value)
    assert f"{BASE}.service" in text
    assert "did not finish" in text
    assert f"systemctl disable --now {BASE}.service" in text
    assert machine.upstream == f"server 127.0.0.1:{PORT + 1};"
    assert BASE in machine.running


def leftover_state(runner: FakeRunner, state: str, file_state: str) -> None:
    """Script what systemd says of the unit the application ran as before the mode."""
    runner.script(
        ["systemctl", "show", "-p", "ActiveState,UnitFileState", f"{BASE}.service"],
        stdout=f"ActiveState={state}\nUnitFileState={file_state}\n",
    )


def test_the_status_reports_the_unit_an_interrupted_switch_left_running(
    root: Path, store: NoustStore, app: App, machine: Machine, runner: FakeRunner
) -> None:
    """``wasm app zero-downtime DOMAIN`` says so before an activation trips on it."""
    switched_on(machine, store, app)
    leftover_state(runner, "active", "enabled")

    status = bluegreen.zero_downtime_status(
        DOMAIN,
        services=machine,  # type: ignore[arg-type]
        web=machine,  # type: ignore[arg-type]
        runner=runner,
    )

    assert status.enabled
    assert status.reason is not None and f"{BASE}.service" in status.reason
    assert status.hint is not None and f"systemctl disable --now {BASE}.service" in status.hint


def test_a_unit_left_enabled_is_reported_too(
    root: Path, store: NoustStore, app: App, machine: Machine, runner: FakeRunner
) -> None:
    """Stopped but enabled: it takes blue's port again at the next boot."""
    switched_on(machine, store, app)
    leftover_state(runner, "inactive", "enabled")

    status = bluegreen.zero_downtime_status(
        DOMAIN,
        services=machine,  # type: ignore[arg-type]
        web=machine,  # type: ignore[arg-type]
        runner=runner,
    )

    assert status.reason is not None and f"{BASE}.service" in status.reason


def test_the_status_of_a_clean_switch_reports_nothing_left_over(
    root: Path, store: NoustStore, app: App, machine: Machine, runner: FakeRunner
) -> None:
    switched_on(machine, store, app)
    leftover_state(runner, "inactive", "disabled")

    status = bluegreen.zero_downtime_status(
        DOMAIN,
        services=machine,  # type: ignore[arg-type]
        web=machine,  # type: ignore[arg-type]
        runner=runner,
    )

    assert status.reason is None and status.hint is None


def test_turning_the_mode_on_refuses_a_stranger_on_greens_port(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    machine.strangers[PORT + 1] = [STRANGER]

    with pytest.raises(DeploymentError, match="python3 \\(pid 999"):
        engine(machine, store, port_free=port_free(machine)).enable(drain_seconds=0)

    assert "template" not in machine.names()
    assert "restart" not in machine.names()
    row = store.get_app(DOMAIN)
    assert row is not None and not row.zero_downtime
    assert BASE in machine.running


def test_turning_the_mode_on_stops_a_green_instance_left_running(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    machine.running[f"{BASE}@green"] = root / "releases" / FIRST

    engine(machine, store, port_free=port_free(machine)).enable(drain_seconds=0)

    assert machine.events[0] == ("stop", f"{BASE}@green")
    row = store.get_app(DOMAIN)
    assert row is not None and row.zero_downtime and row.active_color == "green"


# ---------------------------------------------------------------------------
# After the probe passed
# ---------------------------------------------------------------------------


def test_a_probe_answered_by_a_stranger_that_bound_first_is_refused(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    """The port was free at the check, and taken by someone else before the instance bound."""
    switched_on(machine, store, app)
    machine.strangers[PORT] = [STRANGER]

    with pytest.raises(RolledBackError, match="green instance kept serving") as refused:
        # The check saw a free port: the stranger came after it.
        engine(machine, store, port_free=lambda port: True).activate(
            root / "releases" / FIRST, ReleaseManager(root)
        )

    assert "python3 (pid 999" in str(refused.value)
    assert ("stop", f"{BASE}@blue") in machine.events
    assert "upstream" not in machine.names()
    row = store.get_app(DOMAIN)
    assert row is not None and row.active_color == "green"


def test_a_probe_that_passed_while_the_instance_is_not_active_is_refused(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    """Something answered, but the instance itself is not running: it was not what answered."""
    switched_on(machine, store, app)

    class Crashing(Machine):
        def restart(self, name: str) -> None:
            super().restart(name)
            if name.endswith("@blue"):
                self.running.pop(name)

        def probe(self, url: str, **kwargs: Any) -> bool:
            # Something answers on every port.
            return True

    crashing = Crashing(root, store)
    crashing.running = dict(machine.running)
    crashing.upstream = machine.upstream

    with pytest.raises(RolledBackError, match="not active"):
        engine(crashing, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert "upstream" not in crashing.names()


def test_listeners_nobody_can_name_do_not_block_an_activation(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    """No ss, or a cgroup that cannot be read: the unit's state is what is left to trust."""
    switched_on(machine, store, app)

    switch = engine(
        machine,
        store,
        listeners=lambda port: None,
        unit_facts=lambda unit: bluegreen.UnitFacts(state="", pids=None),
    ).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert switch.to_color == "blue"


# ---------------------------------------------------------------------------
# The switch itself
# ---------------------------------------------------------------------------


def test_an_upstream_that_cannot_be_written_stops_the_new_instance(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    switched_on(machine, store, app)

    def refuse(domain: str, port: int) -> str | None:
        raise NginxError("Could not write the upstream", details="Permission denied")

    machine.write_upstream = refuse  # type: ignore[method-assign]

    with pytest.raises(RolledBackError, match="did not switch"):
        engine(machine, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert f"{BASE}@blue" not in machine.running
    row = store.get_app(DOMAIN)
    assert row is not None and row.active_color == "green"


def test_new_limits_are_put_back_when_the_switch_fails_with_any_wasm_error(
    root: Path, store: NoustStore, app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An NginxError is not a DeploymentError; the old limits must come back all the same."""

    class Refusing:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def activate(self, release: Path, releases: ReleaseManager) -> None:
            raise NginxError("Could not write the upstream", details="Permission denied")

    monkeypatch.setattr(lifecycle, "BlueGreen", Refusing)
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "NginxManager", lambda *args, **kwargs: None)
    put_back: list[bool] = []

    with pytest.raises(DeploymentError, match="previous ones are back") as failure:
        lifecycle._restart_blue_green(app, store, Logger(), lambda: put_back.append(True))

    assert put_back == [True]
    assert "Permission denied" in str(failure.value)


# ---------------------------------------------------------------------------
# Who finds out who listens
# ---------------------------------------------------------------------------


def test_listeners_are_read_from_ss_and_named_by_their_cgroup(
    runner: FakeRunner, tmp_path: Path
) -> None:
    runner.script(
        ["ss", "-ltnpH"],
        stdout=(
            'LISTEN 0 511 127.0.0.1:3100 0.0.0.0:* users:(("node",pid=41,fd=19))\n'
            'LISTEN 0 511 [::1]:3100 [::]:* users:(("node",pid=41,fd=20))\n'
            'LISTEN 0 511 127.0.0.1:3101 0.0.0.0:* users:(("node",pid=42,fd=19))\n'
        ),
    )
    (tmp_path / "41").mkdir()
    (tmp_path / "41" / "cgroup").write_text(
        "0::/system.slice/system-bg\\x2dexample\\x2dcom.slice/bg-example-com@blue.service\n"
    )

    found = bluegreen.listeners_on(3100, runner=runner, proc=tmp_path)

    assert found == [Listener(process="node", pid=41, unit="bg-example-com@blue.service")]
    assert bluegreen.listeners_on(3102, runner=runner, proc=tmp_path) == []


def test_a_listener_ss_cannot_name_is_unknown_not_absent(
    runner: FakeRunner, tmp_path: Path
) -> None:
    runner.script(["ss", "-ltnpH"], stdout="LISTEN 0 511 127.0.0.1:3100 0.0.0.0:*\n")

    assert bluegreen.listeners_on(3100, runner=runner, proc=tmp_path) is None


def test_without_ss_nobody_is_known_to_listen(runner: FakeRunner, tmp_path: Path) -> None:
    runner.script(["ss", "-ltnpH"], exit_code=127, stderr="ss: not found")

    assert bluegreen.listeners_on(3100, runner=runner, proc=tmp_path) is None


def test_a_units_state_and_processes_are_read_from_systemd_and_its_cgroup(
    runner: FakeRunner, tmp_path: Path
) -> None:
    group = "/system.slice/system-bg\\x2dexample\\x2dcom.slice/bg-example-com@blue.service"
    runner.script(
        ["systemctl", "show", "-p", "ActiveState,ControlGroup", "bg-example-com@blue.service"],
        stdout=f"ActiveState=active\nControlGroup={group}\n",
    )
    directory = tmp_path / group.lstrip("/")
    (directory / "worker").mkdir(parents=True)
    (directory / "cgroup.procs").write_text("41\n")
    (directory / "worker" / "cgroup.procs").write_text("43\n44\n")

    facts = bluegreen.unit_facts_of("bg-example-com@blue", runner=runner, cgroups=tmp_path)

    assert facts == bluegreen.UnitFacts(state="active", pids=frozenset({41, 43, 44}))


def test_a_unit_without_a_readable_cgroup_has_unknown_processes(
    runner: FakeRunner, tmp_path: Path
) -> None:
    runner.script(["systemctl", "show"], stdout="ActiveState=inactive\nControlGroup=\n")

    facts = bluegreen.unit_facts_of("x@blue", runner=runner, cgroups=tmp_path)

    assert facts == bluegreen.UnitFacts(state="inactive", pids=None)


def test_a_state_systemd_does_not_give_is_not_held_against_the_instance(
    root: Path, store: NoustStore, app: App, machine: Machine
) -> None:
    """Only a state systemd names refuses; an empty answer is no evidence either way."""
    switched_on(machine, store, app)

    switch = engine(
        machine, store, unit_facts=lambda unit: bluegreen.UnitFacts(state="", pids=None)
    ).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert switch.to_color == "blue"


# ---------------------------------------------------------------------------
# A new application's explicit port
# ---------------------------------------------------------------------------


@pytest.fixture
def owners(store: NoustStore) -> NoustStore:
    """Two applications: one in zero-downtime mode on 3000 and 3001, one on 3005."""
    store.create_app(App(domain="bg.example.com", app_path="/x", port=3000))
    store.set_zero_downtime("bg.example.com", True)
    store.set_active_color("bg.example.com", "blue")
    store.create_app(App(domain="plain.example.com", app_path="/y", port=3005))
    return store


def test_the_idle_instances_port_cannot_be_given_to_a_new_application(owners: NoustStore) -> None:
    issues = preflight.port_taken(3001, allowed_owner_port=None, store=owners)

    assert len(issues) == 1
    assert "bg.example.com" in issues[0] and "green" in issues[0]


def test_another_applications_port_cannot_be_given_to_a_new_application(
    owners: NoustStore,
) -> None:
    issues = preflight.port_taken(3005, allowed_owner_port=None, store=owners)

    assert len(issues) == 1 and "plain.example.com" in issues[0]


def test_an_application_keeps_its_own_port_on_a_redeploy(owners: NoustStore) -> None:
    assert preflight.port_taken(3000, allowed_owner_port=3000, store=owners) == []
    assert preflight.port_taken(3002, allowed_owner_port=None, store=owners) == []


def test_a_redeploy_cannot_move_onto_another_applications_port(owners: NoustStore) -> None:
    issues = preflight.port_taken(3000, allowed_owner_port=3005, store=owners)

    assert len(issues) == 1 and "bg.example.com" in issues[0]


def test_the_deploy_preflight_refuses_a_port_another_application_owns(
    owners: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one chokepoint the CLI, the console and every other deploy go through."""
    from noust.deployers.registry import get_deployer

    deployer: Any = get_deployer("nodejs")
    deployer.configure("new.example.com", "/srv/src", port=3001, webserver="nginx")
    monkeypatch.setattr(preflight, "missing_programs", lambda runner, programs: [])
    monkeypatch.setattr(preflight, "repository_unreachable", lambda runner, source: [])
    monkeypatch.setattr(preflight, "insufficient_disk_space", lambda directory: [])
    monkeypatch.setattr(preflight, "webserver_down", lambda manager, name: [])

    with pytest.raises(NoustError, match="Pre-flight") as refused:
        deployer.pre_flight_check()

    assert "bg.example.com" in str(refused.value)
