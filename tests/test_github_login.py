# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Signing in with GitHub (checklist I-16): OAuth 2.0 without OpenID Connect.

GitHub gives no ID token, so the person is read from its API with the token the code buys.
Everything after that is the one admission every identity source goes through: the same
provisioning policy, linking by a verified address, groups and the second factor. These tests
run the flow against a fake GitHub and check what is specific to it: the numeric id is the
subject (a login can be renamed and handed to somebody else), only a verified address is
believed, and the token is used once and never kept.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("cryptography")

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts.oidc import Answer, OidcManager
from noust.core.accounts.oidc_client import OidcClient, OidcHttp
from noust.core.accounts.oidc_model import OidcError, OidcRefused
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from tests.github_login_support import FakeGitHub
from tests.oidc_support import Clock

PASSWORD = "correct horse battery staple"
REDIRECT = "https://noust.example.test/api/auth/oidc/callback"
IP = "203.0.113.5"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def gh() -> FakeGitHub:
    hub = FakeGitHub()
    hub.add_user(
        "maria-l",
        583231,
        name="Maria Lopez",
        emails=[
            {"email": "old@acme.test", "primary": False, "verified": True},
            {"email": "Maria@Acme.test", "primary": True, "verified": True},
            {"email": "typo@acme.test", "primary": False, "verified": False},
        ],
        orgs=["acme"],
        teams=[("acme", "web")],
    )
    return hub


@pytest.fixture
def accounts(store: NoustStore, clock: Clock) -> AccountManager:
    return AccountManager(store, policy=AuthPolicy(), clock=clock)


@pytest.fixture
def manager(
    store: NoustStore, gh: FakeGitHub, clock: Clock, accounts: AccountManager
) -> OidcManager:
    client = OidcClient(OidcHttp(opener=gh.opener), clock=clock)
    return OidcManager(store, client=client, accounts=accounts, clock=clock)


def secure(accounts: AccountManager, username: str, role: str = "viewer", **kwargs: Any) -> Any:
    account = accounts.create(username, role, password=PASSWORD, **kwargs)
    secret = accounts.begin_totp(account.id)
    assert accounts.confirm_totp(account.id, totp.totp_now(secret, t=accounts.now())) is not None
    return accounts.require(username)


@pytest.fixture
def admin(accounts: AccountManager) -> Any:
    return secure(accounts, "root-admin", "admin")


def configure(manager: OidcManager, gh: FakeGitHub, **changes: Any) -> Any:
    settings: dict[str, Any] = {
        "kind": "github",
        "client_id": gh.client_id,
        "label": "GitHub",
        **changes,
    }
    if gh.host != "https://github.com":
        settings.setdefault("issuer", gh.host)
    return manager.configure("github", settings, client_secret=gh.client_secret)


def run(manager: OidcManager, gh: FakeGitHub, login: str = "maria-l") -> Answer:
    begun = manager.begin("github", redirect_uri=REDIRECT, client_ip=IP, next_path="/apps")
    callback = gh.authorize(begun.url, login)
    query = parse_qs(urlparse(callback).query)
    return manager.finish(
        state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
    )


def refused(manager: OidcManager, gh: FakeGitHub, **kwargs: Any) -> OidcError:
    with pytest.raises(OidcError) as caught:
        run(manager, gh, **kwargs)
    return caught.value


class TestConfiguration:
    def test_github_needs_only_a_client_and_knows_the_rest(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        provider = configure(manager, gh)

        assert provider.kind == "github"
        assert provider.issuer == "https://github.com"
        assert provider.scopes == "read:user user:email"
        assert provider.protocol == "oidc"

    def test_an_enterprise_server_is_named_by_its_address(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        enterprise = FakeGitHub(host="https://ghe.example.test")

        provider = configure(manager, enterprise)

        assert provider.issuer == "https://ghe.example.test"

    def test_an_address_that_is_not_https_is_refused(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        with pytest.raises(ValidationError):
            manager.configure(
                "github",
                {"kind": "github", "client_id": "x", "issuer": "http://ghe.example.test"},
            )

    def test_the_kind_is_chosen_once(self, manager: OidcManager, gh: FakeGitHub) -> None:
        configure(manager, gh)

        with pytest.raises(ValidationError, match="kind"):
            manager.configure("github", {"kind": "oidc"})

    def test_a_kind_that_does_not_exist_is_refused(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        with pytest.raises(ValidationError, match="kind"):
            manager.configure("x", {"kind": "myspace", "client_id": "x"})

    def test_groups_ask_for_the_scope_that_reads_organisations(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        provider = configure(manager, gh, group_sync=True)

        assert "read:org" in provider.scopes.split()

    def test_the_secret_is_kept_where_every_provider_keeps_it(
        self, manager: OidcManager, gh: FakeGitHub, store: NoustStore
    ) -> None:
        configure(manager, gh)

        assert (
            store.db_path.parent / "secrets" / "oidc" / "github"
        ).read_text() == gh.client_secret
        assert gh.client_secret not in repr(
            store._get_connection().execute("SELECT * FROM oidc_providers").fetchall()
        )

    def test_the_sign_in_page_offers_it_like_any_other(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        configure(manager, gh)

        assert manager.public_providers() == [{"name": "github", "label": "GitHub"}]


class TestTheRedirect:
    def test_the_browser_is_sent_to_github_with_what_it_needs(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh)

        begun = manager.begin("github", redirect_uri=REDIRECT, client_ip=IP)
        query = {k: v[0] for k, v in parse_qs(urlparse(begun.url).query).items()}

        assert begun.url.startswith("https://github.com/login/oauth/authorize?")
        assert query["client_id"] == gh.client_id
        assert query["redirect_uri"] == REDIRECT
        assert query["scope"] == "read:user user:email"
        assert query["code_challenge_method"] == "S256"
        assert query["allow_signup"] == "false"
        assert "nonce" not in query and "openid" not in query["scope"]
        assert len(query["state"]) >= 32

    def test_no_account_yet_means_nobody_signs_in_this_way(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        configure(manager, gh)

        with pytest.raises(OidcRefused) as caught:
            manager.begin("github", redirect_uri=REDIRECT, client_ip=IP)
        assert caught.value.reason == "no_accounts"

    def test_a_disabled_provider_is_not_offered(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh, enabled=False)

        with pytest.raises(OidcError) as caught:
            manager.begin("github", redirect_uri=REDIRECT, client_ip=IP)
        assert caught.value.public == "unavailable"


class TestSigningIn:
    def test_a_verified_address_links_the_account_that_has_a_second_factor(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, gh, link_by_email=True)
        secure(accounts, "maria", person_ref="maria@acme.test")

        answer = run(manager, gh)

        assert answer.account.username == "maria"
        (identity,) = manager.identities("maria")
        assert (identity.issuer, identity.subject) == ("https://github.com", "583231")

    def test_the_subject_is_the_numeric_id_not_the_login(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, gh)
        secure(accounts, "maria")
        manager.link("github", "maria", "583231")

        assert run(manager, gh).account.username == "maria"
        # She renames herself and somebody else takes her old handle.
        gh.users["maria-lopez"] = gh.users.pop("maria-l")
        gh.users["maria-lopez"].login = "maria-lopez"
        gh.add_user(
            "maria-l", 999001, emails=[{"email": "x@acme.test", "primary": True, "verified": True}]
        )

        assert run(manager, gh, "maria-lopez").account.username == "maria"
        assert refused(manager, gh, login="maria-l").reason in {"no_account", "not_linked"}

    def test_the_primary_verified_address_is_the_one_believed(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, gh, provisioning="auto", default_role="viewer")

        answer = run(manager, gh)

        assert answer.account.person_ref == "maria@acme.test"

    def test_an_address_github_has_not_verified_is_never_believed(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any, accounts: AccountManager
    ) -> None:
        gh.users["maria-l"].emails = [
            {"email": "maria@acme.test", "primary": True, "verified": False}
        ]
        gh.users["maria-l"].public_email = "maria@acme.test"
        configure(manager, gh, link_by_email=True)
        secure(accounts, "maria", person_ref="maria@acme.test")

        assert refused(manager, gh).reason == "email_unverified"

    def test_a_person_nobody_linked_is_refused_while_provisioning_is_off(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh)

        assert refused(manager, gh).reason == "no_account"

    def test_an_invited_address_activates_its_account(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, gh, provisioning="invite")
        accounts.invite("maria", "viewer", person_ref="maria@acme.test")

        assert run(manager, gh).account.username == "maria"

    def test_allowed_domains_are_judged_on_the_verified_address(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh, provisioning="auto", allowed_domains=["other.test"])

        assert refused(manager, gh).reason == "domain_not_allowed"


class TestTheTokenAndTheExchange:
    def test_a_refusal_with_status_200_is_still_a_refusal(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh, provisioning="auto")
        gh.exchange_error = "bad_verification_code"

        error = refused(manager, gh)

        assert error.reason == "token_bad_verification_code"

    def test_a_code_is_good_once(self, manager: OidcManager, gh: FakeGitHub, admin: Any) -> None:
        configure(manager, gh, provisioning="auto")
        begun = manager.begin("github", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(gh.authorize(begun.url, "maria-l")).query)
        manager.finish(
            state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
        )

        with pytest.raises(OidcError) as caught:
            manager.finish(
                state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
            )
        assert caught.value.reason == "unknown_state"

    def test_the_pkce_verifier_and_the_secret_travel_in_the_exchange(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh, provisioning="auto")

        run(manager, gh)

        form = gh.last_token_request
        assert form["client_id"] == gh.client_id
        assert form["client_secret"] == gh.client_secret
        assert form["redirect_uri"] == REDIRECT
        assert len(form["code_verifier"]) >= 43

    def test_the_token_is_used_for_the_person_and_never_kept(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any, store: NoustStore
    ) -> None:
        configure(manager, gh, provisioning="auto")

        run(manager, gh)

        connection = store._get_connection()
        dump = repr(
            [
                connection.execute("SELECT * FROM oidc_flows").fetchall(),
                connection.execute("SELECT * FROM oidc_tickets").fetchall(),
                connection.execute("SELECT * FROM account_identities").fetchall(),
            ]
        )
        assert "gho_" not in dump

    def test_the_api_is_asked_with_the_token_as_a_bearer(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh, provisioning="auto")

        run(manager, gh)

        assert gh.last_headers["authorization"].startswith("Bearer gho_")
        assert "user-agent" in gh.last_headers

    def test_a_missing_secret_is_said_not_guessed(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        manager.configure("github", {"kind": "github", "client_id": gh.client_id})
        begun = manager.begin("github", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(gh.authorize(begun.url, "maria-l")).query)

        with pytest.raises(OidcError) as caught:
            manager.finish(
                state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
            )
        assert caught.value.reason == "no_client_secret"

    def test_an_unreachable_github_is_unavailable_not_a_crash(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh, provisioning="auto")
        begun = manager.begin("github", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(gh.authorize(begun.url, "maria-l")).query)
        gh.offline = True

        with pytest.raises(OidcError) as caught:
            manager.finish(
                state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
            )
        assert caught.value.public == "unavailable"


class TestGroups:
    def test_organisations_and_teams_are_the_groups(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(
            manager,
            gh,
            provisioning="auto",
            group_sync=True,
            group_mappings=[{"group": "acme/web", "role": "operator"}],
        )

        assert run(manager, gh).account.role == "operator"

    def test_an_organisation_alone_maps_too(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(
            manager,
            gh,
            provisioning="auto",
            group_sync=True,
            group_mappings=[{"group": "acme", "role": "operator"}],
        )

        assert run(manager, gh).account.role == "operator"

    def test_without_group_sync_the_organisations_are_not_even_asked_for(
        self, manager: OidcManager, gh: FakeGitHub, admin: Any
    ) -> None:
        configure(manager, gh, provisioning="auto")

        run(manager, gh)

        assert not [
            url for _method, url in gh.requests if "/user/orgs" in url or "/user/teams" in url
        ]


class TestTheTest:
    def test_testing_reaches_the_api_and_reports_the_secret(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        configure(manager, gh)

        checks = manager.test("github", redirect_uri=REDIRECT)

        assert all(check.ok for check in checks), [c.to_dict() for c in checks]
        assert any(check.name == "client secret" and check.ok for check in checks)
        assert any("authorization callback" in check.detail.lower() for check in checks)

    def test_an_unreachable_github_is_a_failed_check_not_an_error(
        self, manager: OidcManager, gh: FakeGitHub
    ) -> None:
        configure(manager, gh)
        gh.offline = True

        checks = manager.test("github")

        assert not all(check.ok for check in checks)
