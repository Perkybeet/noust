# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application leaves in the network when it is deleted.

The site and the certificate go with it elsewhere. What this pins: the tunnel routes made for
it, the published ports that were for it, its DNS records, its policy and its certificate
choice are all let go of; a step that fails does not stop the ones after it and is returned as
a warning; and another application's are left alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.certificate_store import CertificateSettings, CertificateStore
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.site_policy_store import SitePolicyStore
from noust.core.store import App, NoustStore
from noust.core.stream_store import OPEN, StreamExposure, StreamStore
from noust.core.tunnel_store import Tunnel, TunnelRoute, TunnelStore
from noust.deployers.network_cleanup import forget_network
from noust.managers.dns.records import ManagedDns, Release, ReleaseResult
from noust.managers.stream_exposure import StreamExposures
from noust.managers.tunnels import TunnelManager

APP = "shop.example.com"
OTHER = "blog.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    for name in (APP, OTHER):
        instance.create_app(App(domain=name, app_path=f"/srv/{name}", port=3000))
    DnsStore(instance).save_account(DnsAccount(name="cf", kind="cloudflare"))
    tunnels = TunnelStore(instance)
    tunnels.create(Tunnel("home", "cf", "acc-1", "tun-1", "noust-tunnel-home", "ready"))
    tunnels.set_route(TunnelRoute("home", APP, "http://localhost:80", APP))
    tunnels.set_route(TunnelRoute("home", OTHER, "http://localhost:80", OTHER))
    streams = StreamStore(instance)
    for name, port, app in (("mine", 25565, APP), ("theirs", 25566, OTHER), ("free", 25567, None)):
        streams.create(
            StreamExposure(
                name=name,
                protocol="tcp",
                listen_port=port,
                target_host="127.0.0.1",
                target_port=port,
                app_domain=app,
                state=OPEN,
            )
        )
    SitePolicyStore(instance).set_document(APP, '{"noindex": true}', updated_by=None)
    CertificateStore(instance).set_settings(
        CertificateSettings(APP, challenge="dns-01", dns_provider="cf")
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def done(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def remove_route(self: TunnelManager, tunnel: str, hostname: str, **kwargs: Any) -> None:
        calls.append(("route", tunnel, hostname))
        self.records.delete_route(tunnel, hostname)

    def close(self: StreamExposures, name: str, **kwargs: Any) -> None:
        calls.append(("close", name))
        self.records.delete(name)

    def release(self: ManagedDns, app: str, **kwargs: Any) -> list[ReleaseResult]:
        calls.append(("release", app))
        return [ReleaseResult(APP, "A", Release.DELETED, "cf")]

    monkeypatch.setattr(TunnelManager, "remove_route", remove_route)
    monkeypatch.setattr(StreamExposures, "close", close)
    monkeypatch.setattr(ManagedDns, "release", release)
    return calls


def test_everything_made_for_the_application_is_let_go_of_and_nothing_else(
    store: NoustStore, done: list[tuple[str, ...]]
) -> None:
    warnings = forget_network(APP, Logger())

    assert warnings == []
    assert done == [("route", "home", APP), ("close", "mine"), ("release", APP)]
    assert [r.hostname for r in TunnelStore(store).routes("home")] == [OTHER]
    assert sorted(e.name for e in StreamStore(store).all()) == ["free", "theirs"]
    assert SitePolicyStore(store).get_document(APP) is None
    assert CertificateStore(store).get_settings(APP).is_default is True


def test_an_application_that_made_nothing_costs_nothing(
    store: NoustStore, done: list[tuple[str, ...]]
) -> None:
    store.create_app(App(domain="plain.example.com", app_path="/srv/plain"))

    assert forget_network("plain.example.com", Logger()) == []
    assert done == [("release", "plain.example.com")]


def test_a_step_that_fails_does_not_stop_the_ones_after_it(
    store: NoustStore, done: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: TunnelManager, tunnel: str, hostname: str, **kwargs: Any) -> None:
        raise ValidationError("Cloudflare refused the ingress")

    monkeypatch.setattr(TunnelManager, "remove_route", refuse)

    warnings = forget_network(APP, Logger())

    assert warnings == ["Its tunnel routes were not all removed: Cloudflare refused the ingress"]
    assert ("close", "mine") in done and ("release", APP) in done
    assert SitePolicyStore(store).get_document(APP) is None


def test_a_rehearsal_lets_go_of_nothing(store: NoustStore, done: list[tuple[str, ...]]) -> None:
    from noust.core.fs import DryRunFileSystem, set_fs

    set_fs(DryRunFileSystem())
    try:
        assert forget_network(APP, Logger()) == []
    finally:
        set_fs(None)

    assert done == []
    assert SitePolicyStore(store).get_document(APP) is not None
