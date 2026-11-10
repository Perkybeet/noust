# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Setting up an account's first second factor asks for a recent proof of who is asking (item 132).

An account without a factor may enrol one from a session it signed in with, because it can do
nothing else until it has. That made the cookie of an old session enough to bind an
authenticator of somebody else's choosing, and with it a code to enter sudo mode. The first
factor now needs the sign-in to be recent (at most ten minutes old, by password or through an
identity provider) or the account's password, given with the request; an account that has no
password signs in again. The guard is one function, at the point both ways of enrolling pass.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.web.api.identity_proof import FRESH_SIGN_IN_SECONDS
from noust.web.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from noust.web.server import get_token_manager
from tests.support.auth_clock_support import set_clock
from tests.support.mfa_support import require_second_factor
from tests.support.web_accounts import (  # noqa: F401
    PASSWORD,
    accounts,
    build,
    cheap_hashes,
    make_account,
    sign_in,
    store,
)

ENROLL = "/api/auth/2fa/enroll"
CONFIRM = "/api/auth/2fa/confirm"
PASSKEY_OPTIONS = "/api/auth/passkeys/registration/options"


class Later:
    """The console's clock, moved on by a test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = time.time()
        set_clock(monkeypatch, lambda: self.now)

    def minutes(self, count: float) -> None:
        self.now += count * 60


@pytest.fixture
def later(monkeypatch: pytest.MonkeyPatch) -> Later:
    return Later(monkeypatch)


def signed_in_without_a_factor(client: TestClient) -> str:
    """An account with no second factor, signed in with its password; returns its CSRF token."""
    make_account("maria", "admin", mfa=False)
    require_second_factor()
    return str(sign_in(client, "maria", None)["csrf_token"])


def post(client: TestClient, csrf: str, path: str, **body: Any) -> Any:
    return client.post(path, json=body or None, headers={CSRF_HEADER_NAME: csrf})


class TestAFreshSignIn:
    def test_a_session_that_just_signed_in_sets_its_first_factor_up(
        self, sandbox: Path, later: Later
    ) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(FRESH_SIGN_IN_SECONDS / 60 - 1)

        enrolment = post(client, csrf, ENROLL)

        assert enrolment.status_code == 200, enrolment.text
        confirmed = post(client, csrf, CONFIRM, code=totp.totp_now(enrolment.json()["secret"]))
        assert confirmed.status_code == 200, confirmed.text

    def test_it_asks_for_nothing_when_no_body_is_sent_as_before(
        self, sandbox: Path, later: Later
    ) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)

        assert client.post(ENROLL, headers={CSRF_HEADER_NAME: csrf}).status_code == 200


class TestAnOldSession:
    def test_the_cookie_of_an_old_session_binds_nothing(self, sandbox: Path, later: Later) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(FRESH_SIGN_IN_SECONDS / 60 + 1)

        refused = post(client, csrf, ENROLL)

        assert refused.status_code == 403
        assert refused.json()["error"] == "identity_proof_required"
        # Nothing was begun: there is no secret for a thief to confirm.
        assert accounts().pending_totp_secret(accounts().require("maria").id) is None

    def test_the_password_given_now_is_the_proof(self, sandbox: Path, later: Later) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(30)

        enrolment = post(client, csrf, ENROLL, password=PASSWORD)

        assert enrolment.status_code == 200, enrolment.text
        confirmed = post(client, csrf, CONFIRM, code=totp.totp_now(enrolment.json()["secret"]))
        assert confirmed.status_code == 200, confirmed.text
        assert accounts().require("maria").has_mfa

    def test_a_wrong_password_is_refused_like_any_wrong_credential_and_counted(
        self, sandbox: Path, later: Later
    ) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(30)

        refused = post(client, csrf, ENROLL, password="not the password at all")

        assert refused.status_code == 401
        assert refused.json()["error"] == "invalid_credentials"
        assert accounts().require("maria").failures_since_login >= 1
        assert accounts().pending_totp_secret(accounts().require("maria").id) is None

    def test_wrong_passwords_lock_the_account_as_a_sign_in_does(
        self, sandbox: Path, later: Later
    ) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(30)

        for _ in range(6):
            post(client, csrf, ENROLL, password="not the password at all")

        assert accounts().require("maria").is_locked()

    def test_a_password_in_the_body_of_a_fresh_session_is_not_needed_or_checked(
        self, sandbox: Path, later: Later
    ) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)

        assert post(client, csrf, ENROLL, password="anything").status_code == 200

    def test_it_is_audited_with_the_way_it_was_proved(self, sandbox: Path, later: Later) -> None:
        from tests.support.web_accounts import read_audit

        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(30)
        post(client, csrf, ENROLL, password="not the password at all")
        post(client, csrf, ENROLL, password=PASSWORD)

        events = [e for e in read_audit(sandbox) if e["action"] == "auth.2fa.enroll"]

        assert [e["result"] for e in events] == ["failure", "success"]


class TestAnAccountWithoutAPassword:
    def an_sso_session(self, client: TestClient, later: Later) -> str:
        """A person who signs in through the identity provider and has no password."""
        require_second_factor()
        account = accounts().create_external("lucia", "operator")
        issued = get_token_manager().create_session(
            "testclient", account_id=account.id, auth_method="oidc"
        )
        client.cookies.set(SESSION_COOKIE_NAME, issued.token)
        client.cookies.set(CSRF_COOKIE_NAME, issued.csrf_token)
        return issued.csrf_token

    def test_a_sign_in_through_the_provider_is_a_proof_while_it_is_recent(
        self, sandbox: Path, later: Later
    ) -> None:
        client = build(sandbox)
        csrf = self.an_sso_session(client, later)
        later.minutes(5)

        assert post(client, csrf, ENROLL).status_code == 200

    def test_an_old_one_signs_in_again_there_is_no_password_to_ask(
        self, sandbox: Path, later: Later
    ) -> None:
        client = build(sandbox)
        csrf = self.an_sso_session(client, later)
        later.minutes(30)

        refused = post(client, csrf, ENROLL, password=PASSWORD)

        assert refused.status_code == 403
        assert refused.json()["error"] == "fresh_sign_in_required"
        assert accounts().pending_totp_secret(accounts().require("lucia").id) is None


class TestWhoIsNotAsked:
    def test_an_account_that_has_a_factor_is_not_held_to_it(self, sandbox: Path) -> None:
        # An account with a passkey adding an authenticator is not setting up its first: the
        # sign-in that got it here already asked for the factor it has.
        from types import SimpleNamespace

        from noust.web.api.identity_proof import ensure_first_factor_proof

        old = {"type": "session", "signed_in_at": time.time() - 3 * 3600, "account_id": 7}
        has_one = SimpleNamespace(id=7, has_mfa=True, has_password=True)

        ensure_first_factor_proof(None, old, has_one, None)  # type: ignore[arg-type]

    def test_a_token_that_acts_for_an_account_is_never_a_recent_sign_in(self) -> None:
        from types import SimpleNamespace

        from fastapi import HTTPException

        from noust.web.api.identity_proof import ensure_first_factor_proof

        token = {"type": "api_token", "account_id": 7, "signed_in_at": time.time()}
        without = SimpleNamespace(id=7, has_mfa=False, has_password=True)

        with pytest.raises(HTTPException) as refused:
            ensure_first_factor_proof(SimpleNamespace(), token, without, None)  # type: ignore[arg-type]

        assert refused.value.status_code == 403


class TestPasskeys:
    def test_a_first_passkey_is_held_to_the_same_proof(self, sandbox: Path, later: Later) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(30)

        refused = post(client, csrf, PASSKEY_OPTIONS)

        assert refused.status_code == 403
        assert refused.json()["error"] == "identity_proof_required"

    def test_and_goes_on_with_the_password(self, sandbox: Path, later: Later) -> None:
        client = build(sandbox)
        csrf = signed_in_without_a_factor(client)
        later.minutes(30)

        answered = post(client, csrf, PASSKEY_OPTIONS, password=PASSWORD)

        # Whatever the browser's address says of passkeys, the proof was accepted.
        assert answered.json().get("error") != "identity_proof_required"
        assert answered.status_code != 401
