# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Signing in through a SAML provider over HTTP (checklist I-20): the browser's redirects, the form
the provider makes it post, the cookie, the ticket, the second factor an account still owes, the
lockout, the audit trail and the administrator's side.

The whole console is served by ``create_app``; the identity provider is
``tests/saml_support.FakeIdp`` (a real key, a real signature), so what runs is what a browser
would meet. What the XML must be to be believed is held by ``tests/test_saml.py``; what these tests
say is that a response which is not right never becomes a session, however it arrives.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("cryptography")

from fastapi.testclient import TestClient

from noust.core.accounts import AuthPolicy
from noust.core.accounts.saml import SamlManager
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_brute_force, get_token_manager
from tests.saml_support import FakeIdp, make_identity
from tests.test_web_oidc import (  # noqa: F401 - the fixtures and helpers of the sign-in tests
    BASE,
    PASSWORD,
    accounts,
    audit_entries,
    browser,
    cheap_hashes,
    error_of,
    logged,
    make_account,
    store,
    ticket_of,
)

ISSUER = "https://idp.acme.test/metadata"
SSO = "https://idp.acme.test/sso"
ACS = "/api/auth/saml/acs"


@pytest.fixture
def app(sandbox: Path) -> Any:
    return create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=5000, auth_policy=AuthPolicy()
        )
    )


@pytest.fixture
def idp() -> FakeIdp:
    key, _pem = make_identity()
    return FakeIdp(key, lambda: datetime.datetime.now(datetime.timezone.utc))


def manager() -> SamlManager:
    tokens = get_token_manager()
    return SamlManager(tokens.accounts.store, accounts=tokens.accounts)


def configure(**changes: Any) -> None:
    _key, pem = make_identity()
    settings = {
        "idp_entity_id": ISSUER,
        "sso_url": SSO,
        "certificates": [pem],
        "label": "Acme SSO",
        **changes,
    }
    manager().configure("corp", settings)


def link(username: str, subject: str = "uuid-maria") -> None:
    manager().link("corp", username, subject)


def started(client: TestClient, **params: str) -> Any:
    return client.get(
        "/api/auth/saml/start", params={"provider": "corp", **params}, follow_redirects=False
    )


def post_form(client: TestClient, response: str, relay: str, **more: str) -> Any:
    return client.post(
        ACS, data={"SAMLResponse": response, "RelayState": relay, **more}, follow_redirects=False
    )


def through_the_provider(
    client: TestClient, idp: FakeIdp, *, next: str | None = "/apps", **answer: Any
) -> Any:
    """Click the button, approve at the provider, and have the browser post the answer."""
    begun = started(client, **({"next": next} if next else {}))
    assert begun.status_code == 302, begun.text
    response, relay = idp.answer(begun.headers["location"], **answer)
    return post_form(client, response, relay)


class TestTheButton:
    def test_the_sign_in_page_is_told_the_enabled_providers_and_nothing_else(
        self, app: Any
    ) -> None:
        make_account("root-admin", "admin")
        configure(provisioning="auto", group_sync=True)
        _key, pem = make_identity()
        manager().configure(
            "old",
            {
                "idp_entity_id": "https://old.test",
                "sso_url": SSO,
                "certificates": [pem],
                "enabled": False,
            },
        )

        body = browser(app).get("/api/auth/oidc/providers").json()

        assert body["providers"] == [{"name": "corp", "label": "Acme SSO", "protocol": "saml"}]
        assert "provisioning" not in str(body) and "https://idp.acme.test" not in str(body)

    def test_starting_leaves_for_the_provider_with_a_cookie_that_ties_the_answer(
        self, app: Any
    ) -> None:
        make_account("root-admin", "admin")
        configure()

        response = started(browser(app), next="/apps")

        assert response.status_code == 302
        assert response.headers["location"].startswith(SSO + "?SAMLRequest=")
        assert "RelayState=" in response.headers["location"]
        assert response.headers["cache-control"] == "no-store"
        cookie = response.headers["set-cookie"].lower()
        assert "noust_oidc=" in cookie and "httponly" in cookie and "path=/api" in cookie
        # Over plain HTTP a browser would drop SameSite=None, so the cookie says Lax and the
        # test of the provider says why such a console cannot take a POST answer.
        assert "samesite=lax" in cookie and "secure" not in cookie.replace("samesite", "")

    def test_over_tls_the_cookie_travels_with_the_providers_post(self, app: Any) -> None:
        make_account("root-admin", "admin")
        configure()
        client = TestClient(app, base_url="https://localhost:8443", client=("testclient", 50000))

        cookie = started(client).headers["set-cookie"].lower()

        assert "samesite=none" in cookie and "secure" in cookie

    def test_an_unknown_or_switched_off_provider_is_one_word(self, app: Any, sandbox: Path) -> None:
        make_account("root-admin", "admin")
        configure(enabled=False)
        client = browser(app)

        assert error_of(started(client)) == "unavailable"
        assert error_of(started(client, provider="nope")) == "unavailable"
        failures = [e for e in audit_entries(sandbox) if e["action"] == "auth.saml.login"]
        assert failures and failures[-1]["result"] == "failure"

    def test_the_destination_after_signing_in_cannot_leave_the_console(self, app: Any) -> None:
        make_account("root-admin", "admin")
        configure(provisioning="auto")
        client = browser(app)
        idp_key, _pem = make_identity()
        provider = FakeIdp(idp_key, lambda: datetime.datetime.now(datetime.timezone.utc))
        ticket = ticket_of(through_the_provider(client, provider, next="https://evil.example/x"))

        done = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()

        assert done["next"] == "/"


class TestTheWholeSignIn:
    def test_a_new_person_is_made_an_account_and_signs_in_to_enrol_a_factor(
        self, app: Any, idp: FakeIdp, sandbox: Path
    ) -> None:
        make_account("root-admin", "admin")
        configure(provisioning="auto", default_role="operator")
        client = browser(app)

        answered = through_the_provider(client, idp)
        ticket = ticket_of(answered)
        assert answered.headers["cache-control"] == "no-store"
        assert answered.headers["referrer-policy"] == "no-referrer"
        assert "noust_session" not in answered.headers.get("set-cookie", "")
        done = client.post("/api/auth/oidc/complete", json={"ticket": ticket})

        assert done.status_code == 200, done.text
        body = done.json()
        assert (
            body["next"] == "/apps" and body["label"] == "Acme SSO" and body["provider"] == "corp"
        )
        assert body["login"]["success"] is True and body["login"]["mfa_required"] is True
        assert body["login"]["account"]["username"] == "maria"
        assert client.get("/api/apps").json()["error"] == "mfa_required"
        recorded = logged(sandbox, "auth.saml.login")
        assert recorded and recorded[-1]["result"] == "success"
        assert logged(sandbox, "auth.saml.provision")

    def test_an_account_with_a_second_factor_owes_it_after_the_provider(
        self, app: Any, idp: FakeIdp, sandbox: Path
    ) -> None:
        make_account("root-admin", "admin")
        codes = make_account("maria", "operator")
        configure()
        link("maria")
        client = browser(app)
        ticket = ticket_of(through_the_provider(client, idp))

        step = client.post("/api/auth/oidc/complete", json={"ticket": ticket}).json()["login"]

        assert step["success"] is False and step["second_factor"]["challenge"]
        assert client.get("/api/auth/session").json()["authenticated"] is False
        finished = client.post(
            "/api/auth/login/second-factor",
            json={"challenge": step["second_factor"]["challenge"], "code": codes[0]},
        )
        assert finished.status_code == 200 and finished.json()["success"] is True
        recorded = logged(sandbox, "auth.saml.login")
        assert recorded[-1]["result"] == "success" and "second factor" in str(recorded[-1])

    def test_a_ticket_opens_one_sign_in_and_only_for_the_browser_that_began(
        self, app: Any, idp: FakeIdp
    ) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", mfa=False)
        configure()
        link("maria")
        victim = browser(app)
        ticket = ticket_of(through_the_provider(victim, idp))

        thief = browser(app)
        assert thief.post("/api/auth/oidc/complete", json={"ticket": ticket}).status_code == 401
        assert victim.post("/api/auth/oidc/complete", json={"ticket": ticket}).status_code == 200
        again = victim.post("/api/auth/oidc/complete", json={"ticket": ticket})

        assert again.status_code == 401 and again.json()["error"] == "sign_in_expired"

    def test_a_person_the_policy_does_not_admit_gets_no_session_and_a_reason_in_the_log(
        self, app: Any, idp: FakeIdp, sandbox: Path
    ) -> None:
        make_account("root-admin", "admin")
        configure()

        response = through_the_provider(browser(app), idp)

        assert error_of(response) == "refused"
        failures = [
            e
            for e in audit_entries(sandbox)
            if e["action"] == "auth.saml.login" and e["result"] == "failure"
        ]
        assert "no_account" in failures[-1]["detail"]
        assert "corp:uuid-maria" in failures[-1]["resource"]


class TestRefusals:
    @pytest.fixture(autouse=True)
    def ready(self, app: Any) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", mfa=False)
        configure()
        link("maria")

    def failure_reasons(self, sandbox: Path) -> list[str]:
        return [
            e["detail"]
            for e in audit_entries(sandbox)
            if e["action"] == "auth.saml.login" and e["result"] == "failure"
        ]

    def test_an_unsigned_response_is_one_word_to_the_browser_and_a_reason_in_the_log(
        self, app: Any, idp: FakeIdp, sandbox: Path
    ) -> None:
        response = through_the_provider(browser(app), idp, sign_what="nothing")

        assert error_of(response) == "failed"
        assert (
            "Neither the response nor the assertion is signed" in self.failure_reasons(sandbox)[-1]
        )

    def test_a_response_signed_by_somebody_else_is_refused(self, app: Any, idp: FakeIdp) -> None:
        stranger, _pem = make_identity("rsa", "stranger.example")

        assert error_of(through_the_provider(browser(app), idp, key=stranger)) == "failed"

    def test_a_response_for_another_audience_is_refused(self, app: Any, idp: FakeIdp) -> None:
        assert (
            error_of(through_the_provider(browser(app), idp, audience="https://other.example/"))
            == "failed"
        )

    def test_a_replayed_post_is_refused(self, app: Any, idp: FakeIdp) -> None:
        client = browser(app)
        begun = started(client)
        response, relay = idp.answer(begun.headers["location"])
        assert ticket_of(post_form(client, response, relay))

        assert error_of(post_form(client, response, relay)) == "expired"

    def test_an_answer_without_the_browsers_cookie_is_refused(self, app: Any, idp: FakeIdp) -> None:
        begun = started(browser(app))
        response, relay = idp.answer(begun.headers["location"])

        attacker = browser(app)  # a different browser, with none of the victim's cookies
        assert error_of(post_form(attacker, response, relay)) == "failed"

    def test_an_answer_nobody_was_waiting_for_is_refused(self, app: Any, idp: FakeIdp) -> None:
        begun = started(browser(app))
        response, _relay = idp.answer(begun.headers["location"])

        assert error_of(post_form(browser(app), response, "forged")) == "expired"

    def test_a_post_that_is_not_the_form_is_refused(self, app: Any) -> None:
        client = browser(app)

        as_json = client.post(
            ACS, json={"SAMLResponse": "x", "RelayState": "y"}, follow_redirects=False
        )
        empty = client.post(
            ACS, data={"SAMLResponse": "", "RelayState": "y"}, follow_redirects=False
        )
        no_relay = client.post(ACS, data={"SAMLResponse": "x"}, follow_redirects=False)
        repeated = client.post(
            ACS,
            content="SAMLResponse=a&SAMLResponse=b&RelayState=c",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )

        for response in (as_json, empty, no_relay, repeated):
            assert error_of(response) == "failed"

    def test_a_response_that_is_not_base64_is_refused(self, app: Any, idp: FakeIdp) -> None:
        client = browser(app)
        started(client)

        assert error_of(post_form(client, "%%%", "forged")) in ("failed", "expired")

    def test_the_form_is_the_only_way_to_answer(self, app: Any) -> None:
        response = browser(app).get(ACS, follow_redirects=False)

        assert response.status_code in (404, 405)

    def test_guessing_the_answer_locks_the_address_out(self, app: Any) -> None:
        client = browser(app)
        for _ in range(5):
            post_form(client, "AAAA", "forged")

        refused = post_form(client, "AAAA", "forged")

        assert refused.status_code == 429 and refused.json()["error"] == "locked_out"
        get_brute_force().record_success("testclient")

    def test_a_person_the_provider_says_did_not_sign_in_is_told_so_and_not_counted(
        self, app: Any, idp: FakeIdp
    ) -> None:
        client = browser(app)
        for _ in range(6):
            denied = through_the_provider(
                client,
                idp,
                sign_what="response",
                status="urn:oasis:names:tc:SAML:2.0:status:AuthnFailed",
            )
            assert error_of(denied) == "denied"

        assert not get_brute_force().is_locked("testclient")

    def test_a_refused_person_does_not_count_against_the_address_either(
        self, app: Any, idp: FakeIdp
    ) -> None:
        client = browser(app)
        for _ in range(6):
            assert error_of(through_the_provider(client, idp, name_id="uuid-stranger")) == "refused"

        assert not get_brute_force().is_locked("testclient")

    def test_what_the_provider_sent_is_not_in_the_audit_trail(
        self, app: Any, idp: FakeIdp, sandbox: Path
    ) -> None:
        through_the_provider(browser(app), idp, sign_what="nothing")

        assert "SAMLResponse" not in str(audit_entries(sandbox))


class TestTheMetadata:
    def test_it_is_public_and_says_where_to_post_and_that_assertions_are_signed(
        self, app: Any
    ) -> None:
        make_account("root-admin", "admin")
        configure()

        response = browser(app).get("/api/auth/saml/metadata/corp")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/samlmetadata+xml")
        assert response.headers["cache-control"] == "no-store"
        assert f'entityID="{BASE}/api/auth/saml/metadata/corp"' in response.text
        assert f'Location="{BASE}{ACS}"' in response.text
        assert 'WantAssertionsSigned="true"' in response.text

    def test_a_provider_that_does_not_exist_is_a_404(self, app: Any) -> None:
        assert browser(app).get("/api/auth/saml/metadata/nope").status_code == 404

    def test_the_operators_entity_id_is_the_one_published(self, app: Any) -> None:
        make_account("root-admin", "admin")
        configure(sp_entity_id="urn:acme:noust")

        assert 'entityID="urn:acme:noust"' in browser(app).get("/api/auth/saml/metadata/corp").text


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
            f"/api/auth/saml/config/{name}", json=body, headers={CSRF_HEADER_NAME: client.csrf}
        )

    def settings(self, **more: Any) -> dict[str, Any]:
        _key, pem = make_identity()
        return {
            "idp_entity_id": ISSUER,
            "sso_url": SSO,
            "certificates": [pem],
            "label": "Acme SSO",
            **more,
        }

    def test_a_security_officer_adds_a_provider_in_sudo_mode(self, app: Any, sandbox: Path) -> None:
        client = self.sign_in_as_security(app)

        refused = self.put(client, "corp", self.settings(provisioning="invite"))
        assert refused.status_code == 403 and refused.json()["error"] == "elevation_required"
        self.elevate(client)
        added = self.put(client, "corp", self.settings(provisioning="invite"))

        assert added.status_code == 200, added.text
        info = added.json()
        assert info["provisioning"] == "invite" and info["enabled"] is True
        assert info["acs_url"] == f"{BASE}{ACS}"
        assert info["entity_id"] == f"{BASE}/api/auth/saml/metadata/corp"
        assert info["metadata_url"] == info["entity_id"]
        assert len(info["certificate_details"]) == 1
        assert len(info["certificate_details"][0]["fingerprint"]) == 64
        listed = client.get("/api/auth/saml/config")
        assert listed.status_code == 200
        assert [p["name"] for p in listed.json()["providers"]] == ["corp"]
        assert listed.json()["secure"] is False
        recorded = logged(sandbox, "auth.saml.config")
        assert recorded and recorded[-1]["actor"] == "sofia"
        assert "certificates" in recorded[-1]["detail"]

    def test_the_providers_metadata_fills_what_the_provider_needs(self, app: Any) -> None:
        from tests.test_saml import metadata_xml

        client = self.sign_in_as_security(app)
        self.elevate(client)

        added = self.put(client, "corp", {"idp_metadata": metadata_xml(), "label": "Acme SSO"})

        assert added.status_code == 200, added.text
        assert (added.json()["idp_entity_id"], added.json()["sso_url"]) == (ISSUER, SSO)

    def test_a_group_maps_to_a_role_and_the_next_sign_in_applies_it(
        self, app: Any, idp: FakeIdp
    ) -> None:
        make_account("root-admin", "admin")
        client = self.sign_in_as_security(app)
        self.elevate(client)
        added = self.put(
            client,
            "corp",
            self.settings(
                provisioning="auto",
                group_sync=True,
                group_mappings=[{"group": "ops", "role": "operator"}],
            ),
        )
        assert added.status_code == 200, added.text
        assert added.json()["group_mappings"] == [
            {
                "group": "ops",
                "role": "operator",
                "team": None,
                "team_role": None,
                "bind": None,
                "project": None,
                "environment": None,
            }
        ]

        ticket_of(through_the_provider(browser(app), idp))

        assert get_token_manager().accounts.require("maria").role == "operator"

    def test_what_is_wrong_is_refused_and_nothing_is_written(self, app: Any) -> None:
        client = self.sign_in_as_security(app)
        self.elevate(client)

        bad = self.put(client, "corp", self.settings(sso_url="http://idp.acme.test/sso"))
        weak = self.put(client, "corp", self.settings(certificates=["not a certificate"]))
        incomplete = self.put(client, "corp", {"label": "x"})

        assert bad.status_code == 400 and "https" in bad.text
        assert weak.status_code == 400 and "certificate" in weak.text.lower()
        assert incomplete.status_code == 400
        assert client.get("/api/auth/saml/config").json()["providers"] == []

    def test_the_provider_is_tested_without_signing_anybody_in(self, app: Any) -> None:
        make_account("root-admin", "admin")
        configure()
        client = self.sign_in_as_security(app)

        result = client.post(
            "/api/auth/saml/config/corp/test", headers={CSRF_HEADER_NAME: client.csrf}
        )

        assert result.status_code == 200, result.text
        checks = {c["name"]: c for c in result.json()["checks"]}
        assert checks["certificate"]["ok"] and checks["sign-in address"]["ok"]
        assert checks["entity id"]["detail"] == f"{BASE}/api/auth/saml/metadata/corp"
        # The console of this test is on plain HTTP, which a POST answer cannot use.
        assert checks["assertion consumer address"]["ok"] is False
        assert "TLS" in checks["assertion consumer address"]["detail"]
        assert result.json()["ok"] is False
        missing = client.post(
            "/api/auth/saml/config/nope/test", headers={CSRF_HEADER_NAME: client.csrf}
        )
        assert missing.status_code == 404

    def test_a_person_is_linked_by_hand_at_a_saml_provider_through_the_shared_route(
        self, app: Any, idp: FakeIdp, sandbox: Path
    ) -> None:
        make_account("maria", "operator", mfa=False)
        configure()
        client = self.sign_in_as_security(app)
        self.elevate(client)

        linked = client.post(
            "/api/auth/oidc/identities",
            json={"provider": "corp", "username": "maria", "subject": "uuid-maria"},
            headers={CSRF_HEADER_NAME: client.csrf},
        )

        assert linked.status_code == 201, linked.text
        assert (linked.json()["account"], linked.json()["issuer"]) == ("maria", ISSUER)
        recorded = logged(sandbox, "auth.saml.link")
        assert recorded and recorded[-1]["actor"] == "sofia"
        # And that is all it takes for the provider to recognise her.
        ticket = ticket_of(through_the_provider(browser(app), idp))
        assert ticket

    def test_a_provider_that_does_not_exist_anywhere_is_a_404(self, app: Any) -> None:
        make_account("maria", "operator", mfa=False)
        client = self.sign_in_as_security(app)
        self.elevate(client)

        missing = client.post(
            "/api/auth/oidc/identities",
            json={"provider": "nope", "username": "maria", "subject": "x"},
            headers={CSRF_HEADER_NAME: client.csrf},
        )

        assert missing.status_code == 404

    def test_the_provider_is_removed_in_sudo_mode(self, app: Any, sandbox: Path) -> None:
        make_account("root-admin", "admin")
        configure()
        client = self.sign_in_as_security(app)

        refused = client.delete(
            "/api/auth/saml/config/corp", headers={CSRF_HEADER_NAME: client.csrf}
        )
        assert refused.status_code == 403
        self.elevate(client)
        removed = client.delete(
            "/api/auth/saml/config/corp", headers={CSRF_HEADER_NAME: client.csrf}
        )

        assert removed.status_code == 200 and removed.json()["removed"] == "corp"
        assert client.get("/api/auth/saml/config").json()["providers"] == []
        assert logged(sandbox, "auth.saml.config")[-1]["detail"] == "removed"
        gone = client.delete("/api/auth/saml/config/corp", headers={CSRF_HEADER_NAME: client.csrf})
        assert gone.status_code == 404

    def test_an_operator_cannot_read_or_change_them(self, app: Any) -> None:
        codes = make_account("olga", "operator")
        client = browser(app)
        login = client.post(
            "/api/auth/login",
            json={"username": "olga", "password": PASSWORD, "totp_code": codes[0]},
        )
        csrf = login.json()["csrf_token"]

        assert client.get("/api/auth/saml/config").status_code == 403
        assert (
            client.put(
                "/api/auth/saml/config/corp",
                json=self.settings(),
                headers={CSRF_HEADER_NAME: csrf},
            ).status_code
            == 403
        )

    def test_nobody_signed_out_can(self, app: Any) -> None:
        client = browser(app)

        assert client.get("/api/auth/saml/config").status_code == 401
        assert client.put("/api/auth/saml/config/corp", json=self.settings()).status_code in (
            401,
            403,
        )


def test_every_route_of_the_flow_declares_its_permission(app: Any) -> None:
    from noust.web.permissions.registry import permission_for_route

    for method, path in (
        ("GET", "/api/auth/saml/start"),
        ("POST", "/api/auth/saml/acs"),
        ("GET", "/api/auth/saml/metadata/{provider}"),
        ("GET", "/api/auth/saml/config"),
        ("PUT", "/api/auth/saml/config/{provider}"),
        ("DELETE", "/api/auth/saml/config/{provider}"),
        ("POST", "/api/auth/saml/config/{provider}/test"),
    ):
        assert permission_for_route(method, path) is not None, (method, path)


def test_the_sign_in_asks_a_browser_nothing_it_would_keep(app: Any, idp: FakeIdp) -> None:
    make_account("root-admin", "admin")
    configure()

    begun = started(browser(app))
    query = parse_qs(urlsplit(begun.headers["location"]).query)

    assert set(query) == {"SAMLRequest", "RelayState"}
