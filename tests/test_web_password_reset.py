# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Invitations and password resets by mail, over HTTP.

A mail channel (a transport and a public address) turns two things on: an invitation can be
sent to an address instead of handed over by hand, and a person who forgot their password can
ask for a link. Without it neither exists. The request for a link is answered the same whoever
asks for whom; the link is single-use, short-lived and useless without the account's second
factor.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AccountManager
from noust.core.accounts.mail import AccountMailer, MailUnavailable
from noust.core.exceptions import EmailError
from noust.web.api import auth_accounts
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.server import get_token_manager
from tests.test_web_accounts import (
    PASSWORD,
    build,
    elevate_master,
    make_account,
    master_sign_in,
    store,  # noqa: F401 - the autouse fixture that gives each test a store of its own
)

NEW_PASSWORD = "a different long passphrase 42"


class FakeMailer(AccountMailer):
    """The mailer with the transport replaced: it records what it would send."""

    def __init__(self) -> None:
        self.invitations: list[dict[str, Any]] = []
        self.resets: list[dict[str, Any]] = []
        self.available = True
        self.error: Exception | None = None

    def ready(self) -> bool:
        return self.available

    def send_invitation(self, address, username, code, hours, *, invited_by=None):  # type: ignore[no-untyped-def]
        if not self.available:
            raise MailUnavailable("There is no way to send mail from this server")
        if self.error:
            raise self.error
        self.invitations.append(
            {"address": address, "username": username, "code": code, "hours": hours}
        )

    def send_reset(self, address, username, code, minutes=60):  # type: ignore[no-untyped-def]
        if self.error:
            raise self.error
        self.resets.append({"address": address, "username": username, "code": code})


@pytest.fixture
def mailer(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    fake = FakeMailer()
    monkeypatch.setattr(auth_accounts, "account_mailer", lambda: fake)
    monkeypatch.setattr(auth_accounts, "ens_profile_active", lambda: False)
    return fake


@pytest.fixture
def client(sandbox: Path) -> TestClient:
    return build(sandbox)


def _maria(email: str | None = "maria@example.com") -> str:
    """An operator with an authenticator and an address; returns the authenticator's secret."""
    manager = get_token_manager().accounts
    make_account("maria", "operator", mfa=False)
    account = manager.require("maria")
    secret = manager.begin_totp(account.id)
    assert manager.confirm_totp(account.id, totp.totp_now(secret)) is not None
    if email:
        manager.set_email("maria", email)
    return secret


def _ask(client: TestClient, who: str) -> Any:
    return client.post("/api/auth/password-reset/request", json={"identifier": who})


class TestWhetherItIsOffered:
    def test_the_sign_in_page_is_told_when_a_link_can_be_asked_for(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        make_account("maria", "operator")

        on = client.get("/api/auth/oidc/providers").json()
        mailer.available = False
        off = client.get("/api/auth/oidc/providers").json()

        assert on["password_reset"] is True
        assert off["password_reset"] is False

    def test_without_a_mail_channel_there_is_no_such_thing(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        mailer.available = False

        response = _ask(client, "maria")

        assert response.status_code == 404
        assert response.json()["error"] == "reset_unavailable"

    def test_under_the_ens_profile_accounts_are_recovered_by_a_security_officer(
        self, client: TestClient, mailer: FakeMailer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(auth_accounts, "ens_profile_active", lambda: True)

        assert _ask(client, "maria").status_code == 404
        assert client.get("/api/auth/oidc/providers").json()["password_reset"] is False


class TestAskingForALink:
    def test_the_link_is_mailed_to_the_address_of_the_account(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        _maria()

        response = _ask(client, "maria")

        assert response.status_code == 202
        assert len(mailer.resets) == 1
        assert mailer.resets[0]["address"] == "maria@example.com"
        assert mailer.resets[0]["code"].startswith("noust_reset_")

    def test_the_answer_is_the_same_for_an_account_that_does_not_exist(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        _maria()

        known = _ask(client, "maria")
        unknown = _ask(client, "nobody")
        no_address = _ask(client, "x@example.org")

        assert known.status_code == unknown.status_code == no_address.status_code == 202
        assert known.json() == unknown.json() == no_address.json()
        assert len(mailer.resets) == 1

    def test_the_code_is_in_no_response(self, client: TestClient, mailer: FakeMailer) -> None:
        _maria()

        response = _ask(client, "maria")

        assert "noust_reset_" not in response.text

    def test_an_address_will_do_as_well_as_a_name(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        _maria()

        _ask(client, "Maria@Example.com")

        assert len(mailer.resets) == 1

    def test_a_mail_that_cannot_be_sent_does_not_change_the_answer(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        _maria()
        mailer.error = EmailError("The mail server said no", details="550")

        response = _ask(client, "maria")

        assert response.status_code == 202

    def test_it_is_audited_without_the_code(
        self, client: TestClient, mailer: FakeMailer, sandbox: Path
    ) -> None:
        _maria()
        _ask(client, "maria")

        log = (sandbox / "state" / "web-audit.log").read_text()

        assert "auth.password.reset_request" in log
        assert mailer.resets[0]["code"] not in log


class TestFinishing:
    def _link(self, client: TestClient, mailer: FakeMailer) -> tuple[str, str]:
        secret = _maria()
        _ask(client, "maria")
        return mailer.resets[-1]["code"], secret

    def test_the_link_and_the_authenticator_choose_a_new_password(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        code, secret = self._link(client, mailer)

        done = client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": NEW_PASSWORD, "second_factor": totp.totp_now(secret)},
        )

        assert done.status_code == 200, done.text
        old = client.post(
            "/api/auth/login",
            json={"username": "maria", "password": PASSWORD, "totp_code": "000000"},
        )
        assert old.status_code == 401

    def test_the_old_password_stops_working_and_the_new_one_signs_in(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        code, secret = self._link(client, mailer)
        client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": NEW_PASSWORD, "second_factor": totp.totp_now(secret)},
        )
        manager: AccountManager = get_token_manager().accounts

        account = manager.require("maria")

        assert manager.verify_password(account.id, NEW_PASSWORD, client_ip="203.0.113.5")
        assert not manager.verify_password(account.id, PASSWORD, client_ip="203.0.113.5")

    def test_the_other_sessions_of_the_account_are_signed_out(
        self, client: TestClient, mailer: FakeMailer, sandbox: Path
    ) -> None:
        code, secret = self._link(client, mailer)
        other = build(sandbox)
        signed = other.post(
            "/api/auth/login",
            # The code of the next step: enrolling spent the current one for signing in.
            json={
                "username": "maria",
                "password": PASSWORD,
                "totp_code": totp.totp_now(secret, t=time.time() + 30),
            },
        )
        assert signed.status_code == 200
        client.post(
            "/api/auth/password-reset/confirm",
            json={
                "code": code,
                "password": NEW_PASSWORD,
                "second_factor": totp.totp_now(secret),
            },
        )

        assert other.get("/api/auth/verify").status_code == 401

    def test_a_wrong_authenticator_code_refuses_and_the_link_is_still_good(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        code, secret = self._link(client, mailer)

        refused = client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": NEW_PASSWORD, "second_factor": "000000"},
        )
        done = client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": NEW_PASSWORD, "second_factor": totp.totp_now(secret)},
        )

        assert refused.status_code == 401
        assert refused.json()["error"] == "invalid_credentials"
        assert done.status_code == 200

    def test_a_code_nobody_issued_is_one_answer(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        response = client.post(
            "/api/auth/password-reset/confirm",
            json={"code": "noust_reset_nope", "password": NEW_PASSWORD, "second_factor": "123456"},
        )

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_reset"

    def test_a_password_the_policy_refuses_is_a_400_and_the_link_survives(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        code, secret = self._link(client, mailer)

        refused = client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": "short", "second_factor": totp.totp_now(secret)},
        )
        done = client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": NEW_PASSWORD, "second_factor": totp.totp_now(secret)},
        )

        assert refused.status_code == 400
        assert done.status_code == 200

    def test_a_link_works_once(self, client: TestClient, mailer: FakeMailer) -> None:
        code, secret = self._link(client, mailer)
        client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": NEW_PASSWORD, "second_factor": totp.totp_now(secret)},
        )

        again = client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": "yet another passphrase 77", "second_factor": "123456"},
        )

        assert again.status_code == 401

    def test_finishing_is_audited(
        self, client: TestClient, mailer: FakeMailer, sandbox: Path
    ) -> None:
        code, secret = self._link(client, mailer)
        client.post(
            "/api/auth/password-reset/confirm",
            json={"code": code, "password": NEW_PASSWORD, "second_factor": totp.totp_now(secret)},
        )

        assert "auth.password.reset_done" in (sandbox / "state" / "web-audit.log").read_text()


class TestInvitingByMail:
    def _invite(self, client: TestClient, **body: Any) -> Any:
        token, csrf = master_sign_in(client)
        elevate_master(client, token, csrf)
        return client.post(
            "/api/auth/invitations",
            json={"username": "pablo", "role": "viewer", **body},
            headers={CSRF_HEADER_NAME: csrf},
        )

    def test_an_invitation_is_mailed_when_an_address_is_given(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        response = self._invite(client, email="pablo@example.com")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["emailed"] is True
        assert mailer.invitations[0]["address"] == "pablo@example.com"
        assert mailer.invitations[0]["code"] == body["code"]
        assert body["account"]["email"] == "pablo@example.com"

    def test_without_an_address_nothing_is_mailed(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        response = self._invite(client)

        assert response.json()["emailed"] is False
        assert mailer.invitations == []

    def test_asking_to_mail_without_a_mail_channel_creates_nothing(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        mailer.available = False

        response = self._invite(client, email="pablo@example.com")

        assert response.status_code == 400
        assert get_token_manager().accounts.find("pablo") is None

    def test_a_mail_that_fails_leaves_the_invitation_to_hand_over(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        mailer.error = EmailError("The mail server said no", details="550 mailbox unavailable")

        response = self._invite(client, email="pablo@example.com")

        body = response.json()
        assert response.status_code == 201
        assert body["emailed"] is False
        assert "mail server" in body["email_error"]
        assert body["code"].startswith("noust_inv_")

    def test_an_address_that_is_not_one_is_refused(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        assert self._invite(client, email="not-an-address").status_code == 400


class TestTheAddressOfAnAccount:
    def test_an_administrator_sets_it_and_it_is_shown_back(
        self, client: TestClient, mailer: FakeMailer
    ) -> None:
        make_account("maria", "operator")
        token, csrf = master_sign_in(client)
        elevate_master(client, token, csrf)

        response = client.patch(
            "/api/auth/accounts/maria",
            json={"email": "Maria@Example.com"},
            headers={CSRF_HEADER_NAME: csrf},
        )

        assert response.status_code == 200, response.text
        assert response.json()["email"] == "maria@example.com"
