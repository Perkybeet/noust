# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_bluegreen.py`` that other tests build on."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, Service, get_store
from noust.deployers import bluegreen
from noust.deployers.bluegreen import BlueGreen

DOMAIN = "bg.example.com"
BASE = "bg-example-com"
PORT = 3100
FIRST = "20260925-100000-aaaaaaa"
SECOND = "20260926-100000-bbbbbbb"
GOOD = "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT);\n"
BROKEN = "throw new Error('boom');\n"
COMMAND = "/usr/bin/npm run start"


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

    def __init__(self, root: Path, store: NoustStore) -> None:
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
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An application on releases: two releases, the second active."""
    directory = tmp_path / "apps" / BASE
    write_release(directory, FIRST)
    write_release(directory, SECOND)
    os.symlink(Path("releases") / SECOND, directory / "current")
    return directory


@pytest.fixture
def app(root: Path, store: NoustStore) -> App:
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
def machine(root: Path, store: NoustStore) -> Machine:
    """The fake machine, with the application's own unit running the active release."""
    fake = Machine(root, store)
    fake.running[BASE] = root / "releases" / SECOND
    return fake


def engine(machine: Machine, store: NoustStore, **kwargs: Any) -> BlueGreen:
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


def switched_on(machine: Machine, store: NoustStore, app: App, *, drain: int | None = 5) -> None:
    """Turn the mode on, and forget what that took."""
    engine(machine, store).enable(drain_seconds=drain)
    machine.events.clear()


def link(root: Path, name: str) -> str:
    """Where a link in the application directory points."""
    return os.readlink(root / name)
