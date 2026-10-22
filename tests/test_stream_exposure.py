# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TCP and UDP ports published through nginx's ``stream`` module.

What is pinned: a port is only published when nginx has the module, loads it and
reads a ``stream`` block that carries Noust's directory; every port question is
answered before anything is touched; nginx's own test of the whole configuration is
the last word and its output is the error; the firewall is told to allow only what was
asked, and only what Noust made is closed again; and an interrupted opening or closing
is finished from the row.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.database_exposures import ExposureStore
from noust.core.exceptions import DependencyError, SecurityError, ValidationError
from noust.core.fs import DryRunFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.core.stream_store import CLOSING, OPEN, OPENING, StreamExposure, StreamStore
from noust.core.teams.model import ResourceNotFoundError
from noust.managers import port_claims as port_claims_module
from noust.managers.server.security_firewall import DirectChange, RuleRequest
from noust.managers.stream_exposure import (
    LOADER_NAME,
    StreamExposures,
)

DEBIAN_V = (
    "nginx version: nginx/1.24.0 (Ubuntu)\n"
    "configure arguments: --with-cc-opt='-g -O2' --with-http_ssl_module "
    "--with-stream=dynamic --with-stream_ssl_module --with-threads\n"
)
STATIC_V = (
    "nginx version: nginx/1.26.2\nconfigure arguments: --with-http_ssl_module --with-stream\n"
)
NO_STREAM_V = "nginx version: nginx/1.24.0\nconfigure arguments: --with-http_ssl_module\n"

NGINX_CONF = """\
# configuration file /etc/nginx/nginx.conf:
user www-data;
pid /run/nginx.pid;
include /etc/nginx/modules-enabled/*.conf;
events { worker_connections 768; }
http {
    include /etc/nginx/sites-enabled/*;
}
"""
STREAM_MODULE = """
# configuration file /etc/nginx/modules-enabled/50-mod-stream.conf:
load_module modules/ngx_stream_module.so;
"""

pytestmark = pytest.mark.real_listeners


def dump(*, module: bool = True, extra: str = "", conf: str = NGINX_CONF) -> str:
    return conf + (STREAM_MODULE if module else "") + extra


class FakeFirewall:
    """A firewall manager that records what it was asked and answers as told."""

    def __init__(self) -> None:
        self.allowed: list[RuleRequest] = []
        self.removed: list[RuleRequest] = []
        self.already: set[str] = set()
        self.refuse_source: str | None = None
        self.refuse_removal = False

    def allow_direct(self, request: RuleRequest) -> DirectChange:
        if request.source == self.refuse_source:
            raise SecurityError("The firewall refused 'ufw allow'")
        self.allowed.append(request)
        if request.source in self.already:
            return DirectChange("ufw", True, False, "it is already allowed")
        return DirectChange("ufw", True, True)

    def remove_direct(self, request: RuleRequest) -> DirectChange:
        if self.refuse_removal:
            raise SecurityError("The firewall refused 'ufw delete'")
        self.removed.append(request)
        return DirectChange("ufw", True, True)


@pytest.fixture(autouse=True)
def config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        port_claims_module,
        "Config",
        lambda: SimpleNamespace(get=lambda key, default=None: default),
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    fake.script(("nginx", "-V"), stderr=DEBIAN_V)
    fake.script(("nginx", "-T"), stdout=dump())
    fake.script(("nginx", "-t"))
    fake.script(("systemctl", "reload", "nginx"))
    fake.script(("ss", "-ltnpH"), stdout="")
    fake.script(("ss", "-lunpH"), stdout="")
    return fake


@pytest.fixture
def firewall() -> FakeFirewall:
    return FakeFirewall()


@pytest.fixture
def streams(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, firewall: FakeFirewall
) -> StreamExposures:
    conf = tmp_path / "etc-nginx"
    (conf / "modules-enabled").mkdir(parents=True)
    return StreamExposures(
        store=store,
        runner=runner,
        conf_root=conf,
        firewall=lambda _runner: firewall,  # type: ignore[arg-type,return-value]
    )


def open_port(streams: StreamExposures, **changes: Any) -> Any:
    request: dict[str, Any] = {
        "protocol": "tcp",
        "listen_port": 25565,
        "target_port": 25565,
        "target_host": "127.0.0.1",
    }
    request.update(changes)
    return streams.open("minecraft", **request)


# -- what nginx offers ------------------------------------------------------------


def test_a_debian_nginx_with_the_module_loaded_can_carry_the_loader(
    streams: StreamExposures,
) -> None:
    support = streams.support()

    assert support.available is True
    assert (support.module, support.loader) == ("dynamic", "installable")


def test_a_static_module_needs_no_load_module(streams: StreamExposures, runner: FakeRunner) -> None:
    runner.script(("nginx", "-V"), stderr=STATIC_V)
    runner.script(("nginx", "-T"), stdout=dump(module=False))

    support = streams.support()

    assert support.available is True and support.module == "static"


def test_a_stream_block_that_already_includes_the_directory_is_used_as_it_is(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    block = f"\nstream {{\n    include {streams.directory}/*.conf;\n}}\n"
    runner.script(("nginx", "-T"), stdout=dump(extra=block))

    assert streams.support().loader == "present"


def test_an_operators_stream_block_is_never_edited_and_the_line_to_add_is_printed(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    runner.script(
        ("nginx", "-T"), stdout=dump(extra="\nstream {\n    upstream a { server 1.1.1.1:1; }\n}\n")
    )

    support = streams.support()

    assert support.available is False and support.loader == "foreign"
    assert support.hint is not None and streams.include_line in support.hint


def test_without_modules_enabled_the_block_to_add_is_printed(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    conf = NGINX_CONF.replace("include /etc/nginx/modules-enabled/*.conf;\n", "")
    runner.script(("nginx", "-T"), stdout=dump(conf=conf))

    support = streams.support()

    assert support.available is False and support.loader == "manual"
    assert support.hint is not None and "stream {" in support.hint


def test_a_build_without_the_module_names_the_package_to_install(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-V"), stderr=NO_STREAM_V)

    support = streams.support()

    assert support.available is False and support.module == "missing"
    assert support.hint is not None and "libnginx-mod-stream" in support.hint


def test_a_module_nothing_loads_is_reported(streams: StreamExposures, runner: FakeRunner) -> None:
    runner.script(("nginx", "-T"), stdout=dump(module=False))

    support = streams.support()

    assert support.available is False
    assert support.problem is not None and "nothing loads it" in support.problem


def test_a_configuration_nginx_cannot_read_stops_it_with_nginxs_words(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-T"), stderr="nginx: [emerg] unexpected end of file", exit_code=1)

    support = streams.support()

    assert support.available is False
    assert support.hint is not None and "unexpected end of file" in support.hint


def test_nginx_not_installed_is_said_first(streams: StreamExposures, runner: FakeRunner) -> None:
    runner.only_knows("systemctl")

    assert streams.support().problem == "nginx is not installed"


# -- the questions about a port ---------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"protocol": "sctp"}, "protocol"),
        ({"listen_port": 0}, "listen_port"),
        ({"listen_port": 70000}, "listen_port"),
        ({"target_port": 0}, "target_port"),
        ({"target_host": "db.internal"}, "target_host"),
        ({"sources": ["not an address"]}, "sources"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": 10**6}, "timeout_seconds"),
    ],
)
def test_a_field_that_is_not_acceptable_is_refused_naming_it(
    streams: StreamExposures, changes: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        open_port(streams, **changes)

    assert caught.value.field == field
    assert streams.records.all() == []


@pytest.mark.parametrize("name", ["", "Minecraft", "-mc", "mc server", "../x", "a" * 41])
def test_a_name_that_could_not_be_a_file_is_refused(streams: StreamExposures, name: str) -> None:
    with pytest.raises(ValidationError):
        streams.open(name, protocol="tcp", listen_port=25565, target_port=25565)


@pytest.mark.parametrize("port", [80, 443, 22, 8080])
def test_the_ports_of_the_web_server_ssh_and_the_console_are_never_published(
    streams: StreamExposures, port: int
) -> None:
    with pytest.raises(ValidationError, match=f"Port {port} is"):
        open_port(streams, listen_port=port)


def test_a_port_something_listens_on_is_refused_with_who_holds_it(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 511 0.0.0.0:25565 0.0.0.0:* users:(("java",pid=7,fd=9))\n',
    )

    with pytest.raises(ValidationError, match="already in use"):
        open_port(streams)


def test_the_same_number_over_udp_is_a_different_port(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 511 0.0.0.0:19132 0.0.0.0:* users:(("bedrock",pid=7,fd=9))\n',
    )

    result = open_port(streams, protocol="udp", listen_port=19132, target_port=19132)

    assert result.exposure.protocol == "udp"


def test_a_udp_socket_that_is_bound_is_refused(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-lunpH"),
        stdout='UNCONN 0 0 0.0.0.0:51820 0.0.0.0:* users:(("wg",pid=7,fd=9))\n',
    )

    with pytest.raises(ValidationError, match="UDP 51820 is already in use"):
        open_port(streams, protocol="udp", listen_port=51820, target_port=51820)


def test_a_port_an_exposure_holds_is_refused_by_name(streams: StreamExposures) -> None:
    open_port(streams)

    with pytest.raises(ValidationError, match="already published as minecraft"):
        streams.open("other", protocol="tcp", listen_port=25565, target_port=1)


def test_the_public_port_of_a_database_is_refused(
    streams: StreamExposures, store: NoustStore
) -> None:
    ExposureStore(store).create(
        engine="postgresql",
        db_name="shop",
        port=25570,
        bind_address="0.0.0.0",  # noqa: S104 - a recorded binding
        sources=(),
        tls="required",
        remote_user="r",
        profile="owner",
        unit="u",
        created_by=None,
    )

    with pytest.raises(ValidationError, match="public port of the database postgresql/shop"):
        open_port(streams, listen_port=25570)


def test_the_port_of_an_application_is_refused_over_tcp_and_not_over_udp(
    streams: StreamExposures, store: NoustStore
) -> None:
    store.create_app(App(domain="shop.example.com", app_path="/srv/shop", port=3000))

    with pytest.raises(ValidationError, match=r"port of shop\.example\.com"):
        open_port(streams, listen_port=3000)
    assert open_port(streams, protocol="udp", listen_port=3000).exposure.listen_port == 3000


def test_planning_answers_everything_and_writes_nothing(
    streams: StreamExposures, store: NoustStore
) -> None:
    plan = streams.plan(
        "minecraft",
        protocol="tcp",
        listen_port=25565,
        target_port=25565,
        sources=["203.0.113.0/24"],
    )

    assert plan.file == streams.directory / "minecraft.conf"
    assert "allow 203.0.113.0/24;" in plan.config
    assert plan.loader == "write"
    assert plan.firewall == ({"port": 25565, "proto": "tcp", "source": "203.0.113.0/24"},)
    assert not streams.directory.exists()
    assert StreamStore(store).all() == []


def test_a_target_nothing_listens_on_and_an_open_port_are_warned_about(
    streams: StreamExposures,
) -> None:
    plan = streams.plan("redis", protocol="tcp", listen_port=26379, target_port=6379)

    text = " ".join(plan.warnings)
    assert "Nothing listens on 127.0.0.1:6379" in text
    assert "open to everyone" in text and "authentication" in text


def test_a_source_that_is_everyone_is_the_same_as_no_source(streams: StreamExposures) -> None:
    plan = streams.plan(
        "minecraft", protocol="tcp", listen_port=25565, target_port=1, sources=["0.0.0.0/0", "::/0"]
    )

    assert plan.exposure.sources == ()
    assert "deny all" not in plan.config


def test_a_module_that_is_missing_stops_the_plan_with_the_fix(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-V"), stderr=NO_STREAM_V)

    with pytest.raises(DependencyError) as caught:
        open_port(streams)

    assert "built without the stream module" in caught.value.message
    assert "libnginx-mod-stream" in (caught.value.details or "")


# -- the file ---------------------------------------------------------------------


def test_a_tcp_port_is_rendered_with_its_sources_and_timeouts(streams: StreamExposures) -> None:
    plan = streams.plan(
        "minecraft",
        protocol="tcp",
        listen_port=25565,
        target_port=25565,
        sources=["203.0.113.7", "198.51.100.0/24"],
        timeout_seconds=600,
    )

    assert plan.config == (
        "# Noust stream minecraft: TCP 25565 to 127.0.0.1:25565\n"
        "# Generated by Noust ('noust stream add'); changes made here are lost when it is closed.\n"
        "\n"
        "server {\n"
        "    listen 25565;\n"
        "    listen [::]:25565 ipv6only=on;\n"
        "    proxy_connect_timeout 10s;\n"
        "    proxy_timeout 600s;\n"
        "    allow 203.0.113.7/32;\n"
        "    allow 198.51.100.0/24;\n"
        "    deny all;\n"
        "    proxy_pass 127.0.0.1:25565;\n"
        "}\n"
    )


def test_a_udp_port_listens_for_datagrams_and_an_ipv6_backend_is_bracketed(
    streams: StreamExposures,
) -> None:
    plan = streams.plan(
        "wireguard",
        protocol="udp",
        listen_port=51821,
        target_port=51820,
        target_host="::1",
    )

    assert "    listen 51821 udp;\n" in plan.config
    assert "    listen [::]:51821 udp ipv6only=on;\n" in plan.config
    assert "proxy_connect_timeout" not in plan.config
    assert "    proxy_pass [::1]:51820;\n" in plan.config


# -- opening ----------------------------------------------------------------------


def test_opening_writes_the_loader_and_the_file_tests_reloads_and_opens_the_firewall(
    streams: StreamExposures, runner: FakeRunner, firewall: FakeFirewall
) -> None:
    result = open_port(streams, sources=["203.0.113.0/24", "198.51.100.7"])

    assert result.exposure.state == OPEN
    loader = streams.conf_root / "modules-enabled" / LOADER_NAME
    assert streams.include_line in loader.read_text()
    assert (streams.directory / "minecraft.conf").read_text().startswith("# Noust stream minecraft")
    calls = [call for call in runner.calls if call[0] in ("nginx", "systemctl")]
    assert calls.index(("nginx", "-t")) < calls.index(("systemctl", "reload", "nginx"))
    assert [(r.action, r.port, r.proto, r.source) for r in firewall.allowed] == [
        ("allow", 25565, "tcp", "203.0.113.0/24"),
        ("allow", 25565, "tcp", "198.51.100.7/32"),
    ]
    saved = streams.get("minecraft")
    assert saved.firewall["made"] == [
        {"port": 25565, "proto": "tcp", "source": "203.0.113.0/24"},
        {"port": 25565, "proto": "tcp", "source": "198.51.100.7/32"},
    ]


def test_an_exposure_without_sources_opens_the_port_to_everyone(
    streams: StreamExposures, firewall: FakeFirewall
) -> None:
    open_port(streams)

    assert [r.source for r in firewall.allowed] == ["any"]


def test_a_loader_that_is_already_there_is_not_written_again(
    streams: StreamExposures, runner: FakeRunner
) -> None:
    block = f"\nstream {{\n    include {streams.directory}/*.conf;\n}}\n"
    runner.script(("nginx", "-T"), stdout=dump(extra=block))

    open_port(streams)

    assert not (streams.conf_root / "modules-enabled" / LOADER_NAME).exists()


def test_a_rule_that_was_already_allowed_is_not_ours_to_close(
    streams: StreamExposures, firewall: FakeFirewall
) -> None:
    firewall.already.add("any")

    result = open_port(streams)

    assert result.exposure.firewall["made"] == []
    assert "already allowed" in result.firewall
    streams.close("minecraft")
    assert firewall.removed == []


def test_nginx_refusing_the_configuration_changes_nothing_and_its_words_are_the_error(
    streams: StreamExposures, runner: FakeRunner, firewall: FakeFirewall
) -> None:
    runner.script(
        ("nginx", "-t"),
        stderr='nginx: [emerg] "listen" directive is not allowed here in minecraft.conf:4',
        exit_code=1,
    )

    with pytest.raises(ValidationError) as caught:
        open_port(streams)

    assert "directive is not allowed here" in (caught.value.output or "")
    assert not (streams.directory / "minecraft.conf").exists()
    assert not (streams.conf_root / "modules-enabled" / LOADER_NAME).exists()
    assert streams.records.all() == []
    assert ("systemctl", "reload", "nginx") not in runner.calls
    assert firewall.allowed == []


def test_a_reload_that_fails_puts_the_files_back(
    streams: StreamExposures, runner: FakeRunner, firewall: FakeFirewall
) -> None:
    runner.script(("systemctl", "reload", "nginx"), exit_code=1)

    with pytest.raises(ValidationError, match="did not reload"):
        open_port(streams)

    assert not (streams.directory / "minecraft.conf").exists()
    assert streams.records.all() == []
    assert firewall.allowed == []


def test_a_firewall_that_refuses_closes_the_port_again_and_removes_what_it_made(
    streams: StreamExposures, firewall: FakeFirewall
) -> None:
    firewall.refuse_source = "198.51.100.7/32"

    with pytest.raises(SecurityError):
        open_port(streams, sources=["203.0.113.0/24", "198.51.100.7"])

    assert [r.source for r in firewall.removed] == ["203.0.113.0/24"]
    assert not (streams.directory / "minecraft.conf").exists()
    assert streams.records.all() == []


def test_a_firewall_that_cannot_undo_keeps_the_row_so_the_rule_is_not_forgotten(
    streams: StreamExposures, firewall: FakeFirewall
) -> None:
    firewall.refuse_source = "198.51.100.7/32"
    firewall.refuse_removal = True

    with pytest.raises(SecurityError):
        open_port(streams, sources=["203.0.113.0/24", "198.51.100.7"])

    (left,) = streams.records.all()
    assert left.state == CLOSING
    assert left.firewall["made"] == [{"port": 25565, "proto": "tcp", "source": "203.0.113.0/24"}]


def test_a_second_exposure_on_a_taken_name_is_refused(streams: StreamExposures) -> None:
    open_port(streams)

    with pytest.raises(ValidationError, match="already a published port"):
        open_port(streams, listen_port=25566)


def test_an_opening_that_stopped_half_way_is_finished_by_asking_again(
    streams: StreamExposures, store: NoustStore, firewall: FakeFirewall
) -> None:
    StreamStore(store).create(
        StreamExposure(
            name="minecraft",
            protocol="tcp",
            listen_port=25565,
            target_host="127.0.0.1",
            target_port=25565,
            firewall={
                "backend": "ufw",
                "active": True,
                "made": [{"port": 25565, "proto": "tcp", "source": "any"}],
            },
            state=OPENING,
        )
    )
    firewall.already.add("any")

    result = open_port(streams)

    assert result.exposure.state == OPEN
    # The rule an earlier try made is still ours, though it now reads as already there.
    assert result.exposure.firewall["made"] == [{"port": 25565, "proto": "tcp", "source": "any"}]


def test_a_rehearsal_writes_nothing_and_records_nothing(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, firewall: FakeFirewall
) -> None:
    from noust.core.fs import set_fs

    conf = tmp_path / "etc-nginx"
    rehearsal = DryRunFileSystem()
    set_fs(rehearsal)
    try:
        streams = StreamExposures(
            store=store,
            runner=runner,
            fs=rehearsal,
            conf_root=conf,
            firewall=lambda _runner: firewall,  # type: ignore[arg-type,return-value]
        )
        result = open_port(streams)
    finally:
        set_fs(None)

    assert result.rehearsal is True
    assert not conf.exists()
    assert streams.records.all() == []
    assert firewall.allowed == []
    assert ("systemctl", "reload", "nginx") not in runner.calls


# -- closing ----------------------------------------------------------------------


def test_closing_removes_the_rules_noust_made_the_file_and_the_row(
    streams: StreamExposures, runner: FakeRunner, firewall: FakeFirewall
) -> None:
    open_port(streams, sources=["203.0.113.0/24"])
    runner.calls.clear()

    result = streams.close("minecraft")

    assert result.exposure.name == "minecraft"
    assert [(r.port, r.proto, r.source) for r in firewall.removed] == [
        (25565, "tcp", "203.0.113.0/24")
    ]
    assert not (streams.directory / "minecraft.conf").exists()
    assert ("systemctl", "reload", "nginx") in runner.calls
    assert streams.records.all() == []


def test_a_close_the_firewall_refuses_is_finished_by_closing_again(
    streams: StreamExposures, firewall: FakeFirewall
) -> None:
    open_port(streams)
    firewall.refuse_removal = True

    with pytest.raises(SecurityError):
        streams.close("minecraft")
    assert streams.get("minecraft").state == CLOSING

    firewall.refuse_removal = False
    streams.close("minecraft")

    assert streams.records.all() == []
    assert [r.port for r in firewall.removed] == [25565]


def test_closing_what_is_not_published_is_a_not_found(streams: StreamExposures) -> None:
    with pytest.raises(ResourceNotFoundError, match="No published port is called ghost"):
        streams.close("ghost")


def test_repair_finishes_what_was_interrupted_and_leaves_the_open_alone(
    streams: StreamExposures, store: NoustStore
) -> None:
    open_port(streams)
    StreamStore(store).create(
        StreamExposure(
            name="half",
            protocol="udp",
            listen_port=19132,
            target_host="127.0.0.1",
            target_port=19132,
            state=OPENING,
        )
    )

    assert streams.repair() == ["half"]
    assert [e.name for e in streams.all()] == ["minecraft"]


def test_the_table_holds_one_exposure_per_port_and_protocol(store: NoustStore) -> None:
    records = StreamStore(store)
    base = StreamExposure(
        name="a", protocol="tcp", listen_port=9000, target_host="127.0.0.1", target_port=1
    )
    records.create(base)

    with pytest.raises(sqlite3.IntegrityError):
        records.create(StreamExposure(**{**base.__dict__, "name": "b"}))
    records.create(StreamExposure(**{**base.__dict__, "name": "c", "protocol": "udp"}))
    assert records.ports() == {9000}
