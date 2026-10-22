# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cloudflare Tunnel as the way into an application.

What is pinned: the connector's token is only ever in a 0600 secret file the unit reads
through ``EnvironmentFile=`` (never an argument, never the unit); the whole ingress
Cloudflare holds is built from the routes Noust records; a route reaches nginx with the
public host name kept, over plain HTTP for a site that serves none (a redirect would loop)
and over TLS without verification for one that does; the CNAME is ours only if we made it;
and a creation or a removal that fails leaves either nothing or a row that finishes it.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import sealing
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.exceptions import (
    DomainConflictError,
    PrerequisiteError,
    ServiceError,
    ValidationError,
)
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.core.teams.model import ResourceNotFoundError
from noust.core.tunnel_store import READY, REMOVING, Tunnel, TunnelStore
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.provider import DnsProviderError
from noust.managers.dns.records import ManagedDns
from noust.managers.tunnels import (
    TunnelManager,
    checked_service,
    ingress_for,
    secret_name,
    unit_name,
)
from tests.dns_support import FakeTunnelProvider

CLOUDFLARED = "/usr/bin/cloudflared"
APP = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain=APP, app_path="/srv/shop", port=3000))
    instance.add_domain(APP, "www.shop.example.com", "alias")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "state" / "secrets")


@pytest.fixture
def provider() -> FakeTunnelProvider:
    return FakeTunnelProvider("example.com")


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    fake.script(("systemctl", "is-active"), stdout="active\n")
    return fake


@pytest.fixture
def tls() -> dict[str, bool]:
    return {"serves": False}


@pytest.fixture
def tunnels(
    tmp_path: Path,
    store: NoustStore,
    secrets: SecretStore,
    provider: FakeTunnelProvider,
    runner: FakeRunner,
    tls: dict[str, bool],
    monkeypatch: pytest.MonkeyPatch,
) -> TunnelManager:
    DnsStore(store).save_account(DnsAccount(name="cf", kind="cloudflare", automatic=True))
    monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: provider)
    accounts = DnsAccounts(store=store, secrets=secrets)
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    return TunnelManager(
        accounts=accounts,
        store=store,
        runner=runner,
        secrets=secrets,
        systemd_dir=systemd,
        which=lambda program: CLOUDFLARED if program == "cloudflared" else None,
        dns=lambda: ManagedDns(accounts=accounts, store=store, label="srv"),
        serves_tls=lambda app: tls["serves"],
    )


def make(tunnels: TunnelManager, name: str = "home") -> Any:
    return tunnels.create(name, account="cf")


# -- what this server can do ------------------------------------------------------


def test_a_server_with_the_connector_and_a_cloudflare_account_can_run_a_tunnel(
    tunnels: TunnelManager,
) -> None:
    support = tunnels.support()

    assert support.available is True
    assert support.cloudflared == CLOUDFLARED and support.accounts == ("cf",)


def test_a_missing_connector_names_cloudflares_own_package_repository(
    tunnels: TunnelManager,
) -> None:
    tunnels._which = lambda program: None

    support = tunnels.support()

    assert support.available is False and support.problem == "cloudflared is not installed"
    assert support.hint is not None and "https://pkg.cloudflare.com/" in support.hint
    with pytest.raises(PrerequisiteError, match="cloudflared is not installed"):
        make(tunnels)


def test_a_sealed_store_cannot_feed_a_connector_at_boot(
    tunnels: TunnelManager, secrets: SecretStore
) -> None:
    sealing.header_path(secrets.root).parent.mkdir(parents=True, exist_ok=True)
    sealing.header_path(secrets.root).write_text("sealed")

    support = tunnels.support()

    assert support.available is False and support.sealed is True
    with pytest.raises(PrerequisiteError, match="sealed"):
        make(tunnels)


def test_without_a_cloudflare_account_it_says_how_to_add_one(
    tunnels: TunnelManager, store: NoustStore
) -> None:
    DnsStore(store).delete_account("cf")

    support = tunnels.support()

    assert support.available is False
    assert support.hint is not None and "noust dns provider add" in support.hint


# -- creating ---------------------------------------------------------------------


def test_creating_runs_the_connector_with_the_token_in_a_private_file_and_nowhere_else(
    tunnels: TunnelManager,
    provider: FakeTunnelProvider,
    runner: FakeRunner,
    secrets: SecretStore,
) -> None:
    result = make(tunnels)

    tunnel = result.tunnel
    assert (tunnel.state, tunnel.tunnel_id, tunnel.unit) == (READY, "tun-1", "noust-tunnel-home")
    assert tunnel.account_id == "acc-1"
    assert provider.tunnels == {"tun-1": "home"}
    assert provider.ingress["tun-1"] == [{"service": "http_status:404"}]
    env_file = secrets.path(secret_name("home"))
    assert env_file.read_text() == f"TUNNEL_TOKEN={provider.token}\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    unit = (tunnels._systemd_dir / "noust-tunnel-home.service").read_text()
    assert f"EnvironmentFile={env_file}" in unit
    assert f"ExecStart={CLOUDFLARED} tunnel --no-autoupdate run" in unit
    assert "DynamicUser=yes" in unit and "# Generated by Noust" in unit
    assert provider.token not in unit
    assert not any(provider.token in word for call in runner.calls for word in call)
    assert not any(provider.token in str(value) for value in runner.envs if value)
    systemctl = [call for call in runner.calls if call[0] == "systemctl"]
    assert ("systemctl", "daemon-reload") in systemctl
    assert ("systemctl", "enable", "--now", "noust-tunnel-home.service") in systemctl


def test_the_only_cloudflare_account_is_the_default(tunnels: TunnelManager) -> None:
    assert tunnels.create("home").tunnel.provider == "cf"


def test_an_account_that_cannot_manage_a_tunnel_is_refused(
    tunnels: TunnelManager, store: NoustStore
) -> None:
    DnsStore(store).save_account(DnsAccount(name="do", kind="digitalocean"))

    with pytest.raises(ValidationError, match="not a Cloudflare account"):
        tunnels.create("home", account="do")


def test_a_token_that_reaches_several_accounts_must_say_which(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    provider.cloudflare_accounts = [("acc-1", "Main"), ("acc-2", "Other")]

    with pytest.raises(ValidationError, match="more than one Cloudflare account") as caught:
        make(tunnels)
    assert "acc-2 (Other)" in caught.value.details
    assert provider.tunnels == {}

    assert (
        tunnels.create("home", account="cf", cloudflare_account="acc-2").tunnel.account_id
        == "acc-2"
    )


def test_the_recorded_account_id_of_the_dns_account_is_used(
    tunnels: TunnelManager, provider: FakeTunnelProvider, store: NoustStore
) -> None:
    provider.cloudflare_accounts = [("acc-1", "Main"), ("acc-2", "Other")]
    DnsStore(store).save_account(
        DnsAccount(name="cf", kind="cloudflare", settings={"account_id": "acc-2"})
    )

    assert make(tunnels).tunnel.account_id == "acc-2"


@pytest.mark.parametrize("name", ["", "Home", "-home", "a b", "../x", "a" * 41])
def test_a_name_that_could_not_be_a_unit_is_refused(tunnels: TunnelManager, name: str) -> None:
    with pytest.raises(ValidationError):
        tunnels.create(name, account="cf")


def test_a_taken_name_is_refused(tunnels: TunnelManager) -> None:
    make(tunnels)

    with pytest.raises(ValidationError, match="already a tunnel called home"):
        make(tunnels)


def test_cloudflare_refusing_the_creation_leaves_nothing_behind(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    provider.fail_on = "create_tunnel"

    with pytest.raises(DnsProviderError, match="refused the create_tunnel"):
        make(tunnels)

    assert tunnels.records.all() == []
    assert not (tunnels._systemd_dir / "noust-tunnel-home.service").exists()


def test_a_connector_that_does_not_start_undoes_everything_and_systemds_words_are_the_error(
    tunnels: TunnelManager, provider: FakeTunnelProvider, runner: FakeRunner, secrets: SecretStore
) -> None:
    runner.script(
        ("systemctl", "enable", "--now"),
        stderr="Failed to enable unit: Unit file is masked.",
        exit_code=1,
    )

    with pytest.raises(ServiceError, match="did not start") as caught:
        make(tunnels)

    assert "Unit file is masked" in caught.value.details
    assert provider.tunnels == {}
    assert tunnels.records.all() == []
    assert not (tunnels._systemd_dir / "noust-tunnel-home.service").exists()
    assert secrets.read(secret_name("home")) is None


def test_a_token_that_is_not_one_is_never_written(
    tunnels: TunnelManager, provider: FakeTunnelProvider, secrets: SecretStore
) -> None:
    provider.token = "abc\nExecStartPre=/bin/sh"

    with pytest.raises(DnsProviderError, match="not one"):
        make(tunnels)

    assert secrets.read(secret_name("home")) is None
    assert provider.tunnels == {}


def test_a_failed_creation_that_cannot_delete_at_cloudflare_keeps_the_row_to_finish_from(
    tunnels: TunnelManager, provider: FakeTunnelProvider, runner: FakeRunner
) -> None:
    runner.script(("systemctl", "enable", "--now"), stderr="boom", exit_code=1)
    provider.refuse_delete = True

    with pytest.raises(ServiceError):
        make(tunnels)

    (left,) = tunnels.records.all()
    assert left.state == REMOVING and left.tunnel_id == "tun-1"

    provider.refuse_delete = False
    tunnels.repair()

    assert tunnels.records.all() == [] and provider.tunnels == {}


def test_a_rehearsal_creates_nothing(
    tmp_path: Path, tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    rehearsal = DryRunFileSystem()
    set_fs(rehearsal)
    try:
        result = make(tunnels)
    finally:
        set_fs(None)

    assert result.rehearsal is True
    assert provider.tunnels == {} and tunnels.records.all() == []


# -- routes -----------------------------------------------------------------------


def test_an_application_without_tls_is_reached_over_plain_http_with_its_host_kept(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)

    result = tunnels.add_route("home", APP, app_domain=APP)

    assert result.route.service == "http://localhost:80"
    assert provider.ingress["tun-1"] == [
        {
            "hostname": APP,
            "service": "http://localhost:80",
            "originRequest": {"httpHostHeader": APP},
        },
        {"service": "http_status:404"},
    ]


def test_an_application_with_tls_is_reached_over_tls_under_its_own_name(
    tunnels: TunnelManager, provider: FakeTunnelProvider, tls: dict[str, bool]
) -> None:
    tls["serves"] = True
    make(tunnels)

    tunnels.add_route("home", APP, app_domain=APP)

    rule = provider.ingress["tun-1"][0]
    assert rule["service"] == "https://localhost:443"
    assert rule["originRequest"] == {"noTLSVerify": True, "originServerName": APP}


def test_the_name_is_pointed_at_the_tunnel_with_a_proxied_cname_that_is_ours(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)

    result = tunnels.add_route("home", APP, app_domain=APP)

    assert result.dns["outcome"] == "created"
    (record,) = provider.records["zone-example.com"]
    assert (record.type, record.name, record.content, record.proxied) == (
        "CNAME",
        APP,
        "tun-1.cfargotunnel.com",
        True,
    )
    assert record.comment is not None and record.comment.startswith("managed-by: noust")


def test_a_route_can_name_its_service_and_needs_no_application(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)

    result = tunnels.add_route("home", "grafana.example.com", service="http://localhost:3001")

    assert result.route.service == "http://localhost:3001" and result.route.app_domain is None
    assert provider.holds("example.com", "grafana.example.com", "CNAME", "tun-1.cfargotunnel.com")
    # No application uses it, so the tunnel does: the sweep must not take it.
    record = ManagedDns(accounts=tunnels.accounts, store=tunnels._store).records.list_records()[0]
    assert ManagedDns(accounts=tunnels.accounts, store=tunnels._store).records.refs(
        record.id or 0
    ) == ["tunnel:home"]


def test_every_route_is_in_the_ingress_sorted_with_the_catch_all_last(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    tunnels.add_route("home", "zeta.example.com", service="http://localhost:1")
    tunnels.add_route("home", "alpha.example.com", service="http://localhost:2")

    rules = provider.ingress["tun-1"]

    assert [r.get("hostname") for r in rules] == ["alpha.example.com", "zeta.example.com", None]
    assert rules[-1] == {"service": "http_status:404"}


def test_a_name_that_is_not_the_applications_is_refused_with_how_to_add_it(
    tunnels: TunnelManager,
) -> None:
    make(tunnels)

    with pytest.raises(ValidationError, match=r"not a domain of shop\.example\.com") as caught:
        tunnels.add_route("home", "other.example.com", app_domain=APP)

    assert f"noust domain add {APP} other.example.com" in caught.value.details


def test_a_name_with_no_application_and_no_service_has_nowhere_to_go(
    tunnels: TunnelManager,
) -> None:
    make(tunnels)

    with pytest.raises(ValidationError, match="Say where the name goes"):
        tunnels.add_route("home", "x.example.com")


def test_an_unknown_application_and_an_unknown_tunnel_are_not_found(
    tunnels: TunnelManager,
) -> None:
    make(tunnels)

    with pytest.raises(ResourceNotFoundError, match="no application called ghost"):
        tunnels.add_route("home", APP, app_domain="ghost")
    with pytest.raises(ResourceNotFoundError, match="no tunnel called nope"):
        tunnels.add_route("nope", APP, app_domain=APP)


@pytest.mark.parametrize(
    "service",
    [
        "ssh://localhost:22",
        "tcp://localhost:5432",
        "http://localhost:3000/admin",
        "http://user:pass@localhost:3000",
        "http://localhost:3000?x=1",
        "localhost:3000",
        "http://",
        "http://localhost:99999",
        "http://local host:80",
    ],
)
def test_a_service_a_tunnel_does_not_carry_is_refused(service: str) -> None:
    with pytest.raises(ValidationError):
        checked_service(service)


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("http://localhost:3000", "http://localhost:3000"),
        ("https://10.0.0.5:8443/", "https://10.0.0.5:8443"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("HTTP://nas.lan", "http://nas.lan"),
    ],
)
def test_a_service_is_normalised(service: str, expected: str) -> None:
    assert checked_service(service) == expected


def test_a_name_another_tunnel_carries_is_a_conflict_that_says_where(
    tunnels: TunnelManager,
) -> None:
    make(tunnels)
    make(tunnels, "office")
    tunnels.add_route("home", "x.example.com", service="http://localhost:1")

    with pytest.raises(DomainConflictError, match="already carried by the tunnel home"):
        tunnels.add_route("office", "x.example.com", service="http://localhost:1")


def test_asking_again_changes_where_the_route_goes(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    tunnels.add_route("home", "x.example.com", service="http://localhost:1")

    again = tunnels.add_route("home", "x.example.com", service="http://localhost:2")

    assert [r.service for r in tunnels.routes("home")] == ["http://localhost:2"]
    assert provider.ingress["tun-1"][0]["service"] == "http://localhost:2"
    assert again.dns["outcome"] == "exists"


def test_a_record_somebody_else_made_is_a_conflict_the_operator_confirms_not_an_overwrite(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    other = provider.seed("example.com", "x.example.com", "CNAME", "elsewhere.example.net")

    first = tunnels.add_route("home", "x.example.com", service="http://localhost:1")

    assert first.dns["outcome"] == "conflict"
    assert first.dns["record_id"] == other.id and first.dns["current"] == "elsewhere.example.net"
    assert provider.holds("example.com", "x.example.com", "CNAME", "elsewhere.example.net")
    # The route works at Cloudflare the moment the name points at it.
    assert provider.ingress["tun-1"][0]["hostname"] == "x.example.com"

    second = tunnels.add_route(
        "home",
        "x.example.com",
        service="http://localhost:1",
        replace_record=other.id,
        replace_current="elsewhere.example.net",
    )

    assert second.dns["outcome"] == "replaced"
    assert provider.holds("example.com", "x.example.com", "CNAME", "tun-1.cfargotunnel.com")


def test_a_zone_no_account_holds_is_left_to_the_operator_with_the_record_to_make(
    tunnels: TunnelManager,
) -> None:
    make(tunnels)

    result = tunnels.add_route("home", "x.other.org", service="http://localhost:1")

    assert result.dns["outcome"] == "manual"
    assert result.dns["content"] == "tun-1.cfargotunnel.com"
    assert tunnels.records.routes("home")[0].hostname == "x.other.org"


def test_dns_can_be_left_alone(tunnels: TunnelManager, provider: FakeTunnelProvider) -> None:
    make(tunnels)

    result = tunnels.add_route("home", "x.example.com", service="http://localhost:1", dns=False)

    assert result.dns == {"outcome": "skipped"}
    assert provider.records["zone-example.com"] == []


def test_cloudflare_refusing_the_ingress_keeps_no_new_route_and_the_old_one_as_it_was(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    tunnels.add_route("home", "x.example.com", service="http://localhost:1")
    provider.refuse_configure = True

    with pytest.raises(DnsProviderError):
        tunnels.add_route("home", "x.example.com", service="http://localhost:2")
    with pytest.raises(DnsProviderError):
        tunnels.add_route("home", "y.example.com", service="http://localhost:3")

    assert [(r.hostname, r.service) for r in tunnels.routes("home")] == [
        ("x.example.com", "http://localhost:1")
    ]


def test_a_route_on_a_tunnel_that_is_not_ready_waits(
    tunnels: TunnelManager, store: NoustStore
) -> None:
    TunnelStore(store).create(
        Tunnel("half", "cf", "acc-1", "tun-9", "noust-tunnel-half", state="creating")
    )

    with pytest.raises(PrerequisiteError, match="half is creating"):
        tunnels.add_route("half", "x.example.com", service="http://localhost:1")


def test_removing_a_route_updates_the_ingress_and_deletes_the_record_we_made(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    tunnels.add_route("home", "x.example.com", service="http://localhost:1")
    tunnels.add_route("home", "y.example.com", service="http://localhost:2")

    result = tunnels.remove_route("home", "x.example.com")

    assert result.dns["outcome"] == "deleted"
    assert [r.get("hostname") for r in provider.ingress["tun-1"]] == ["y.example.com", None]
    assert not provider.holds("example.com", "x.example.com", "CNAME", "tun-1.cfargotunnel.com")
    assert provider.holds("example.com", "y.example.com", "CNAME", "tun-1.cfargotunnel.com")


def test_a_record_that_was_already_there_is_left_when_its_route_goes(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    provider.seed("example.com", "x.example.com", "CNAME", "tun-1.cfargotunnel.com")
    added = tunnels.add_route("home", "x.example.com", service="http://localhost:1")
    assert added.dns["outcome"] == "adopted"

    removed = tunnels.remove_route("home", "x.example.com")

    assert removed.dns["outcome"] == "not_owned"
    assert provider.holds("example.com", "x.example.com", "CNAME", "tun-1.cfargotunnel.com")


def test_keeping_the_dns_touches_no_record(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    tunnels.add_route("home", "x.example.com", service="http://localhost:1")

    result = tunnels.remove_route("home", "x.example.com", keep_dns=True)

    assert result.dns == {"outcome": "skipped"}
    assert provider.holds("example.com", "x.example.com", "CNAME", "tun-1.cfargotunnel.com")


def test_removing_a_route_a_tunnel_does_not_carry_is_a_not_found(tunnels: TunnelManager) -> None:
    make(tunnels)

    with pytest.raises(ResourceNotFoundError, match=r"does not carry x\.example\.com"):
        tunnels.remove_route("home", "x.example.com")


# -- deleting ---------------------------------------------------------------------


def test_deleting_stops_the_connector_deletes_the_tunnel_and_every_trace_of_it(
    tunnels: TunnelManager,
    provider: FakeTunnelProvider,
    runner: FakeRunner,
    secrets: SecretStore,
) -> None:
    make(tunnels)
    tunnels.add_route("home", "x.example.com", service="http://localhost:1")

    result = tunnels.delete("home")

    assert result.tunnel.name == "home"
    assert [item["outcome"] for item in result.released] == ["deleted"]
    assert ("systemctl", "disable", "--now", "noust-tunnel-home.service") in runner.calls
    assert provider.tunnels == {}
    assert provider.records["zone-example.com"] == []
    assert not (tunnels._systemd_dir / "noust-tunnel-home.service").exists()
    assert secrets.read(secret_name("home")) is None
    assert tunnels.records.all() == [] and tunnels.records.routes("home") == []


def test_a_deletion_cloudflare_refuses_keeps_the_row_and_asking_again_finishes_it(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)
    tunnels.add_route("home", "x.example.com", service="http://localhost:1")
    provider.refuse_delete = True

    with pytest.raises(DnsProviderError, match="active connections"):
        tunnels.delete("home")
    assert tunnels.get("home").state == REMOVING
    assert provider.holds("example.com", "x.example.com", "CNAME", "tun-1.cfargotunnel.com")

    provider.refuse_delete = False
    tunnels.delete("home")

    assert tunnels.records.all() == [] and provider.tunnels == {}
    assert provider.records["zone-example.com"] == []


def test_deleting_what_does_not_exist_is_a_not_found(tunnels: TunnelManager) -> None:
    with pytest.raises(ResourceNotFoundError, match="no tunnel called ghost"):
        tunnels.delete("ghost")


def test_a_dns_account_that_runs_a_tunnel_cannot_be_removed_from_under_it(
    tunnels: TunnelManager,
) -> None:
    make(tunnels)

    with pytest.raises(ValidationError, match="manages the tunnel home") as caught:
        tunnels.accounts.remove("cf")

    assert "noust tunnel remove home" in caught.value.details
    assert tunnels.accounts.get("cf").name == "cf"


def test_the_connector_is_a_unit_of_noust_and_not_an_applications() -> None:
    from noust.core import paths

    assert paths.is_own_unit_name(unit_name("home"))


def test_the_ingress_of_no_routes_is_only_the_catch_all() -> None:
    assert ingress_for([]) == [{"service": "http_status:404"}]
