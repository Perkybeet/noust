# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for what a site reaches on this machine (``noust.managers.site_topology``).

The structure of a site says ``proxy_pass http://nestjs_upstream`` and
``server 127.0.0.1:3000``; the console's diagram has to say what that port is:
a Noust application, a Compose service, a systemd unit, or nothing at all. Each
owner is pinned here from the command that tells it (the store, ``docker ps``
with its labels, ``ss -ltnpH`` and the process's cgroup), and so is the probe's
contract: a TCP connection to the loopback with a one-second deadline, asked at
most once every ten seconds per port, never to another machine.

The include reader is the one way the structure reads a file the site names,
and it reads only the web server's own directory and Noust's files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, Service
from noust.managers.cert_manager import CertificateInfo
from noust.managers.site_topology import (
    CACHE_SECONDS,
    CONNECT_TIMEOUT,
    TopologyProbe,
    include_reader,
)
from noust.managers.siteconf import parse, structure

FIXTURES = Path(__file__).parent / "fixtures" / "siteconf"


# ---------------------------------------------------------------------------
# The include reader
# ---------------------------------------------------------------------------


@pytest.fixture
def conf_root(tmp_path: Path) -> Path:
    """An nginx configuration directory with a site enabled through a link."""
    root = tmp_path / "etc/nginx"
    (root / "sites-available").mkdir(parents=True)
    (root / "sites-enabled").mkdir()
    (root / "snippets").mkdir()
    (root / "snippets/b.conf").write_text("proxy_set_header B b;\n")
    (root / "snippets/a.conf").write_text("proxy_set_header A a;\n")
    (root / "sites-available/shop").write_text("server {}\n")
    (root / "sites-enabled/shop").symlink_to(root / "sites-available/shop")
    return root


def test_reader_reads_a_relative_include_from_the_configuration_directory(
    conf_root: Path,
) -> None:
    read = include_reader(conf_root)

    assert read("snippets/a.conf") == [
        (str(conf_root / "snippets/a.conf"), "proxy_set_header A a;\n")
    ]


def test_reader_expands_a_glob_in_name_order(conf_root: Path) -> None:
    read = include_reader(conf_root)

    names = [Path(path).name for path, _ in read(str(conf_root / "snippets/*.conf"))]

    assert names == ["a.conf", "b.conf"]


def test_reader_follows_a_link_that_stays_inside(conf_root: Path) -> None:
    read = include_reader(conf_root)

    assert read(str(conf_root / "sites-enabled/*"))[0][1] == "server {}\n"


def test_reader_refuses_a_file_outside_the_configuration(conf_root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "shadow"
    secret.write_text("root:x\n")
    read = include_reader(conf_root)

    with pytest.raises(ValueError, match="outside"):
        read(str(secret))
    with pytest.raises(ValueError, match="outside"):
        read("../../shadow")


def test_reader_refuses_a_link_that_points_out(conf_root: Path, tmp_path: Path) -> None:
    """A link planted in snippets/ must not make root read what it points at."""
    secret = tmp_path / "shadow"
    secret.write_text("root:x\n")
    (conf_root / "snippets/evil.conf").symlink_to(secret)
    read = include_reader(conf_root)

    with pytest.raises(ValueError, match="outside"):
        read("snippets/evil.conf")
    with pytest.raises(ValueError, match="outside"):
        read("snippets/*.conf")


def test_reader_reads_noust_servers_files_kept_elsewhere(conf_root: Path, tmp_path: Path) -> None:
    upstreams = tmp_path / "noust-upstreams"
    (upstreams / "shop").mkdir(parents=True)
    (upstreams / "shop/web.servers").write_text("server 127.0.0.1:3005;\n")
    read = include_reader(conf_root, extra_roots=[upstreams])

    assert read(str(upstreams / "shop/web.servers"))[0][1] == "server 127.0.0.1:3005;\n"


def test_reader_refuses_a_file_too_large_to_be_a_configuration(conf_root: Path) -> None:
    (conf_root / "snippets/huge.conf").write_text("#" * (2 * 1024 * 1024))
    read = include_reader(conf_root)

    with pytest.raises(ValueError, match="large"):
        read("snippets/huge.conf")


def test_reader_feeds_the_structure_the_upstream_servers_a_site_includes(
    conf_root: Path, tmp_path: Path
) -> None:
    """The servers file the relay writes shows up as the upstream's servers."""
    upstreams = tmp_path / "noust-upstreams"
    (upstreams / "shop").mkdir(parents=True)
    servers = upstreams / "shop/web.servers"
    servers.write_text("server 127.0.0.1:3005;\n")
    text = f"upstream web {{\n    include {servers};\n}}\nserver {{\n    location / {{ proxy_pass http://web; }}\n}}\n"

    model = structure(
        parse(text, "nginx"), read_include=include_reader(conf_root, extra_roots=[upstreams])
    )

    assert [server.address for server in model.upstreams[0].servers] == ["127.0.0.1:3005"]


# ---------------------------------------------------------------------------
# Owners, reachability and certificates
# ---------------------------------------------------------------------------


@dataclass
class FakeStore:
    """The store's apps and services, in memory."""

    apps: list[App] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)

    def list_apps(self) -> list[App]:
        return list(self.apps)

    def list_services(self) -> list[Service]:
        return list(self.services)


class FakeCerts:
    """CertManager's listing, scripted."""

    def __init__(self, certificates: list[CertificateInfo]) -> None:
        self.certificates = certificates
        self.calls = 0

    def list_certificates(self) -> list[CertificateInfo]:
        self.calls += 1
        return list(self.certificates)


class Clock:
    """A monotonic clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Connections:
    """The TCP probe, recorded: ports in ``open`` accept."""

    def __init__(self, open_ports: set[int]) -> None:
        self.open = open_ports
        self.calls: list[tuple[int, str, float]] = []

    def __call__(self, port: int, host: str, timeout: float) -> bool:
        self.calls.append((port, host, timeout))
        return port in self.open


SITE = """\
upstream web {
    server 127.0.0.1:3001;
    server 127.0.0.1:3002;
    server 10.0.0.9:8080;
}
server {
    listen 443 ssl;
    server_name shop.example.com;
    ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;
    location / { proxy_pass http://web; }
    location /api/ { proxy_pass http://127.0.0.1:3000; }
    location /php { fastcgi_pass unix:/run/php/php8.3-fpm.sock; }
}
"""


def _docker_line(name: str, ports: str, **labels: str) -> str:
    return json.dumps(
        {
            "Names": name,
            "Ports": ports,
            "Labels": ",".join(f"{key}={value}" for key, value in labels.items()),
        }
    )


@pytest.fixture
def machine(tmp_path: Path) -> dict[str, Any]:
    """A runner, a store, certificates, a clock and a probe over them."""
    runner = FakeRunner()
    runner.script(
        ["docker", "ps"],
        stdout=_docker_line(
            "shop-frontend-1",
            "127.0.0.1:3001->3000/tcp",
            **{
                "com.docker.compose.project": "shop",
                "com.docker.compose.service": "frontend",
                "com.docker.compose.project.working_dir": "/var/www/apps/shop-example-com",
            },
        )
        + "\n",
    )
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242/cgroup").write_text("0::/system.slice/legacy-api.service\n")
    runner.script(
        ["ss", "-ltnpH"],
        stdout='LISTEN 0 511 127.0.0.1:3002 0.0.0.0:* users:(("node",pid=4242,fd=19))\n',
    )
    store = FakeStore(
        apps=[
            App(
                id=1,
                domain="shop.example.com",
                app_type="docker-compose",
                port=None,
                app_path="/var/www/apps/shop-example-com",
            ),
            App(id=2, domain="api.example.com", app_type="nestjs", port=3000),
        ],
        services=[Service(id=1, app_id=2, name="api-example-com", port=3000)],
    )
    certs = FakeCerts(
        [
            CertificateInfo(
                name="shop.example.com",
                domains=["shop.example.com"],
                expiry="2026-11-01",
                cert_path="/etc/letsencrypt/live/shop.example.com/fullchain.pem",
            )
        ]
    )
    clock = Clock()
    connections = Connections({3000, 3001})
    probe = TopologyProbe(
        runner=runner,
        store=store,
        certs=certs,
        clock=clock,
        today=lambda: date(2026, 10, 2),
        connect=connections,
        proc=proc,
    )
    return {
        "runner": runner,
        "probe": probe,
        "clock": clock,
        "connections": connections,
        "certs": certs,
    }


def _backends(machine: dict[str, Any]) -> dict[str, Any]:
    model = structure(parse(SITE, "nginx"))
    topology = machine["probe"].topology(model)
    return {backend.address: backend for backend in topology.backends}


def test_a_port_a_compose_service_publishes_is_owned_by_that_service(
    machine: dict[str, Any],
) -> None:
    owner = _backends(machine)["127.0.0.1:3001"].owner

    assert owner.kind == "compose"
    assert (owner.project, owner.service, owner.container) == (
        "shop",
        "frontend",
        "shop-frontend-1",
    )
    # The stack's working directory is the application's: it is that app's service.
    assert owner.app == "shop.example.com"


def test_a_port_a_noust_application_records_is_owned_by_it(machine: dict[str, Any]) -> None:
    owner = _backends(machine)["127.0.0.1:3000"].owner

    assert owner.kind == "app"
    assert owner.app == "api.example.com"
    assert owner.unit == "api-example-com.service"


def test_a_port_only_ss_knows_is_owned_by_the_unit_its_process_runs_in(
    machine: dict[str, Any],
) -> None:
    owner = _backends(machine)["127.0.0.1:3002"].owner

    assert owner.kind == "unit"
    assert owner.unit == "legacy-api.service"
    assert (owner.process, owner.pid) == ("node", 4242)


def test_a_free_port_has_no_owner_and_does_not_answer(machine: dict[str, Any]) -> None:
    model = structure(
        parse("server {\n    location / { proxy_pass http://127.0.0.1:3999; }\n}\n", "nginx")
    )

    (backend,) = machine["probe"].topology(model).backends

    assert backend.owner is None
    assert backend.listening is False
    assert backend.reachable is False


def test_backends_say_which_upstreams_and_locations_reach_them(machine: dict[str, Any]) -> None:
    backends = _backends(machine)

    assert backends["127.0.0.1:3001"].upstreams == ["web"]
    assert backends["127.0.0.1:3001"].locations == ["s0/l0"]
    assert backends["127.0.0.1:3000"].upstreams == []
    assert backends["127.0.0.1:3000"].locations == ["s0/l1"]


def test_reachable_is_a_one_second_connection_to_the_loopback(machine: dict[str, Any]) -> None:
    backends = _backends(machine)

    assert backends["127.0.0.1:3001"].reachable is True
    assert backends["127.0.0.1:3002"].reachable is False
    assert {call[1:] for call in machine["connections"].calls} == {("127.0.0.1", CONNECT_TIMEOUT)}
    assert CONNECT_TIMEOUT == 1.0


def test_another_machine_is_never_probed(machine: dict[str, Any]) -> None:
    remote = _backends(machine)["10.0.0.9:8080"]

    assert remote.local is False
    assert remote.reachable is None
    assert remote.owner is None
    assert all(call[0] != 8080 for call in machine["connections"].calls)


def test_a_unix_socket_is_listed_without_a_probe(machine: dict[str, Any]) -> None:
    socket = _backends(machine)["unix:/run/php/php8.3-fpm.sock"]

    assert socket.port is None
    assert socket.reachable is None


def test_probes_are_cached_for_ten_seconds(machine: dict[str, Any]) -> None:
    runner: FakeRunner = machine["runner"]
    _backends(machine)
    first = (len(machine["connections"].calls), len(runner.calls), machine["certs"].calls)

    machine["clock"].now += CACHE_SECONDS - 1
    _backends(machine)
    assert (len(machine["connections"].calls), len(runner.calls), machine["certs"].calls) == first

    machine["clock"].now += 2
    _backends(machine)
    assert len(machine["connections"].calls) == 2 * first[0]
    assert machine["certs"].calls == 2
    assert CACHE_SECONDS == 10.0


def test_every_command_is_a_declared_read_only_probe(machine: dict[str, Any]) -> None:
    from noust.core.runner import is_read_only

    _backends(machine)

    assert machine["runner"].calls
    assert all(is_read_only(call) for call in machine["runner"].calls)


def test_without_docker_the_other_owners_still_answer(machine: dict[str, Any]) -> None:
    runner: FakeRunner = machine["runner"]
    runner.script(["docker", "ps"], stderr="Command not found: docker", exit_code=127)

    topology = machine["probe"].topology(structure(parse(SITE, "nginx")))

    assert topology.docker is False
    owners = {backend.address: backend.owner for backend in topology.backends}
    assert owners["127.0.0.1:3000"].kind == "app"
    # Port 3001 is published by a container nobody can see now: ss is asked.
    assert owners["127.0.0.1:3001"] is None


def test_certificates_carry_their_expiry(machine: dict[str, Any]) -> None:
    topology = machine["probe"].topology(structure(parse(SITE, "nginx")))

    (certificate,) = topology.certificates
    assert certificate.server_id == "s0"
    assert certificate.name == "shop.example.com"
    assert certificate.expiry == "2026-11-01"
    assert certificate.days_left == 30


def test_a_certificate_certbot_does_not_manage_has_no_expiry(machine: dict[str, Any]) -> None:
    text = "server {\n    listen 443 ssl;\n    ssl_certificate /etc/ssl/own.pem;\n}\n"

    (certificate,) = machine["probe"].topology(structure(parse(text, "nginx"))).certificates

    assert certificate.path == "/etc/ssl/own.pem"
    assert certificate.name is None
    assert certificate.expiry is None


def test_the_proggest_site_is_two_upstreams_on_two_ports(machine: dict[str, Any]) -> None:
    text = (FIXTURES / "proggest/proggest.es").read_text()

    topology = machine["probe"].topology(structure(parse(text, "nginx")))

    addresses = {backend.address: backend.upstreams for backend in topology.backends}
    assert addresses == {
        "127.0.0.1:3001": ["nextjs_upstream"],
        "127.0.0.1:3000": ["nestjs_upstream"],
    }
