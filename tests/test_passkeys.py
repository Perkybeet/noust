"""
Passkeys in the store: relying party, challenges, registration, sign-in, ownership.

:class:`~noust.core.accounts.passkeys.PasskeyManager` is the one implementation
of a passkey: the console's endpoints and ``noust passkey`` call it. These
tests drive it with a software authenticator (real ES256 and RS256
signatures) against a real store, and pin the rules the spec (§2.4) and the
research (``passkeys.md`` §5) decided: a random ``user.id`` per server and
owner, single-use challenges bound to their purpose and session, a counter
that may never go back, a passkey that counts as a second factor, and an
account that may not remove its last one.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")

from noust.core import totp
from noust.core.accounts import AccountManager, AuthenticationFailed, passwords
from noust.core.accounts.model import AccountError
from noust.core.accounts.passkeys import (
    CHALLENGE_SECONDS,
    PasskeyManager,
    PasskeyOwner,
    PasskeysUnavailable,
    RelyingParty,
    resolve_relying_party,
)
from noust.core.accounts.webauthn import (
    ALG_RS256,
    WebAuthnError,
    b64url_decode,
)
from noust.core.store import NoustStore
from tests.webauthn_authenticator import SoftwareAuthenticator

PASSWORD = "correct horse battery staple"
RP = RelyingParty(id="localhost", origins=("http://localhost:8080",))
ORIGIN = "http://localhost:8080"
MASTER = PasskeyOwner(account_id=None, username="master", display_name="")


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


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def manager(store: NoustStore, clock: Clock) -> PasskeyManager:
    return PasskeyManager(store, clock=clock, allow_synced=True, hostname="web1")


@pytest.fixture
def accounts(store: NoustStore) -> AccountManager:
    return AccountManager(store)


def owner_of(accounts: AccountManager, name: str = "maria", role: str = "admin") -> PasskeyOwner:
    account = accounts.find(name) or accounts.create(name, role, password=PASSWORD)
    return PasskeyOwner(account_id=account.id, username=account.username, display_name="")


def enrol(
    manager: PasskeyManager,
    owner: PasskeyOwner,
    authenticator: SoftwareAuthenticator | None = None,
    *,
    name: str = "Laptop",
    binding: str = "session:a",
) -> tuple[SoftwareAuthenticator, Any]:
    authenticator = authenticator or SoftwareAuthenticator()
    options = manager.registration_options(owner, RP, binding=binding)
    response = authenticator.create(options, ORIGIN)
    registration = manager.register(
        owner, RP, response, name=name, binding=binding, created_by="test"
    )
    return authenticator, registration


def sign_in(manager: PasskeyManager, authenticator: SoftwareAuthenticator, **bend: Any) -> Any:
    options = manager.authentication_options(RP, purpose="login", binding="")
    response = authenticator.get(options, ORIGIN, **bend)
    return manager.authenticate(
        RP, response, purpose="login", binding="", client_ip="10.0.0.9", any_owner=True
    )


class TestRelyingParty:
    def test_localhost_over_http_is_allowed(self) -> None:
        rp = resolve_relying_party("localhost:8080", secure=False)
        assert rp.id == "localhost"
        assert rp.origins == ("http://localhost:8080",)

    def test_a_name_over_https(self) -> None:
        rp = resolve_relying_party("Noust.Example.com", secure=True)
        assert rp.id == "noust.example.com"
        assert rp.origins == ("https://noust.example.com",)

    @pytest.mark.parametrize("host", ["127.0.0.1:8080", "192.168.1.5", "[::1]:8443", "[fe80::1]"])
    def test_an_address_is_refused_with_the_reason(self, host: str) -> None:
        with pytest.raises(PasskeysUnavailable) as caught:
            resolve_relying_party(host, secure=True)
        assert caught.value.reason == "ip_address"
        assert "localhost" in caught.value.details

    def test_an_address_names_the_public_url_when_there_is_one(self) -> None:
        with pytest.raises(PasskeysUnavailable) as caught:
            resolve_relying_party(
                "10.0.0.5:8443", secure=True, public_url="https://noust.example.com"
            )
        assert "https://noust.example.com" in caught.value.details

    def test_plain_http_to_a_name_is_refused(self) -> None:
        with pytest.raises(PasskeysUnavailable) as caught:
            resolve_relying_party("nas.lan:8080", secure=False)
        assert caught.value.reason == "insecure_context"

    def test_a_localhost_subdomain_is_a_secure_context(self) -> None:
        assert resolve_relying_party("web1.localhost:8080", secure=False).id == "web1.localhost"

    @pytest.mark.parametrize("host", ["", "bad host", "-x.example.com", "a..b"])
    def test_a_host_that_is_not_a_name_is_refused(self, host: str) -> None:
        with pytest.raises(PasskeysUnavailable) as caught:
            resolve_relying_party(host, secure=True)
        assert caught.value.reason == "invalid_host"

    def test_a_configured_rp_id_covers_its_subdomains(self) -> None:
        rp = resolve_relying_party(
            "console.example.com", secure=True, rp_id="example.com", origins=None
        )
        assert rp.id == "example.com"
        assert rp.origins == ("https://console.example.com",)

    def test_a_host_outside_the_configured_rp_id_is_refused(self) -> None:
        with pytest.raises(PasskeysUnavailable) as caught:
            resolve_relying_party("other.test", secure=True, rp_id="example.com")
        assert caught.value.reason == "host_mismatch"

    def test_configured_origins_replace_the_derived_one(self) -> None:
        rp = resolve_relying_party(
            "noust.example.com",
            secure=True,
            rp_id="noust.example.com",
            origins=["https://noust.example.com:8443"],
        )
        assert rp.origins == ("https://noust.example.com:8443",)

    def test_the_public_url_is_an_origin_of_its_own_host(self) -> None:
        rp = resolve_relying_party(
            "noust.example.com", secure=True, public_url="https://noust.example.com:8443/"
        )
        assert set(rp.origins) == {"https://noust.example.com", "https://noust.example.com:8443"}


class TestRegistration:
    def test_a_passkey_is_registered_and_listed(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        authenticator, registration = enrol(manager, owner)

        passkey = registration.passkey
        assert passkey.name == "Laptop"
        assert passkey.account_id == owner.account_id
        assert passkey.rp_id == "localhost"
        assert passkey.synced is False
        assert [item.id for item in manager.list(owner.account_id)] == [passkey.id]
        assert passkey.credential_id == authenticator.credential_id

    def test_options_ask_for_a_discoverable_verified_passkey(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        options = manager.registration_options(owner_of(accounts), RP, binding="session:a")

        assert options["authenticatorSelection"]["residentKey"] == "required"
        assert options["authenticatorSelection"]["userVerification"] == "required"
        assert options["attestation"] == "none"
        assert options["rp"] == {"id": "localhost", "name": "Noust"}
        assert options["user"]["name"] == "maria@web1"
        assert options["pubKeyCredParams"][0]["alg"] == -7
        assert -257 in [param["alg"] for param in options["pubKeyCredParams"]]

    def test_the_user_id_is_random_per_owner_and_kept_for_the_next(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        maria = owner_of(accounts)
        first = manager.registration_options(maria, RP, binding="session:a")["user"]["id"]
        other = manager.registration_options(owner_of(accounts, "juan"), RP, binding="s")["user"][
            "id"
        ]
        assert len(b64url_decode(first)) == 32
        assert first != other
        assert first != manager.registration_options(maria, RP, binding="session:a")["user"]["id"]

        _authenticator, registration = enrol(manager, maria)
        again = manager.registration_options(maria, RP, binding="session:a")
        assert again["user"]["id"] == manager.user_handle_b64(registration.passkey)
        assert again["excludeCredentials"][0]["id"] == manager.credential_id_b64(
            registration.passkey
        )

    def test_the_same_passkey_twice_is_refused(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        authenticator, _ = enrol(manager, owner)
        with pytest.raises(AccountError):
            enrol(manager, owner, authenticator)

    def test_a_challenge_serves_once(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        options = manager.registration_options(owner, RP, binding="session:a")
        manager.register(
            owner,
            RP,
            SoftwareAuthenticator().create(options, ORIGIN),
            name="A",
            binding="session:a",
            created_by="t",
        )
        with pytest.raises(WebAuthnError) as caught:
            manager.register(
                owner,
                RP,
                SoftwareAuthenticator().create(options, ORIGIN),
                name="B",
                binding="session:a",
                created_by="t",
            )
        assert caught.value.reason == "challenge"

    def test_a_challenge_expires(
        self, manager: PasskeyManager, accounts: AccountManager, clock: Clock
    ) -> None:
        owner = owner_of(accounts)
        options = manager.registration_options(owner, RP, binding="session:a")
        clock.now += CHALLENGE_SECONDS + 1
        with pytest.raises(WebAuthnError) as caught:
            manager.register(
                owner,
                RP,
                SoftwareAuthenticator().create(options, ORIGIN),
                name="A",
                binding="session:a",
                created_by="t",
            )
        assert caught.value.reason == "challenge"

    def test_a_challenge_is_bound_to_its_session(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        options = manager.registration_options(owner, RP, binding="session:a")
        with pytest.raises(WebAuthnError) as caught:
            manager.register(
                owner,
                RP,
                SoftwareAuthenticator().create(options, ORIGIN),
                name="A",
                binding="session:b",
                created_by="t",
            )
        assert caught.value.reason == "challenge"

    def test_a_sign_in_challenge_cannot_register(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        login = manager.authentication_options(RP, purpose="login", binding="session:a")
        options = manager.registration_options(owner, RP, binding="session:a")
        options["challenge"] = login["challenge"]
        with pytest.raises(WebAuthnError) as caught:
            manager.register(
                owner,
                RP,
                SoftwareAuthenticator().create(options, ORIGIN),
                name="A",
                binding="session:a",
                created_by="t",
            )
        assert caught.value.reason == "challenge"

    def test_another_server_key_does_not_recognise_the_challenge(
        self, store: NoustStore, accounts: AccountManager, clock: Clock
    ) -> None:
        owner = owner_of(accounts)
        issuing = PasskeyManager(store, clock=clock, hostname="web1")
        other = PasskeyManager(store, clock=clock, hostname="web1", challenge_key=b"k" * 32)
        options = issuing.registration_options(owner, RP, binding="s")
        with pytest.raises(WebAuthnError):
            other.register(
                owner,
                RP,
                SoftwareAuthenticator().create(options, ORIGIN),
                name="A",
                binding="s",
                created_by="t",
            )

    @pytest.mark.parametrize("name", ["", "   ", "x" * 65, "tab\there"])
    def test_names_are_checked(
        self, manager: PasskeyManager, accounts: AccountManager, name: str
    ) -> None:
        with pytest.raises(Exception, match=r"(?i)name"):
            enrol(manager, owner_of(accounts), name=name)

    def test_synced_passkeys_can_be_refused(
        self, store: NoustStore, accounts: AccountManager, clock: Clock
    ) -> None:
        strict = PasskeyManager(store, clock=clock, allow_synced=False, hostname="web1")
        with pytest.raises(AccountError, match="synced"):
            enrol(strict, owner_of(accounts), SoftwareAuthenticator(backup_eligible=True))

    def test_the_first_passkey_of_an_account_brings_backup_codes(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        _authenticator, first = enrol(manager, owner)
        _authenticator, second = enrol(manager, owner, name="Phone")

        assert first.backup_codes is not None and len(first.backup_codes) == 8
        assert second.backup_codes is None

    def test_the_master_token_has_passkeys_of_its_own(self, manager: PasskeyManager) -> None:
        _authenticator, registration = enrol(manager, MASTER)
        assert registration.passkey.account_id is None
        assert registration.backup_codes is None
        assert manager.count(None) == 1


class TestSignIn:
    def test_a_discoverable_sign_in_names_the_owner(
        self, manager: PasskeyManager, accounts: AccountManager, clock: Clock
    ) -> None:
        owner = owner_of(accounts)
        authenticator, _ = enrol(manager, owner)
        clock.now += 60

        passkey = sign_in(manager, authenticator)

        assert passkey.account_id == owner.account_id
        assert passkey.last_used_at == clock.now
        assert passkey.last_used_ip == "10.0.0.9"
        assert passkey.sign_count == 1

    def test_rs256_signs_in(self, manager: PasskeyManager, accounts: AccountManager) -> None:
        authenticator, _ = enrol(manager, owner_of(accounts), SoftwareAuthenticator(ALG_RS256))
        assert sign_in(manager, authenticator).sign_count == 1

    def test_the_options_hold_nothing_about_anybody(self, manager: PasskeyManager) -> None:
        options = manager.authentication_options(RP, purpose="login", binding="")
        assert options["allowCredentials"] == []
        assert options["userVerification"] == "required"
        assert options["rpId"] == "localhost"

    def test_a_tampered_signature_is_refused(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        authenticator, _ = enrol(manager, owner_of(accounts))
        with pytest.raises(WebAuthnError) as caught:
            sign_in(manager, authenticator, tamper_signature=True)
        assert caught.value.reason == "signature"

    def test_a_counter_that_goes_back_is_recorded_as_a_possible_clone(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        authenticator, registration = enrol(manager, owner_of(accounts))
        sign_in(manager, authenticator)
        sign_in(manager, authenticator)
        with pytest.raises(WebAuthnError) as caught:
            sign_in(manager, authenticator, sign_count=1)
        assert caught.value.reason == "counter"
        passkey = manager.get(registration.passkey.id)
        assert passkey is not None and passkey.clone_warning_at is not None
        assert passkey.sign_count == 2

    def test_a_passkey_of_another_server_is_unknown(self, manager: PasskeyManager) -> None:
        stranger = SoftwareAuthenticator()
        stranger.rp_id = "localhost"
        stranger.user_handle = b"x" * 32
        with pytest.raises(WebAuthnError) as caught:
            sign_in(manager, stranger)
        assert caught.value.reason == "unknown_credential"

    def test_confirming_takes_the_account_s_own_passkey(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        maria = owner_of(accounts)
        juan = owner_of(accounts, "juan")
        maria_key, _ = enrol(manager, maria)
        enrol(manager, juan, binding="session:j")

        options = manager.authentication_options(
            RP, purpose="elevate", binding="session:j", account_id=juan.account_id
        )
        assert len(options["allowCredentials"]) == 1
        response = maria_key.get(options, ORIGIN)
        with pytest.raises(WebAuthnError) as caught:
            manager.authenticate(
                RP,
                response,
                purpose="elevate",
                binding="session:j",
                client_ip="x",
                account_id=juan.account_id,
            )
        assert caught.value.reason == "wrong_owner"

    def test_a_confirmation_challenge_does_not_sign_in(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        authenticator, _ = enrol(manager, owner)
        options = manager.authentication_options(
            RP, purpose="elevate", binding="session:a", account_id=owner.account_id
        )
        response = authenticator.get(options, ORIGIN)
        with pytest.raises(WebAuthnError) as caught:
            manager.authenticate(
                RP, response, purpose="login", binding="", client_ip="x", any_owner=True
            )
        assert caught.value.reason == "challenge"


class TestManagement:
    def test_rename_and_remove_are_the_owner_s(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        maria = owner_of(accounts)
        juan = owner_of(accounts, "juan")
        _key, first = enrol(manager, maria)
        enrol(manager, maria, name="Phone")

        assert manager.rename(
            first.passkey.id, "Work laptop", account_id=maria.account_id
        ).name == ("Work laptop")
        with pytest.raises(AccountError):
            manager.rename(first.passkey.id, "Mine now", account_id=juan.account_id)
        with pytest.raises(AccountError):
            manager.remove(first.passkey.id, account_id=juan.account_id)
        manager.remove(first.passkey.id, account_id=maria.account_id)
        assert manager.count(maria.account_id) == 1

    def test_an_account_cannot_remove_its_only_second_factor(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        _key, registration = enrol(manager, owner)
        with pytest.raises(AccountError, match="only second factor"):
            manager.remove(registration.passkey.id, account_id=owner.account_id)

    def test_an_account_with_an_authenticator_can_remove_its_last_passkey(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        secret = accounts.begin_totp(owner.account_id or 0)
        accounts.confirm_totp(owner.account_id or 0, totp.totp_now(secret))
        _key, registration = enrol(manager, owner)
        manager.remove(registration.passkey.id, account_id=owner.account_id)
        assert manager.count(owner.account_id) == 0

    def test_the_master_token_may_remove_its_last(self, manager: PasskeyManager) -> None:
        _key, registration = enrol(manager, MASTER)
        manager.remove(registration.passkey.id, account_id=None)
        assert manager.count(None) == 0

    def test_reset_removes_every_passkey_of_one_owner(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        maria = owner_of(accounts)
        enrol(manager, maria)
        enrol(manager, maria, name="Phone")
        enrol(manager, MASTER)

        assert manager.reset(maria.account_id) == 2
        assert manager.count(maria.account_id) == 0
        assert manager.count(None) == 1

    def test_removing_an_account_removes_its_passkeys(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        enrol(manager, owner)
        accounts.remove("maria")
        assert manager.list_all() == []

    def test_the_list_names_every_owner(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        enrol(manager, owner_of(accounts))
        enrol(manager, MASTER)
        owners = sorted(item.to_dict()["owner"] for item in manager.list_all())
        assert owners == ["maria", "master"]


class TestSecondFactor:
    def test_a_passkey_is_an_account_s_second_factor(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        assert not accounts.require("maria").has_mfa

        _key, registration = enrol(manager, owner)

        account = accounts.require("maria")
        assert account.has_mfa
        assert account.passkeys == 1
        assert account.to_dict()["passkeys"] == 1
        assert registration.backup_codes is not None

    def test_a_password_alone_no_longer_signs_a_passkey_account_in(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        _key, registration = enrol(manager, owner_of(accounts))
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate("maria", PASSWORD, None, client_ip="x")
        assert caught.value.reason == "code_required"

        codes = registration.backup_codes or []
        assert accounts.authenticate("maria", PASSWORD, codes[0], client_ip="x").username == "maria"

    def test_resetting_the_second_factor_removes_the_passkeys(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        enrol(manager, owner_of(accounts))
        accounts.reset_mfa("maria")
        assert manager.count(accounts.require("maria").id) == 0
        assert not accounts.require("maria").has_mfa

    def test_backup_codes_can_be_renewed_with_only_a_passkey(
        self, manager: PasskeyManager, accounts: AccountManager
    ) -> None:
        owner = owner_of(accounts)
        enrol(manager, owner)
        assert len(accounts.regenerate_backup_codes(owner.account_id or 0)) == 8


class TestAvailability:
    def test_a_missing_library_is_reported(
        self, manager: PasskeyManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.accounts import passkeys, webauthn

        def missing() -> Any:
            raise webauthn.WebAuthnUnavailable("no", details="Install python3-cryptography")

        monkeypatch.setattr(passkeys, "load_crypto", missing)
        with pytest.raises(PasskeysUnavailable) as caught:
            passkeys.require_library()
        assert caught.value.reason == "library_missing"
        assert "python3-cryptography" in caught.value.details
