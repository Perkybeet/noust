# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Accounts' TOTP secrets are encrypted in the store (ENS G22, op.exp.10).

A copy of ``noust.db`` - in a backup, an incident package, a support bundle -
must not hand over every account's second factor. The key lives beside the
store in a file of its own, so the database file alone discloses nothing;
secrets written by an earlier 3.1 build in clear are sealed on first use and
by the sweep the console runs at start.
"""

from __future__ import annotations

import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.store import NoustStore

PASSWORD = "correct horse battery staple"


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


def raw(store: NoustStore, account_id: int) -> tuple[str | None, str | None]:
    connection = sqlite3.connect(store.db_path)
    try:
        row = connection.execute(
            "SELECT totp_secret, totp_pending_secret FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    finally:
        connection.close()
    return row[0], row[1]


def write_raw(store: NoustStore, account_id: int, secret: str) -> None:
    connection = sqlite3.connect(store.db_path)
    try:
        connection.execute("UPDATE accounts SET totp_secret = ? WHERE id = ?", (secret, account_id))
        connection.commit()
    finally:
        connection.close()


class TestTheConstruction:
    def test_a_sealed_secret_opens_to_itself(self) -> None:
        key = b"k" * 32
        secret = totp.generate_secret()

        sealed = totp.seal_secret(secret, key, "account:1")

        assert sealed.startswith(totp.SEALED_PREFIX)
        assert secret not in sealed
        assert totp.open_secret(sealed, key, "account:1") == secret

    def test_two_seals_of_one_secret_differ(self) -> None:
        key = b"k" * 32

        assert totp.seal_secret("ABC", key, "account:1") != totp.seal_secret(
            "ABC", key, "account:1"
        )

    def test_a_secret_moved_to_another_account_is_refused(self) -> None:
        key = b"k" * 32
        sealed = totp.seal_secret(totp.generate_secret(), key, "account:1")

        with pytest.raises(totp.TotpSealError):
            totp.open_secret(sealed, key, "account:2")

    def test_a_tampered_secret_is_refused(self) -> None:
        key = b"k" * 32
        sealed = totp.seal_secret(totp.generate_secret(), key, "account:1")
        flipped = sealed[:-3] + ("A" if sealed[-3] != "A" else "B") + sealed[-2:]

        with pytest.raises(totp.TotpSealError):
            totp.open_secret(flipped, key, "account:1")

    def test_the_wrong_key_is_refused(self) -> None:
        sealed = totp.seal_secret(totp.generate_secret(), b"k" * 32, "account:1")

        with pytest.raises(totp.TotpSealError):
            totp.open_secret(sealed, b"j" * 32, "account:1")

    def test_a_secret_in_clear_is_read_as_it_is(self) -> None:
        assert totp.open_secret("JBSWY3DPEHPK3PXP", None, "account:1") == "JBSWY3DPEHPK3PXP"

    def test_the_key_is_created_owner_only_and_read_back(self, tmp_path: Path) -> None:
        path = tmp_path / totp.KEY_FILE_NAME

        first = totp.load_key(path, create=True)
        second = totp.load_key(path, create=False)

        assert first == second and first is not None and len(first) == 32
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_no_key_without_create(self, tmp_path: Path) -> None:
        assert totp.load_key(tmp_path / "missing", create=False) is None


class TestTheAccounts:
    def test_enrolment_stores_only_ciphertext(self, accounts, store, clock) -> None:
        account = accounts.create("maria", "security", password=PASSWORD)

        secret = accounts.begin_totp(account.id)

        _, pending = raw(store, account.id)
        assert pending and pending.startswith(totp.SEALED_PREFIX) and secret not in pending
        assert accounts.pending_totp_secret(account.id) == secret

        codes = accounts.confirm_totp(account.id, totp.totp_now(secret, t=clock.now))
        assert codes

        confirmed, pending = raw(store, account.id)
        assert confirmed and confirmed.startswith(totp.SEALED_PREFIX) and secret not in confirmed
        assert pending is None
        clock.now += 60
        assert accounts.verify_second_factor(
            account.id, totp.totp_now(secret, t=clock.now), purpose="login"
        )

    def test_the_key_lives_beside_the_store_not_in_it(self, accounts, store) -> None:
        account = accounts.create("maria", "security", password=PASSWORD)
        accounts.begin_totp(account.id)

        key_file = store.db_path.parent / totp.KEY_FILE_NAME
        assert key_file.is_file()
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    def test_a_secret_stored_in_clear_is_sealed_when_used(self, accounts, store, clock) -> None:
        account = accounts.create("maria", "security", password=PASSWORD)
        secret = totp.generate_secret()
        write_raw(store, account.id, secret)

        assert accounts.verify_second_factor(
            account.id, totp.totp_now(secret, t=clock.now), purpose="login"
        )

        confirmed, _ = raw(store, account.id)
        assert confirmed and confirmed.startswith(totp.SEALED_PREFIX)

    def test_the_sweep_seals_every_secret_in_clear(self, accounts, store, clock) -> None:
        first = accounts.create("maria", "security", password=PASSWORD)
        second = accounts.create("juan", "admin", password=PASSWORD)
        write_raw(store, first.id, totp.generate_secret())
        write_raw(store, second.id, totp.generate_secret())

        assert accounts.seal_stored_totp_secrets() == 2
        assert accounts.seal_stored_totp_secrets() == 0
        assert all(raw(store, a.id)[0].startswith(totp.SEALED_PREFIX) for a in (first, second))
        assert accounts.plaintext_totp_secrets() == 0
