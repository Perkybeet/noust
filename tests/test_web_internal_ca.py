# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/certs/ca``: the server's own certificate authority over the API.

The authority is made by the real ``openssl`` (the rules are pinned in ``tests/test_internal_ca.py``
and ``tests/test_cli_cert_ca.py``). Here: making it, deleting it and trusting it on this server ask
for sudo mode, the root can be downloaded in both forms and the key is in no response, a server
without one answers 404 and says how to make it, the literal path is not read as a domain named
``ca``, an application can choose to be served by it through the certificate endpoint, and the
status of each refusal.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import paths
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import certificates, internal_certificates
from noust.managers import ca_trust
from noust.managers.ca_trust import ANCHOR_NAME, ServerStore
from noust.managers.internal_ca import InternalCA
from noust.web.api import cert_settings, internal_ca
from noust.web.api import certs as certs_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not installed"
)
real = pytest.mark.allow_subprocess

BEARER = {"sid": "token", "scope": "admin", "source": "bearer", "type": "api_token"}
COOKIE = {"sid": "s1", "scope": "admin", "source": "cookie", "type": "session"}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "state" / "noust.db")
    NoustStore._instance = store
    store.create_app(App(domain="wiki.home.arpa", app_path="/var/www/apps/wiki"))
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "etc-noust")
    anchors = tmp_path / "anchors"
    anchors.mkdir()
    system = ServerStore("debian", "Debian", anchors, "update-ca-certificates", (), ("--fresh",))
    original = ca_trust.ServerTrust
    monkeypatch.setattr(
        internal_ca.ca_trust,
        "ServerTrust",
        lambda: original(
            runner=FakeRunner(), stores=(system,), which=lambda tool: f"/usr/sbin/{tool}"
        ),
    )
    try:
        yield tmp_path
    finally:
        NoustStore.reset_instance()


def build(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    # The literal path first, as the router mounts it.
    app.include_router(internal_ca.router, prefix="/api/certs/ca")
    app.include_router(cert_settings.router, prefix="/api/certs")
    app.include_router(certs_api.router, prefix="/api/certs")
    app.include_router(cert_settings.apps_router, prefix="/api/apps")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def client(world: Path) -> TestClient:
    return build(BEARER)


def make(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/certs/ca", json=body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


# -- before there is one -------------------------------------------------------------------


def test_a_server_without_an_authority_shows_the_defaults_a_new_one_would_have(
    client: TestClient,
) -> None:
    response = client.get("/api/certs/ca")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authority"] is None and body["issued"] == [] and body["trusted_here"] is False
    assert body["root_pem"] is None
    assert "home.arpa" in body["default_dns"] and "10.0.0.0/8" in body["default_networks"]
    assert body["system_store"] == "Debian"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/certs/ca/root"),
        ("GET", "/api/certs/ca/trust"),
        ("POST", "/api/certs/ca/trust-here"),
        ("DELETE", "/api/certs/ca"),
        ("POST", "/api/certs/ca/issued"),
    ],
)
def test_without_an_authority_each_route_answers_404_and_says_how_to_make_one(
    client: TestClient, method: str, path: str
) -> None:
    body = {"name": "wiki", "names": ["wiki.home.arpa"]} if path.endswith("issued") else None

    response = client.request(method, path, json=body)

    assert response.status_code == 404, response.text
    assert "noust cert ca init" in response.text or "POST /api/certs/ca" in response.text


def test_renewing_what_was_never_signed_is_a_404(client: TestClient) -> None:
    response = client.post("/api/certs/ca/issued/internal-wiki/renew")

    assert response.status_code == 404, response.text


def test_the_literal_path_is_not_read_as_a_domain_named_ca(client: TestClient) -> None:
    # certs_api's /{domain} would answer for "ca" if the authority's router were mounted after it
    response = client.get("/api/certs/ca")

    assert response.status_code == 200 and "authority" in response.json()


# -- making it ----------------------------------------------------------------------------------


@needs_openssl
@real
def test_the_authority_is_made_shown_and_has_no_key_in_any_answer(
    client: TestClient, world: Path
) -> None:
    made = make(client, permit_dns=["corp.example.com"], name="Test CA")

    shown = client.get("/api/certs/ca").json()
    assert made["subject"] == "CN = Test CA"
    assert "corp.example.com" in made["permitted_dns"]
    assert shown["authority"]["fingerprint"] == made["fingerprint"]
    assert "PRIVATE KEY" not in client.get("/api/certs/ca").text
    assert shown["root_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert (world / "etc-noust" / "ca" / "ca.key").exists()


@needs_openssl
@real
def test_making_a_second_one_is_a_conflict_and_a_bad_request_makes_nothing(
    client: TestClient, world: Path
) -> None:
    first = make(client)

    again = client.post("/api/certs/ca", json={})
    short = client.post("/api/certs/ca", json={"days": 3})

    assert again.status_code == 409, again.text
    assert "already has an internal certificate authority" in again.text
    assert short.status_code == 422
    assert client.get("/api/certs/ca").json()["authority"]["fingerprint"] == first["fingerprint"]


@needs_openssl
@real
def test_an_authority_that_would_sign_for_a_public_domain_is_refused_naming_the_field(
    client: TestClient, world: Path
) -> None:
    response = client.post("/api/certs/ca", json={"permit_dns": ["com"]})

    assert response.status_code == 400, response.text
    assert list(response.json()["fields"]) == ["permit"]
    assert "top-level domain" in response.json()["fields"]["permit"]
    assert not (world / "etc-noust" / "ca").exists()


@needs_openssl
@real
def test_making_deleting_and_trusting_here_ask_for_sudo_mode(world: Path) -> None:
    cookie = build(COOKIE)

    for call in (
        cookie.post("/api/certs/ca", json={}),
        cookie.delete("/api/certs/ca"),
        cookie.post("/api/certs/ca/trust-here"),
        cookie.delete("/api/certs/ca/trust-here"),
    ):
        assert call.status_code == 403 and call.json()["error"] == "elevation_required"
    assert not (world / "etc-noust").exists()


# -- handing it to a device ---------------------------------------------------------------------


@needs_openssl
@real
def test_the_root_is_downloaded_as_pem_and_as_der(client: TestClient, tmp_path: Path) -> None:
    made = make(client)

    pem = client.get("/api/certs/ca/root")
    der = client.get("/api/certs/ca/root", params={"format": "der"})
    bad = client.get("/api/certs/ca/root", params={"format": "p12"})

    assert pem.status_code == 200 and pem.headers["content-type"].startswith("application/x-pem")
    assert pem.text.startswith("-----BEGIN CERTIFICATE-----") and "PRIVATE" not in pem.text
    assert 'filename="ca.crt"' in pem.headers["content-disposition"]
    assert der.headers["content-type"] == "application/x-x509-ca-cert"
    target = tmp_path / "ca.cer"
    target.write_bytes(der.content)
    printed = subprocess.run(
        [
            "openssl",
            "x509",
            "-inform",
            "der",
            "-in",
            str(target),
            "-noout",
            "-fingerprint",
            "-sha256",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert made["fingerprint"].upper() in printed.upper()
    assert bad.status_code == 422


@needs_openssl
@real
def test_the_guides_carry_the_fingerprint_to_compare(client: TestClient) -> None:
    made = make(client)

    body = client.get("/api/certs/ca/trust").json()

    assert body["fingerprint"] == made["fingerprint"]
    assert {guide["id"] for guide in body["guides"]} >= {"debian", "macos", "windows", "ios"}
    windows = next(guide for guide in body["guides"] if guide["id"] == "windows")
    assert windows["file"] == "der" and made["fingerprint"] in " ".join(windows["steps"])


@needs_openssl
@real
def test_this_server_can_trust_it_and_stop(client: TestClient, world: Path) -> None:
    make(client)
    assert client.get("/api/certs/ca").json()["trusted_here"] is False

    on = client.post("/api/certs/ca/trust-here")
    assert (world / "anchors" / ANCHOR_NAME).read_text().startswith("-----BEGIN CERTIFICATE-----")
    off = client.delete("/api/certs/ca/trust-here")

    assert on.status_code == 200 and on.json()["trusted_here"] is True
    assert off.status_code == 200 and off.json()["trusted_here"] is False
    assert not (world / "anchors" / ANCHOR_NAME).exists()


@needs_openssl
@real
def test_making_it_can_trust_it_here_in_the_same_request(client: TestClient, world: Path) -> None:
    make(client, trust_here=True)

    assert (world / "anchors" / ANCHOR_NAME).exists()
    assert client.get("/api/certs/ca").json()["trusted_here"] is True


# -- what it signs ------------------------------------------------------------------------------


@needs_openssl
@real
def test_a_certificate_is_signed_listed_and_renewed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(client)

    signed = client.post(
        "/api/certs/ca/issued", json={"name": "wiki", "names": ["wiki.home.arpa"], "days": 60}
    )
    listed = client.get("/api/certs/ca").json()
    monkeypatch.setattr(certificates, "refresh_sites_serving", lambda name: [])
    renewed = client.post("/api/certs/ca/issued/internal-wiki/renew")

    assert signed.status_code == 201, signed.text
    assert signed.json()["name"] == "internal-wiki" and "PRIVATE" not in signed.text
    assert [item["name"] for item in listed["issued"]] == ["internal-wiki"]
    assert listed["authority"]["issued"] == 1
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["fingerprint"] != signed.json()["fingerprint"]


@needs_openssl
@real
@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "names": ["www.bank.com"]},
        {"name": "x", "names": ["8.8.8.8", "wiki.lan"]},
        {"name": "x", "names": ["10.0.0.1"]},
        {"name": "***", "names": ["wiki.lan"]},
    ],
)
def test_a_name_the_authority_cannot_sign_is_a_400_naming_the_field(
    client: TestClient, body: dict[str, Any]
) -> None:
    make(client)

    response = client.post("/api/certs/ca/issued", json=body)

    assert response.status_code == 400, response.text
    assert client.get("/api/certs/ca").json()["issued"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "names": []},
        {"name": "x", "names": ["a.lan"], "days": 398},
        {"names": ["a.lan"]},
    ],
)
def test_a_malformed_request_is_a_422(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/certs/ca/issued", json=body).status_code == 422


@needs_openssl
@real
def test_an_application_is_served_by_it_through_the_certificate_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(client)
    seen: dict[str, Any] = {}

    def fake_use(app: str, name: str, *, verbose: bool = False) -> Any:
        seen["use"] = (app, name)
        return certificates.CertificateChange(
            certificates.CertificateView(
                app=app,
                source="custom",
                name=name,
                challenge="http-01",
                dns_provider=None,
                authority="Let's Encrypt",
                tls=True,
                names=("wiki.home.arpa",),
                certificate=None,
            )
        )

    monkeypatch.setattr(certificates, "use_uploaded", fake_use)
    monkeypatch.setattr(
        internal_certificates, "certificate_names", lambda store, domain: ["wiki.home.arpa"]
    )

    response = client.put(
        "/api/apps/wiki.home.arpa/certificate", json={"source": "internal", "days": 30}
    )

    assert response.status_code == 200, response.text
    assert seen["use"] == ("wiki.home.arpa", "internal-wiki-home-arpa")
    assert (
        response.json()["source"] == "custom"
        and response.json()["name"] == "internal-wiki-home-arpa"
    )
    listed = client.get("/api/certs/ca").json()["issued"]
    assert [item["name"] for item in listed] == ["internal-wiki-home-arpa"]


def test_an_unknown_source_names_the_three_there_are(client: TestClient) -> None:
    response = client.put("/api/apps/wiki.home.arpa/certificate", json={"source": "magic"})

    assert response.status_code == 400
    assert "acme, custom or internal" in response.text


def test_removing_the_authority_passes_force_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # what makes a plain delete refuse (certificates still kept) is pinned with the real openssl
    # in the manager's tests; here only the translation of the query
    calls: list[bool] = []
    monkeypatch.setattr(
        InternalCA, "remove", lambda self, *, force=False: calls.append(force), raising=True
    )

    assert client.delete("/api/certs/ca").status_code == 204
    assert client.delete("/api/certs/ca", params={"force": "true"}).status_code == 204

    assert calls == [False, True]
