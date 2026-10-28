# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_native_adopt.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import NoustStore, get_store
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ServiceManager
from noust.managers.site_topology import TopologyProbe
from noust.managers.webserver import APACHE_BACKEND, NGINX_BACKEND

UNIT = "legacy-api"
PROXY_SITE = """upstream legacy_backend { server 127.0.0.1:8000; }
server {
    listen 80;
    server_name legacy.example.com;
    location / { proxy_pass http://legacy_backend; }
}
"""

UNIT_FILE = """[Unit]
Description=Legacy API, written by hand

[Service]
User=legacy
WorkingDirectory={work}
ExecStart=/usr/bin/node server.js
Environment=PORT=8000
Restart=always

[Install]
WantedBy=multi-user.target
"""


def wordpress(root: Path) -> str:
    return f"""server {{
    listen 80;
    server_name blog.example.com;
    root {root};
    index index.php;
    include snippets/not-here.conf;
    location / {{ try_files $uri $uri/ /index.php?$args; }}
    location ~ \\.php$ {{ fastcgi_pass unix:/run/php/php8.3-fpm.sock; }}
}}
"""


def static_site(root: Path) -> str:
    return f"""server {{
    listen 80;
    server_name docs.example.com;
    root {root};
    location / {{ try_files $uri $uri/ =404; }}
}}
"""


@dataclass
class Server:
    """The directories of the machine, and the answers its commands give."""

    base: Path
    units: Path
    system_units: Path
    nginx: NginxManager
    apache: ApacheManager
    legacy: Path
    blog: Path
    docs: Path
    proc: Path

    def site(self, name: str, text: str, *, manager: NginxManager | ApacheManager | None = None):
        web = manager or self.nginx
        (web.sites_available / name).write_text(text)
        (web.sites_enabled / name).symlink_to(web.sites_available / name)

    def unit(self, name: str, *, work: Path | None = None) -> Path:
        path = self.units / f"{name}.service"
        path.write_text(UNIT_FILE.format(work=work or self.legacy))
        return path


class Machine(FakeRunner):
    """systemd, ss and docker answering as this server does."""

    def __init__(self, server: Server) -> None:
        super().__init__()
        self.server = server
        #: unit -> ActiveState
        self.active: dict[str, str] = {UNIT: "active"}
        #: port -> (process, pid, unit)
        self.listeners: dict[int, tuple[str, int, str]] = {8000: ("node", 4242, f"{UNIT}.service")}
        self.working = {UNIT: str(server.legacy)}

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv
        if args[:2] == ("ss", "-ltnpH"):
            lines = []
            for port, (process, pid, unit) in self.listeners.items():
                (self.server.proc / str(pid)).mkdir(exist_ok=True)
                (self.server.proc / str(pid) / "cgroup").write_text(f"0::/system.slice/{unit}\n")
                lines.append(
                    f'LISTEN 0 511 127.0.0.1:{port} 0.0.0.0:* users:(("{process}",pid={pid},fd=19))'
                )
            return replace(result, stdout="\n".join(lines) + ("\n" if lines else ""))
        if args[:2] == ("systemctl", "show"):
            unit = next(a for a in args if a.endswith(".service")).removesuffix(".service")
            if "FragmentPath" in args and "--no-pager" not in args:
                known = (self.server.units / f"{unit}.service").exists()
                path = self.server.units / f"{unit}.service" if known else ""
                return replace(result, stdout=f"FragmentPath={path}\n")
            body = (
                f"Id={unit}.service\nActiveState={self.active.get(unit, 'inactive')}\n"
                f"SubState=running\nWorkingDirectory={self.working.get(unit, '')}\n"
                f"FragmentPath={self.server.units / (unit + '.service')}\n"
                "MainPID=4242\nNRestarts=0\nResult=success\nUnitFileState=enabled\n"
            )
            return replace(result, stdout=body)
        if args[:2] == ("systemctl", "is-active"):
            unit = args[-1].removesuffix(".service")
            state = self.active.get(unit, "inactive")
            return replace(result, stdout=f"{state}\n", exit_code=0 if state == "active" else 3)
        if args[:2] == ("systemctl", "is-enabled"):
            return replace(result, stdout="enabled\n")
        if args[:1] == ("journalctl",):
            return replace(result, stdout="Oct 16 10:00:00 host node[4242]: listening on 8000\n")
        return result

    def changing(self) -> list[tuple[str, ...]]:
        """Every command that could change a unit, a site or a service."""
        verbs = {"start", "stop", "restart", "reload", "enable", "disable", "daemon-reload"}
        return [
            call
            for call in self.calls
            if (call[:1] == ("systemctl",) and verbs & set(call[1:2]))
            or call[:1] in (("nginx",), ("certbot",), ("git",))
        ]


def tree(path: Path) -> dict[str, bytes]:
    """Every file under a directory, with its content."""
    return {
        str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()
    }


@pytest.fixture
def store() -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


@pytest.fixture
def server(tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Server:
    units = tmp_path / "etc/systemd/system"
    system_units = tmp_path / "usr/lib/systemd/system"
    for directory in (units, system_units):
        directory.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", units)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (units, system_units))
    nginx = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    apache = ApacheManager(
        backend=replace(
            APACHE_BACKEND,
            sites_available=tmp_path / "apache/sites-available",
            sites_enabled=tmp_path / "apache/sites-enabled",
        )
    )
    for web in (nginx, apache):
        web.sites_available.mkdir(parents=True)
        web.sites_enabled.mkdir(parents=True)
    legacy = tmp_path / "srv/legacy"
    blog = tmp_path / "var/www/blog"
    docs = tmp_path / "var/www/docs"
    for directory in (legacy, blog, docs):
        directory.mkdir(parents=True)
    (legacy / "server.js").write_text("console.log('legacy')\n")
    (legacy / ".env").write_text("SECRET=1\n")
    (blog / "wp-config.php").write_text("<?php // wordpress\n")
    (docs / "index.html").write_text("<h1>docs</h1>\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    return Server(tmp_path, units, system_units, nginx, apache, legacy, blog, docs, proc)


@pytest.fixture
def machine(server: Server) -> Iterator[Machine]:
    fake = Machine(server)
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def probe(machine: Machine, server: Server) -> TopologyProbe:
    return TopologyProbe(
        runner=machine, proc=server.proc, connect=lambda port, host, timeout: True, ttl=0.0
    )


@pytest.fixture
def world(server: Server, machine: Machine, probe: TopologyProbe) -> Server:
    """The operator's three sites and the unit behind the first."""
    server.site("legacy.example.com", PROXY_SITE)
    server.site("blog.example.com", wordpress(server.blog))
    server.site("docs.example.com", static_site(server.docs))
    server.unit(UNIT)
    return server
