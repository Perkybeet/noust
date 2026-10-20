# ruff: noqa: F811
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Changing where an application's certificate comes from: ordered, proven another way, uploaded.

The invariant under all of it: the certificate that serves keeps serving until the new one
is in place. A failed order or a site the web server refuses puts the choice back, and
never takes TLS away. Run on the harness of ``tests/test_domains.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import paths
from noust.core.certificate_store import CertificateSettings, CertificateStore, CustomCertificate
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.exceptions import CertificateError, ValidationError
from noust.core.runner import FakeRunner
from noust.deployers import certificates, domains
from tests.test_domains import (  # noqa: F401 - the harness
    Machine,
    certs,
    machine,
    put_certificate,
    store,
    web,
)

LISTING = (
    "  Certificate Name: example.com\n"
    "    Domains: example.com\n"
    "    Expiry Date: 2026-11-30 12:00:00+00:00 (VALID: 60 days)\n"
    "    Certificate Path: /etc/letsencrypt/live/example.com/fullchain.pem\n"
    "    Private Key Path: /etc/letsencrypt/live/example.com/privkey.pem\n"
)


@pytest.fixture(autouse=True)
def the_machines_certificates(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(certificates, "CertManager", lambda verbose=False: machine.certs)
    monkeypatch.setattr(
        "noust.managers.cert_manager.find_noust_executable", lambda: "/usr/bin/noust"
    )


def settings(domain: str = "example.com") -> CertificateSettings:
    return CertificateStore().get_settings(domain)


def account(name: str = "cf") -> None:
    DnsStore().save_account(DnsAccount(name=name, kind="cloudflare"))


def upload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "etc-noust")
    directory = tmp_path / "etc-noust" / "certs" / "origin"
    directory.mkdir(parents=True)
    (directory / "fullchain.pem").write_text("chain")
    (directory / "privkey.pem").write_text("key")
    CertificateStore().save_custom(
        CustomCertificate(
            name="origin",
            names=names,
            subject="CN = origin",
            issuer="CN = Cloudflare Origin CA",
            key_type="RSA 2048",
            fingerprint="AA:BB",
            not_before="2026-01-01T00:00:00+00:00",
            not_after="2031-01-01T00:00:00+00:00",
            uploaded_by="test",
            uploaded_at="2026-10-13T00:00:00+00:00",
        )
    )


# -- ordering ------------------------------------------------------------------------


def test_switching_a_served_certificate_to_dns_orders_it_again_over_dns(
    machine: Machine,
) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    account()
    machine.runner.script(["certbot", "certificates"], stdout=LISTING)

    change = certificates.use_acme("example.com", challenge="dns-01", dns_provider="cf")

    (order,) = machine.certbot_orders()
    assert "--manual" in order and "--force-renewal" in order and "--expand" in order
    assert change.ordered is True
    assert (settings().challenge, settings().dns_provider) == ("dns-01", "cf")
    assert change.view.challenge == "dns-01"
    assert "listen 443 ssl http2;" in machine.config()


def test_a_failed_order_puts_the_choice_back_and_the_certificate_keeps_serving(
    machine: Machine,
) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    account()
    machine.runner.script(["certbot", "certificates"], stdout=LISTING)
    machine.runner.script(
        ["certbot", "certonly"], stderr="DNS problem: NXDOMAIN looking up TXT", exit_code=1
    )
    before = machine.config()

    with pytest.raises(CertificateError):
        certificates.use_acme("example.com", challenge="dns-01", dns_provider="cf")

    assert settings().is_default
    assert machine.config() == before


def test_the_choice_is_only_kept_for_a_site_that_serves_no_tls_yet(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    account()

    change = certificates.use_acme("example.com", challenge="dns-01", dns_provider="cf")

    assert change.ordered is False
    assert machine.certbot_orders() == []
    assert settings().challenge == "dns-01"


def test_a_certificate_ordered_for_a_plain_site_is_written_into_it(machine: Machine) -> None:
    # `noust cert create` (and the console's "Issue certificate") order a certificate and record
    # that the site serves TLS. certbot's nginx plugin edits the site on an HTTP order; the DNS
    # challenge has no installer, so the site stayed on port 80 while Noust said it served TLS,
    # and the advice after a failed deploy ("To add SSL later: noust cert create") did not
    # deliver. Found against a real certbot and a real authority.
    machine.deploy(app_type="nodejs")
    account()
    certificates.use_acme("example.com", challenge="dns-01", dns_provider="cf")
    put_certificate(machine.certs, "example.com")
    machine.certs.obtain("example.com")
    assert "listen 443" not in machine.config()  # what the order alone leaves

    served = certificates.serve_ordered("example.com")

    assert served is True
    config = machine.config()
    assert "listen 443 ssl http2;" in config
    assert "ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem" in config.replace(
        str(machine.certs.LETSENCRYPT_DIR), "/etc/letsencrypt"
    )


def test_a_name_that_is_no_application_is_left_to_its_order(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    assert certificates.serve_ordered("elsewhere.example.org") is False
    assert "listen 443" not in machine.config()


def test_a_type_with_no_site_to_render_is_left_to_its_order(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.deploy(app_type="nodejs")
    put_certificate(machine.certs, "example.com")

    def refuses(*args: Any, **kwargs: Any) -> Any:
        raise ValidationError("container applications do not support aliases and redirects yet")

    monkeypatch.setattr(domains, "site_deployer", refuses)

    assert certificates.serve_ordered("example.com") is False


def test_a_site_that_does_not_serve_tls_yet_is_not_given_a_certificate_it_lacks(
    machine: Machine,
) -> None:
    machine.deploy(app_type="nodejs")  # no TLS recorded, no lineage on disk

    assert certificates.serve_ordered("example.com") is False
    assert "listen 443" not in machine.config()


def test_the_dns_challenge_needs_an_account_that_exists(machine: Machine) -> None:
    machine.deploy(app_type="nodejs", tls=True)

    with pytest.raises(ValidationError) as missing:
        certificates.use_acme("example.com", challenge="dns-01")
    with pytest.raises(ValidationError) as unknown:
        certificates.use_acme("example.com", challenge="dns-01", dns_provider="ghost")
    with pytest.raises(ValidationError) as odd:
        certificates.use_acme("example.com", challenge="tls-alpn-01")

    assert missing.value.field == "dns_provider"
    assert "noust dns provider list" in unknown.value.details
    assert odd.value.field == "challenge"
    assert settings().is_default


def test_going_back_to_http_forgets_the_account(machine: Machine) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    account()
    machine.runner.script(["certbot", "certificates"], stdout=LISTING)
    certificates.use_acme("example.com", challenge="dns-01", dns_provider="cf")
    machine.runner.calls.clear()

    certificates.use_acme("example.com", challenge="http-01")

    assert settings().is_default
    (order,) = machine.certbot_orders()
    assert "--manual" not in order and "--force-renewal" in order


def test_a_monorepo_certificate_is_its_deploys_to_order(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    app = machine.store.get_app("example.com")
    assert app is not None
    app.app_type = "monorepo"
    machine.store.update_app(app)

    with pytest.raises(ValidationError) as raised:
        certificates.use_acme("example.com")

    assert "ordered by its deploy" in raised.value.message


# -- uploaded -------------------------------------------------------------------------


def test_an_uploaded_certificate_is_written_into_the_site_and_nothing_is_ordered(
    machine: Machine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    upload(tmp_path, monkeypatch, "example.com", "*.example.com")
    machine.runner.calls.clear()

    change = certificates.use_uploaded("example.com", "origin")

    config = machine.config()
    origin = tmp_path / "etc-noust" / "certs" / "origin"
    assert f"ssl_certificate {origin}/fullchain.pem;" in config
    assert f"ssl_certificate_key {origin}/privkey.pem;" in config
    assert machine.certbot_orders() == []
    assert machine.reloaded()
    assert (settings().source, settings().name) == ("custom", "origin")
    assert change.view.source == "custom"
    # A redeploy renders the same: the upload is not put back over by the lineage.
    machine.deployer("nodejs").configure("example.com", "/x", port=3000, app_path=tmp_path)
    domains.refresh_site(machine.store.get_app("example.com"))  # type: ignore[arg-type]
    assert f"ssl_certificate {origin}/fullchain.pem;" in machine.config()


def test_an_uploaded_certificate_that_misses_a_name_is_refused_before_anything_changes(
    machine: Machine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    domains.add_domain("example.com", "shop.example.com", dns=False)
    upload(tmp_path, monkeypatch, "example.com")
    before = machine.config()

    with pytest.raises(ValidationError) as raised:
        certificates.use_uploaded("example.com", "origin")

    assert "shop.example.com" in raised.value.message
    assert settings().is_default
    assert machine.config() == before


def test_a_site_nginx_refuses_goes_back_to_the_lineage(
    machine: Machine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    upload(tmp_path, monkeypatch, "example.com")
    machine.runner.script(
        ["nginx", "-t"], stderr="nginx: [emerg] cannot load certificate\n", exit_code=1
    )
    before = machine.config()

    with pytest.raises(ValidationError):
        certificates.use_uploaded("example.com", "origin")

    assert settings().is_default
    assert machine.config() == before


def test_something_never_uploaded_is_said(machine: Machine) -> None:
    machine.deploy(app_type="nodejs", tls=True)

    with pytest.raises(CertificateError) as raised:
        certificates.use_uploaded("example.com", "ghost")

    assert "noust cert upload" in raised.value.details


def test_going_from_an_upload_back_to_an_order_keeps_the_upload_until_the_order_works(
    machine: Machine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    upload(tmp_path, monkeypatch, "example.com")
    certificates.use_uploaded("example.com", "origin")
    machine.runner.script(["certbot", "certonly"], stderr="Rate limited", exit_code=1)
    served = machine.config()

    with pytest.raises(CertificateError):
        certificates.use_acme("example.com")

    assert settings().source == "custom"
    assert machine.config() == served


def test_the_view_says_what_is_served_and_how_it_comes_to_be(
    machine: Machine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.deploy(app_type="nodejs", tls=True)
    machine.runner.script(["certbot", "certificates"], stdout=LISTING)

    view = certificates.describe("example.com")

    assert (view.source, view.challenge, view.tls) == ("acme", "http-01", True)
    assert view.names == ("example.com",)
    assert view.certificate is not None and view.certificate["expires_on"] == "2026-11-30"
    assert view.authority == "Let's Encrypt"
    _ = (tmp_path, monkeypatch, Any, FakeRunner)
