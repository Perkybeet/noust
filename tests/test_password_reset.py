# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An account's address, and a password reset by mail (checklist I, K).

The address is where an invitation or a reset link is mailed and nothing else: it never signs
anybody in. A reset link is single-use and expires, only the digest of its code is stored, a new
request spends the ones before it, and finishing one needs the account's second factor as well,
so an inbox alone is not an account.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import totp
from noust.core.accounts import (
    AccountError,
    AccountManager,
    AuthenticationFailed,
    AuthPolicy,
    passwords,
)
from noust.core.accounts.reset import PasswordResets, ResetRefused
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different long passphrase 42"


class Clock:
    def __init__(self, start: float = 1_900_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


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
def accounts(store: NoustStore, clock: Clock) -> AccountManager:
    return AccountManager(store, policy=AuthPolicy(), clock=clock)


@pytest.fixture
def resets(accounts: AccountManager, clock: Clock) -> PasswordResets:
    return PasswordResets(accounts, clock=clock)


def _with_authenticator(accounts: AccountManager, username: str) -> str:
    """Give an account a confirmed authenticator; return its secret."""
    account = accounts.require(username)
    secret = accounts.begin_totp(account.id)
    code = totp.totp_now(secret, t=accounts.now())
    assert accounts.confirm_totp(account.id, code) is not None
    return secret


def _code(accounts: AccountManager, secret: str, ahead: int = 0) -> str:
    return totp.totp_now(secret, t=accounts.now() + 30 * ahead)


@pytest.fixture
def maria(accounts: AccountManager) -> str:
    accounts.create("maria", "operator", password=PASSWORD)
    accounts.set_email("maria", "Maria@Example.COM")
    return _with_authenticator(accounts, "maria")


class TestTheAddress:
    def test_it_is_stored_lower_case_and_shown_back(self, accounts: AccountManager) -> None:
        accounts.create("ana", "viewer", password=PASSWORD)

        account = accounts.set_email("ana", " Ana@Example.COM ")

        assert account.email == "ana@example.com"
        assert account.to_dict()["email"] == "ana@example.com"

    @pytest.mark.parametrize(
        "bad",
        [
            "nobody",
            "a@b",
            "a b@example.com",
            "a@example.com\r\nBcc: x@y.z",
            "@example.com",
            "a" * 250 + "@x.com",
        ],
    )
    def test_what_is_not_an_address_is_refused(self, accounts: AccountManager, bad: str) -> None:
        accounts.create("ana", "viewer", password=PASSWORD)

        with pytest.raises(ValidationError, match="address"):
            accounts.set_email("ana", bad)

    def test_an_empty_address_clears_it(self, accounts: AccountManager) -> None:
        accounts.create("ana", "viewer", password=PASSWORD)
        accounts.set_email("ana", "ana@example.com")

        assert accounts.set_email("ana", "").email is None

    def test_accounts_are_found_by_it_in_any_case(self, accounts: AccountManager) -> None:
        accounts.create("ana", "viewer", password=PASSWORD)
        accounts.set_email("ana", "ana@example.com")

        assert [a.username for a in accounts.find_by_email("ANA@example.com")] == ["ana"]
        assert accounts.find_by_email("nobody@example.com") == []
        assert accounts.find_by_email("not an address") == []

    def test_an_invitation_remembers_where_it_was_sent(self, accounts: AccountManager) -> None:
        account, _code_ = accounts.invite("pablo", "viewer", email="pablo@example.com")

        assert account.email == "pablo@example.com"

    def test_an_account_can_be_made_with_one(self, accounts: AccountManager) -> None:
        account = accounts.create("eva", "viewer", password=PASSWORD, email="eva@example.com")

        assert account.email == "eva@example.com"

    def test_it_is_never_a_way_in(self, accounts: AccountManager) -> None:
        accounts.create("ana", "viewer", password=PASSWORD)
        accounts.set_email("ana", "ana@example.com")

        with pytest.raises(AuthenticationFailed):
            accounts.authenticate_password("ana@example.com", PASSWORD, client_ip="203.0.113.5")


class TestRequestingAReset:
    def test_a_known_account_with_an_address_gets_a_code(
        self, resets: PasswordResets, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")

        assert issued is not None
        assert issued.account.username == "maria"
        assert issued.address == "maria@example.com"
        assert issued.code.startswith("noust_reset_")

    def test_it_can_be_asked_for_by_the_address(self, resets: PasswordResets, maria: str) -> None:
        issued = resets.request("MARIA@example.com", client_ip="203.0.113.5")

        assert issued is not None and issued.account.username == "maria"

    @pytest.mark.parametrize("who", ["ghost", "", "x@y.z"])
    def test_an_unknown_name_gets_nothing(self, resets: PasswordResets, who: str) -> None:
        assert resets.request(who, client_ip="203.0.113.5") is None

    def test_an_account_with_no_address_gets_nothing(
        self, resets: PasswordResets, accounts: AccountManager
    ) -> None:
        accounts.create("noaddr", "viewer", password=PASSWORD)

        assert resets.request("noaddr", client_ip="203.0.113.5") is None

    def test_a_disabled_account_gets_nothing(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        accounts.disable("maria", "left")

        assert resets.request("maria", client_ip="203.0.113.5") is None

    def test_an_account_that_has_not_accepted_its_invitation_gets_nothing(
        self, resets: PasswordResets, accounts: AccountManager
    ) -> None:
        accounts.invite("pablo", "viewer", email="pablo@example.com")

        assert resets.request("pablo", client_ip="203.0.113.5") is None

    def test_only_the_digest_of_the_code_is_stored(
        self, resets: PasswordResets, store: NoustStore, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None

        rows = (
            store._get_connection()
            .execute("SELECT code_hash FROM account_password_resets")
            .fetchall()
        )

        assert len(rows) == 1
        assert issued.code not in rows[0][0]
        assert len(rows[0][0]) == 64

    def test_a_new_request_spends_the_earlier_code(
        self, resets: PasswordResets, accounts: AccountManager, clock: Clock, maria: str
    ) -> None:
        first = resets.request("maria", client_ip="203.0.113.5")
        clock.now += 60
        second = resets.request("maria", client_ip="203.0.113.5")
        assert first is not None and second is not None

        with pytest.raises(ResetRefused):
            resets.redeem(first.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5")
        resets.redeem(second.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5")

    def test_an_account_is_not_flooded_with_links(
        self, resets: PasswordResets, clock: Clock, maria: str
    ) -> None:
        issued = []
        for _ in range(5):
            clock.now += 10
            issued.append(resets.request("maria", client_ip="203.0.113.5"))

        assert sum(1 for item in issued if item is not None) == 3
        clock.now += 3700
        assert resets.request("maria", client_ip="203.0.113.5") is not None


class TestFinishingOne:
    def test_the_code_and_the_authenticator_set_a_new_password(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None

        account = resets.redeem(
            issued.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5"
        )

        assert account.username == "maria"
        assert accounts.verify_password(account.id, NEW_PASSWORD, client_ip="203.0.113.5")
        assert not accounts.verify_password(account.id, PASSWORD, client_ip="203.0.113.5")

    def test_a_code_is_good_once(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None
        resets.redeem(issued.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5")

        with pytest.raises(ResetRefused):
            resets.redeem(
                issued.code,
                "yet another passphrase 77",
                _code(accounts, maria),
                client_ip="203.0.113.5",
            )

    def test_a_code_expires(
        self, resets: PasswordResets, accounts: AccountManager, clock: Clock, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None
        clock.now += 61 * 60

        with pytest.raises(ResetRefused):
            resets.redeem(
                issued.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5"
            )

    def test_the_inbox_alone_is_not_enough(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None

        with pytest.raises(AuthenticationFailed):
            resets.redeem(issued.code, NEW_PASSWORD, "000000", client_ip="203.0.113.5")

        account = accounts.require("maria")
        assert accounts.verify_password(account.id, PASSWORD, client_ip="203.0.113.5")

    def test_a_wrong_authenticator_code_does_not_spend_the_link(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None
        with pytest.raises(AuthenticationFailed):
            resets.redeem(issued.code, NEW_PASSWORD, "000000", client_ip="203.0.113.5")

        resets.redeem(issued.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5")

    def test_a_backup_code_will_do(self, resets: PasswordResets, accounts: AccountManager) -> None:
        accounts.create("lia", "viewer", password=PASSWORD)
        accounts.set_email("lia", "lia@example.com")
        account = accounts.require("lia")
        secret = accounts.begin_totp(account.id)
        backup = accounts.confirm_totp(account.id, totp.totp_now(secret, t=accounts.now()))
        assert backup
        issued = resets.request("lia", client_ip="203.0.113.5")
        assert issued is not None

        resets.redeem(issued.code, NEW_PASSWORD, backup[0], client_ip="203.0.113.5")

    def test_the_password_must_meet_the_policy_and_the_link_survives_a_refusal(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None

        with pytest.raises(ValidationError):
            resets.redeem(issued.code, "short", _code(accounts, maria), client_ip="203.0.113.5")

        resets.redeem(issued.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5")

    def test_an_account_with_no_second_factor_cannot_use_a_link(
        self, resets: PasswordResets, accounts: AccountManager
    ) -> None:
        accounts.create("raw", "viewer", password=PASSWORD)
        accounts.set_email("raw", "raw@example.com")

        # Nothing to prove the person with: the link is not even issued.
        assert resets.request("raw", client_ip="203.0.113.5") is None

    def test_an_unknown_code_is_refused_in_one_way(
        self, resets: PasswordResets, accounts: AccountManager
    ) -> None:
        with pytest.raises(ResetRefused):
            resets.redeem("noust_reset_nonsense", NEW_PASSWORD, "123456", client_ip="203.0.113.5")

    def test_an_account_disabled_after_the_request_cannot_finish(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None
        accounts.disable("maria", "left")

        with pytest.raises(ResetRefused):
            resets.redeem(
                issued.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5"
            )

    def test_it_is_not_the_invitation_that_replaces_the_authenticator(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None
        resets.redeem(issued.code, NEW_PASSWORD, _code(accounts, maria), client_ip="203.0.113.5")

        assert accounts.require("maria").totp_enabled is True

    def test_a_reset_code_is_not_an_invitation_code(
        self, resets: PasswordResets, accounts: AccountManager, maria: str
    ) -> None:
        issued = resets.request("maria", client_ip="203.0.113.5")
        assert issued is not None

        with pytest.raises(AuthenticationFailed):
            accounts.open_invitation(issued.code)

    def test_an_error_is_an_account_error_family_member(self) -> None:
        assert issubclass(ResetRefused, AccountError)
