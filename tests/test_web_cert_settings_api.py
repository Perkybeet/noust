# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/certs/acme``, ``/api/certs/uploaded`` and ``/api/apps/{domain}/certificate``.

The rules are pinned in ``tests/test_cert_network.py``, ``tests/test_custom_certificates.py``
and ``tests/test_certificate_choice.py``. This module owns the translation: the HMAC key and
the private key never come back, putting a key in needs sudo mode, certbot is a job and not
the request, the literal paths are not read as a domain, and the status of each refusal.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import paths
from noust.core.certificate_store import CertificateSettings, CertificateStore
from noust.core.config import _validate_known_value
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.deployers import certificates
from noust.managers.acme import AcmeSettings
from noust.managers.cert_manager import CertificateInfo
from noust.web.api import cert_settings as api
from noust.web.api import certs as certs_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.test_custom_certificates import KEY, LEAF, RSA_TEXT

BEARER = {"sid": "token", "scope": "admin", "source": "bearer", "type": "api_token"}
COOKIE = {"sid": "s1", "scope": "admin", "source": "cookie", "type": "session"}


class MemoryConfig:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        # The rules of the real configuration, without its file.
        self.values[key] = _validate_known_value(key, value)

    def write(self) -> None:
        pass


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_path="/var/www/apps/shop"))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def acme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AcmeSettings:
    settings = AcmeSettings(
        config=MemoryConfig(),  # type: ignore[arg-type]
        secrets=SecretStore(root=tmp_path / "state" / "secrets"),
    )
    monkeypatch.setattr(api, "AcmeSettings", lambda: settings)
    return settings


def build(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    # The literal paths first, as the router mounts them.
    app.include_router(api.router, prefix="/api/certs")
    app.include_router(certs_api.router, prefix="/api/certs")
    app.include_router(api.apps_router, prefix="/api/apps")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def client(store: NoustStore, acme: AcmeSettings) -> TestClient:
    return build(BEARER)


class FakeJobs:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create_job(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        return SimpleNamespace(
            id="job-1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "job-1"}
        )


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> FakeJobs:
    fake = FakeJobs()
    monkeypatch.setattr(api, "get_job_manager", lambda: fake)
    return fake


# -- the authority ---------------------------------------------------------------------


def test_the_authority_is_shown_with_the_choices_and_without_the_key(client: TestClient) -> None:
    response = client.get("/api/certs/acme")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["ca"], body["label"], body["eab_configured"]) == (
        "letsencrypt",
        "Let's Encrypt",
        False,
    )
    assert [a["name"] for a in body["authorities"]] == [
        "letsencrypt",
        "letsencrypt-staging",
        "zerossl",
        "google",
        "custom",
    ]
    assert (
        next(a for a in body["authorities"] if a["name"] == "letsencrypt-staging")["trusted"]
        is False
    )


def test_changing_the_authority_and_putting_a_key_in_needs_sudo_mode_only_for_the_key(
    store: NoustStore, acme: AcmeSettings
) -> None:
    cookie = build(COOKIE)

    plain = cookie.put(
        "/api/certs/acme", json={"ca": "letsencrypt-staging", "email": "ops@example.com"}
    )
    key = cookie.put("/api/certs/acme", json={"eab_hmac_key": "secret-hmac-key"})

    assert plain.status_code == 200 and plain.json()["ca"] == "letsencrypt-staging"
    assert key.status_code == 403 and key.json()["error"] == "elevation_required"
    assert acme.current().eab_configured is False


def test_the_hmac_key_goes_in_and_never_comes_back(client: TestClient, acme: AcmeSettings) -> None:
    response = client.put(
        "/api/certs/acme",
        json={"ca": "zerossl", "eab_kid": "kid-1234", "eab_hmac_key": "secret-hmac-key"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["eab_configured"] is True
    assert "secret-hmac-key" not in response.text
    assert "secret-hmac-key" not in client.get("/api/certs/acme").text


def test_a_value_the_settings_refuse_is_a_400_naming_the_field(client: TestClient) -> None:
    custom = client.put("/api/certs/acme", json={"ca": "custom"})
    odd = client.put("/api/certs/acme", json={"ca": "nobody"})

    assert custom.status_code == 400 and custom.json()["fields"]["acme_server"]
    assert odd.status_code == 400


def test_registering_the_account_is_a_job(client: TestClient, jobs: FakeJobs) -> None:
    response = client.post("/api/certs/acme/register")

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == "job-1"
    (job,) = jobs.created
    assert job["func"] is api.register_account_job


def test_acme_and_uploaded_are_not_read_as_a_certificate_named_so(client: TestClient) -> None:
    assert client.get("/api/certs/acme").json()["ca"] == "letsencrypt"
    assert client.get("/api/certs/uploaded").json() == {"certificates": []}


def test_the_certificates_list_says_where_each_one_comes_from() -> None:
    uploaded = certs_api._to_cert_info(
        CertificateInfo(
            name="origin",
            domains=["example.com"],
            expiry="2030-01-01",
            source="custom",
            challenge="",
        )
    )
    ordered = certs_api._to_cert_info(
        CertificateInfo(
            name="shop.example.com",
            domains=["shop.example.com"],
            expiry="2030-01-01",
            challenge="dns-01",
        )
    )

    assert (uploaded.source, uploaded.challenge, uploaded.auto_renew) == ("custom", "", False)
    assert (ordered.source, ordered.challenge, ordered.auto_renew) == (
        "letsencrypt",
        "dns-01",
        True,
    )


# -- uploads ----------------------------------------------------------------------------


@pytest.fixture
def uploading(runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "etc-noust")
    runner.script(["openssl", "x509", "-noout", "-text"], stdout=RSA_TEXT)
    runner.script(["openssl", "x509", "-noout", "-pubkey"], stdout="PUB\n")
    runner.script(["openssl", "pkey", "-pubout"], stdout="PUB\n")
    return tmp_path / "etc-noust" / "certs"


def test_an_upload_is_checked_kept_and_listed_without_its_key(
    client: TestClient, uploading: Path
) -> None:
    response = client.post(
        "/api/certs/uploaded", json={"name": "origin", "certificate": LEAF, "private_key": KEY}
    )

    assert response.status_code == 201, response.text
    assert response.json()["names"] == ["origin.example.com", "*.example.com"]
    assert "KEYKEYKEY" not in response.text
    listing = client.get("/api/certs/uploaded").json()["certificates"]
    assert [c["name"] for c in listing] == ["origin"]
    assert listing[0]["used_by"] == [] and listing[0]["days_remaining"] > 0
    assert (uploading / "origin" / "privkey.pem").stat().st_mode & 0o777 == 0o600


def test_uploading_and_removing_ask_for_sudo_mode(store: NoustStore, acme: AcmeSettings) -> None:
    cookie = build(COOKIE)

    uploading = cookie.post(
        "/api/certs/uploaded", json={"name": "origin", "certificate": LEAF, "private_key": KEY}
    )
    removing = cookie.delete("/api/certs/uploaded/origin")

    assert uploading.status_code == 403 and removing.status_code == 403


def test_a_wrong_key_is_a_400_naming_the_field_and_an_unknown_upload_a_404(
    client: TestClient, uploading: Path, runner: FakeRunner
) -> None:
    runner.script(["openssl", "pkey", "-pubout"], stdout="OTHER\n")

    wrong = client.post(
        "/api/certs/uploaded", json={"name": "origin", "certificate": LEAF, "private_key": KEY}
    )
    missing = client.delete("/api/certs/uploaded/ghost")

    assert wrong.status_code == 400 and wrong.json()["fields"]["private_key"]
    assert missing.status_code == 404


def test_an_upload_an_application_serves_cannot_be_removed(
    client: TestClient, uploading: Path, store: NoustStore
) -> None:
    client.post(
        "/api/certs/uploaded", json={"name": "origin", "certificate": LEAF, "private_key": KEY}
    )
    CertificateStore(store).set_settings(
        CertificateSettings(domain="shop.example.com", source="custom", name="origin")
    )

    removed = client.delete("/api/certs/uploaded/origin")
    listing = client.get("/api/certs/uploaded").json()["certificates"]

    assert removed.status_code == 400
    assert listing[0]["used_by"] == ["shop.example.com"]


# -- an application's choice ---------------------------------------------------------------


@pytest.fixture
def operations(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    view = certificates.CertificateView(
        app="shop.example.com",
        source="acme",
        name=None,
        challenge="http-01",
        dns_provider=None,
        authority="Let's Encrypt",
        tls=True,
        names=("shop.example.com",),
        certificate={
            "name": "shop.example.com",
            "names": ["shop.example.com"],
            "issuer": "R11",
            "expires_on": "2026-11-30",
            "days_remaining": 48,
            "source": "letsencrypt",
        },
    )

    def stub(name: str, result: Any) -> Any:
        def fake(*args: Any, **kwargs: Any) -> Any:
            calls.append((name, args, kwargs))
            return result

        return fake

    monkeypatch.setattr(certificates, "describe", stub("describe", view))
    monkeypatch.setattr(certificates, "use_uploaded", stub("use_uploaded", view))
    return calls


def test_the_certificate_of_an_application_is_described(
    client: TestClient, operations: list[Any]
) -> None:
    response = client.get("/api/apps/shop.example.com/certificate")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["source"], body["challenge"], body["authority"]) == (
        "acme",
        "http-01",
        "Let's Encrypt",
    )
    assert body["certificate"]["days_remaining"] == 48
    assert body["job_id"] is None


def test_serving_an_upload_happens_at_once_and_orders_nothing(
    client: TestClient, operations: list[Any], jobs: FakeJobs
) -> None:
    response = client.put(
        "/api/apps/shop.example.com/certificate", json={"source": "custom", "name": "origin"}
    )

    assert response.status_code == 200, response.text
    assert operations[-1][:2] == ("use_uploaded", ("shop.example.com", "origin"))
    assert jobs.created == []


def test_ordering_is_a_job_not_the_request(
    client: TestClient, operations: list[Any], jobs: FakeJobs, store: NoustStore
) -> None:
    from noust.core.dns_store import DnsAccount, DnsStore

    DnsStore(store).save_account(DnsAccount(name="cf", kind="cloudflare"))

    response = client.put(
        "/api/apps/shop.example.com/certificate",
        json={"source": "acme", "challenge": "dns-01", "dns_provider": "cf"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "job-1"
    (job,) = jobs.created
    assert job["func"] is api.choose_certificate_job
    assert job["kwargs"] == {
        "app_domain": "shop.example.com",
        "challenge": "dns-01",
        "dns_provider": "cf",
        "order": True,
    }


@pytest.mark.parametrize(
    "body",
    [
        {"source": "custom"},
        {"source": "nowhere"},
        {"source": "acme", "challenge": "dns-01"},
        {"source": "acme", "challenge": "dns-01", "dns_provider": "ghost"},
        {"source": "acme", "challenge": "tls-alpn-01"},
    ],
)
def test_a_choice_that_cannot_work_is_refused_before_a_job_is_queued(
    client: TestClient, operations: list[Any], jobs: FakeJobs, body: dict[str, Any]
) -> None:
    response = client.put("/api/apps/shop.example.com/certificate", json=body)

    assert response.status_code == 400, response.text
    assert jobs.created == []


def test_an_unknown_application_is_a_404(client: TestClient, operations: list[Any]) -> None:
    assert client.get("/api/apps/ghost.example.com/certificate").status_code == 404
    assert client.put("/api/apps/ghost.example.com/certificate", json={}).status_code == 404
