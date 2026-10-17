# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adopting a site that already runs on nginx or Apache (checklist B-85).

The server these tests model is the one of somebody who comes from another panel: a Node API in
``/srv/legacy`` that a unit written by hand runs and nginx proxies to, a WordPress in
``/var/www/blog`` behind PHP-FPM, and a folder of files. None of it was deployed by Noust, so
adopting it has one rule above the others: after it, the unit, the site and the directory are byte
for byte what they were, and the only thing that changed is the store. What Noust may do from then
on is start, stop and restart the unit and read its logs; everything that would rebuild, rewrite or
delete is refused at the place that would do it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ServiceError, ValidationError
from noust.core.native_adoption_store import adopted_units, get_record
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, AppType, NoustStore, get_store, is_adopted
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
TLS_PROXY_SITE = """server {
    listen 443 ssl;
    server_name tls.example.com;
    ssl_certificate /etc/letsencrypt/live/tls.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tls.example.com/privkey.pem;
    location / { proxy_pass http://127.0.0.1:8000; }
}
"""
REDIRECT_SITE = """server {
    listen 80;
    server_name old.example.com;
    return 301 https://new.example.com$request_uri;
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


def webs(server: Server) -> list[Any]:
    return [server.nginx, server.apache]


def plan(server: Server, probe: TopologyProbe, domain: str, **options: Any) -> Any:
    from noust.deployers.native_adopt import plan_adoption

    return plan_adoption(domain, managers=webs(server), probe=probe, **options)


def adopt(server: Server, probe: TopologyProbe, domain: str, **options: Any) -> Any:
    from noust.deployers.native_adopt import adopt_site

    return adopt_site(domain, managers=webs(server), probe=probe, **options)


# -- What is on the server ------------------------------------------------------------------


def test_discovery_says_what_each_site_the_operator_wrote_is(
    world: Server, probe: TopologyProbe
) -> None:
    from noust.deployers.native_adopt import discover

    found = {s.site: s for s in discover(managers=webs(world), probe=probe)}

    legacy = found["legacy.example.com"]
    assert (legacy.kind, legacy.port, legacy.unit, legacy.unit_active) == (
        "proxy",
        8000,
        UNIT,
        True,
    )
    assert legacy.adoptable and legacy.domains == ("legacy.example.com",)
    blog = found["blog.example.com"]
    assert (blog.kind, blog.root, blog.unit) == ("php", str(world.blog), None)
    assert blog.upstream == "unix:/run/php/php8.3-fpm.sock" and blog.adoptable
    docs = found["docs.example.com"]
    assert (docs.kind, docs.root, docs.unit) == ("static", str(world.docs), None)


def test_discovery_leaves_out_what_noust_wrote_and_what_an_application_records(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    from noust.deployers.native_adopt import discover

    world.site("shop.example.com", "# Generated by Noust\n" + PROXY_SITE.replace("legacy", "shop"))
    world.site("api.example.com", PROXY_SITE.replace("legacy", "api"))
    store.create_app(App(domain="api.example.com", app_type="nodejs", app_path="/var/www/apps/api"))
    store.set_app_site_name("api.example.com", "api.example.com")

    names = {s.site for s in discover(managers=webs(world), probe=probe)}

    assert "shop.example.com" not in names
    assert "api.example.com" not in names
    assert {"legacy.example.com", "blog.example.com", "docs.example.com"} <= names


def test_discovery_says_why_a_site_cannot_be_adopted(world: Server, probe: TopologyProbe) -> None:
    from noust.deployers.native_adopt import discover

    world.site("old.example.com", REDIRECT_SITE)
    world.site("ghost.example.com", PROXY_SITE.replace("legacy", "ghost").replace("8000", "9999"))

    found = {s.site: s for s in discover(managers=webs(world), probe=probe)}

    assert not found["old.example.com"].adoptable
    assert "redirects" in (found["old.example.com"].reason or "")
    ghost = found["ghost.example.com"]
    assert not ghost.adoptable and ghost.unit is None
    assert "nothing listens on 127.0.0.1:9999" in (ghost.reason or "")


def test_a_port_a_container_publishes_is_not_proposed_a_unit(
    world: Server, machine: Machine, probe: TopologyProbe
) -> None:
    from noust.deployers.native_adopt import discover

    machine.listeners.clear()
    machine.script(
        ["docker", "ps"],
        stdout='{"Names":"web-1","Ports":"127.0.0.1:8000->80/tcp","Labels":'
        '"com.docker.compose.project=blog,com.docker.compose.service=web"}\n',
    )

    legacy = next(
        s for s in discover(managers=webs(world), probe=probe) if s.site == "legacy.example.com"
    )

    assert not legacy.adoptable and legacy.unit is None
    assert "noust app adopt" in (legacy.reason or "")


# -- The plan ---------------------------------------------------------------------------------


def test_a_site_in_front_of_a_unit_is_planned_with_the_unit_that_listens(
    world: Server, probe: TopologyProbe
) -> None:
    result = plan(world, probe, "legacy.example.com")

    assert result.kind == "proxy"
    assert result.unit == UNIT and result.unit_active is True
    assert result.root == world.legacy  # the unit's WorkingDirectory: the site names no root
    assert result.port == 8000
    assert result.site == "legacy.example.com" and result.site_name is None
    assert result.webserver == "nginx" and not result.ssl and result.enabled


def test_a_site_file_not_named_after_the_domain_is_recorded(
    world: Server, probe: TopologyProbe
) -> None:
    world.site("legacy", PROXY_SITE.replace("legacy.example.com", "api.legacy.example.com"))

    result = plan(world, probe, "api.legacy.example.com")

    assert result.site == "legacy" and result.site_name == "legacy"


def test_tls_is_read_from_the_site(world: Server, probe: TopologyProbe) -> None:
    world.site("tls.example.com", TLS_PROXY_SITE)

    assert plan(world, probe, "tls.example.com").ssl is True


def test_the_unit_can_be_named_when_nothing_listens_yet(
    world: Server, machine: Machine, probe: TopologyProbe
) -> None:
    machine.listeners.clear()
    machine.active[UNIT] = "inactive"

    with pytest.raises(ValidationError, match="cannot tell which unit"):
        plan(world, probe, "legacy.example.com")

    result = plan(world, probe, "legacy.example.com", unit=f"{UNIT}.service")
    assert result.unit == UNIT and result.unit_active is False
    assert any("not running" in w for w in result.warnings)


def test_other_backends_of_the_site_are_said_to_stay_outside(
    world: Server, machine: Machine, probe: TopologyProbe
) -> None:
    world.site(
        "split.example.com",
        """server {
    listen 80;
    server_name split.example.com;
    location /api/ { proxy_pass http://127.0.0.1:8001; }
    location / { proxy_pass http://127.0.0.1:8000; }
}
""",
    )
    machine.listeners[8001] = ("node", 4243, "other-api.service")

    result = plan(world, probe, "split.example.com")

    assert result.unit == UNIT  # the one behind "/"
    assert any("127.0.0.1:8001" in w and "other-api.service" in w for w in result.warnings)


def test_php_and_static_sites_have_no_unit_and_say_what_they_do_not_touch(
    world: Server, probe: TopologyProbe
) -> None:
    blog = plan(world, probe, "blog.example.com")
    docs = plan(world, probe, "docs.example.com")

    assert (blog.kind, blog.unit, blog.root, blog.port) == ("php", None, world.blog, None)
    assert any("PHP-FPM" in w for w in blog.warnings)
    assert (docs.kind, docs.unit, docs.root) == ("static", None, world.docs)


# -- Refusals -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "options", "message"),
    [
        ("missing.example.com", {}, "No site in nginx or Apache"),
        ("legacy.example.com", {"unit": "no-such-unit"}, "cannot be adopted"),
        ("legacy.example.com", {"unit": "nginx"}, "cannot be adopted"),
        ("legacy.example.com", {"unit": "noust-web"}, "cannot be adopted"),
        ("legacy.example.com", {"unit": "shop@blue"}, "cannot be adopted"),
        ("blog.example.com", {"unit": UNIT}, "no unit to name"),
    ],
)
def test_what_cannot_be_adopted_is_refused_and_says_why(
    world: Server, probe: TopologyProbe, domain: str, options: dict[str, str], message: str
) -> None:
    (world.system_units / "nginx.service").write_text("[Service]\nExecStart=/usr/sbin/nginx\n")
    world.unit("nginx")  # an operator's copy of the system's: still the web server

    with pytest.raises(ValidationError, match=message):
        plan(world, probe, domain, **options)


def test_a_unit_noust_manages_is_not_adopted(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    (world.units / f"{UNIT}.service").write_text(
        "# Generated by Noust\n" + UNIT_FILE.format(work=world.legacy)
    )

    with pytest.raises(ValidationError, match="Noust already manages it"):
        plan(world, probe, "legacy.example.com")


def test_a_domain_that_is_an_application_is_refused(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    store.create_app(
        App(domain="legacy.example.com", app_type="nodejs", app_path="/var/www/apps/x")
    )

    with pytest.raises(ValidationError, match="already an application"):
        plan(world, probe, "legacy.example.com")


def test_a_site_that_only_redirects_is_refused(world: Server, probe: TopologyProbe) -> None:
    world.site("old.example.com", REDIRECT_SITE)

    with pytest.raises(ValidationError, match="serves nothing Noust can adopt"):
        plan(world, probe, "old.example.com")


def test_a_directory_that_is_not_the_sites_alone_is_refused(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    with pytest.raises(ValidationError, match="system directory"):
        plan(world, probe, "legacy.example.com", path="/etc")
    with pytest.raises(ValidationError, match="does not exist"):
        plan(world, probe, "legacy.example.com", path=str(world.base / "nowhere"))
    store.create_app(App(domain="other.example.com", app_type="nodejs", app_path=str(world.legacy)))
    with pytest.raises(ValidationError, match=r"overlaps other\.example\.com"):
        plan(world, probe, "legacy.example.com")


def test_a_site_file_name_the_store_cannot_record_is_refused(
    world: Server, probe: TopologyProbe
) -> None:
    world.site("legacy_site", PROXY_SITE.replace("legacy.example.com", "under.example.com"))

    with pytest.raises(ValidationError, match="not a site file name"):
        plan(world, probe, "under.example.com")


# -- Adopting ---------------------------------------------------------------------------------------


def test_adopting_changes_nothing_but_the_store(
    world: Server, machine: Machine, probe: TopologyProbe, store: NoustStore
) -> None:
    site_before = tree(world.nginx.sites_available)
    unit_before = tree(world.units)
    legacy_before, blog_before = tree(world.legacy), tree(world.blog)
    calls = len(machine.calls)

    result = adopt(world, probe, "legacy.example.com")
    adopt(world, probe, "blog.example.com")

    assert result.adopted
    assert tree(world.nginx.sites_available) == site_before
    assert tree(world.units) == unit_before
    assert (tree(world.legacy), tree(world.blog)) == (legacy_before, blog_before)
    assert machine.changing() == [], machine.calls[calls:]
    app = store.get_app("legacy.example.com")
    assert app is not None and is_adopted(app.app_type) and app.app_type == AppType.ADOPTED.value
    assert (app.port, app.app_path, app.webserver, app.layout) == (
        8000,
        str(world.legacy),
        "nginx",
        "inplace",
    )
    record = get_record("legacy.example.com", store=store)
    assert record is not None
    assert (record.kind, record.unit, record.site_file, record.root) == (
        "proxy",
        UNIT,
        "legacy.example.com",
        str(world.legacy),
    )
    assert store.list_services() == []  # the unit is not a service Noust created
    assert adopted_units(store) == {UNIT: "legacy-example-com"}


def test_a_unit_less_site_is_registered_as_served_by_the_web_server(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    adopt(world, probe, "blog.example.com")

    app = store.get_app("blog.example.com")
    record = get_record("blog.example.com", store=store)
    assert app is not None and app.is_static and app.port is None
    assert record is not None and (record.kind, record.unit) == ("php", None)
    assert record.upstream == "unix:/run/php/php8.3-fpm.sock"


def test_a_rehearsal_records_nothing(
    world: Server, probe: TopologyProbe, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core import fs
    from noust.deployers.native_adopt import adopt as adopt_plan

    found = plan(world, probe, "legacy.example.com")
    monkeypatch.setattr(fs, "is_rehearsal", lambda: True)
    monkeypatch.setattr("noust.deployers.native_adopt.is_rehearsal", lambda: True)

    result = adopt_plan(found)

    assert not result.adopted
    assert store.get_app("legacy.example.com") is None


def test_a_unit_runs_one_adopted_site_only(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    adopt(world, probe, "legacy.example.com")
    world.site("copy.example.com", PROXY_SITE.replace("legacy.example.com", "copy.example.com"))

    with pytest.raises(ValidationError, match="is an adopted site and already listens"):
        plan(world, probe, "copy.example.com")
    with pytest.raises(ValidationError, match="already runs an adopted site"):
        plan(world, probe, "copy.example.com", unit=UNIT)


def test_an_adoption_that_cannot_record_what_it_is_leaves_no_application(
    world: Server, probe: TopologyProbe, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> bool:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr("noust.deployers.native_adopt.set_record", broken)

    with pytest.raises(sqlite3.OperationalError):
        adopt(world, probe, "legacy.example.com")

    assert store.get_app("legacy.example.com") is None


# -- What Noust may do to the unit ----------------------------------------------------------------


def test_noust_starts_stops_restarts_and_reads_the_logs_of_the_adopted_unit(
    world: Server, machine: Machine, probe: TopologyProbe
) -> None:
    adopt(world, probe, "legacy.example.com")
    services = ServiceManager()

    # By the name Noust derives from the domain, which is how everything asks.
    services.restart("legacy-example-com")
    services.stop("legacy-example-com")
    services.start("legacy-example-com")
    status = services.get_status("legacy-example-com")
    logs = services.logs("legacy-example-com")

    assert ("systemctl", "restart", f"{UNIT}.service") in machine.calls
    assert ("systemctl", "stop", f"{UNIT}.service") in machine.calls
    assert ("systemctl", "start", f"{UNIT}.service") in machine.calls
    assert status["active"] is True and status["managed"] is False
    assert "listening on 8000" in logs


def test_noust_never_rewrites_enables_limits_or_deletes_the_adopted_unit(
    world: Server, machine: Machine, probe: TopologyProbe
) -> None:
    adopt(world, probe, "legacy.example.com")
    services = ServiceManager()
    before = tree(world.units)
    refused = [
        lambda: services.update_config(UNIT, "# Generated by Noust\n[Service]\n"),
        lambda: services.set_unit_tuning(UNIT, ["MemorySwapMax=0"]),
        lambda: services.set_unit_hardening(UNIT, ["NoNewPrivileges=true"]),
        lambda: services.delete_service(UNIT),
        lambda: services.enable(UNIT),
        lambda: services.disable(UNIT),
        lambda: services.delete_service("legacy-example-com"),
        lambda: services.update_config("legacy-example-com", "# Generated by Noust\n"),
    ]

    for attempt in refused:
        with pytest.raises(ServiceError, match="adopted site"):
            attempt()

    assert tree(world.units) == before
    assert not [
        c for c in machine.calls if c[:2] in {("systemctl", "enable"), ("systemctl", "disable")}
    ]


def test_the_alias_does_not_reach_a_unit_that_is_not_adopted(
    world: Server, machine: Machine, probe: TopologyProbe
) -> None:
    adopt(world, probe, "legacy.example.com")
    services = ServiceManager()

    with pytest.raises(ServiceError, match="Unknown service"):
        services.restart("shop-example-com")


def test_an_adopted_site_is_asked_for_its_own_unit(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    adopt(world, probe, "legacy.example.com")
    adopt(world, probe, "blog.example.com")
    services = ServiceManager()

    proxy = store.get_app("legacy.example.com")
    php = store.get_app("blog.example.com")

    assert proxy is not None and php is not None
    assert services.app_units(proxy) == [UNIT]
    assert services.app_units(php) == []


def test_the_state_of_an_adopted_site_is_its_units(
    world: Server, machine: Machine, probe: TopologyProbe, store: NoustStore
) -> None:
    from noust.core import app_state

    adopt(world, probe, "legacy.example.com")
    adopt(world, probe, "blog.example.com")
    app = store.get_app("legacy.example.com")
    assert app is not None

    running = app_state.resolve_state(app, ServiceManager(), probe=False)
    machine.active[UNIT] = "failed"
    failed = app_state.resolve_state(app, ServiceManager(), probe=False)
    blog = store.get_app("blog.example.com")
    assert blog is not None
    served = app_state.resolve_state(blog, ServiceManager(), probe=False)

    assert running.healthy and not failed.healthy
    assert served.healthy


# -- What Noust will not do -----------------------------------------------------------------------


def test_an_adopted_site_has_no_deployer(world: Server, probe: TopologyProbe) -> None:
    from noust.deployers.registry import get_deployer

    with pytest.raises(ValidationError, match="does not build an adopted site"):
        get_deployer("adopted")


def test_updating_an_adopted_site_is_refused_before_anything_is_touched(
    world: Server, machine: Machine, probe: TopologyProbe
) -> None:
    from noust.deployers.lifecycle import update_app

    adopt(world, probe, "legacy.example.com")
    before = (tree(world.legacy), tree(world.units), tree(world.nginx.sites_available))
    calls = len(machine.calls)

    with pytest.raises(ValidationError, match=r"legacy\.example\.com is an adopted site"):
        update_app("legacy.example.com")

    assert (tree(world.legacy), tree(world.units), tree(world.nginx.sites_available)) == before
    assert machine.calls[calls:] == []


def test_the_environment_of_an_adopted_site_is_not_written(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    from noust.deployers.helpers.app_env import check_app_env, read_runtime_env, write_app_env

    adopt(world, probe, "legacy.example.com")
    app = store.get_app("legacy.example.com")
    assert app is not None
    before = (world.legacy / ".env").read_bytes()

    assert read_runtime_env(app) == {}  # the unit's own variables are not this file
    with pytest.raises(ValidationError, match="does not manage its environment"):
        write_app_env(app, {"A": "1"})
    with pytest.raises(ValidationError, match="does not manage its environment"):
        check_app_env(app, {"A": "1"})

    assert (world.legacy / ".env").read_bytes() == before


def test_the_sandbox_the_account_and_the_tuning_are_not_for_an_adopted_unit(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    from noust.managers import app_identity, unit_hardening

    adopt(world, probe, "legacy.example.com")
    app = store.get_app("legacy.example.com")
    assert app is not None

    assert "your" in (unit_hardening.ineligible(app) or "")
    assert "your" in (app_identity.ineligible(app) or "")


# -- Deleting ---------------------------------------------------------------------------------------


def test_deleting_an_adopted_site_forgets_it_and_removes_nothing(
    world: Server, machine: Machine, probe: TopologyProbe, store: NoustStore
) -> None:
    from noust.deployers.lifecycle import delete_app

    adopt(world, probe, "legacy.example.com")
    before = (tree(world.legacy), tree(world.units), tree(world.nginx.sites_available))
    calls = len(machine.calls)

    deleted = delete_app("legacy.example.com")

    assert store.get_app("legacy.example.com") is None
    assert get_record("legacy.example.com", store=store) is None
    assert (tree(world.legacy), tree(world.units), tree(world.nginx.sites_available)) == before
    assert not deleted.files_removed and not deleted.certificate_removed and deleted.units == ()
    assert deleted.kept_directory == str(world.legacy)
    assert any(UNIT in sentence for sentence in deleted.kept)
    assert machine.changing() == [], machine.calls[calls:]
    # And the unit is Noust's to touch no more, by the name of the application or by its own.
    with pytest.raises(ServiceError, match="Unknown service"):
        ServiceManager().restart("legacy-example-com")
    with pytest.raises(ServiceError, match="does not manage"):
        ServiceManager().restart(UNIT)


def test_deleting_an_adopted_site_never_removes_its_directory_even_when_asked(
    world: Server, probe: TopologyProbe, store: NoustStore
) -> None:
    from noust.deployers.lifecycle import delete_app

    adopt(world, probe, "blog.example.com")

    with pytest.raises(ValidationError, match="never removes its files"):
        delete_app("blog.example.com", remove_adopted_directory=str(world.blog))

    assert store.get_app("blog.example.com") is not None
    assert (world.blog / "wp-config.php").exists()


# -- What the rest of Noust does with an adopted site ----------------------------------------------


def test_an_adopted_site_is_backed_up_from_where_it_is(
    world: Server, machine: Machine, probe: TopologyProbe, store: NoustStore
) -> None:
    import tarfile

    from noust.managers.backup_manager import BackupManager

    adopt(world, probe, "legacy.example.com")
    manager = BackupManager(verbose=False, runner=machine)
    manager.backup_dir = world.base / "backups"
    before = tree(world.legacy)

    backup = manager.create("legacy.example.com", include_env=False)

    with tarfile.open(manager.backup_dir / "legacy-example-com" / f"{backup.id}.tar.gz") as archive:
        names = archive.getnames()
    assert any(name.endswith("server.js") for name in names), names
    assert not any(name.endswith(".env") for name in names)
    assert tree(world.legacy) == before
