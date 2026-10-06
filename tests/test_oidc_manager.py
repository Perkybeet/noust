"""
Single sign-in through OpenID Connect, against a fake provider.

The flow is the real one: ``begin`` builds the authorization URL, the fake
provider plays the person approving and mints a signed ID token, ``finish``
exchanges the code, verifies the token and decides who the person is, and
``collect`` hands the account to the console. Each test bends one thing.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("cryptography")

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts.oidc import Answer, OidcManager, safe_next
from noust.core.accounts.oidc_client import OidcClient, OidcHttp
from noust.core.accounts.oidc_model import OidcError, OidcRefused
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from tests.oidc_support import DROP, Clock, FakeIdP, SigningKey

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
def idp(clock: Clock) -> FakeIdP:
    provider = FakeIdP(clock=clock)
    provider.add_user("sub-maria", email="maria@acme.test", email_verified=True, name="Maria Lopez")
    return provider


@pytest.fixture
def accounts(store: NoustStore, clock: Clock) -> AccountManager:
    return AccountManager(store, policy=AuthPolicy(), clock=clock)


@pytest.fixture
def manager(store: NoustStore, idp: FakeIdP, clock: Clock, accounts: AccountManager) -> OidcManager:
    client = OidcClient(OidcHttp(opener=idp.opener), clock=clock)
    return OidcManager(store, client=client, accounts=accounts, clock=clock)


def secure(accounts: AccountManager, username: str, role: str = "viewer", **kwargs: Any) -> Any:
    """An account with a password and an authenticator."""
    account = accounts.create(username, role, password=PASSWORD, **kwargs)
    secret = accounts.begin_totp(account.id)
    assert accounts.confirm_totp(account.id, totp.totp_now(secret, t=accounts.now())) is not None
    return accounts.require(username)


@pytest.fixture
def admin(accounts: AccountManager) -> Any:
    """Some account must exist: single sign-in never makes the first one."""
    return secure(accounts, "root-admin", "admin")


def configure(manager: OidcManager, idp: FakeIdP, **changes: Any) -> Any:
    settings = {"issuer": idp.issuer, "client_id": idp.client_id, "label": "Acme SSO", **changes}
    return manager.configure("corp", settings, client_secret=idp.client_secret)


def run(
    manager: OidcManager,
    idp: FakeIdP,
    sub: str = "sub-maria",
    *,
    binding: str | None = None,
    **claims: Any,
) -> Answer:
    """One whole sign-in, from the click to the ticket."""
    begun = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP, next_path="/apps")
    callback = idp.authorize(begun.url, sub, **claims)
    query = parse_qs(urlparse(callback).query)
    answer = manager.finish(
        state=query["state"][0],
        code=query["code"][0],
        binding=begun.binding if binding is None else binding,
        client_ip=IP,
    )
    BINDINGS[answer.ticket] = begun.binding
    return answer


#: The cookie secret each ticket's sign-in began with, for collecting it.
BINDINGS: dict[str, str] = {}


def refused(manager: OidcManager, idp: FakeIdP, **kwargs: Any) -> OidcError:
    with pytest.raises(OidcError) as caught:
        run(manager, idp, **kwargs)
    return caught.value


class TestConfiguration:
    def test_a_new_provider_needs_an_issuer_and_a_client(self, manager: OidcManager) -> None:
        with pytest.raises(ValidationError):
            manager.configure("corp", {"label": "Acme"})

    def test_the_secret_is_a_private_file_and_never_in_the_database(
        self, manager: OidcManager, idp: FakeIdP, store: NoustStore
    ) -> None:
        provider = configure(manager, idp)

        path = store.db_path.parent / "secrets" / "oidc" / "corp"
        assert path.read_text(encoding="utf-8") == "s3cret-value"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        dump = repr(store._get_connection().execute("SELECT * FROM oidc_providers").fetchall())
        assert "s3cret-value" not in dump
        assert "secret" not in repr(provider.to_dict()).replace("client_secret_set", "")
        assert manager.has_secret("corp") is True

    def test_a_change_touches_only_what_it_names(self, manager: OidcManager, idp: FakeIdP) -> None:
        configure(manager, idp, provisioning="auto", default_role="operator")

        changed = manager.configure("corp", {"label": "Acme Login"})

        assert changed.label == "Acme Login"
        assert changed.provisioning == "auto" and changed.default_role == "operator"
        assert changed.issuer == idp.issuer
        assert manager.has_secret("corp") is True

    def test_the_secret_can_be_replaced_and_cleared(
        self, manager: OidcManager, idp: FakeIdP, store: NoustStore
    ) -> None:
        configure(manager, idp)
        manager.configure("corp", {}, client_secret="another-secret")
        path = store.db_path.parent / "secrets" / "oidc" / "corp"
        assert path.read_text(encoding="utf-8") == "another-secret"

        manager.configure("corp", {}, clear_secret=True)

        assert not path.exists() and manager.has_secret("corp") is False

    def test_removing_forgets_the_provider_and_its_secret(
        self, manager: OidcManager, idp: FakeIdP, store: NoustStore
    ) -> None:
        configure(manager, idp)
        manager.remove("corp")

        assert manager.provider("corp") is None
        assert not (store.db_path.parent / "secrets" / "oidc" / "corp").exists()
        with pytest.raises(ConfigError):
            manager.remove("corp")

    @pytest.mark.parametrize(
        "bad",
        [
            {"issuer": "http://idp.example.test"},
            {"issuer": "https://user:pw@idp.example.test"},
            {"issuer": "https://idp.example.test/?x=1"},
            {"issuer": "https://169.254.169.254/"},
            {"provisioning": "everyone"},
            {"default_role": "security"},
            {"default_role": "auditor"},
            {"clock_skew_seconds": 9000},
            {"scopes": "openid bad\x01scope"},
            {"allowed_domains": ["not a domain"]},
            {"group_mappings": [{"group": "x", "role": "security"}]},
            {"group_mappings": [{"group": "x", "role": "admin", "team": "acme"}]},
            {"group_mappings": [{"group": "x"}]},
            {"group_mappings": [{"role": "admin"}]},
            {"redirect_uri": "https://noust.example.test/somewhere"},
            {"nonsense": 1},
        ],
    )
    def test_nonsense_is_refused_and_nothing_is_written(
        self, manager: OidcManager, idp: FakeIdP, bad: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError):
            configure(manager, idp, **bad)
        assert manager.provider("corp") is None

    def test_a_loopback_provider_may_use_plain_http(self, manager: OidcManager) -> None:
        provider = manager.configure(
            "dev", {"issuer": "http://127.0.0.1:9000/realm", "client_id": "noust"}
        )
        assert provider.issuer == "http://127.0.0.1:9000/realm"

    def test_openid_is_always_a_scope(self, manager: OidcManager, idp: FakeIdP) -> None:
        provider = configure(manager, idp, scopes="email groups")
        assert provider.scopes == "openid email groups"

    def test_a_team_the_mapping_names_must_exist(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        with pytest.raises(ValidationError):
            configure(
                manager,
                idp,
                group_mappings=[{"group": "ops", "team": "ghost", "team_role": "viewer"}],
            )
        TeamStore(manager.accounts.store).create_team("Ghost", slug="ghost")
        provider = configure(
            manager, idp, group_mappings=[{"group": "ops", "team": "ghost", "team_role": "viewer"}]
        )
        assert provider.group_mappings[0].team == "ghost"

    def test_the_ens_profile_does_not_let_a_provider_hand_out_admin(
        self, store: NoustStore, idp: FakeIdP, clock: Clock
    ) -> None:
        ens = AccountManager(store, policy=AuthPolicy(profile="ens-medium"), clock=clock)
        manager = OidcManager(store, accounts=ens, client=OidcClient(OidcHttp(opener=idp.opener)))
        with pytest.raises(ValidationError):
            configure(manager, idp, provisioning="auto", default_role="admin")
        with pytest.raises(ValidationError):
            configure(manager, idp, group_mappings=[{"group": "root", "role": "admin"}])

    def test_the_sign_in_page_is_told_only_the_enabled_names_and_labels(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        configure(manager, idp)
        manager.configure("old", {"issuer": idp.issuer, "client_id": "x", "enabled": False})

        assert manager.public_providers() == [{"name": "corp", "label": "Acme SSO"}]


class TestTheFlow:
    def test_a_linked_person_signs_in_through_every_step(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        maria = secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

        answer = run(manager, idp)

        assert answer.account.username == "maria"
        assert answer.next_path == "/apps"
        ticket, account = manager.collect(answer.ticket, _binding(answer), IP)
        assert account.id == maria.id and ticket.provider == "corp"

    def test_the_browser_is_sent_with_state_nonce_and_pkce(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, scopes="openid email groups")

        begun = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)

        query = {k: v[0] for k, v in parse_qs(urlparse(begun.url).query).items()}
        assert begun.url.startswith(idp.authorization_endpoint)
        assert query["response_type"] == "code"
        assert query["client_id"] == idp.client_id
        assert query["redirect_uri"] == REDIRECT
        assert query["scope"] == "openid email groups"
        assert query["code_challenge_method"] == "S256"
        assert len(query["state"]) >= 32 and len(query["nonce"]) >= 24
        assert len(query["code_challenge"]) == 43
        assert begun.binding not in begun.url

    def test_the_code_is_traded_with_the_verifier_and_the_secret_in_basic_auth(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

        run(manager, idp)

        form = idp.last_token_request
        assert form["grant_type"] == "authorization_code"
        assert len(form["code_verifier"]) == 64
        assert "client_secret" not in form  # basic auth is the default

    def test_a_client_secret_goes_in_the_body_when_the_provider_only_takes_that(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        idp.discovery_overrides["token_endpoint_auth_methods_supported"] = ["client_secret_post"]
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

        run(manager, idp)

        assert idp.last_token_request["client_secret"] == "s3cret-value"

    def test_a_public_client_signs_in_with_pkce_alone(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        idp.client_secret = None
        manager.configure("corp", {"issuer": idp.issuer, "client_id": idp.client_id})
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

        run(manager, idp)

        assert idp.last_token_request["client_id"] == idp.client_id
        assert "client_secret" not in idp.last_token_request

    def test_es256_providers_work_too(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        idp.key = SigningKey("ES256", "ec-1")
        idp.published = [idp.key.jwk()]
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

        assert run(manager, idp).account.username == "maria"

    def test_a_disabled_provider_cannot_begin(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, enabled=False)
        with pytest.raises(OidcError) as caught:
            manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        assert caught.value.public == "unavailable"

    def test_nobody_signs_in_this_way_before_the_first_account_exists(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        configure(manager, idp, provisioning="auto", default_role="admin")

        with pytest.raises(OidcRefused) as caught:
            manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        assert caught.value.reason == "no_accounts"


def _binding(answer: Answer) -> str:
    """The cookie secret of the sign-in that produced ``answer``."""
    return BINDINGS[answer.ticket]


class TestTokensAreJudged:
    @pytest.fixture(autouse=True)
    def ready(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

    def test_a_token_with_a_changed_payload_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        def tamper(token: str) -> str:
            import json

            from tests.oidc_support import b64

            header, payload, signature = token.split(".")
            claims = {"iss": idp.issuer, "sub": "sub-admin", "aud": idp.client_id, "exp": 9e9}
            return ".".join([header, b64(json.dumps(claims).encode()), signature])

        idp.tamper = tamper
        assert refused(manager, idp).reason == "token_signature"

    def test_a_token_signed_by_a_key_the_provider_never_published_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.key = SigningKey("RS256", idp.published[0]["kid"])  # same kid, another key
        assert refused(manager, idp).reason == "token_signature"

    def test_an_unsigned_token_is_refused(self, manager: OidcManager, idp: FakeIdP) -> None:
        import json

        from tests.oidc_support import b64

        def strip(token: str) -> str:
            _header, payload, _signature = token.split(".")
            return b64(json.dumps({"alg": "none"}).encode()) + "." + payload + "."

        idp.tamper = strip
        assert refused(manager, idp).reason in {"token_algorithm", "token_malformed"}

    def test_a_token_for_another_audience_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["aud"] = "someone-elses-client"
        assert refused(manager, idp).reason == "token_audience"

    def test_a_token_with_several_audiences_needs_us_as_the_authorized_party(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["aud"] = [idp.client_id, "other"]
        assert refused(manager, idp).reason == "token_authorized_party"
        idp.bend["azp"] = idp.client_id
        assert run(manager, idp).account.username == "maria"

    def test_a_token_from_another_issuer_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["iss"] = "https://evil.example.test"
        assert refused(manager, idp).reason == "token_issuer"

    def test_an_expired_token_is_refused_but_a_little_skew_is_forgiven(
        self, manager: OidcManager, idp: FakeIdP, clock: Clock
    ) -> None:
        idp.bend["exp"] = int(clock()) - 30  # inside the 60 seconds of skew
        assert run(manager, idp).account.username == "maria"

        idp.bend["exp"] = int(clock()) - 61
        assert refused(manager, idp).reason == "token_expired"

    def test_a_token_from_the_future_is_refused_beyond_the_skew(
        self, manager: OidcManager, idp: FakeIdP, clock: Clock
    ) -> None:
        idp.bend["iat"] = int(clock()) + 30
        assert run(manager, idp).account.username == "maria"
        idp.bend["iat"] = int(clock()) + 3600
        assert refused(manager, idp).reason == "token_issued_in_future"

    def test_a_token_that_is_not_valid_yet_is_refused(
        self, manager: OidcManager, idp: FakeIdP, clock: Clock
    ) -> None:
        idp.bend["nbf"] = int(clock()) + 3600
        assert refused(manager, idp).reason == "token_not_yet_valid"

    def test_the_skew_is_the_providers_setting(
        self, manager: OidcManager, idp: FakeIdP, clock: Clock
    ) -> None:
        manager.configure("corp", {"clock_skew_seconds": 0})
        idp.bend["exp"] = int(clock()) - 1
        assert refused(manager, idp).reason == "token_expired"

    def test_a_token_without_the_nonce_of_this_sign_in_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["nonce"] = "a-nonce-from-another-sign-in"
        assert refused(manager, idp).reason == "token_nonce"
        idp.bend["nonce"] = DROP
        assert refused(manager, idp).reason == "token_nonce"

    def test_a_token_without_a_subject_is_refused(self, manager: OidcManager, idp: FakeIdP) -> None:
        idp.bend["sub"] = ""
        assert refused(manager, idp, sub="sub-maria").reason == "token_subject"

    def test_a_replayed_answer_is_refused(self, manager: OidcManager, idp: FakeIdP) -> None:
        begun = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(idp.authorize(begun.url, "sub-maria")).query)
        args = {"state": query["state"][0], "code": query["code"][0], "binding": begun.binding}
        manager.finish(**args, client_ip=IP)

        with pytest.raises(OidcError) as caught:
            manager.finish(**args, client_ip=IP)
        assert caught.value.reason == "unknown_state"

    def test_a_code_cannot_be_exchanged_twice(self, manager: OidcManager, idp: FakeIdP) -> None:
        begun = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(idp.authorize(begun.url, "sub-maria")).query)
        manager.finish(
            state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
        )
        # A second sign-in is started with the code of the first.
        again = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        with pytest.raises(OidcError) as caught:
            manager.finish(
                state=parse_qs(urlparse(again.url).query)["state"][0],
                code=query["code"][0],
                binding=again.binding,
                client_ip=IP,
            )
        assert caught.value.reason == "token_invalid_grant"

    def test_an_answer_that_comes_back_to_another_browser_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        assert refused(manager, idp, binding="the-attackers-own-cookie").reason == "binding"
        assert refused(manager, idp, binding="").reason == "binding"

    def test_a_wrong_browser_cannot_use_the_sign_in_up(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        begun = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(idp.authorize(begun.url, "sub-maria")).query)
        state, code = query["state"][0], query["code"][0]

        with pytest.raises(OidcError) as caught:
            manager.finish(state=state, code=code, binding="somebody-elses-cookie", client_ip=IP)
        assert caught.value.reason == "binding"

        # The browser that began is not locked out of its own sign-in.
        answer = manager.finish(state=state, code=code, binding=begun.binding, client_ip=IP)
        assert answer.account.username == "maria"

    def test_an_answer_to_a_sign_in_nobody_began_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        with pytest.raises(OidcError) as caught:
            manager.finish(state="forged", code="x", binding="y", client_ip=IP)
        assert caught.value.reason == "unknown_state"

    def test_an_answer_after_the_time_allowed_is_refused(
        self, manager: OidcManager, idp: FakeIdP, clock: Clock
    ) -> None:
        begun = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(idp.authorize(begun.url, "sub-maria")).query)
        clock.advance(601)

        with pytest.raises(OidcError) as caught:
            manager.finish(
                state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
            )
        assert caught.value.reason == "unknown_state"

    def test_the_provider_refusing_the_code_is_an_error_not_a_sign_in(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.token_status = 400
        assert refused(manager, idp).reason == "token_server_error"

    def test_an_unreachable_provider_says_so(self, manager: OidcManager, idp: FakeIdP) -> None:
        begun = manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        query = parse_qs(urlparse(idp.authorize(begun.url, "sub-maria")).query)
        idp.offline = True

        with pytest.raises(OidcError) as caught:
            manager.finish(
                state=query["state"][0], code=query["code"][0], binding=begun.binding, client_ip=IP
            )
        assert caught.value.reason == "idp_unreachable" and caught.value.public == "unavailable"

    def test_a_discovery_document_for_another_issuer_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.discovery_overrides["issuer"] = "https://another.example.test"
        with pytest.raises(OidcError) as caught:
            manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        assert caught.value.reason == "issuer_mismatch"

    def test_endpoints_that_are_not_https_are_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.discovery_overrides["token_endpoint"] = "http://idp.example.test/token"
        with pytest.raises(OidcError) as caught:
            manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        assert caught.value.reason == "discovery_invalid"

    def test_a_provider_without_pkce_s256_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.discovery_overrides["code_challenge_methods_supported"] = ["plain"]
        with pytest.raises(OidcError) as caught:
            manager.begin("corp", redirect_uri=REDIRECT, client_ip=IP)
        assert caught.value.reason == "pkce_unsupported"

    def test_an_algorithm_the_provider_does_not_advertise_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.discovery_overrides["id_token_signing_alg_values_supported"] = ["ES256"]
        assert refused(manager, idp).reason == "token_algorithm"


class TestKeyRotation:
    @pytest.fixture(autouse=True)
    def ready(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

    def test_a_rotated_key_is_fetched_when_a_token_names_it(
        self, manager: OidcManager, idp: FakeIdP, clock: Clock
    ) -> None:
        run(manager, idp)  # caches the keys
        fetched = sum(url == idp.jwks_uri for _, url in idp.requests)
        idp.key = SigningKey("RS256", "rotated")
        idp.published = [idp.key.jwk()]
        clock.advance(120)  # past the minimum between refreshes

        assert run(manager, idp).account.username == "maria"
        assert sum(url == idp.jwks_uri for _, url in idp.requests) == fetched + 1

    def test_forged_tokens_cannot_make_the_server_fetch_without_end(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp)
        fetched = sum(url == idp.jwks_uri for _, url in idp.requests)
        idp.key = SigningKey("RS256", "never-published")

        for _ in range(5):
            assert refused(manager, idp).reason.startswith("token_")

        assert sum(url == idp.jwks_uri for _, url in idp.requests) <= fetched + 1

    def test_a_key_set_is_remembered_between_sign_ins(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp)
        run(manager, idp)

        assert sum(url == idp.jwks_uri for _, url in idp.requests) == 1
        discovery = f"{idp.issuer}/.well-known/openid-configuration"
        assert sum(url == discovery for _, url in idp.requests) == 1


class TestEmail:
    @pytest.fixture(autouse=True)
    def ready(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

    def test_an_unverified_email_is_refused_by_default(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        exc = refused(manager, idp, email_verified=False)
        assert isinstance(exc, OidcRefused) and exc.reason == "email_unverified"

    def test_a_missing_verification_claim_is_not_a_verification(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["email_verified"] = DROP
        idp.userinfo = {"sub": "sub-maria", "email": "maria@acme.test"}  # it says nothing either
        assert refused(manager, idp).reason == "email_unverified"

    def test_the_string_true_is_what_some_providers_write(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        assert run(manager, idp, email_verified="true").account.username == "maria"
        assert refused(manager, idp, email_verified="yes").reason == "email_unverified"

    def test_a_directory_that_never_says_can_be_assumed_to_have_verified(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["email_verified"] = DROP
        idp.userinfo = {"sub": "sub-maria", "email": "maria@acme.test"}  # it says nothing either
        assert refused(manager, idp).reason == "email_unverified"

        manager.configure("corp", {"assume_email_verified": True})

        assert run(manager, idp).account.username == "maria"

    def test_assuming_never_overrides_a_provider_that_says_no(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        manager.configure("corp", {"assume_email_verified": True})

        assert refused(manager, idp, email_verified=False).reason == "email_unverified"
        assert refused(manager, idp, email_verified="false").reason == "email_unverified"

    def test_a_provider_that_does_not_vouch_can_still_sign_in_linked_people(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        manager.configure("corp", {"require_email_verified": False})
        assert run(manager, idp, email_verified=False).account.username == "maria"

    def test_only_the_allowed_domains_sign_in(self, manager: OidcManager, idp: FakeIdP) -> None:
        manager.configure("corp", {"allowed_domains": ["acme.test"]})
        assert run(manager, idp).account.username == "maria"

        exc = refused(manager, idp, email="maria@evil.test")
        assert exc.reason == "domain_not_allowed"
        assert refused(manager, idp, email="maria@sub.acme.test").reason == "domain_not_allowed"

    def test_a_required_method_must_be_among_those_the_provider_used(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        manager.configure("corp", {"require_amr": ["mfa", "hwk"]})
        assert refused(manager, idp, amr=["pwd"]).reason == "amr_missing"
        assert refused(manager, idp).reason == "amr_missing"
        assert run(manager, idp, amr=["pwd", "mfa"]).account.username == "maria"

    def test_userinfo_completes_what_the_token_left_out(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["email"] = DROP
        idp.bend["email_verified"] = DROP
        manager.configure("corp", {"require_email_verified": False})

        answer = run(manager, idp)

        assert answer.account.username == "maria"
        assert any(url == idp.userinfo_endpoint for _, url in idp.requests)

    def test_userinfo_can_say_what_the_token_does_not_about_the_email(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["email_verified"] = DROP  # the token has the address and not its verification

        answer = run(manager, idp)

        assert answer.account.username == "maria"
        assert any(url == idp.userinfo_endpoint for _, url in idp.requests)

    def test_userinfo_about_another_person_is_ignored(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        idp.bend["email"] = DROP
        idp.bend["email_verified"] = DROP
        idp.userinfo = {"sub": "someone-else", "email": "ceo@acme.test", "email_verified": True}

        assert refused(manager, idp).reason == "email_unverified"


class TestWhoIsAllowedIn:
    def test_a_person_nobody_linked_is_refused_when_provisioning_is_off(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")

        exc = refused(manager, idp)

        assert exc.reason == "no_account" and exc.who == "corp:sub-maria"

    def test_an_email_alone_never_links_when_the_provider_is_not_trusted_for_it(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria", person_ref="maria@acme.test")

        assert refused(manager, idp).reason == "no_account"
        assert manager.identities("maria") == []

    def test_a_verified_email_links_an_account_that_has_a_second_factor_when_trusted(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, link_by_email=True)
        secure(accounts, "maria", person_ref="maria@acme.test")

        answer = run(manager, idp)

        assert answer.account.username == "maria"
        (identity,) = manager.identities("maria")
        assert (identity.issuer, identity.subject) == (idp.issuer, "sub-maria")

    def test_an_assumed_email_finds_the_account_like_a_verified_one(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        idp.bend["email_verified"] = DROP
        idp.userinfo = {"sub": "sub-maria", "email": "maria@acme.test"}  # it says nothing either
        configure(manager, idp, link_by_email=True)
        secure(accounts, "maria", person_ref="maria@acme.test")
        assert refused(manager, idp).reason == "email_unverified"

        manager.configure("corp", {"assume_email_verified": True})

        assert run(manager, idp).account.username == "maria"

    def test_after_the_link_the_email_no_longer_matters(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, link_by_email=True)
        secure(accounts, "maria", person_ref="maria@acme.test")
        run(manager, idp)

        # The provider reassigns maria@acme.test to somebody else, a new subject.
        idp.add_user("sub-impostor", email="maria@acme.test", email_verified=True)
        exc = refused(manager, idp, sub="sub-impostor")
        assert exc.reason == "already_linked_account"

        # And the real Maria changes her address at the provider: still her.
        assert run(manager, idp, email="maria.lopez@acme.test").account.username == "maria"

    def test_an_account_nobody_secured_yet_is_not_linked_by_email(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, link_by_email=True)
        accounts.create("maria", "viewer", password=PASSWORD, person_ref="maria@acme.test")

        assert refused(manager, idp).reason == "link_needs_second_factor"

    def test_two_accounts_of_one_email_are_never_guessed_between(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, link_by_email=True)
        secure(accounts, "maria", person_ref="maria@acme.test")
        secure(accounts, "maria.sec", "security", person_ref="maria@acme.test")

        assert refused(manager, idp).reason == "ambiguous_email"

    def test_a_disabled_account_is_not_linked_by_email(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, link_by_email=True)
        secure(accounts, "maria", person_ref="maria@acme.test")
        accounts.disable("maria", "left")

        assert refused(manager, idp).reason == "no_account"
        assert manager.identities("maria") == []

    def test_an_unverified_email_never_links_even_with_the_check_off(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, link_by_email=True, require_email_verified=False)
        secure(accounts, "maria", person_ref="maria@acme.test")

        assert refused(manager, idp, email_verified=False).reason == "no_account"

    def test_an_explicit_link_by_subject_needs_no_email(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria", actor="cli:root")

        assert run(manager, idp).account.username == "maria"

    def test_a_person_cannot_be_linked_twice(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        secure(accounts, "juan")
        manager.link("corp", "maria", "sub-maria")

        with pytest.raises(OidcError) as caught:
            manager.link("corp", "juan", "sub-maria")
        assert caught.value.reason == "already_linked"

    def test_an_unlinked_person_signs_in_no_more(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        identity = manager.link("corp", "maria", "sub-maria")
        manager.unlink_identity(identity.id)

        assert refused(manager, idp).reason == "no_account"

    @pytest.mark.parametrize(
        ("change", "reason"),
        [("disable", "disabled")],
    )
    def test_an_account_that_cannot_sign_in_does_not_through_a_provider_either(
        self,
        manager: OidcManager,
        idp: FakeIdP,
        admin: Any,
        accounts: AccountManager,
        change: str,
        reason: str,
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")
        accounts.disable("maria", "left the company")

        assert refused(manager, idp).reason == reason

    def test_a_locked_account_is_refused_until_it_unlocks(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        maria = secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")
        for _ in range(AuthPolicy().lockout_threshold):
            accounts.record_failure(maria.id, "198.51.100.9")

        assert refused(manager, idp).reason == "locked"
        accounts.unlock("maria")
        assert run(manager, idp).account.username == "maria"


class TestProvisioning:
    def test_invite_only_activates_the_account_invited_for_that_email(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, provisioning="invite")
        invited, code = accounts.invite("maria", "operator", person_ref="maria@acme.test")
        assert invited.status == "invited"

        answer = run(manager, idp)

        assert answer.account.username == "maria" and answer.account.status == "active"
        assert answer.account.has_password is False
        # The invitation is used up: the code no longer opens a second way in.
        from noust.core.accounts import AuthenticationFailed

        with pytest.raises(AuthenticationFailed):
            accounts.open_invitation(code)

    def test_invite_only_refuses_a_person_nobody_invited(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, provisioning="invite")

        assert refused(manager, idp).reason == "not_invited"

    def test_an_invitation_is_matched_by_a_verified_email_only(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, provisioning="invite", require_email_verified=False)
        accounts.invite("maria", "operator", person_ref="maria@acme.test")

        assert refused(manager, idp, email_verified=False).reason == "not_invited"

    def test_auto_creates_an_account_with_the_default_role_and_no_password(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, provisioning="auto", default_role="operator")

        answer = run(manager, idp)

        account = answer.account
        assert account.username == "maria" and account.role == "operator"
        assert account.has_password is False and account.status == "active"
        assert account.display_name == "Maria Lopez"
        assert account.person_ref == "maria@acme.test"
        assert account.has_mfa is False  # its own second factor is enrolled at the first sign-in
        teams = TeamStore(accounts.store).teams_of(account.id)
        assert [team.slug for team, _ in teams] == ["default"]

    def test_auto_can_put_the_new_account_in_a_team_of_its_own(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        TeamStore(accounts.store).create_team("Acme", slug="acme")
        configure(
            manager, idp, provisioning="auto", default_team="acme", default_team_role="operator"
        )

        account = run(manager, idp).account

        teams = TeamStore(accounts.store).teams_of(account.id)
        assert [(team.slug, role) for team, role in teams] == [("acme", "operator")]

    def test_a_username_taken_is_made_free_with_a_number(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, provisioning="auto")
        secure(accounts, "maria", person_ref="somebody-else@acme.test")

        assert run(manager, idp).account.username == "maria-2"

    def test_the_username_comes_from_the_provider_when_it_offers_one(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, provisioning="auto")

        assert run(manager, idp, preferred_username="Maria.Lopez").account.username == "maria.lopez"

    def test_an_address_used_as_the_username_is_cut_at_the_at_sign(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, provisioning="auto")

        answer = run(manager, idp, preferred_username="Maria.Lopez@acme.test")

        assert answer.account.username == "maria.lopez"

    def test_control_characters_in_the_name_do_not_stop_the_sign_in(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, provisioning="auto")

        answer = run(manager, idp, name="Maria\x00 Lopez\n\x1b[31m")

        assert answer.account.display_name == "Maria Lopez[31m"

    def test_a_reserved_or_hostile_name_becomes_a_valid_one(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, provisioning="auto")
        idp.add_user("sub-r", email="root@acme.test", email_verified=True)

        account = run(manager, idp, sub="sub-r", preferred_username="../../etc").account
        assert account.username.replace(".", "").replace("-", "").replace("_", "").isalnum()
        assert account.username not in ("root", "..")

    def test_auto_does_not_make_a_second_account_for_an_email_that_has_one(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, provisioning="auto")
        secure(accounts, "maria", person_ref="maria@acme.test")

        assert refused(manager, idp).reason == "email_taken"

    def test_auto_still_needs_the_policy_gates(
        self, manager: OidcManager, idp: FakeIdP, admin: Any
    ) -> None:
        configure(manager, idp, provisioning="auto", allowed_domains=["corp.test"])
        assert refused(manager, idp).reason == "domain_not_allowed"

    def test_an_account_made_by_the_provider_has_no_password_to_guess(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, provisioning="auto")
        run(manager, idp)

        from noust.core.accounts import AuthenticationFailed

        for attempt in ("", "x", PASSWORD, "None"):
            with pytest.raises(AuthenticationFailed):
                accounts.authenticate("maria", attempt, None, client_ip="198.51.100.7")

    def test_a_disabled_account_made_by_the_provider_can_be_enabled_again(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp, provisioning="auto")
        run(manager, idp)
        accounts.disable("maria")

        assert accounts.enable("maria").status == "active"  # not an invitation to accept

    def test_the_default_team_gone_refuses_instead_of_making_a_homeless_account(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        teams = TeamStore(accounts.store)
        teams.create_team("Acme", slug="acme")
        configure(manager, idp, provisioning="auto", default_team="acme")
        teams.delete_team("acme")

        assert refused(manager, idp).reason == "team_missing"
        assert accounts.find("maria") is None


class TestGroups:
    @pytest.fixture(autouse=True)
    def ready(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        teams = TeamStore(accounts.store)
        teams.create_team("Acme", slug="acme")
        teams.create_team("Globex", slug="globex")
        configure(
            manager,
            idp,
            provisioning="auto",
            default_role="viewer",
            group_sync=True,
            group_mappings=[
                {"group": "noust-admins", "role": "admin"},
                {"group": "noust-ops", "role": "operator"},
                {"group": "acme-ops", "team": "acme", "team_role": "operator"},
                {"group": "acme-owners", "team": "acme", "team_role": "owner"},
                {"group": "globex", "team": "globex", "team_role": "viewer"},
            ],
        )

    def test_groups_give_a_role_and_teams_on_the_first_sign_in(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        answer = run(manager, idp, groups=["noust-ops", "acme-ops"])

        assert answer.account.role == "operator"
        teams = {t.slug: role for t, role in TeamStore(accounts.store).teams_of(answer.account.id)}
        assert teams["acme"] == "operator"
        assert any("role viewer to operator" in note for note in answer.notes)

    def test_the_first_role_mapping_that_matches_decides(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        assert run(manager, idp, groups=["noust-ops", "noust-admins"]).account.role == "admin"
        # (admins is listed first, so it wins whichever order the token lists them in)
        assert run(manager, idp, groups=["noust-admins", "noust-ops"]).account.role == "admin"

    def test_the_strongest_team_role_of_the_groups_wins(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        account = run(manager, idp, groups=["acme-ops", "acme-owners"]).account

        roles = {t.slug: role for t, role in TeamStore(accounts.store).teams_of(account.id)}
        assert roles["acme"] == "owner"

    def test_leaving_a_group_at_the_provider_takes_back_what_it_gave(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        run(manager, idp, groups=["noust-admins", "acme-ops", "globex"])
        assert accounts.require("maria").role == "admin"

        again = run(manager, idp, groups=["globex"])

        assert again.account.role == "viewer"
        teams = {t.slug for t, _ in TeamStore(accounts.store).teams_of(again.account.id)}
        assert "acme" not in teams and "globex" in teams

    def test_a_role_an_administrator_set_by_hand_is_not_taken_back(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        run(manager, idp, groups=["noust-ops"])
        accounts.set_role("maria", "admin")  # an administrator decided

        assert run(manager, idp, groups=[]).account.role == "admin"

    def test_a_membership_an_administrator_added_is_left_alone(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        account = run(manager, idp, groups=[]).account
        teams = TeamStore(accounts.store)
        teams.add_member("globex", "maria", "operator", created_by="cli:root")

        run(manager, idp, groups=["acme-ops"])
        run(manager, idp, groups=[])

        roles = {t.slug: role for t, role in teams.teams_of(account.id)}
        assert roles.get("globex") == "operator" and "acme" not in roles

    def test_groups_that_are_not_mapped_give_nothing(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        assert run(manager, idp, groups=["everyone", "random"]).account.role == "viewer"

    def test_the_groups_claim_can_be_nested(self, manager: OidcManager, idp: FakeIdP) -> None:
        manager.configure("corp", {"groups_claim": "realm_access>roles"})

        answer = run(manager, idp, realm_access={"roles": ["noust-ops"]})

        assert answer.account.role == "operator"

    def test_a_single_group_written_as_a_string_is_one_group(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        assert run(manager, idp, groups="noust-ops").account.role == "operator"

    def test_a_security_officer_is_never_demoted_by_a_group(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        secure(accounts, "sofia", "security")
        manager.link("corp", "sofia", "sub-sofia")

        answer = run(
            manager,
            idp,
            sub="sub-sofia",
            groups=["noust-ops"],
            email="sofia@acme.test",
            email_verified=True,
        )

        assert answer.account.role == "security"

    def test_the_groups_do_nothing_while_the_sync_is_off(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        manager.configure("corp", {"group_sync": False})

        assert run(manager, idp, groups=["noust-admins"]).account.role == "viewer"

    def test_groups_that_would_break_separation_of_duties_refuse_the_sign_in(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        secure(accounts, "maria.sec", "security", person_ref="maria@acme.test")
        manager.configure("corp", {"link_by_email": False})

        # The new account of the same person would be an admin next to a security account.
        with pytest.raises(OidcRefused) as caught:
            run(manager, idp, groups=["noust-admins"])
        assert caught.value.reason in {"role_conflict", "email_taken"}

    def test_the_ens_profile_never_lets_a_group_make_an_admin(
        self, store: NoustStore, idp: FakeIdP, clock: Clock, accounts: AccountManager
    ) -> None:
        manager = OidcManager(
            store,
            client=OidcClient(OidcHttp(opener=idp.opener), clock=clock),
            accounts=AccountManager(store, policy=AuthPolicy(profile="ens-medium"), clock=clock),
            clock=clock,
        )
        manager.configure(
            "corp",
            {
                "issuer": idp.issuer,
                "client_id": idp.client_id,
                "provisioning": "auto",
                "group_sync": True,
                "group_mappings": [{"group": "noust-ops", "role": "operator"}],
            },
            client_secret=idp.client_secret,
        )
        # Written before the profile was turned on: an old row that gives admin.
        store._get_connection().execute(
            "UPDATE oidc_providers SET group_mappings = ?",
            ('[{"group": "noust-admins", "role": "admin"}]',),
        )
        store._get_connection().commit()

        answer = run(manager, idp, groups=["noust-admins"])

        assert answer.account.role == "viewer"
        assert any("ENS" in note for note in answer.notes)


class TestTheTicket:
    @pytest.fixture(autouse=True)
    def ready(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        configure(manager, idp)
        secure(accounts, "maria")
        manager.link("corp", "maria", "sub-maria")

    def test_a_ticket_is_collected_once(self, manager: OidcManager, idp: FakeIdP) -> None:
        answer = run(manager, idp)
        manager.collect(answer.ticket, _binding(answer), IP)

        with pytest.raises(OidcError) as caught:
            manager.collect(answer.ticket, _binding(answer), IP)
        assert caught.value.reason == "ticket"

    def test_another_browser_cannot_collect_it_and_cannot_burn_it(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        answer = run(manager, idp)

        with pytest.raises(OidcError) as caught:
            manager.collect(answer.ticket, "somebody-elses-cookie", IP)
        assert caught.value.reason == "binding"
        manager.collect(answer.ticket, _binding(answer), IP)  # still the owner's

    def test_another_address_cannot_collect_it(self, manager: OidcManager, idp: FakeIdP) -> None:
        answer = run(manager, idp)

        with pytest.raises(OidcError) as caught:
            manager.collect(answer.ticket, _binding(answer), "198.51.100.77")
        assert caught.value.reason == "address"

    def test_a_ticket_expires(self, manager: OidcManager, idp: FakeIdP, clock: Clock) -> None:
        answer = run(manager, idp)
        clock.advance(121)

        with pytest.raises(OidcError):
            manager.collect(answer.ticket, _binding(answer), IP)

    def test_an_account_disabled_meanwhile_is_not_let_in(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        answer = run(manager, idp)
        accounts.disable("maria")

        with pytest.raises(OidcError) as caught:
            manager.collect(answer.ticket, _binding(answer), IP)
        assert caught.value.reason == "account"

    def test_the_ticket_is_stored_as_a_digest(
        self, manager: OidcManager, idp: FakeIdP, store: NoustStore
    ) -> None:
        answer = run(manager, idp)
        rows = store._get_connection().execute("SELECT * FROM oidc_tickets").fetchall()
        assert answer.ticket not in repr([tuple(row) for row in rows])
        flows = store._get_connection().execute("SELECT * FROM oidc_flows").fetchall()
        assert flows == []  # spent by the answer


class TestTheProviderTest:
    def test_a_good_provider_passes_every_check(self, manager: OidcManager, idp: FakeIdP) -> None:
        configure(manager, idp)

        checks = manager.test("corp", redirect_uri=REDIRECT)

        assert all(check.ok for check in checks), [c.to_dict() for c in checks]
        assert {check.name for check in checks} >= {"discovery", "signing keys", "client secret"}

    def test_an_unreachable_provider_is_a_failed_check_not_an_error(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        configure(manager, idp)
        idp.offline = True

        checks = manager.test("corp")

        assert checks[0].name == "discovery" and checks[0].ok is False

    def test_a_wrong_issuer_says_what_to_set(self, manager: OidcManager, idp: FakeIdP) -> None:
        manager.configure("corp", {"issuer": idp.issuer + "/", "client_id": "x"})

        checks = manager.test("corp")

        assert checks[0].ok is False and "exactly" in checks[0].detail


class TestSafeNext:
    @pytest.mark.parametrize(
        "given",
        [
            None,
            "",
            "apps",
            "//evil.test",
            "/\\evil.test",
            "https://evil.test/",
            "/a\nb",
            "/api/apps",
            "/assets/x.js",
            "/" + "a" * 600,
            "javascript:alert(1)",
        ],
    )
    def test_anything_but_a_page_of_this_console_is_home(self, given: str | None) -> None:
        assert safe_next(given) == "/"

    @pytest.mark.parametrize("given", ["/", "/apps", "/apps/shop?tab=logs", "/n/web-2/apps"])
    def test_a_page_of_the_console_is_kept(self, given: str) -> None:
        assert safe_next(given) == given


def test_the_secret_directory_is_private(
    manager: OidcManager, idp: FakeIdP, store: NoustStore
) -> None:
    configure(manager, idp)
    directory = store.db_path.parent / "secrets"
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
