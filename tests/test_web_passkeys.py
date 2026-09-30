"""
Passkeys over HTTP: registration, sign-in, sudo mode, the master token, and refusals.

A software authenticator plays the browser and the security key, with real
signatures, against the whole console served by ``create_app``. The console
is reached at ``http://localhost:8080``, the one plain-HTTP address a browser
offers WebAuthn on (an SSH tunnel to a server); ``127.0.0.1`` is refused with
the reason, as a browser would refuse it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")

from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_brute_force, get_token_manager
from tests.webauthn_authenticator import SoftwareAuthenticator

PASSWORD = "correct horse battery staple"
BASE = "http://localhost:8080"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def app(sandbox: Path) -> Any:
    config = SecurityConfig(
        state_dir=sandbox / "state", rate_limit_requests=5000, auth_policy=AuthPolicy()
    )
    return create_app(config)


def client_for(app: Any, base_url: str = BASE) -> TestClient:
    return TestClient(app, base_url=base_url, client=("testclient", 50000))


def accounts() -> AccountManager:
    return get_token_manager().accounts


def make_account(username: str, role: str = "admin", *, mfa: bool = True) -> list[str]:
    account = accounts().create(username, role, password=PASSWORD)
    if not mfa:
        return []
    secret = accounts().begin_totp(account.id)
    codes = accounts().confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    return codes


class Browser:
    """A client signed in, carrying its CSRF token."""

    def __init__(self, client: TestClient, csrf: str) -> None:
        self.client = client
        self.csrf = csrf

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.client.post(path, headers={CSRF_HEADER_NAME: self.csrf}, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.client.patch(path, headers={CSRF_HEADER_NAME: self.csrf}, **kwargs)

    def delete(self, path: str) -> Any:
        return self.client.delete(path, headers={CSRF_HEADER_NAME: self.csrf})


def sign_in(app: Any, username: str, code: str | None) -> Browser:
    client = client_for(app)
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD, "totp_code": code}
    )
    assert response.status_code == 200, response.text
    return Browser(client, response.json()["csrf_token"])


def elevate(browser: Browser, code: str) -> None:
    response = browser.post("/api/auth/elevate", json={"password": PASSWORD, "code": code})
    assert response.status_code == 200, response.text


def register(
    browser: Browser, authenticator: SoftwareAuthenticator, name: str = "Laptop"
) -> dict[str, Any]:
    options = browser.post("/api/auth/passkeys/registration/options")
    assert options.status_code == 200, options.text
    credential = authenticator.create(options.json()["public_key"], BASE)
    response = browser.post(
        "/api/auth/passkeys/registration", json={"credential": credential, "name": name}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def passkey_sign_in(app: Any, authenticator: SoftwareAuthenticator, **bend: Any) -> Any:
    client = client_for(app)
    options = client.post("/api/auth/passkeys/login/options", json={})
    assert options.status_code == 200, options.text
    credential = authenticator.get(options.json()["public_key"], BASE, **bend)
    return client, client.post("/api/auth/passkeys/login", json={"credential": credential})


class TestRegistration:
    def test_an_account_registers_a_passkey_in_sudo_mode(self, app: Any) -> None:
        codes = make_account("maria")
        browser = sign_in(app, "maria", codes[0])

        refused = browser.post("/api/auth/passkeys/registration/options")
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"

        elevate(browser, codes[1])
        body = register(browser, SoftwareAuthenticator())

        assert body["passkey"]["name"] == "Laptop"
        assert body["passkey"]["rp_id"] == "localhost"
        assert body["passkey"]["owner"] == "maria"
        assert body["backup_codes"] is None  # the authenticator brought them already
        listed = browser.client.get("/api/auth/passkeys").json()
        assert [item["id"] for item in listed["passkeys"]] == [body["passkey"]["id"]]
        assert listed["availability"] == {
            "supported": True,
            "rp_id": "localhost",
            "reason": None,
            "detail": None,
            "hint": None,
        }

    def test_a_first_second_factor_needs_no_sudo_and_brings_backup_codes(self, app: Any) -> None:
        make_account("juan", "operator", mfa=False)
        browser = sign_in(app, "juan", None)
        assert browser.client.get("/api/apps").json()["error"] == "mfa_required"

        body = register(browser, SoftwareAuthenticator())

        assert len(body["backup_codes"]) == 8
        assert browser.client.get("/api/auth/session").json()["mfa_required"] is False
        assert accounts().require("juan").has_mfa

    def test_the_options_carry_the_decisions(self, app: Any) -> None:
        make_account("juan", "operator", mfa=False)
        browser = sign_in(app, "juan", None)
        options = browser.post("/api/auth/passkeys/registration/options").json()

        public_key = options["public_key"]
        assert public_key["authenticatorSelection"]["residentKey"] == "required"
        assert public_key["authenticatorSelection"]["userVerification"] == "required"
        assert public_key["attestation"] == "none"
        assert options["expires_in"] == 300

    def test_a_token_cannot_hold_a_passkey(self, app: Any) -> None:
        token = get_token_manager().generate_master_token()
        response = client_for(app).post(
            "/api/auth/passkeys/registration/options",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "passkey_session_required"

    def test_an_address_is_refused_with_the_reason(self, app: Any) -> None:
        make_account("juan", "operator", mfa=False)
        client = client_for(app, "http://127.0.0.1:8080")
        login = client.post("/api/auth/login", json={"username": "juan", "password": PASSWORD})
        csrf = login.json()["csrf_token"]

        listed = client.get("/api/auth/passkeys").json()
        assert listed["availability"]["supported"] is False
        assert listed["availability"]["reason"] == "ip_address"
        refused = client.post(
            "/api/auth/passkeys/registration/options", headers={CSRF_HEADER_NAME: csrf}
        )
        assert refused.status_code == 400
        assert refused.json()["error"] == "passkeys_ip_address"
        assert "localhost" in refused.json()["hint"]

    def test_an_answer_from_another_origin_is_rejected(self, app: Any) -> None:
        make_account("juan", "operator", mfa=False)
        browser = sign_in(app, "juan", None)
        options = browser.post("/api/auth/passkeys/registration/options").json()
        credential = SoftwareAuthenticator().create(options["public_key"], "http://evil.localhost")
        response = browser.post(
            "/api/auth/passkeys/registration", json={"credential": credential, "name": "x"}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "passkey_rejected"


class TestSignIn:
    def enrolled(self, app: Any) -> tuple[SoftwareAuthenticator, list[str]]:
        codes = make_account("maria")
        browser = sign_in(app, "maria", codes[0])
        elevate(browser, codes[1])
        authenticator = SoftwareAuthenticator()
        register(browser, authenticator)
        return authenticator, codes

    def test_a_passkey_signs_in_on_its_own(self, app: Any) -> None:
        authenticator, _codes = self.enrolled(app)

        client, response = passkey_sign_in(app, authenticator)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["account"]["username"] == "maria"
        assert body["mfa_required"] is False
        session = client.get("/api/auth/session").json()
        assert session["authenticated"] is True
        assert session["role"] == "admin"

    def test_the_sign_in_options_are_anonymous_and_name_nobody(self, app: Any) -> None:
        options = client_for(app).post(
            "/api/auth/passkeys/login/options", json={"conditional": True}
        )
        assert options.status_code == 200
        assert options.json()["public_key"]["allowCredentials"] == []
        assert options.json()["mediation"] == "conditional"

    def test_a_tampered_assertion_is_refused_and_counted(self, app: Any) -> None:
        authenticator, _codes = self.enrolled(app)
        _client, response = passkey_sign_in(app, authenticator, tamper_signature=True)

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_passkey"
        assert get_brute_force().get_attempts_remaining("testclient") < 5
        assert accounts().require("maria").failures_since_login == 1

    def test_an_unknown_passkey_is_named_as_such(self, app: Any) -> None:
        stranger = SoftwareAuthenticator()
        stranger.rp_id = "localhost"
        stranger.user_handle = b"z" * 32
        _client, response = passkey_sign_in(app, stranger)
        assert response.status_code == 401
        assert response.json()["error"] == "passkey_unknown"
        assert "localhost" in response.json()["hint"]

    def test_a_disabled_account_s_passkey_does_not_sign_in(self, app: Any) -> None:
        authenticator, _codes = self.enrolled(app)
        accounts().disable("maria", "left the company")
        _client, response = passkey_sign_in(app, authenticator)
        assert response.status_code == 401

    def test_a_locked_out_address_is_refused_before_the_passkey(self, app: Any) -> None:
        authenticator, _codes = self.enrolled(app)
        for _ in range(5):
            get_brute_force().record_failure("testclient")
        _client, response = passkey_sign_in(app, authenticator)
        assert response.status_code == 429

    def test_a_passkey_confirms_sudo_mode(self, app: Any) -> None:
        authenticator, _codes = self.enrolled(app)
        client, response = passkey_sign_in(app, authenticator)
        browser = Browser(client, response.json()["csrf_token"])
        assert client.get("/api/auth/session").json()["elevated_until"] is None

        options = browser.post("/api/auth/passkeys/elevate/options").json()["public_key"]
        assert len(options["allowCredentials"]) == 1
        confirmed = browser.post(
            "/api/auth/passkeys/elevate", json={"credential": authenticator.get(options, BASE)}
        )

        assert confirmed.status_code == 200, confirmed.text
        assert client.get("/api/auth/session").json()["elevated_until"] is not None

    def test_a_password_alone_no_longer_signs_a_passkey_only_account_in(self, app: Any) -> None:
        make_account("juan", "operator", mfa=False)
        browser = sign_in(app, "juan", None)
        codes = register(browser, SoftwareAuthenticator())["backup_codes"]

        refused = client_for(app).post(
            "/api/auth/login", json={"username": "juan", "password": PASSWORD}
        )
        assert refused.status_code == 401
        with_code = client_for(app).post(
            "/api/auth/login",
            json={"username": "juan", "password": PASSWORD, "totp_code": codes[0]},
        )
        assert with_code.status_code == 200


class TestGovernance:
    def test_rename_and_remove(self, app: Any) -> None:
        codes = make_account("maria")
        browser = sign_in(app, "maria", codes[0])
        elevate(browser, codes[1])
        first = register(browser, SoftwareAuthenticator())["passkey"]["id"]

        renamed = browser.patch(f"/api/auth/passkeys/{first}", json={"name": "Office key"})
        assert renamed.json()["name"] == "Office key"
        removed = browser.delete(f"/api/auth/passkeys/{first}")
        assert removed.status_code == 200, removed.text
        assert browser.client.get("/api/auth/passkeys").json()["passkeys"] == []

    def test_the_only_second_factor_stays(self, app: Any) -> None:
        make_account("juan", "operator", mfa=False)
        browser = sign_in(app, "juan", None)
        body = register(browser, SoftwareAuthenticator())
        elevate(browser, body["backup_codes"][0])

        refused = browser.delete(f"/api/auth/passkeys/{body['passkey']['id']}")
        assert refused.status_code == 400
        assert "only second factor" in refused.json()["detail"]

    def test_someone_else_s_passkey_is_not_theirs_to_touch(self, app: Any) -> None:
        make_account("juan", "operator", mfa=False)
        juan = sign_in(app, "juan", None)
        juans = register(juan, SoftwareAuthenticator())["passkey"]["id"]
        codes = make_account("maria")
        maria = sign_in(app, "maria", codes[0])
        elevate(maria, codes[1])

        assert maria.patch(f"/api/auth/passkeys/{juans}", json={"name": "mine"}).status_code == 400
        assert maria.delete(f"/api/auth/passkeys/{juans}").status_code == 400

    def test_removing_needs_sudo_mode(self, app: Any) -> None:
        codes = make_account("maria")
        browser = sign_in(app, "maria", codes[0])
        elevate(browser, codes[1])
        passkey = register(browser, SoftwareAuthenticator())["passkey"]["id"]
        sid = browser.client.get("/api/auth/sessions").json()["current_session"]
        get_token_manager().sessions.set_elevated(sid, None)
        response = browser.delete(f"/api/auth/passkeys/{passkey}")
        assert response.status_code == 403


class TestMasterToken:
    def master(self, app: Any) -> tuple[Browser, str]:
        token = get_token_manager().generate_master_token()
        client = client_for(app)
        response = client.post("/api/auth/login", json={"token": token})
        assert response.status_code == 200, response.text
        browser = Browser(client, response.json()["csrf_token"])
        confirmed = browser.post("/api/auth/elevate", json={"token": token})
        assert confirmed.status_code == 200, confirmed.text
        return browser, token

    def test_once_it_has_a_passkey_the_token_alone_does_not_sign_in(self, app: Any) -> None:
        browser, token = self.master(app)
        authenticator = SoftwareAuthenticator()
        body = register(browser, authenticator)
        assert body["passkey"]["owner"] == "master"
        assert body["backup_codes"] is None

        alone = client_for(app).post("/api/auth/login", json={"token": token})
        assert alone.status_code == 401
        assert alone.json()["error"] == "passkey_required"

        _client, response = passkey_sign_in(app, authenticator)
        assert response.status_code == 200, response.text
        assert response.json()["grant"] == "compat"

    def test_nor_does_it_confirm_sudo_mode(self, app: Any) -> None:
        browser, token = self.master(app)
        register(browser, SoftwareAuthenticator())
        refused = browser.post("/api/auth/elevate", json={"token": token})
        assert refused.status_code == 401
        assert refused.json()["error"] == "passkey_required"

    def test_with_totp_as_well_either_factor_is_named(self, app: Any) -> None:
        browser, token = self.master(app)
        manager = get_token_manager()
        secret = manager.begin_totp_enrollment()
        assert manager.confirm_totp_enrollment(totp.totp_now(secret)) is not None
        register(browser, SoftwareAuthenticator())

        alone = client_for(app).post("/api/auth/login", json={"token": token})
        assert alone.json()["error"] == "second_factor_required"

    def test_without_a_passkey_nothing_changes(self, app: Any) -> None:
        _browser, token = self.master(app)
        assert client_for(app).post("/api/auth/login", json={"token": token}).status_code == 200


def test_the_account_list_counts_passkeys(app: Any) -> None:
    make_account("juan", "operator", mfa=False)
    register(sign_in(app, "juan", None), SoftwareAuthenticator())
    assert accounts().require("juan").to_dict()["passkeys"] == 1
    from noust.web.api.auth_accounts import AccountInfo

    assert AccountInfo.of(accounts().require("juan")).passkeys == 1
