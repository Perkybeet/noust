# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application served with a certificate its server's own authority signed, and kept renewed.

The authority and what it signs are tested with the real ``openssl`` in
``tests/test_internal_ca.py``; the web server is not part of this (the integration harness proves
that, with a real nginx). Pinned here: every name the application answers on is on the one
certificate; a name outside what the authority signs for stops it before anything is made or
changed; a renewal signs again for the same names and reloads the sites that serve it, one that
cannot be renewed does not keep the others from it, and a site that cannot be reloaded is named
because it still serves the old certificate; and the network maintenance pass does it by itself
only when something is due.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from noust.core import paths
from noust.core.certificate_store import SOURCE_CUSTOM, CertificateSettings, CertificateStore
from noust.core.exceptions import CertificateError, NoustError, ValidationError
from noust.core.store import App, NoustStore
from noust.deployers import certificates, domains, internal_certificates
from noust.managers import network_maintenance
from noust.managers.custom_certificates import fullchain_path
from noust.managers.internal_ca import InternalCA, NoAuthorityError

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not installed"
)
real = pytest.mark.allow_subprocess

WIKI = "wiki.home.arpa"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    NoustStore._instance = instance
    instance.create_app(App(domain=WIKI, app_path="/var/www/apps/wiki"))
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "etc-noust")
    try:
        yield instance
    finally:
        NoustStore.reset_instance()


class Sites:
    """What the web server was asked to reload, and which applications it refuses."""

    def __init__(self) -> None:
        self.reloaded: list[str] = []
        self.refuse: dict[str, str] = {}

    def deployer(self, app: App, *, verbose: bool = False) -> Any:
        sites = self

        class Deployer:
            def refresh_site(self, *, with_ssl: bool) -> None:
                if app.domain in sites.refuse:
                    raise CertificateError(sites.refuse[app.domain])
                assert with_ssl is True
                sites.reloaded.append(app.domain)

        return Deployer()


@pytest.fixture
def sites(monkeypatch: pytest.MonkeyPatch) -> Sites:
    fake = Sites()
    monkeypatch.setattr(domains, "site_deployer", fake.deployer)
    return fake


def serve_with(monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> list[tuple[str, str]]:
    """Stand in for the web server's side of choosing an uploaded certificate."""
    chosen: list[tuple[str, str]] = []

    def use(app: str, name: str, *, verbose: bool = False) -> Any:
        chosen.append((app, name))
        CertificateStore(store).set_settings(
            CertificateSettings(domain=app, source=SOURCE_CUSTOM, name=name)
        )
        return certificates.CertificateChange(
            certificates.CertificateView(
                app=app,
                source="custom",
                name=name,
                challenge="http-01",
                dns_provider=None,
                authority="Let's Encrypt",
                tls=True,
                names=(app,),
                certificate=None,
            )
        )

    monkeypatch.setattr(certificates, "use_uploaded", use)
    return chosen


# -- serving an application ---------------------------------------------------------------


@needs_openssl
@real
def test_every_name_the_application_answers_on_is_on_the_one_certificate(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = serve_with(monkeypatch, store)
    authority = InternalCA()
    authority.init()
    monkeypatch.setattr(
        internal_certificates,
        "certificate_names",
        lambda _store, domain: [WIKI, "docs.home.arpa", "wiki.lan"],
    )

    served = internal_certificates.serve(WIKI, days=45)

    assert chosen == [(WIKI, "internal-wiki-home-arpa")]
    assert set(served.names) == {WIKI, "docs.home.arpa", "wiki.lan"}
    assert served.certificate == "internal-wiki-home-arpa"
    info = authority.authority.inspect(fullchain_path(served.certificate))
    assert 44 <= (info.not_after - info.not_before).days <= 45


@needs_openssl
@real
def test_a_name_the_authority_cannot_sign_stops_everything_before_anything_changes(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = serve_with(monkeypatch, store)
    InternalCA().init()
    monkeypatch.setattr(
        internal_certificates,
        "certificate_names",
        lambda _store, domain: [WIKI, "wiki.example.com"],
    )

    with pytest.raises(ValidationError) as raised:
        internal_certificates.serve(WIKI)

    assert "wiki.example.com" in raised.value.message
    assert chosen == [] and InternalCA().issued() == []
    assert CertificateStore(store).get_settings(WIKI).source != SOURCE_CUSTOM


def test_without_an_authority_nothing_is_served_and_the_way_to_make_one_is_said(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = serve_with(monkeypatch, store)

    with pytest.raises(NoAuthorityError) as raised:
        internal_certificates.serve(WIKI)

    assert "noust cert ca init" in raised.value.details and chosen == []


def test_an_application_nobody_deployed_is_refused(store: NoustStore) -> None:
    with pytest.raises(NoustError) as raised:
        internal_certificates.serve("ghost.home.arpa")

    assert "ghost.home.arpa" in raised.value.message


# -- reloading the sites ------------------------------------------------------------------------


def serve_two(store: NoustStore, name: str) -> None:
    store.create_app(App(domain="docs.home.arpa", app_path="/var/www/apps/docs"))
    settings = CertificateStore(store)
    for domain in (WIKI, "docs.home.arpa"):
        settings.set_settings(CertificateSettings(domain=domain, source=SOURCE_CUSTOM, name=name))


def test_every_site_serving_a_renewed_certificate_is_reloaded(
    store: NoustStore, sites: Sites
) -> None:
    serve_two(store, "internal-wiki")

    reloaded = certificates.refresh_sites_serving("internal-wiki")

    assert sorted(reloaded) == sorted(sites.reloaded) == ["docs.home.arpa", WIKI]
    assert certificates.refresh_sites_serving("nobody-serves-this") == []


def test_a_site_that_cannot_be_reloaded_is_named_and_does_not_keep_the_others_waiting(
    store: NoustStore, sites: Sites
) -> None:
    serve_two(store, "internal-wiki")
    sites.refuse["docs.home.arpa"] = "nginx: [emerg] unexpected end of file"

    with pytest.raises(CertificateError) as raised:
        certificates.refresh_sites_serving("internal-wiki")

    assert sites.reloaded == [WIKI]
    assert "docs.home.arpa could not be reloaded" in raised.value.message
    assert "unexpected end of file" in raised.value.details
    assert "noust cert use docs.home.arpa --upload internal-wiki" in raised.value.details


# -- keeping it renewed ---------------------------------------------------------------------------


def later(days: int) -> Any:
    moment = datetime.now(timezone.utc) + timedelta(days=days)
    return partial(InternalCA, now=moment)


@needs_openssl
@real
def test_nothing_is_due_without_an_authority_or_while_the_certificates_have_time(
    store: NoustStore, sites: Sites
) -> None:
    assert internal_certificates.renewal_due() is False
    assert internal_certificates.renew_due() == []
    InternalCA().init()
    InternalCA().issue("wiki", [WIKI])  # 90 days

    assert internal_certificates.renewal_due() is False
    assert internal_certificates.renew_due() == []


@needs_openssl
@real
def test_what_is_within_thirty_days_is_signed_again_and_its_sites_reloaded(
    store: NoustStore, sites: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    InternalCA().init()
    first = InternalCA().issue("wiki", [WIKI, "10.0.0.5"])
    CertificateStore(store).set_settings(
        CertificateSettings(domain=WIKI, source=SOURCE_CUSTOM, name=first.name)
    )
    InternalCA().issue("fresh", ["fresh.home.arpa"], days=300)
    monkeypatch.setattr(internal_certificates, "InternalCA", later(70))

    assert internal_certificates.renewal_due() is True
    renewed = internal_certificates.renew_due()

    assert renewed == ["internal-wiki"] and sites.reloaded == [WIKI]
    after = {item.name: item for item in InternalCA().issued()}
    assert after["internal-wiki"].fingerprint != first.fingerprint
    assert after["internal-wiki"].apps == (WIKI,)
    info = InternalCA().authority.inspect(fullchain_path("internal-wiki"))
    assert "10.0.0.5" in info.names  # the address was kept too


@needs_openssl
@real
def test_a_short_certificate_is_not_signed_again_every_hour(
    store: NoustStore, sites: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A certificate made for 20 days is always within 30 of expiring: its window is a third."""
    InternalCA().init()
    InternalCA().issue("brief", ["brief.lan"], days=20)

    assert internal_certificates.renewal_due() is False
    monkeypatch.setattr(internal_certificates, "InternalCA", later(15))
    assert internal_certificates.renewal_due() is True


@needs_openssl
@real
def test_one_that_cannot_be_renewed_does_not_keep_the_others_from_it(
    store: NoustStore, sites: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    InternalCA().init(permit_dns=["corp.example.com"])
    InternalCA().issue("good", ["good.lan"], days=40)
    InternalCA().issue("corp", ["wiki.corp.example.com"], days=40)
    # a root made again without the suffix: its certificate for it can no longer be renewed
    authority = InternalCA()
    authority.remove(force=True)
    authority.init(permit_dns=["lan"], only=True)
    monkeypatch.setattr(internal_certificates, "InternalCA", later(35))

    with pytest.raises(CertificateError) as raised:
        internal_certificates.renew_due()

    assert "internal-corp" in raised.value.message
    assert "internal-good" not in raised.value.message
    assert "Renewed: internal-good" in raised.value.details


# -- the hourly pass --------------------------------------------------------------------------------


@needs_openssl
@real
def test_the_network_pass_renews_them_and_says_so(
    store: NoustStore, sites: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    InternalCA().init()
    InternalCA().issue("wiki", [WIKI], days=40)
    monkeypatch.setattr(internal_certificates, "InternalCA", later(35))
    monkeypatch.setattr("noust.deployers.private_access.renewal_due", lambda now=None: False)
    monkeypatch.setattr("noust.deployers.private_access.renew_tailnet_certificates", lambda: [])

    assert network_maintenance.work_due() is True
    result = network_maintenance.maintain()

    assert result.renewed == ("internal-wiki",) and result.errors == ()


@needs_openssl
@real
def test_a_failure_of_the_authority_is_reported_and_the_rest_of_the_pass_still_runs(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> list[str]:
        raise CertificateError("The authority has expired")

    monkeypatch.setattr(internal_certificates, "renew_due", refuse)
    monkeypatch.setattr("noust.deployers.private_access.renew_tailnet_certificates", lambda: [])

    result = network_maintenance.maintain()

    assert result.errors == (
        "The internal authority's certificates were not renewed: The authority has expired",
    )
