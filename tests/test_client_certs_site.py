# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811

"""
A site that asks for client certificates: what is rendered, and the guards in front of it.

The certificates themselves are made by the real ``openssl`` in ``tests/test_client_certs.py`` and
the whole handshake was driven against a real nginx when this was written; here, over the domains
harness, is what the site's file says and when Noust refuses to write it: the check is in the TLS
handshake (before any HTTP), the revoked serials are refused by name, nobody is locked out by
accident, and turning the requirement off leaves nothing behind.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.client_cert_store import ClientCertificateStore
from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.deployers import access
from noust.managers.client_certs import ClientCertificates
from tests.test_domains import (  # noqa: F401 - fixtures of the domains harness
    Machine,
    certs,
    machine,
    store,
    web,
)

SERIAL = "1A" + "0" * 30
OTHER = "2B" + "0" * 30


@pytest.fixture
def served(machine: Machine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Machine:
    # openssl is the real thing in test_client_certs.py; here only the file nginx would read.
    root = tmp_path / "clients"
    real = ClientCertificates.__init__

    def init(self: ClientCertificates, *args: object, **kwargs: object) -> None:
        real(self, *args, **{**kwargs, "root": root})  # type: ignore[arg-type]

    def authority(self: ClientCertificates, domain: str) -> None:
        self.ca_path(domain).parent.mkdir(parents=True, exist_ok=True)
        self.ca_path(domain).write_text("-----BEGIN CERTIFICATE-----\n")

    monkeypatch.setattr(ClientCertificates, "__init__", init)
    monkeypatch.setattr(ClientCertificates, "ensure_authority", authority)
    machine.deploy(app_type="nodejs", tls=True)
    return machine


def keep(
    machine: Machine,
    serial: str = SERIAL,
    *,
    name: str = "ana",
    days: int = 30,
    revoked: bool = False,
) -> None:
    records = ClientCertificateStore(machine.store)
    now = datetime.now(timezone.utc)
    records.add(
        "example.com",
        name=name,
        serial=serial,
        fingerprint="AA:BB",
        not_before=(now - timedelta(days=1)).isoformat(),
        not_after=(now + timedelta(days=days)).isoformat(),
        issued_by="ana",
    )
    if revoked:
        records.revoke("example.com", serial, by="ana")


def test_a_site_that_asks_checks_in_the_handshake_with_its_own_authority(served: Machine) -> None:
    keep(served)

    access.set_policy("example.com", {"client_certs": True})

    config = served.config()
    assert "ssl_verify_client on;" in config
    assert "ssl_verify_depth 1;" in config
    authority = [line for line in config.splitlines() if "ssl_client_certificate" in line]
    assert len(authority) == 1 and authority[0].strip().endswith("/ca.crt;")
    assert "example-com/ca.crt" in authority[0]  # this application's, not a shared one
    assert "proxy_set_header X-SSL-Client-Subject $ssl_client_s_dn;" in config
    assert "proxy_set_header X-SSL-Client-Serial $ssl_client_serial;" in config


def test_nothing_is_refused_by_name_until_a_certificate_is_revoked(served: Machine) -> None:
    keep(served)
    keep(served, OTHER, name="bob")

    access.set_policy("example.com", {"client_certs": True})

    assert "return 403" not in served.config()
    assert OTHER not in served.config()

    access.revoke_client_certificate("example.com", OTHER)

    config = served.config()
    assert OTHER in config and SERIAL not in config
    assert "return 403;" in config


def test_the_headers_the_application_reads_cannot_be_forged_by_the_visitor(
    served: Machine,
) -> None:
    keep(served)

    access.set_policy("example.com", {"client_certs": True})

    # proxy_set_header replaces what the visitor sent under the same name: both are set from the
    # verified certificate, so an application can trust them.
    config = served.config()
    assert config.count("X-SSL-Client-Subject $ssl_client_s_dn") >= 1
    assert "X-SSL-Client-Subject $http_" not in config


def test_turning_it_off_leaves_nothing_of_it(served: Machine) -> None:
    keep(served)
    keep(served, OTHER, name="bob", revoked=True)
    access.set_policy("example.com", {"client_certs": True})

    access.set_policy("example.com", {"client_certs": False})

    config = served.config()
    for leftover in ("ssl_verify_client", "ssl_client_certificate", "X-SSL-Client", OTHER):
        assert leftover not in config


def test_no_valid_certificate_is_refused_in_words_and_the_site_is_untouched(
    served: Machine,
) -> None:
    before = served.config()

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"client_certs": True})

    assert raised.value.field == "client_certs"
    assert "so no one could open it" in raised.value.message
    assert "noust app policy clients issue" in (raised.value.details or "")
    assert served.config() == before
    assert access.describe("example.com").policy.client_certs is False


@pytest.mark.parametrize("kind", ["revoked", "expired"])
def test_a_certificate_that_would_not_get_anyone_in_does_not_count(
    served: Machine, kind: str
) -> None:
    keep(served, revoked=kind == "revoked", days=30 if kind == "revoked" else -2)

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"client_certs": True})

    assert "so no one could open it" in raised.value.message


def test_the_operator_may_accept_that_nobody_gets_in_yet(served: Machine) -> None:
    access.set_policy("example.com", {"client_certs": True}, accept_lockout=True)

    assert "ssl_verify_client on;" in served.config()


def test_the_last_valid_certificate_is_not_revoked_by_accident(served: Machine) -> None:
    keep(served)
    access.set_policy("example.com", {"client_certs": True})
    before = served.config()

    with pytest.raises(ValidationError) as raised:
        access.revoke_client_certificate("example.com", SERIAL)

    assert raised.value.field == "serial" and "last valid" in raised.value.message
    assert served.config() == before
    assert [c.revoked for c in access.client_certificates("example.com")] == [False]

    assert access.revoke_client_certificate("example.com", SERIAL, accept_lockout=True) is True
    assert SERIAL in served.config()


def test_revoking_where_the_site_does_not_ask_changes_the_record_and_not_the_site(
    served: Machine,
) -> None:
    keep(served)
    before = served.config()

    assert access.revoke_client_certificate("example.com", SERIAL) is True

    assert served.config() == before
    assert access.revoke_client_certificate("example.com", SERIAL) is False  # once


def test_a_serial_nobody_made_is_refused(served: Machine) -> None:
    with pytest.raises(ValidationError) as raised:
        access.revoke_client_certificate("example.com", "FF" * 16)

    assert raised.value.field == "serial"


def test_a_site_with_no_https_cannot_ask_in_a_handshake_it_does_not_have(
    machine: Machine, runner: FakeRunner
) -> None:
    machine.deploy(app_type="nodejs", tls=False)

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"client_certs": True}, accept_lockout=True)

    assert raised.value.field == "client_certs" and "HTTPS" in raised.value.message


def test_apache_is_refused_in_words(machine: Machine, runner: FakeRunner) -> None:
    app = machine.deploy(app_type="nodejs", tls=True)
    machine.store.update_app(replace(app, webserver="apache"))

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"client_certs": True}, accept_lockout=True)

    assert raised.value.field == "client_certs" and "nginx" in raised.value.message


def test_a_refused_site_is_put_back_and_the_requirement_with_it(
    served: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep(served)
    before = served.config()
    monkeypatch.setattr(served.web, "config_errors", lambda: "nginx: [emerg] bad directive")

    with pytest.raises(ValidationError):
        access.set_policy("example.com", {"client_certs": True})

    assert served.config() == before
    assert access.describe("example.com").policy.client_certs is False
