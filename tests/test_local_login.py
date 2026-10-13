# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turning the password and passkey sign-in off once single sign-on is in (I-23).

With ``auth.local_login.enabled`` off only the identity provider signs people
in; the break-glass accounts keep their password; the master token and the
command line, the ways back in, are untouched; and nothing may be switched off
that would leave nobody able to administer the server.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")

from noust.core.accounts.local_login import (
    LocalLoginRefused,
    clean_exempt,
    load_policy,
    refuse_local_sign_in,
    refuse_to_lose_a_way_in,
    survivors,
)
from noust.core.accounts.oidc import OidcManager
from noust.core.config import Config
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME
from tests.oidc_support import FakeIdP
from tests.test_web_oidc import (  # noqa: F401 - the fixtures of the sign-in tests
    PASSWORD,
    accounts,
    app,
    browser,
    cheap_hashes,
    configure,
    error_of,
    idp,
    link,
    make_account,
    store,
    through_the_provider,
    ticket_of,
)


@pytest.fixture(autouse=True)
def isolated_config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The configuration file is the test's own, and forgotten when it ends."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture(autouse=True)
def served(app: Any, isolated_config: None) -> Any:
    """The console exists for every test: its token manager is where accounts live."""
    return app


def turn_off() -> None:
    """Switch the password sign-in off, through the same setting everything else uses."""
    Config().set("auth.local_login.enabled", False)


def sign_in(client: Any, username: str, password: str = PASSWORD, code: str | None = None) -> Any:
    """
    Args:
        client: A browser.
        username: The account.
        password: Its password.
        code: A code from its authenticator, to finish in one request.

    Returns:
        The response.
    """
    body: dict[str, Any] = {"username": username, "password": password}
    if code:
        body["totp_code"] = code
    return client.post("/api/auth/login", json=body)


class TestTheSetting:
    def test_it_is_on_by_default(self, store: NoustStore) -> None:
        policy = load_policy()

        assert policy.enabled and policy.exempt == frozenset()

    def test_a_yes_or_a_no_is_all_it_takes(self, store: NoustStore) -> None:
        with pytest.raises(ConfigError, match="true or false"):
            Config().set("auth.local_login.enabled", "maybe")

    def test_it_is_refused_while_nobody_could_administer_without_a_password(
        self, idp: FakeIdP, store: NoustStore
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp)

        with pytest.raises(ConfigError, match="nobody able to administer") as caught:
            turn_off()

        assert "noust auth oidc link" in (caught.value.details or "")
        assert load_policy().enabled

    def test_an_administrator_linked_to_an_enabled_provider_is_enough(
        self, idp: FakeIdP, store: NoustStore
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp)
        link("root-admin")

        turn_off()

        assert not load_policy().enabled
        assert survivors(store) == ["root-admin"]

    def test_an_operator_who_is_linked_cannot_administer(
        self, idp: FakeIdP, store: NoustStore
    ) -> None:
        make_account("olga", "operator")
        configure(idp)
        link("olga")

        with pytest.raises(ConfigError):
            turn_off()

    def test_a_link_to_a_provider_that_is_disabled_does_not_count(
        self, idp: FakeIdP, store: NoustStore
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp, enabled=False)
        link("root-admin")

        with pytest.raises(ConfigError):
            turn_off()

    def test_a_break_glass_account_is_enough_without_a_provider(self, store: NoustStore) -> None:
        make_account("root-admin", "admin")
        Config().set("auth.local_login.exempt", ["root-admin"])

        turn_off()

        assert load_policy().exempt == frozenset({"root-admin"})

    def test_a_disabled_administrator_is_not_somebody_who_could_sign_in(
        self, idp: FakeIdP, store: NoustStore
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp)
        link("root-admin")
        accounts().disable("root-admin")

        with pytest.raises(ConfigError):
            turn_off()

    def test_the_list_of_break_glass_accounts_is_checked(self, store: NoustStore) -> None:
        assert clean_exempt("Root-Admin, ana  ana") == ["root-admin", "ana"]
        with pytest.raises(ValidationError):
            clean_exempt(["not a name!"])
        with pytest.raises(ValidationError, match="At most"):
            clean_exempt([f"user{n:02d}" for n in range(11)])
        with pytest.raises(ValidationError):
            clean_exempt(42)

    def test_shortening_the_list_cannot_lock_everybody_out(self, store: NoustStore) -> None:
        make_account("root-admin", "admin")
        Config().set("auth.local_login.exempt", ["root-admin"])
        turn_off()

        with pytest.raises(ConfigError, match="nobody able to administer"):
            Config().set("auth.local_login.exempt", [])

        assert load_policy().exempt == frozenset({"root-admin"})


class TestWhatItRefusesAndWhatItDoesNot:
    def prepared(self, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        make_account("maria", "operator", person_ref="maria@acme.test")
        make_account("glass", "admin")
        configure(idp)
        link("root-admin", "sub-root")
        Config().set("auth.local_login.exempt", ["glass"])
        turn_off()

    def test_a_right_password_no_longer_signs_an_account_in(self, app: Any, idp: FakeIdP) -> None:
        self.prepared(idp)
        client = browser(app)
        before = len(accounts().list_all())

        response = sign_in(client, "maria")

        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "local_login_disabled"
        assert "identity provider" in body["hint"]
        assert len(accounts().list_all()) == before
        assert "wasm_session" not in client.cookies

    def test_a_wrong_password_is_answered_as_ever_so_nothing_is_learnt(
        self, app: Any, idp: FakeIdP
    ) -> None:
        self.prepared(idp)

        wrong = sign_in(browser(app), "maria", "not-the-password")
        unknown = sign_in(browser(app), "nobody-here", "not-the-password")
        exempt_wrong = sign_in(browser(app), "glass", "not-the-password")

        assert wrong.status_code == unknown.status_code == exempt_wrong.status_code == 401
        assert wrong.json()["error"] == unknown.json()["error"] == "invalid_credentials"

    def test_a_break_glass_account_keeps_its_password(self, app: Any, idp: FakeIdP) -> None:
        self.prepared(idp)
        codes = make_account("glass2", "admin")
        Config().set("auth.local_login.exempt", ["glass", "glass2"])

        response = sign_in(browser(app), "glass2", code=codes[0])

        assert response.status_code == 200, response.text

    def test_the_second_step_of_a_password_sign_in_cannot_finish_either(
        self, app: Any, idp: FakeIdP
    ) -> None:
        make_account("root-admin", "admin")
        codes = make_account("maria", "operator")
        configure(idp)
        link("root-admin", "sub-root")
        client = browser(app)
        challenge = sign_in(client, "maria").json()["second_factor"]["challenge"]
        Config().set("auth.local_login.exempt", ["root-admin"])
        turn_off()

        response = client.post(
            "/api/auth/login/second-factor", json={"challenge": challenge, "code": codes[0]}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "local_login_disabled"

    def test_the_identity_provider_still_signs_people_in(self, app: Any, idp: FakeIdP) -> None:
        make_account("maria", "operator")
        make_account("root-admin", "admin")
        configure(idp)
        link("maria")
        link("root-admin", "sub-root")
        turn_off()

        ticket = ticket_of(through_the_provider(browser(app), idp))

        assert ticket

    def test_the_master_token_still_signs_in(self, app: Any, idp: FakeIdP) -> None:
        self.prepared(idp)
        from noust.web.server import get_token_manager

        token = get_token_manager().generate_master_token()

        response = browser(app).post("/api/auth/login", json={"token": token})

        assert response.status_code in (200, 401)
        assert response.json().get("error") != "local_login_disabled"

    def test_the_sign_in_page_is_told(self, app: Any, idp: FakeIdP) -> None:
        self.prepared(idp)

        body = browser(app).get("/api/auth/oidc/providers").json()

        assert body["local_login"] is False
        assert [p["name"] for p in body["providers"]] == ["corp"]

    def test_the_decision_is_made_by_the_one_that_ends_a_sign_in_too(self, idp: FakeIdP) -> None:
        make_account("maria", "operator")
        make_account("root-admin", "admin")
        configure(idp)
        link("root-admin", "sub-root")
        turn_off()
        account = accounts().require("maria")

        with pytest.raises(LocalLoginRefused):
            refuse_local_sign_in(account)
        Config().set("auth.local_login.exempt", ["maria"])
        refuse_local_sign_in(account)  # exempt now: no refusal


class TestNotTakingTheLastWayInAway:
    def prepared(self, idp: FakeIdP) -> None:
        make_account("root-admin", "admin")
        configure(idp)
        link("root-admin", "sub-root")
        turn_off()

    def test_the_last_provider_cannot_be_removed(self, idp: FakeIdP, store: NoustStore) -> None:
        self.prepared(idp)
        manager = OidcManager(accounts().store, accounts=accounts())

        with pytest.raises(LocalLoginRefused, match="Removing the provider"):
            manager.remove("corp")

        assert manager.provider("corp") is not None

    def test_nor_disabled(self, idp: FakeIdP, store: NoustStore) -> None:
        self.prepared(idp)
        manager = OidcManager(accounts().store, accounts=accounts())

        with pytest.raises(LocalLoginRefused, match="Disabling the provider"):
            manager.configure("corp", {"enabled": False})

    def test_nor_the_last_link_cut(self, idp: FakeIdP, store: NoustStore) -> None:
        self.prepared(idp)
        manager = OidcManager(accounts().store, accounts=accounts())
        identity = manager.identities("root-admin")[0]

        with pytest.raises(LocalLoginRefused, match="Cutting this link"):
            manager.unlink_identity(identity.id)

    def test_with_the_password_on_every_one_of_them_is_allowed(
        self, idp: FakeIdP, store: NoustStore
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp)
        link("root-admin", "sub-root")
        manager = OidcManager(accounts().store, accounts=accounts())

        manager.configure("corp", {"enabled": False})
        manager.remove("corp")

        assert manager.provider("corp") is None

    def test_another_administrator_who_is_linked_makes_a_link_expendable(
        self, idp: FakeIdP, store: NoustStore
    ) -> None:
        make_account("root-admin", "admin")
        make_account("second", "admin")
        configure(idp)
        link("root-admin", "sub-root")
        link("second", "sub-second")
        turn_off()
        manager = OidcManager(accounts().store, accounts=accounts())

        manager.unlink_identity(manager.identities("second")[0].id)

        assert survivors(store) == ["root-admin"]

    def test_the_helper_is_quiet_while_the_password_is_on(self, store: NoustStore) -> None:
        refuse_to_lose_a_way_in(store, without_provider="anything")


class TestTheApi:
    def sign_in_as_security(self, app: Any) -> Any:
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

    def test_a_security_officer_reads_where_it_stands(self, app: Any, idp: FakeIdP) -> None:
        client = self.sign_in_as_security(app)

        body = client.get("/api/auth/oidc/local-login").json()

        assert body == {"enabled": True, "exempt": [], "survivors": [], "providers": 0}

    def test_changing_it_needs_sudo_mode_and_is_refused_when_it_would_lock_everyone_out(
        self, app: Any, idp: FakeIdP
    ) -> None:
        make_account("root-admin", "admin")
        configure(idp)
        client = self.sign_in_as_security(app)
        headers = {CSRF_HEADER_NAME: client.csrf}

        refused = client.put("/api/auth/oidc/local-login", json={"enabled": False}, headers=headers)
        self.elevate(client)
        locked_out = client.put(
            "/api/auth/oidc/local-login", json={"enabled": False}, headers=headers
        )

        assert refused.status_code == 403 and refused.json()["error"] == "elevation_required"
        assert locked_out.status_code == 400
        assert "nobody able to administer" in locked_out.json()["detail"]

    def test_the_break_glass_list_is_set_before_the_password_goes_off(
        self, app: Any, idp: FakeIdP, sandbox: Path
    ) -> None:
        make_account("root-admin", "admin")
        client = self.sign_in_as_security(app)
        self.elevate(client)

        response = client.put(
            "/api/auth/oidc/local-login",
            json={"enabled": False, "exempt": ["root-admin"]},
            headers={CSRF_HEADER_NAME: client.csrf},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "enabled": False,
            "exempt": ["root-admin"],
            "survivors": ["root-admin"],
            "providers": 0,
        }
        from tests.test_web_oidc import logged

        assert logged(sandbox, "auth.local_login.change")

    def test_an_operator_does_not_touch_it(self, app: Any, idp: FakeIdP) -> None:
        codes = make_account("olga", "operator")
        client = browser(app)
        login = client.post(
            "/api/auth/login",
            json={"username": "olga", "password": PASSWORD, "totp_code": codes[0]},
        ).json()

        assert client.get("/api/auth/oidc/local-login").status_code == 403
        assert (
            client.put(
                "/api/auth/oidc/local-login",
                json={"enabled": False},
                headers={CSRF_HEADER_NAME: login["csrf_token"]},
            ).status_code
            == 403
        )


def test_error_of_is_for_the_provider_flow(app: Any, idp: FakeIdP) -> None:
    # A sign-in the provider refuses still ends on the sign-in page with one word.
    make_account("root-admin", "admin")
    configure(idp)
    link("root-admin", "sub-root")
    turn_off()

    assert error_of(through_the_provider(browser(app), idp, "sub-stranger")) in (
        "denied",
        "failed",
        "refused",
    )
