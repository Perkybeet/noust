"""
A central registers nodes once its sign-in has a second factor: the console's, or a person's own.

3.0 asked for the console-wide TOTP before a central added its first node,
because whoever signs in there reaches every node. With accounts (3.1), a
person signs in with a second factor of their own - an authenticator or a
passkey - and the master token's own passkey protects its sign-in too; the
rule accepts either, and still refuses a central whose only way in is a
single factor.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import totp
from noust.core.accounts import AccountManager, passwords
from noust.core.store import NoustStore
from noust.fleet.policy import node_registration_blockers

PASSWORD = "correct horse battery staple"


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


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from noust.web import auth

    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(auth.STATE_DIR_ENV, str(directory))
    monkeypatch.setattr(auth, "_global_token_manager", None)
    return directory


def with_authenticator(name: str, role: str) -> None:
    manager = AccountManager()
    account = manager.create(name, role, password=PASSWORD)
    secret = manager.begin_totp(account.id)
    assert manager.confirm_totp(account.id, totp.totp_now(secret)) is not None


def with_passkey(account_id: int | None, username: str) -> None:
    pytest.importorskip("cryptography")
    from noust.core.accounts.passkeys import PasskeyManager, PasskeyOwner, RelyingParty
    from tests.webauthn_authenticator import SoftwareAuthenticator

    rp = RelyingParty(id="localhost", origins=("http://localhost:8080",))
    passkeys = PasskeyManager(hostname="central")
    owner = PasskeyOwner(account_id=account_id, username=username)
    options = passkeys.registration_options(owner, rp, binding="s")
    credential = SoftwareAuthenticator().create(options, "http://localhost:8080")
    passkeys.register(owner, rp, credential, name="Key", binding="s", created_by="test")


def test_a_central_with_no_second_factor_anywhere_is_blocked() -> None:
    AccountManager().create("maria", "admin", password=PASSWORD)
    blockers = node_registration_blockers()
    assert len(blockers) == 1
    assert "passkey" in blockers[0]


def test_an_admin_with_an_authenticator_of_their_own_is_enough() -> None:
    with_authenticator("maria", "admin")
    assert node_registration_blockers() == []


def test_an_admin_with_a_passkey_of_their_own_is_enough() -> None:
    account = AccountManager().create("maria", "admin", password=PASSWORD)
    with_passkey(account.id, "maria")
    assert node_registration_blockers() == []


def test_the_master_token_s_own_passkey_is_enough() -> None:
    with_passkey(None, "master")
    assert node_registration_blockers() == []


def test_a_second_factor_of_someone_who_cannot_add_nodes_is_not() -> None:
    with_authenticator("vera", "viewer")
    assert len(node_registration_blockers()) == 1


def test_a_disabled_account_does_not_count() -> None:
    with_authenticator("maria", "admin")
    AccountManager().disable("maria", "left")
    assert len(node_registration_blockers()) == 1
