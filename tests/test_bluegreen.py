# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for blue/green activation: the next release answers before it serves.

The releases, ``current`` and the instance links are real directories and
links in a temporary tree; systemd, nginx and the HTTP probe are faked
through the seams :class:`~wasm.deployers.bluegreen.BlueGreen` takes. The
fakes simulate what matters: a unit that is started runs the release its link
points at, on its port, and the probe answers for a port only while a unit
that runs a healthy release listens on it. What is pinned:

- The order of an activation: start the idle instance, probe it, write the
  upstream, ``nginx -t``, reload, stop the old one after the drain, and move
  ``current`` last.
- A failed gate stops the new instance and never touches the old one, the
  upstream or ``current``: :class:`RolledBackError` when the old one still
  answers, a plain :class:`DeploymentError` when it does not.
- An upstream nginx refuses is put back before anything reloads.
- Turning the mode on and off is a switch without a cut, undone whole.
- What cannot run as two instances is refused with what to do instead.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

from wasm.core.exceptions import DeploymentError, RolledBackError, ValidationError, WASMError
from wasm.core.fs import DryRunFileSystem, set_fs
from wasm.core.logger import Logger
from wasm.core.store import App, Service, WASMStore, get_store
from wasm.deployers import bluegreen
from wasm.deployers.bluegreen import BlueGreen, instance_command
from wasm.deployers.releases import ReleaseManager

DOMAIN = "bg.example.com"
BASE = "bg-example-com"
PORT = 3100
FIRST = "20260925-100000-aaaaaaa"
SECOND = "20260926-100000-bbbbbbb"
GOOD = "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT);\n"
BROKEN = "throw new Error('boom');\n"
COMMAND = "/usr/bin/npm run start"


# ---------------------------------------------------------------------------
# The fake machine
# ---------------------------------------------------------------------------


class Machine:
    """
    systemd, nginx and the probe, sharing one event log.

    Attributes:
        root: The application directory.
        events: Everything that happened, in order.
        running: Unit to the release directory it runs.
        templates: Template name to what it was written with.
        units: Units of an application's own name, created by the fake.
        upstream: The upstream file's content, or None.
        config_problem: What ``nginx -t`` says, or None when it passes.
        reload_ok: Whether ``systemctl reload nginx`` succeeds.
        site_modes: What the site was rendered as, each time.
        sleeps: Every drain.
    """

    def __init__(self, root: Path, store: WASMStore) -> None:
        self.root = root
        self.store = store
        self.events: list[tuple[Any, ...]] = []
        self.running: dict[str, Path] = {}
        self.templates: dict[str, dict[str, Any]] = {}
        self.units: dict[str, dict[str, Any]] = {}
        self.upstream: str | None = None
        self.config_problem: str | None = None
        self.reload_ok = True
        self.site_modes: list[str] = []
        self.sleeps: list[float] = []
        self.refresh_fails = False
        self.strangers: dict[int, list[bluegreen.Listener]] = {}

    # -- systemd -----------------------------------------------------------

    def install_instance_template(self, name: str, **kwargs: Any) -> str | None:
        self.events.append(("template", name))
        previous = self.templates.get(name)
        self.templates[name] = kwargs
        return None if previous is None else repr(previous)

    def restore_template(self, name: str, previous: str | None) -> None:
        self.events.append(("restore_template", name, previous))
        if previous is None:
            self.templates.pop(name, None)

    def remove_template(self, name: str) -> bool:
        self.events.append(("remove_template", name))
        return self.templates.pop(name, None) is not None

    def restart(self, name: str) -> None:
        if "@" in name:
            link = self.root / "colors" / name.split("@")[1]
        else:
            link = self.root / "current"
        release = Path(os.path.realpath(link))
        self.events.append(("restart", name, release.name))
        self.running[name] = release

    def start(self, name: str) -> None:
        self.restart(name)

    def stop(self, name: str) -> bool:
        self.events.append(("stop", name))
        self.running.pop(name, None)
        return True

    def enable(self, name: str) -> bool:
        self.events.append(("enable", name))
        return True

    def disable(self, name: str) -> bool:
        self.events.append(("disable", name))
        return True

    def logs(self, name: str, lines: int = 50) -> str:
        return f"Error: boom (journal of {name})"

    def service_exists(self, name: str) -> bool:
        return name in self.units

    def create_service(self, name: str, **kwargs: Any) -> None:
        self.events.append(("create_service", name))
        self.units[name] = kwargs

    def delete_service(self, name: str, keep_record: bool = False) -> None:
        self.events.append(("delete_service", name, keep_record))
        self.running.pop(name, None)
        self.units.pop(name, None)

    def get_status(self, name: str) -> dict[str, Any]:
        return {"active_state": "active" if name in self.running else "inactive"}

    # -- nginx -------------------------------------------------------------

    def write_upstream(self, domain: str, port: int) -> str | None:
        self.events.append(("upstream", port))
        previous = self.upstream
        self.upstream = f"server 127.0.0.1:{port};"
        return previous

    def restore_upstream(self, domain: str, previous: str | None) -> None:
        self.events.append(("restore_upstream", previous))
        self.upstream = previous

    def remove_upstream(self, domain: str) -> bool:
        self.events.append(("remove_upstream",))
        self.upstream = None
        return True

    def config_errors(self) -> str | None:
        self.events.append(("nginx -t",))
        return self.config_problem

    def reload(self) -> bool:
        self.events.append(("reload",))
        return self.reload_ok

    def site_exists(self, domain: str) -> bool:
        return True

    def get_site_config(self, domain: str) -> str:
        return (
            "include /etc/nginx/wasm-upstreams/x.conf;"
            if self.site_modes[-1:] == ["upstream"]
            else "proxy_pass http://127.0.0.1:3100;"
        )

    def upstream_path(self, domain: str) -> Path:
        return Path("/etc/nginx/wasm-upstreams/x.conf")

    def upstream_port(self, domain: str) -> int | None:
        match = re.search(r":(\d+);", self.upstream or "")
        return int(match.group(1)) if match else None

    # -- the rest ----------------------------------------------------------

    def refresh_site(self, app: App) -> None:
        row = self.store.get_app(app.domain)
        assert row is not None
        mode = "upstream" if row.zero_downtime else "direct"
        self.events.append(("site", mode))
        if self.refresh_fails:
            raise ValidationError("nginx rejected the new configuration", details="emerg: boom")
        self.site_modes.append(mode)

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", seconds))
        self.sleeps.append(seconds)

    def probe(self, url: str, **kwargs: Any) -> bool:
        port = int(re.search(r":(\d+)/", url).group(1))  # type: ignore[union-attr]
        self.events.append(("probe", port))
        for unit, release in self.running.items():
            listens = PORT + 1 if unit.endswith("@green") else PORT
            if listens == port and "throw" not in (release / "server.js").read_text():
                return True
        on_attempt = kwargs.get("on_attempt")
        if on_attempt is not None:
            on_attempt("Health check attempt 1 failed: [Errno 111] Connection refused")
        return False

    def names(self) -> list[str]:
        """The events, reduced to their first word."""
        return [event[0] for event in self.events]

    # -- who listens where -------------------------------------------------

    @staticmethod
    def port_of(unit: str) -> int:
        """The port a unit listens on while it runs: green the next one, the rest the app's."""
        return PORT + 1 if unit.endswith("@green") else PORT

    @staticmethod
    def pid_of(unit: str) -> int:
        """A stable process id for a unit of the fake machine."""
        return 4000 + sum(map(ord, unit)) % 1000

    def listeners(self, port: int) -> list[bluegreen.Listener]:
        """What ``ss`` would say listens on a port: running units, and any stranger."""
        found = [
            bluegreen.Listener(process="node", pid=self.pid_of(unit), unit=f"{unit}.service")
            for unit in self.running
            if self.port_of(unit) == port
        ]
        return found + list(self.strangers.get(port, ()))

    def unit_facts(self, unit: str) -> bluegreen.UnitFacts:
        """A unit's state, and the processes in its cgroup: its own, while it runs."""
        if unit in self.running:
            return bluegreen.UnitFacts(state="active", pids=frozenset({self.pid_of(unit)}))
        return bluegreen.UnitFacts(state="inactive", pids=frozenset())


def write_release(root: Path, release_id: str, server: str = GOOD) -> Path:
    """Create a release directory with a server."""
    path = root / "releases" / release_id
    path.mkdir(parents=True)
    (path / "server.js").write_text(server)
    return path


@pytest.fixture(autouse=True)
def nobody_can_name_listeners(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Engines built by the deployer and the lifecycle ask ss and the cgroups by default.

    Here nobody can say who listens: the instance's state, which the fake
    machine answers, is what the gate goes on. :func:`engine` injects the
    fake machine's own answers instead. The status asks systemd about the
    unit the application ran as before the mode; here none is left over.
    """
    monkeypatch.setattr(bluegreen, "listeners_on", lambda port, **kwargs: None)
    monkeypatch.setattr(
        bluegreen, "unit_facts_of", lambda unit, **kwargs: bluegreen.UnitFacts("", None)
    )
    monkeypatch.setattr(bluegreen, "leftover_unit", lambda app, **kwargs: None)


@pytest.fixture
def store() -> Any:
    """The process-wide store, at the location conftest redirects it to."""
    WASMStore.reset_instance()
    instance = get_store()
    yield instance
    WASMStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An application on releases: two releases, the second active."""
    directory = tmp_path / "apps" / BASE
    write_release(directory, FIRST)
    write_release(directory, SECOND)
    os.symlink(Path("releases") / SECOND, directory / "current")
    return directory


@pytest.fixture
def app(root: Path, store: WASMStore) -> App:
    """The application's rows: a Node app on releases, behind nginx."""
    row = store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            port=PORT,
            app_path=str(root),
            layout="releases",
            webserver="nginx",
            status="running",
        )
    )
    store.create_service(
        Service(
            app_id=row.id,
            name=BASE,
            unit_file=f"/etc/systemd/system/{BASE}.service",
            working_directory=str(root / "current"),
            command=COMMAND,
            environment={"PORT": str(PORT), "NODE_ENV": "production"},
        )
    )
    return row


@pytest.fixture
def machine(root: Path, store: WASMStore) -> Machine:
    """The fake machine, with the application's own unit running the active release."""
    fake = Machine(root, store)
    fake.running[BASE] = root / "releases" / SECOND
    return fake


def engine(machine: Machine, store: WASMStore, **kwargs: Any) -> BlueGreen:
    """Build the engine over the fakes, for the application as the store has it now."""
    row = store.get_app(DOMAIN)
    assert row is not None
    return BlueGreen(
        row,
        logger=Logger(),
        store=store,
        services=machine,  # type: ignore[arg-type]
        web=machine,  # type: ignore[arg-type]
        probe=machine.probe,
        sleep=machine.sleep,
        refresh_site=machine.refresh_site,
        port_free=kwargs.pop("port_free", lambda port: True),
        listeners=kwargs.pop("listeners", machine.listeners),
        unit_facts=kwargs.pop("unit_facts", machine.unit_facts),
        **kwargs,
    )


def switched_on(machine: Machine, store: WASMStore, app: App, *, drain: int | None = 5) -> None:
    """Turn the mode on, and forget what that took."""
    engine(machine, store).enable(drain_seconds=drain)
    machine.events.clear()


def link(root: Path, name: str) -> str:
    """Where a link in the application directory points."""
    return os.readlink(root / name)


# ---------------------------------------------------------------------------
# Turning the mode on
# ---------------------------------------------------------------------------


def test_enabling_starts_green_beside_the_unit_and_retires_the_unit_after_the_switch(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """The unit serves until nginx has moved to green; nothing is ever down."""
    engine(machine, store).enable(drain_seconds=5)

    row = store.get_app(DOMAIN)
    assert row is not None and row.zero_downtime and row.active_color == "green"
    assert row.drain_seconds == 5
    assert machine.names() == [
        "template",
        "restart",
        "probe",
        "upstream",
        "site",
        "enable",
        "sleep",
        "delete_service",
    ]
    assert machine.events[1] == ("restart", f"{BASE}@green", SECOND)
    assert machine.events[2] == ("probe", PORT + 1)
    assert machine.events[3] == ("upstream", PORT + 1)
    assert machine.events[4] == ("site", "upstream")
    assert machine.events[-1] == ("delete_service", BASE, True), "the row is kept"
    assert link(root, "colors/blue") == f"../releases/{SECOND}"
    assert link(root, "colors/green") == f"../releases/{SECOND}"
    assert (root / "colors/blue.env").read_text() == f"PORT={PORT}\n"
    assert (root / "colors/green.env").read_text() == f"PORT={PORT + 1}\n"
    template = machine.templates[BASE]
    assert template["command"] == COMMAND
    assert "PORT" not in template["environment"]
    assert template["colors_directory"] == str(root / "colors")
    assert store.get_service(BASE) is not None


def test_a_green_that_does_not_answer_leaves_everything_as_it_was(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """The unit never stopped; the template, the links and the mode are gone again."""
    (root / "releases" / SECOND / "server.js").write_text(BROKEN)
    machine.running[BASE] = root / "releases" / FIRST

    with pytest.raises(DeploymentError, match="keeps running as") as failure:
        engine(machine, store).enable(drain_seconds=None)

    assert "Connection refused" in failure.value.details
    assert "journal of bg-example-com@green" in failure.value.details
    row = store.get_app(DOMAIN)
    assert row is not None and not row.zero_downtime and row.active_color is None
    assert BASE not in machine.templates
    assert not (root / "colors").exists()
    assert ("stop", f"{BASE}@green") in machine.events
    assert BASE in machine.running, "the application's own unit was never touched"
    assert machine.upstream is None


def test_a_site_nginx_refuses_turns_the_mode_back_off(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """The upstream goes, the flag goes, green stops; the unit still serves."""
    machine.refresh_fails = True

    with pytest.raises(ValidationError, match="rejected"):
        engine(machine, store).enable(drain_seconds=None)

    row = store.get_app(DOMAIN)
    assert row is not None and not row.zero_downtime
    assert machine.upstream is None
    assert f"{BASE}@green" not in machine.running
    assert BASE in machine.running
    assert not (root / "colors").exists()


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


def test_an_activation_starts_probes_switches_drains_and_stops_in_that_order(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """New first, traffic second, old last; current moves at the very end."""
    switched_on(machine, store, app)
    releases = ReleaseManager(root)

    switch = engine(machine, store).activate(root / "releases" / FIRST, releases)

    assert (switch.from_color, switch.to_color, switch.port) == ("green", "blue", PORT)
    assert machine.names() == [
        "restart",
        "probe",
        "upstream",
        "nginx -t",
        "reload",
        "enable",
        "disable",
        "sleep",
        "stop",
    ]
    assert machine.events[0] == ("restart", f"{BASE}@blue", FIRST)
    assert machine.events[1] == ("probe", PORT)
    assert machine.events[2] == ("upstream", PORT)
    assert machine.events[-2] == ("sleep", 5)
    assert machine.events[-1] == ("stop", f"{BASE}@green")
    assert link(root, "current") == f"releases/{FIRST}"
    assert link(root, "colors/blue") == f"../releases/{FIRST}"
    row = store.get_app(DOMAIN)
    assert row is not None and row.active_color == "blue"


def test_a_release_that_fails_the_gate_is_stopped_and_the_old_instance_never_is(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """Nothing to undo in nginx: the upstream was never touched."""
    switched_on(machine, store, app)
    (root / "releases" / FIRST / "server.js").write_text(BROKEN)

    with pytest.raises(RolledBackError, match="green instance kept serving") as failure:
        engine(machine, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert "Connection refused" in failure.value.details
    assert "journal of bg-example-com@blue" in failure.value.details
    assert ("stop", f"{BASE}@blue") in machine.events
    assert ("stop", f"{BASE}@green") not in machine.events
    assert "upstream" not in machine.names()
    assert "reload" not in machine.names()
    assert link(root, "current") == f"releases/{SECOND}"
    assert machine.upstream == f"server 127.0.0.1:{PORT + 1};"
    row = store.get_app(DOMAIN)
    assert row is not None and row.active_color == "green"


def test_a_failed_gate_with_the_old_instance_down_too_is_not_a_rollback(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """RolledBackError promises the previous version answers; here it does not."""
    switched_on(machine, store, app)
    (root / "releases" / FIRST / "server.js").write_text(BROKEN)
    machine.running.pop(f"{BASE}@green")

    with pytest.raises(DeploymentError, match="not answering either") as failure:
        engine(machine, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert not isinstance(failure.value, RolledBackError)


def test_an_upstream_nginx_refuses_is_put_back_before_anything_reloads(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """nginx -t fails: the old upstream is back, nothing reloaded, the new one stopped."""
    switched_on(machine, store, app)
    before = machine.upstream
    machine.config_problem = "nginx: [emerg] unexpected end of file"

    with pytest.raises(RolledBackError, match="did not switch") as failure:
        engine(machine, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert failure.value.details == "nginx: [emerg] unexpected end of file"
    assert machine.upstream == before
    assert "reload" not in machine.names()
    assert machine.names()[-3:] == ["restore_upstream", "stop", "probe"]
    assert ("stop", f"{BASE}@green") not in machine.events
    assert link(root, "current") == f"releases/{SECOND}"


def test_a_reload_that_fails_puts_the_old_upstream_back_and_reloads_it(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """A valid configuration nginx did not load: what nginx holds matches the disk again."""
    switched_on(machine, store, app)
    before = machine.upstream
    machine.reload_ok = False

    with pytest.raises(RolledBackError, match="did not switch"):
        engine(machine, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert machine.upstream == before
    assert machine.names().count("reload") == 2


def test_rolling_back_through_releases_is_the_same_switch(
    root: Path, store: WASMStore, app: App, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wasm releases rollback: the idle instance takes the previous release; one history row."""
    from wasm.deployers import lifecycle

    switched_on(machine, store, app)
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine)
    monkeypatch.setattr(lifecycle, "NginxManager", lambda **kwargs: machine)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", machine.probe)
    monkeypatch.setattr(bluegreen.time, "sleep", machine.sleep)

    outcome = lifecycle.activate_release(DOMAIN, trigger="panel")

    assert outcome.release.id == FIRST and outcome.went_back
    assert link(root, "current") == f"releases/{FIRST}"
    assert machine.events[0] == ("restart", f"{BASE}@blue", FIRST)
    assert ("stop", f"{BASE}@green") in machine.events
    row = store.list_deployments(DOMAIN)[0]
    assert (row.status, row.triggered_by) == ("success", "panel")


def test_a_rollback_that_fails_its_gate_is_recorded_as_rolled_back(
    root: Path, store: WASMStore, app: App, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The history row fails, and says the previous version kept serving."""
    from wasm.deployers import deploy_events, lifecycle
    from wasm.deployers.deploy_events import DeployEventKind

    switched_on(machine, store, app)
    (root / "releases" / FIRST / "server.js").write_text(BROKEN)
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine)
    monkeypatch.setattr(lifecycle, "NginxManager", lambda **kwargs: machine)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", machine.probe)
    seen: list[Any] = []
    stop = deploy_events.subscribe(seen.append)
    try:
        with pytest.raises(RolledBackError):
            lifecycle.activate_release(DOMAIN, FIRST)
    finally:
        stop()

    assert link(root, "current") == f"releases/{SECOND}"
    assert store.list_deployments(DOMAIN)[0].status == "failed"
    assert DeployEventKind.ROLLED_BACK in {event.kind for event in seen}


# ---------------------------------------------------------------------------
# Turning the mode off
# ---------------------------------------------------------------------------


def test_disabling_from_green_starts_the_unit_and_removes_both_instances(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """The unit answers on the application's port before the site moves back to it."""
    switched_on(machine, store, app)

    engine(machine, store).disable()

    row = store.get_app(DOMAIN)
    assert row is not None and not row.zero_downtime and row.active_color is None
    names = machine.names()
    assert names[:4] == ["create_service", "restart", "probe", "site"]
    assert machine.events[1] == ("restart", BASE, SECOND)
    assert machine.events[2] == ("probe", PORT)
    assert machine.events[3] == ("site", "direct")
    assert names.index("sleep") < names.index("delete_service")
    assert ("delete_service", f"{BASE}@blue", True) in machine.events
    assert ("delete_service", f"{BASE}@green", True) in machine.events
    assert ("remove_template", BASE) in machine.events
    assert names[-1] == "remove_upstream"
    assert not (root / "colors").exists()
    assert set(machine.running) == {BASE}


def test_disabling_from_blue_moves_to_green_first(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """Blue holds the application's port, which its own unit needs back."""
    switched_on(machine, store, app)
    engine(machine, store).activate(root / "releases" / SECOND, ReleaseManager(root))
    machine.events.clear()
    row = store.get_app(DOMAIN)
    assert row is not None and row.active_color == "blue"

    engine(machine, store).disable()

    assert machine.events[0] == ("restart", f"{BASE}@green", SECOND)
    assert ("stop", f"{BASE}@blue") in machine.events
    assert machine.names().index("create_service") > machine.names().index("stop")
    assert set(machine.running) == {BASE}


def test_a_unit_that_does_not_answer_leaves_the_instances_serving(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """Turning the mode off is undone whole too."""
    switched_on(machine, store, app)
    original = machine.probe

    def refuse_own_port(url: str, **kwargs: Any) -> bool:
        if f":{PORT}/" in url and BASE in machine.running:
            kwargs["on_attempt"]("Health check attempt 1 failed: [Errno 111] Connection refused")
            return False
        return original(url, **kwargs)

    bg = engine(machine, store)
    bg._probe = refuse_own_port

    with pytest.raises(DeploymentError, match="green instance keeps serving"):
        bg.disable()

    row = store.get_app(DOMAIN)
    assert row is not None and row.zero_downtime and row.active_color == "green"
    assert ("delete_service", BASE, True) in machine.events
    assert BASE not in machine.units
    assert f"{BASE}@green" in machine.running


# ---------------------------------------------------------------------------
# Who may use it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"layout": "inplace"}, "deployed in place"),
        ({"is_static": True, "app_type": "static"}, "static site"),
        ({"app_type": "docker-compose"}, "deploys in place"),
        ({"app_type": "monorepo"}, "deploys in place"),
        ({"webserver": "apache"}, "nginx-only in 2.2"),
    ],
)
def test_what_cannot_run_twice_is_refused_with_the_way_forward(
    store: WASMStore, app: App, change: dict[str, Any], message: str
) -> None:
    """Each refusal says why, and what to do."""
    row = store.get_app(DOMAIN)
    assert row is not None
    for key, value in change.items():
        setattr(row, key, value)

    with pytest.raises(ValidationError, match=message) as refusal:
        bluegreen.check_eligible(row, store=store)

    assert refusal.value.details


def test_the_port_after_the_apps_own_must_be_free(store: WASMStore, app: App) -> None:
    """Another application on port + 1, or anything listening there, is refused."""
    other = store.create_app(App(domain="other.example.com", port=PORT + 1, app_type="nodejs"))
    row = store.get_app(DOMAIN)
    assert row is not None

    with pytest.raises(ValidationError, match=r"other\.example\.com"):
        bluegreen.check_eligible(row, store=store)

    store.delete_app(other.domain)
    with pytest.raises(ValidationError, match="is in use"):
        bluegreen.check_eligible(row, store=store, port_free=lambda port: port != PORT + 1)
    bluegreen.check_eligible(row, store=store, port_free=lambda port: True)


def test_a_site_with_its_own_routes_is_refused(root: Path, store: WASMStore, app: App) -> None:
    """wasm.nginx.yaml routes to ports two instances cannot share."""
    (root / "releases" / SECOND / "wasm.nginx.yaml").write_text("routes: []\n")
    row = store.get_app(DOMAIN)
    assert row is not None

    with pytest.raises(ValidationError, match="routes of its own"):
        bluegreen.check_eligible(row, store=store)


# ---------------------------------------------------------------------------
# The entry point the CLI and the API share
# ---------------------------------------------------------------------------


def test_a_rehearsal_checks_and_changes_nothing(
    store: WASMStore, app: App, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run: the checks run, no engine is built, the row is untouched."""
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)
    set_fs(DryRunFileSystem())

    change = bluegreen.set_zero_downtime(
        DOMAIN, True, engine=lambda row, log: pytest.fail("a rehearsal switched")
    )

    assert change.rehearsed and change.enabled
    row = store.get_app(DOMAIN)
    assert row is not None and not row.zero_downtime


def test_changing_the_drain_of_an_app_already_on_only_records_it(
    store: WASMStore, app: App, machine: Machine
) -> None:
    """No switch runs for a new drain."""
    switched_on(machine, store, app)

    change = bluegreen.set_zero_downtime(
        DOMAIN, True, drain_seconds=30, engine=lambda row, log: pytest.fail("switched")
    )

    assert change.changed and change.drain_seconds == 30
    row = store.get_app(DOMAIN)
    assert row is not None and row.drain_seconds == 30 and row.active_color == "green"


def test_set_zero_downtime_switches_through_the_engine_both_ways(
    store: WASMStore, app: App, machine: Machine
) -> None:
    """On, then off: the round trip ends where it started."""
    build = lambda row, log: engine(machine, store)  # noqa: E731

    on = bluegreen.set_zero_downtime(DOMAIN, True, drain_seconds=0, engine=build)
    status = bluegreen.zero_downtime_status(DOMAIN, services=machine, web=machine)  # type: ignore[arg-type]
    off = bluegreen.set_zero_downtime(DOMAIN, False, engine=build)

    assert (on.enabled, on.active_color, on.changed) == (True, "green", True)
    assert status.enabled and status.active_color == "green"
    assert [(i.color, i.port, i.release, i.serving) for i in status.instances] == [
        ("blue", PORT, SECOND, False),
        ("green", PORT + 1, SECOND, True),
    ]
    assert status.upstream_port == PORT + 1
    assert (off.enabled, off.changed) == (False, True)
    row = store.get_app(DOMAIN)
    assert row is not None and not row.zero_downtime
    assert machine.sleeps == [], "a drain of 0 does not wait"


def test_the_status_of_an_app_that_cannot_use_the_mode_says_why(store: WASMStore, app: App) -> None:
    """Off and ineligible: the reason and the hint, for the console to show."""
    row = store.get_app(DOMAIN)
    assert row is not None
    row.layout = "inplace"
    store.update_app(row)

    status = bluegreen.zero_downtime_status(DOMAIN)

    assert not status.enabled and not status.eligible
    assert status.reason is not None and "in place" in status.reason
    assert status.hint is not None and "migrate" in status.hint


def test_an_unknown_drain_is_refused_before_anything(store: WASMStore, app: App) -> None:
    """The range is the store's."""
    with pytest.raises(ValidationError):
        bluegreen.set_zero_downtime(DOMAIN, True, drain_seconds=301)


# ---------------------------------------------------------------------------
# Every app type's command works for either instance
# ---------------------------------------------------------------------------


def test_node_and_next_commands_read_port_from_the_instance_environment() -> None:
    """npm start, next start and a standalone server read PORT; nothing to change."""
    root = Path("/var/www/apps/shop")
    for command in (
        "/usr/bin/npm run start",
        "/usr/bin/pnpm run start",
        "/usr/bin/node .next/standalone/server.js",
    ):
        assert instance_command(command, app_path=root, port=PORT, app_type="nodejs") == command


def test_a_python_command_binds_the_instance_port_from_its_own_release() -> None:
    """gunicorn's -b names the port, and the interpreter is found through current."""
    from wasm.deployers.helpers.release_build import StagedRelease
    from wasm.deployers.python import PythonDeployer

    root = Path("/var/www/apps/shop")
    deployer = PythonDeployer()
    deployer.configure("shop.example.com", "https://example.com/r.git", port=PORT, app_path=root)
    deployer._layout = "releases"
    release = root / "releases" / FIRST
    deployer._staged = StagedRelease(path=release, commit=None, manager=ReleaseManager(root))
    deployer.venv_path = release / "venv"
    deployer.wsgi_app = "app:app"

    command = instance_command(
        deployer.get_start_command(), app_path=root, port=PORT, app_type="python"
    )

    assert command == (
        "/var/www/apps/shop/colors/%i/venv/bin/python -m gunicorn app:app -w 4 -b 0.0.0.0:${PORT}"
    )


def test_a_vite_preview_is_told_the_instance_port() -> None:
    """vite preview does not read PORT; the flag is added, after npm's separator."""
    root = Path("/var/www/apps/shop")

    assert (
        instance_command("/usr/bin/npm run preview", app_path=root, port=PORT, app_type="vite")
        == "/usr/bin/npm run preview -- --port ${PORT} --strictPort"
    )
    assert (
        instance_command("/usr/bin/yarn preview", app_path=root, port=PORT, app_type="vite")
        == "/usr/bin/yarn preview --port ${PORT} --strictPort"
    )


def test_a_port_that_only_looks_like_the_apps_is_left_alone() -> None:
    """-w 3100 workers is not a port; app:3100 is not a bind address with a host."""
    root = Path("/srv/a")
    command = "/usr/bin/gunicorn app:app -w 3100 --timeout 31000"

    assert instance_command(command, app_path=root, port=PORT, app_type="python") == command


# ---------------------------------------------------------------------------
# Deploys and updates of an application in the mode
# ---------------------------------------------------------------------------


def test_a_redeploy_writes_the_template_and_activates_blue_green(
    tmp_path: Path, root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """The deployer's own activation: no restart of current, the idle instance takes it."""
    from wasm.deployers.helpers.release_build import StagedRelease
    from wasm.deployers.nodejs import NodeJSDeployer

    switched_on(machine, store, app)
    deployer = NodeJSDeployer()
    deployer.configure(DOMAIN, "https://example.com/r.git", port=PORT, app_path=root)
    deployer.resolve_layout()
    deployer.service_manager = machine  # type: ignore[assignment]
    deployer._webserver_manager = lambda: machine  # type: ignore[method-assign]
    third = write_release(root, "20260927-100000-ccccccc")
    deployer._staged = StagedRelease(path=third, commit=None, manager=ReleaseManager(root))
    from wasm.deployers import base as base_module

    original = base_module.wait_until_healthy
    base_module.wait_until_healthy = machine.probe  # type: ignore[assignment]
    bluegreen_sleep = bluegreen.time.sleep
    bluegreen.time.sleep = machine.sleep  # type: ignore[assignment]
    try:
        deployer.create_service()
        deployer._activate_release()
    finally:
        base_module.wait_until_healthy = original
        bluegreen.time.sleep = bluegreen_sleep  # type: ignore[assignment]

    assert machine.events[0] == ("template", BASE)
    assert ("restart", f"{BASE}@blue", third.name) in machine.events
    assert ("restart", BASE, third.name) not in machine.events
    assert link(root, "current") == f"releases/{third.name}"
    row = store.get_app(DOMAIN)
    assert row is not None and row.active_color == "blue"


def test_a_redeploy_that_renders_an_advanced_site_is_refused_in_the_mode(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """The instances share one upstream; a site with routes of its own cannot."""
    from wasm.deployers.nodejs import NodeJSDeployer

    switched_on(machine, store, app)
    deployer = NodeJSDeployer()
    deployer.configure(DOMAIN, "https://example.com/r.git", port=PORT, app_path=root)
    deployer.resolve_layout()
    deployer.get_nginx_template = lambda: "advanced"  # type: ignore[method-assign]

    with pytest.raises(DeploymentError, match="needs the proxy site"):
        deployer._write_site(machine, with_ssl=False)  # type: ignore[arg-type]


def test_nothing_about_the_mode_is_consulted_for_a_new_application(
    root: Path, store: WASMStore
) -> None:
    """No row, no mode: a first deploy activates exactly as before."""
    from wasm.deployers.nodejs import NodeJSDeployer

    deployer = NodeJSDeployer()
    deployer.configure("new.example.com", "https://example.com/r.git", port=PORT, app_path=root)
    deployer.resolve_layout()

    assert deployer._zero_downtime_app() is None


def test_limits_restarted_in_the_mode_switch_instances_instead_of_restarting(
    root: Path, store: WASMStore, app: App, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The template gets the limits once; the idle instance starts under them first."""
    from wasm.deployers import lifecycle
    from wasm.managers.service_manager import ResourceLimits

    switched_on(machine, store, app)
    written: list[tuple[str, ResourceLimits]] = []
    machine.set_resource_limits = lambda unit, limits: (  # type: ignore[attr-defined]
        written.append((unit, limits)) or "previous body"
    )
    machine.app_units = lambda row: [f"{BASE}@green", f"{BASE}@blue"]  # type: ignore[attr-defined]
    machine.update_config = lambda unit, body: None  # type: ignore[attr-defined]
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine)
    monkeypatch.setattr(lifecycle, "NginxManager", lambda **kwargs: machine)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", machine.probe)
    monkeypatch.setattr(bluegreen.time, "sleep", machine.sleep)

    change = lifecycle.set_resource_limits(DOMAIN, ResourceLimits(memory_max_mb=256), restart=True)

    assert written == [(f"{BASE}@green", ResourceLimits(memory_max_mb=256))]
    assert change.units == (f"{BASE}@green", f"{BASE}@blue") and change.restarted
    assert machine.events[0] == ("restart", f"{BASE}@blue", SECOND)
    row = store.get_app(DOMAIN)
    assert row is not None and row.memory_max_mb == 256 and row.active_color == "blue"


def test_an_engine_without_a_serving_instance_refuses_to_guess(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """A row in the mode without a color says how to repair it."""
    store.set_zero_downtime(DOMAIN, True)

    with pytest.raises(DeploymentError, match="no instance is recorded") as failure:
        engine(machine, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert "off, then on" in failure.value.details


def test_errors_are_wasm_errors() -> None:
    """The CLI and API boundaries translate them; nothing else escapes."""
    assert issubclass(RolledBackError, WASMError)


def test_a_rehearsed_activation_starts_nothing_and_moves_nothing(
    root: Path, store: WASMStore, app: App, machine: Machine
) -> None:
    """--dry-run: the switch is described, not made."""
    switched_on(machine, store, app)
    set_fs(DryRunFileSystem())

    switch = engine(machine, store).activate(root / "releases" / FIRST, ReleaseManager(root))

    assert (switch.from_color, switch.to_color) == ("green", "blue")
    assert machine.events == []
    assert link(root, "current") == f"releases/{SECOND}"


def test_the_gate_an_operator_restart_passes_is_the_serving_instances(
    store: WASMStore, app: App, machine: Machine
) -> None:
    """health_gate_for: the unit and the port that answer now."""
    from wasm.deployers import lifecycle

    switched_on(machine, store, app)
    row = store.get_app(DOMAIN)
    assert row is not None

    gate = lifecycle.health_gate_for(row, store, Logger())

    assert gate.unit == f"{BASE}@green"
    assert gate.url == f"http://127.0.0.1:{PORT + 1}/"
