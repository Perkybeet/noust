# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Single sign-in over HTTP: the browser's redirects, the cookie, the ticket, the
second factor an account still owes, the lockout, the audit trail and the
administrator's side.

The whole console is served by ``create_app``; a fake provider (real keys, real
signatures) answers behind the client's opener, so what runs is what a browser
would meet.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("cryptography")

from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts.oidc_client import OidcClient, OidcHttp, set_client
from noust.core.accounts.policy import build_policy
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_brute_force, get_token_manager
from tests.oidc_support import Clock, FakeIdP, b64

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
def idp() -> Iterator[FakeIdP]:
    clock = Clock()
    provider = FakeIdP(clock=clock)
    provider.add_user("sub-maria", email="maria@acme.test", email_verified=True, name="Maria Lopez")
    set_client(OidcClient(OidcHttp(opener=provider.opener), clock=clock))
    yield provider
    set_client(None)


@pytest.fixture
def app(sandbox: Path, idp: FakeIdP) -> Any:
    return create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=5000, auth_policy=AuthPolicy()
        )
    )


def browser(app: Any) -> TestClient:
    return TestClient(app, base_url=BASE, client=("testclient", 50000))


def accounts() -> AccountManager:
    return get_token_manager().accounts


def make_account(
    username: str, role: str = "viewer", *, mfa: bool = True, person_ref: str | None = None
) -> list[str]:
    account = accounts().create(username, role, password=PASSWORD, person_ref=person_ref)
    if not mfa:
        return []
    secret = accounts().begin_totp(account.id)
    codes = accounts().confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    return codes


def configure(idp: FakeIdP, **changes: Any) -> None:
    from noust.core.accounts.oidc import OidcManager

    manager = OidcManager(accounts().store, accounts=accounts())
    settings = {"issuer": idp.issuer, "client_id": idp.client_id, "label": "Acme SSO", **changes}
    manager.configure("corp", settings, client_secret=idp.client_secret)


def link(username: str, subject: str = "sub-maria") -> None:
    from noust.core.accounts.oidc import OidcManager

    OidcManager(accounts().store, accounts=accounts()).link("corp", username, subject)


def target(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def through_the_provider(
    client: TestClient,
    idp: FakeIdP,
    sub: str = "sub-maria",
    *,
    next: str | None = "/apps",
    **claims: Any,
) -> Any:
    """Click the button, approve at the provider, and arrive at the callback."""
    params = {"provider": "corp", **({"next": next} if next else {})}
    started = client.get("/api/auth/oidc/start", params=params, follow_redirects=False)
    assert started.status_code == 302, started.text
    callback = idp.authorize(started.headers["location"], sub, **claims)
    return client.get(target(callback), follow_redirects=False)


def ticket_of(response: Any) -> str:
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert "sso" in query, response.headers["location"]
    return query["sso"][0]


def error_of(response: Any) -> str:
    assert response.status_code in (303, 302), response.text
    location = urlsplit(response.headers["location"])
    assert location.path == "/login", response.headers["location"]
    return parse_qs(location.query)["sso_error"][0]


def audit_entries(sandbox: Path) -> list[dict[str, Any]]:
    path = sandbox / "state" / "web-audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def logged_all(sandbox: Path) -> list[dict[str, Any]]:
    return audit_entries(sandbox)


def logged(sandbox: Path, action: str) -> list[dict[str, Any]]:
    return [entry for entry in audit_entries(sandbox) if entry["action"] == action]


class TestTheButton:
    def test_the_sign_in_page_is_told_the_enabled_providers_and_nothing_else(
        self, app: Any, idp: FakeIdP
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp, provisioning="auto", link_by_email=True, group_sync=True)

        response = browser(app).get("/api/auth/oidc/providers")

        assert response.status_code == 200
        assert response.json() == {
            "providers": [{"name": "corp", "label": "Acme SSO", "protocol": "oidc"}],
            "local_login": True,
            "password_reset": False,
        }
        assert "no-store" in response.headers["cache-control"]
        assert idp.issuer not in response.text and idp.client_id not in response.text

    def test_no_providers_is_an_empty_list(self, app: Any) -> None:
        assert browser(app).get("/api/auth/oidc/providers").json() == {
            "providers": [],
            "local_login": True,
            "password_reset": False,
        }

    def test_a_disabled_provider_is_not_offered(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        configure(idp, enabled=False)
        assert browser(app).get("/api/auth/oidc/providers").json() == {
            "providers": [],
            "local_login": True,
            "password_reset": False,
        }


class TestStart:
    @pytest.fixture(autouse=True)
    def ready(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        configure(idp)

    def test_the_browser_is_sent_to_the_provider_with_a_binding_cookie(
        self, app: Any, idp: FakeIdP
    ) -> None:
        response = browser(app).get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )

        assert response.status_code == 302
        location = urlsplit(response.headers["location"])
        query = parse_qs(location.query)
        assert response.headers["location"].startswith(idp.authorization_endpoint)
        assert query["redirect_uri"] == [f"{BASE}/api/auth/oidc/callback"]
        assert query["code_challenge_method"] == ["S256"]
        assert response.headers["cache-control"] == "no-store"
        cookie = response.headers["set-cookie"].lower()
        assert "noust_oidc=" in cookie and "httponly" in cookie
        assert "samesite=lax" in cookie and "path=/api" in cookie
        # The secret of the cookie is not in the address the provider sees.
        secret = response.headers["set-cookie"].split("noust_oidc=")[1].split(";")[0]
        assert secret not in response.headers["location"]

    def test_the_public_url_is_what_the_provider_is_told_when_it_is_set(
        self, app: Any, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.config import Config

        real = Config.get
        monkeypatch.setattr(
            Config,
            "get",
            lambda self, key, default=None: (
                "https://noust.example.test"
                if key == "web.public_url"
                else real(self, key, default)
            ),
        )
        response = browser(app).get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["redirect_uri"] == ["https://noust.example.test/api/auth/oidc/callback"]

    def test_an_unknown_provider_lands_on_the_sign_in_page_with_one_word(self, app: Any) -> None:
        response = browser(app).get(
            "/api/auth/oidc/start", params={"provider": "nope"}, follow_redirects=False
        )
        assert error_of(response) == "unavailable"
        assert "set-cookie" not in response.headers

    def test_an_unreachable_provider_is_told_apart_from_a_refusal(
        self, app: Any, idp: FakeIdP
    ) -> None:
        idp.offline = True
        response = browser(app).get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )
        assert error_of(response) == "unavailable"

    def test_the_provider_parameter_is_required(self, app: Any) -> None:
        assert browser(app).get("/api/auth/oidc/start", follow_redirects=False).status_code == 422


class TestTheWholeSignIn:
    def test_an_account_without_a_second_factor_is_signed_in_to_enrol_one(
        self, app: Any, idp: FakeIdP
    ) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", mfa=False)
        configure(idp)
        link("maria")
        client = browser(app)

        callback = through_the_provider(client, idp)
        ticket = ticket_of(callback)
        assert callback.headers["cache-control"] == "no-store"
        assert callback.headers["referrer-policy"] == "no-referrer"
        assert (
            "set-cookie" not in callback.headers
            or "noust_session" not in callback.headers["set-cookie"]
        )

        done = client.post("/api/auth/oidc/complete", json={"ticket": ticket})

        assert done.status_code == 200, done.text
        body = done.json()
        assert body["next"] == "/apps" and body["label"] == "Acme SSO"
        login = body["login"]
        assert login["success"] is True and login["mfa_required"] is True
        assert login["account"]["username"] == "maria"
        session = client.get("/api/auth/session").json()
        assert session["authenticated"] is True and session["mfa_required"] is True
        # Until it has a second factor, the session may do nothing else.
        assert client.get("/api/apps").json()["error"] == "mfa_required"
        listed = client.get("/api/auth/sessions").json()["sessions"]
        assert listed[0]["kind"] == "account"

    def test_an_account_with_a_second_factor_owes_it_after_the_provider(
        self, app: Any, idp: FakeIdP, sandbox: Path
    ) -> None:
        make_account("root-admin", "admin")
        codes = make_account("maria", "operator")
        configure(idp)
        link("maria")
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))

        step = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()["login"]

        assert step["success"] is False and step["csrf_token"] == ""
        assert step["second_factor"]["methods"][0] == "totp"
        assert client.get("/api/auth/session").json()["authenticated"] is False

        wrong = client.post(
            "/api/auth/login/second-factor",
            json={"challenge": step["second_factor"]["challenge"], "code": "000000"},
        )
        assert wrong.status_code == 401 and wrong.json()["error"] == "invalid_totp"
        right = client.post(
            "/api/auth/login/second-factor",
            json={"challenge": step["second_factor"]["challenge"], "code": codes[0]},
        )
        assert right.status_code == 200, right.text
        assert right.json()["success"] is True
        assert client.get("/api/auth/session").json()["account"]["username"] == "maria"
        events = [e for e in audit_entries(sandbox) if e["action"] == "auth.oidc.login"]
        assert [(e["result"]) for e in events] == ["failure", "success"]
        assert "second factor" in events[-1]["detail"]

    def test_the_provider_alone_is_never_the_whole_sign_in(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator")
        configure(idp)
        link("maria")
        client = browser(app)
        callback = through_the_provider(client, idp)

        assert client.get("/api/auth/session").json()["authenticated"] is False
        assert "noust_session" not in client.cookies
        ticket_of(callback)

    def test_the_providers_claims_about_its_own_mfa_do_not_skip_ours(
        self, app: Any, idp: FakeIdP
    ) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator")
        configure(idp)
        link("maria")
        client = browser(app)
        ticket = ticket_of(
            through_the_provider(
                client, idp, amr=["mfa", "hwk"], acr="urn:mace:incommon:iap:silver"
            )
        )

        step = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()["login"]

        assert step["success"] is False and step["second_factor"] is not None

    def test_a_passkey_can_finish_the_step_too(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator")
        configure(idp)
        link("maria")
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))

        step = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()["login"]

        # The challenge the password step issues is the one a passkey presents.
        assert get_token_manager().login_challenge_via(step["second_factor"]["challenge"]) == "oidc"

    def test_the_destination_is_a_page_of_this_console_whatever_was_asked(
        self, app: Any, idp: FakeIdP
    ) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", mfa=False)
        configure(idp)
        link("maria")

        for asked, kept in (
            ("https://evil.test/", "/"),
            ("//evil.test", "/"),
            ("/apps/x", "/apps/x"),
        ):
            client = browser(app)
            ticket = ticket_of(through_the_provider(client, idp, next=asked))
            done = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()
            assert done["next"] == kept

    def test_a_bearer_client_can_ask_for_the_session_token(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", mfa=False)
        configure(idp)
        link("maria")
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))

        done = client.post("/api/auth/oidc/complete", json={"ticket": ticket, "bearer": True})

        assert done.json()["login"]["session_token"]


class TestTheTicket:
    @pytest.fixture(autouse=True)
    def ready(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", mfa=False)
        configure(idp)
        link("maria")

    def test_a_ticket_opens_one_sign_in(self, app: Any, idp: FakeIdP) -> None:
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))
        assert client.post("/api/auth/oidc/complete", json={"ticket": ticket}).status_code == 200

        again = client.post("/api/auth/oidc/complete", json={"ticket": ticket})

        assert again.status_code == 401 and again.json()["error"] == "sign_in_expired"

    def test_another_browser_cannot_use_a_stolen_ticket(self, app: Any, idp: FakeIdP) -> None:
        victim = browser(app)
        ticket = ticket_of(through_the_provider(victim, idp))

        thief = browser(app)  # no binding cookie
        stolen = thief.post("/api/auth/oidc/complete", json={"ticket": ticket})

        assert stolen.status_code == 401
        assert thief.get("/api/auth/session").json()["authenticated"] is False
        # The owner still can: the thief's try did not use it up.
        assert victim.post("/api/auth/oidc/complete", json={"ticket": ticket}).status_code == 200

    def test_a_ticket_cannot_be_guessed(self, app: Any) -> None:
        response = browser(app).post("/api/auth/oidc/complete", json={"ticket": "x" * 43})
        assert response.status_code == 401 and response.json()["error"] == "sign_in_expired"

    def test_an_account_disabled_after_the_answer_is_not_signed_in(
        self, app: Any, idp: FakeIdP
    ) -> None:
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))
        accounts().disable("maria")

        assert client.post("/api/auth/oidc/complete", json={"ticket": ticket}).status_code == 401
        assert client.get("/api/auth/session").json()["authenticated"] is False


class TestRefusals:
    @pytest.fixture(autouse=True)
    def ready(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", mfa=False)
        configure(idp)
        link("maria")

    def test_a_tampered_token_is_one_word_to_the_browser_and_a_reason_in_the_log(
        self, app: Any, idp: FakeIdP, sandbox: Path
    ) -> None:
        def tamper(token: str) -> str:
            header, payload, signature = token.split(".")
            claims = json.loads(base64_decode(payload))
            claims["sub"] = "sub-root"
            return ".".join([header, b64(json.dumps(claims).encode()), signature])

        idp.tamper = tamper

        response = through_the_provider(browser(app), idp)

        assert error_of(response) == "failed"
        failures = [
            e
            for e in audit_entries(sandbox)
            if e["action"] == "auth.oidc.login" and e["result"] == "failure"
        ]
        assert "token_signature" in failures[-1]["detail"]

    def test_a_token_for_another_audience_is_refused(self, app: Any, idp: FakeIdP) -> None:
        idp.bend["aud"] = "another-client"
        assert error_of(through_the_provider(browser(app), idp)) == "failed"

    def test_an_expired_token_is_refused(self, app: Any, idp: FakeIdP) -> None:
        idp.bend["exp"] = int(idp.clock()) - 600
        assert error_of(through_the_provider(browser(app), idp)) == "failed"

    def test_a_replayed_callback_is_refused(self, app: Any, idp: FakeIdP) -> None:
        client = browser(app)
        started = client.get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )
        callback = target(idp.authorize(started.headers["location"], "sub-maria"))
        assert ticket_of(client.get(callback, follow_redirects=False))

        assert error_of(client.get(callback, follow_redirects=False)) == "expired"

    def test_an_answer_without_the_browsers_cookie_is_refused(self, app: Any, idp: FakeIdP) -> None:
        started = browser(app).get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )
        callback = target(idp.authorize(started.headers["location"], "sub-maria"))

        attacker = browser(app)  # a different browser, with none of the victim's cookies
        assert error_of(attacker.get(callback, follow_redirects=False)) == "failed"

    def test_an_answer_with_nobody_waiting_for_it_is_refused(self, app: Any) -> None:
        response = browser(app).get(
            "/api/auth/oidc/callback",
            params={"code": "x", "state": "forged"},
            follow_redirects=False,
        )
        assert error_of(response) == "expired"

    def test_a_callback_without_a_state_or_a_code_is_refused(self, app: Any) -> None:
        client = browser(app)
        assert error_of(client.get("/api/auth/oidc/callback", follow_redirects=False)) == "failed"
        assert (
            error_of(
                client.get("/api/auth/oidc/callback", params={"state": "s"}, follow_redirects=False)
            )
            == "failed"
        )

    def test_a_person_who_declines_at_the_provider_is_told_so(self, app: Any, idp: FakeIdP) -> None:
        client = browser(app)
        started = client.get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )
        denied = idp.deny(started.headers["location"])

        assert error_of(client.get(target(denied), follow_redirects=False)) == "denied"
        # And that sign-in is over: the state cannot be answered afterwards.
        late = idp.authorize(started.headers["location"], "sub-maria")
        assert error_of(client.get(target(late), follow_redirects=False)) == "expired"

    def test_a_person_the_policy_refuses_gets_no_session_and_a_reason_in_the_log(
        self, app: Any, idp: FakeIdP, sandbox: Path
    ) -> None:
        client = browser(app)
        response = through_the_provider(
            client, idp, sub="sub-stranger", email="x@evil.test", email_verified=True
        )

        assert error_of(response) == "refused"
        failures = [
            e
            for e in audit_entries(sandbox)
            if e["action"] == "auth.oidc.login" and e["result"] == "failure"
        ]
        assert "no_account" in failures[-1]["detail"]
        assert "corp:sub-stranger" in failures[-1]["resource"]

    def test_an_unverified_email_is_refused(self, app: Any, idp: FakeIdP) -> None:
        response = through_the_provider(browser(app), idp, email_verified=False)
        assert error_of(response) == "refused"

    def test_guessing_the_callback_locks_the_address_out(self, app: Any, idp: FakeIdP) -> None:
        client = browser(app)
        for _ in range(5):
            client.get(
                "/api/auth/oidc/callback",
                params={"code": "x", "state": "forged"},
                follow_redirects=False,
            )

        refused = client.get(
            "/api/auth/oidc/callback",
            params={"code": "x", "state": "forged"},
            follow_redirects=False,
        )

        assert refused.status_code == 429 and refused.json()["error"] == "locked_out"
        # The page that starts a sign-in is not refused by the lockout of the callback
        # but an address locked out is refused the ticket's collection too.
        locked = client.post("/api/auth/oidc/complete", json={"ticket": "x"})
        assert locked.status_code == 429
        get_brute_force().record_success("testclient")

    def test_a_provider_being_down_does_not_count_against_the_address(
        self, app: Any, idp: FakeIdP
    ) -> None:
        client = browser(app)
        started = client.get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )
        callback = target(idp.authorize(started.headers["location"], "sub-maria"))
        idp.offline = True
        assert error_of(client.get(callback, follow_redirects=False)) == "unavailable"
        assert not get_brute_force().is_locked("testclient")
        assert get_brute_force().get_attempts_remaining("testclient") == 5

    def test_a_refused_person_does_not_count_against_the_address_either(
        self, app: Any, idp: FakeIdP
    ) -> None:
        client = browser(app)
        for _ in range(6):
            error_of(
                through_the_provider(
                    client, idp, sub="sub-stranger", email_verified=True, email="x@acme.test"
                )
            )
        assert not get_brute_force().is_locked("testclient")


def base64_decode(text: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TestProvisioning:
    def test_an_invited_person_is_activated_and_enrols_a_second_factor(
        self, app: Any, idp: FakeIdP
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp, provisioning="invite")
        accounts().invite("maria", "operator", person_ref="maria@acme.test")
        client = browser(app)

        ticket = ticket_of(through_the_provider(client, idp))
        done = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()

        assert done["login"]["account"]["status"] == "active"
        assert done["login"]["mfa_required"] is True
        # Enrol: the session of an account with no second factor may.
        enrolled = client.post(
            "/api/auth/2fa/enroll",
            headers={CSRF_HEADER_NAME: done["login"]["csrf_token"]},
        )
        assert enrolled.status_code == 200, enrolled.text

    def test_auto_provisioning_makes_a_viewer_without_a_password(
        self, app: Any, idp: FakeIdP, sandbox: Path
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp, provisioning="auto")
        client = browser(app)

        ticket = ticket_of(through_the_provider(client, idp))
        done = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()

        account = done["login"]["account"]
        assert account["username"] == "maria" and account["role"] == "viewer"
        assert logged(sandbox, "auth.oidc.provision")[-1]["resource"] == "account:maria"
        assert logged(sandbox, "auth.oidc.link")
        # It cannot sign in with a password: there is none to know.
        refused = browser(app).post(
            "/api/auth/login", json={"username": "maria", "password": PASSWORD}
        )
        assert refused.status_code == 401

    def test_nobody_signs_in_this_way_before_the_first_account_exists(
        self, app: Any, idp: FakeIdP
    ) -> None:
        configure(idp, provisioning="auto", default_role="admin")
        response = browser(app).get(
            "/api/auth/oidc/start", params={"provider": "corp"}, follow_redirects=False
        )
        assert error_of(response) == "refused"

    def test_the_console_is_not_published_while_an_sso_account_has_no_second_factor(
        self, app: Any, idp: FakeIdP
    ) -> None:
        from noust.managers.console_exposure import second_factor_blockers

        make_account("root-admin", "admin")
        configure(idp, provisioning="auto")
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))
        client.post("/api/auth/oidc/complete", json={"ticket": ticket})

        blockers = second_factor_blockers()

        assert any("maria" in line and "identity provider" in line for line in blockers)


class TestSudoMode:
    def sso_account_with_a_factor(self, app: Any, idp: FakeIdP) -> tuple[TestClient, list[str]]:
        accounts().create("root-admin", "admin", password=PASSWORD)
        configure(idp, provisioning="auto", default_role="operator")
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))
        done = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()
        csrf = done["login"]["csrf_token"]
        enrol = client.post("/api/auth/2fa/enroll", headers={CSRF_HEADER_NAME: csrf}).json()
        confirmed = client.post(
            "/api/auth/2fa/confirm",
            json={"code": totp.totp_now(enrol["secret"])},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert confirmed.status_code == 200, confirmed.text
        return client, confirmed.json()["backup_codes"]

    def test_an_sso_account_confirms_sudo_mode_with_its_own_factor(
        self, app: Any, idp: FakeIdP
    ) -> None:
        client, codes = self.sso_account_with_a_factor(app, idp)
        csrf = client.cookies.get("noust_csrf")

        session = client.get("/api/auth/session").json()
        assert session["elevation_requires_password"] is False
        assert "totp" in session["elevation_factors"]
        response = client.post(
            "/api/auth/elevate", json={"code": codes[0]}, headers={CSRF_HEADER_NAME: csrf or ""}
        )

        assert response.status_code == 200, response.text

    def test_under_the_ens_profile_an_account_with_no_password_is_told_how_to_get_one(
        self, sandbox: Path, idp: FakeIdP
    ) -> None:
        app = create_app(
            SecurityConfig(
                state_dir=sandbox / "state",
                rate_limit_requests=5000,
                auth_policy=build_policy({"profile": "ens-medium"}),
            )
        )
        client, codes = self.sso_account_with_a_factor(app, idp)
        csrf = client.cookies.get("noust_csrf")

        response = client.post(
            "/api/auth/elevate", json={"code": codes[0]}, headers={CSRF_HEADER_NAME: csrf or ""}
        )

        assert response.status_code == 400
        assert "has none" in response.text and "noust user invite" in response.text


class TestAdministration:
    def sign_in_as_security(self, app: Any) -> TestClient:
        codes = make_account("sofia", "security")
        client = browser(app)
        login = client.post(
            "/api/auth/login",
            json={"username": "sofia", "password": PASSWORD, "totp_code": codes[0]},
        )
        assert login.status_code == 200, login.text
        client.csrf = login.json()["csrf_token"]  # type: ignore[attr-defined]
        client.codes = codes  # type: ignore[attr-defined]
        return client

    def elevate(self, client: Any) -> None:
        response = client.post(
            "/api/auth/elevate",
            json={"code": client.codes[1]},
            headers={CSRF_HEADER_NAME: client.csrf},
        )
        assert response.status_code == 200, response.text

    def put(self, client: Any, name: str, body: dict[str, Any]) -> Any:
        return client.put(
            f"/api/auth/oidc/config/{name}", json=body, headers={CSRF_HEADER_NAME: client.csrf}
        )

    def test_a_security_officer_adds_a_provider_in_sudo_mode(
        self, app: Any, idp: FakeIdP, store: NoustStore, sandbox: Path
    ) -> None:
        client = self.sign_in_as_security(app)
        body = {
            "issuer": idp.issuer,
            "client_id": idp.client_id,
            "client_secret": "s3cret-value",
            "label": "Acme SSO",
            "provisioning": "invite",
        }

        refused = self.put(client, "corp", body)
        assert refused.status_code == 403 and refused.json()["error"] == "elevation_required"
        self.elevate(client)
        added = self.put(client, "corp", body)

        assert added.status_code == 200, added.text
        info = added.json()
        assert info["client_secret_set"] is True and info["provisioning"] == "invite"
        assert "s3cret-value" not in added.text
        assert info["redirect_uri"] == f"{BASE}/api/auth/oidc/callback"
        listed = client.get("/api/auth/oidc/config")
        assert listed.status_code == 200 and "s3cret-value" not in listed.text
        assert [p["name"] for p in listed.json()["providers"]] == ["corp"]
        assert (store.db_path.parent / "secrets" / "oidc" / "corp").read_text() == "s3cret-value"
        recorded = logged(sandbox, "auth.oidc.config")
        assert recorded and recorded[-1]["actor"] == "sofia"
        assert "client_secret" in recorded[-1]["detail"] and "s3cret-value" not in str(recorded)

    def test_a_provider_maps_a_group_to_a_role_at_a_project(
        self, app: Any, idp: FakeIdP, store: NoustStore
    ) -> None:
        from noust.core.teams import TeamStore
        from noust.web.permissions.access import AccessManager

        teams = TeamStore(store)
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        AccessManager(store).create_role("release-manager", ["apps.read", "apps.deploy"])
        client = self.sign_in_as_security(app)
        self.elevate(client)
        mappings = [
            {"group": "ops", "role": "operator"},
            {"group": "release", "bind": "release-manager", "team": "acme", "project": "web"},
        ]

        added = self.put(
            client,
            "corp",
            {
                "issuer": idp.issuer,
                "client_id": idp.client_id,
                "client_secret": "s3cret-value",
                "group_sync": True,
                "group_mappings": mappings,
            },
        )
        assert added.status_code == 200, added.text
        shown = added.json()["group_mappings"]

        assert shown[1]["bind"] == "release-manager" and shown[1]["project"] == "web"
        assert shown[0]["bind"] is None
        refused = self.put(
            client,
            "corp",
            {"group_mappings": [{**mappings[1], "bind": "owner"}]},
        )
        assert refused.status_code == 400, refused.text
        assert "cannot give" in refused.text

    def test_an_operator_cannot_touch_the_providers(self, app: Any, idp: FakeIdP) -> None:
        codes = make_account("olga", "operator")
        client = browser(app)
        login = client.post(
            "/api/auth/login",
            json={"username": "olga", "password": PASSWORD, "totp_code": codes[0]},
        ).json()
        csrf = {CSRF_HEADER_NAME: login["csrf_token"]}

        assert client.get("/api/auth/oidc/config").status_code == 403
        assert client.put("/api/auth/oidc/config/corp", json={}, headers=csrf).status_code == 403
        assert client.delete("/api/auth/oidc/config/corp", headers=csrf).status_code == 403
        assert client.get("/api/auth/oidc/identities").status_code == 403
        assert client.post("/api/auth/oidc/config/corp/test", headers=csrf).status_code == 403

    def test_an_auditor_can_read_and_not_change(self, app: Any, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        configure(idp)
        codes = make_account("aldo", "auditor")
        client = browser(app)
        login = client.post(
            "/api/auth/login",
            json={"username": "aldo", "password": PASSWORD, "totp_code": codes[0]},
        ).json()

        read = client.get("/api/auth/oidc/config")
        assert read.status_code == 200 and "s3cret-value" not in read.text
        denied = client.put(
            "/api/auth/oidc/config/corp",
            json={"label": "x"},
            headers={CSRF_HEADER_NAME: login["csrf_token"]},
        )
        assert denied.status_code == 403

    def test_a_bad_setting_is_refused_with_the_field_and_changes_nothing(
        self, app: Any, idp: FakeIdP
    ) -> None:
        client = self.sign_in_as_security(app)
        self.elevate(client)

        response = self.put(
            client,
            "corp",
            {"issuer": "http://idp.example.test", "client_id": "x"},
        )

        assert response.status_code == 400
        assert client.get("/api/auth/oidc/config").json()["providers"] == []

    def test_a_provider_can_be_tested_and_removed(self, app: Any, idp: FakeIdP) -> None:
        client = self.sign_in_as_security(app)
        self.elevate(client)
        self.put(
            client,
            "corp",
            {"issuer": idp.issuer, "client_id": idp.client_id, "client_secret": "s3cret-value"},
        )

        tested = client.post(
            "/api/auth/oidc/config/corp/test", headers={CSRF_HEADER_NAME: client.csrf}
        )
        assert tested.status_code == 200 and tested.json()["ok"] is True
        removed = client.delete(
            "/api/auth/oidc/config/corp", headers={CSRF_HEADER_NAME: client.csrf}
        )
        assert removed.status_code == 200
        gone = client.post(
            "/api/auth/oidc/config/corp/test", headers={CSRF_HEADER_NAME: client.csrf}
        )
        assert gone.status_code == 404

    def test_people_are_linked_and_unlinked_by_hand(
        self, app: Any, idp: FakeIdP, sandbox: Path
    ) -> None:
        make_account("maria", "operator", mfa=False)
        client = self.sign_in_as_security(app)
        self.elevate(client)
        self.put(client, "corp", {"issuer": idp.issuer, "client_id": idp.client_id})
        headers = {CSRF_HEADER_NAME: client.csrf}

        made = client.post(
            "/api/auth/oidc/identities",
            json={"provider": "corp", "username": "maria", "subject": "sub-maria"},
            headers=headers,
        )
        assert made.status_code == 201, made.text
        assert made.json()["account"] == "maria"
        twice = client.post(
            "/api/auth/oidc/identities",
            json={"provider": "corp", "username": "maria", "subject": "sub-maria"},
            headers=headers,
        )
        assert twice.status_code == 400
        listed = client.get("/api/auth/oidc/identities", params={"username": "maria"}).json()
        assert [i["subject"] for i in listed["identities"]] == ["sub-maria"]

        gone = client.delete(f"/api/auth/oidc/identities/{made.json()['id']}", headers=headers)
        assert gone.status_code == 200
        assert client.get("/api/auth/oidc/identities").json()["identities"] == []
        missing = client.delete("/api/auth/oidc/identities/999", headers=headers)
        assert missing.status_code == 404 and "999" in missing.text
        assert [e["action"] for e in logged_all(sandbox)].count("auth.oidc.unlink") == 1

    def test_a_fleet_token_cannot_reach_it(self, app: Any) -> None:
        from noust.web.auth import fleet_refusal

        assert fleet_refusal("PUT", "/api/auth/oidc/config/corp") is not None
        assert fleet_refusal("GET", "/api/auth/oidc/start") is not None


def test_every_route_of_the_flow_declares_its_permission(app: Any) -> None:
    from noust.web.permissions.registry import permission_for_route

    for method, path in (
        ("GET", "/api/auth/oidc/providers"),
        ("GET", "/api/auth/oidc/start"),
        ("GET", "/api/auth/oidc/callback"),
        ("POST", "/api/auth/oidc/complete"),
        ("GET", "/api/auth/oidc/config"),
        ("PUT", "/api/auth/oidc/config/{provider}"),
        ("DELETE", "/api/auth/oidc/config/{provider}"),
        ("POST", "/api/auth/oidc/config/{provider}/test"),
        ("GET", "/api/auth/oidc/identities"),
        ("POST", "/api/auth/oidc/identities"),
        ("DELETE", "/api/auth/oidc/identities/{identity_id}"),
    ):
        assert permission_for_route(method, path) is not None, (method, path)
