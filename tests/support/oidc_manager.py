# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_oidc_manager.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts.oidc import Answer, OidcManager
from noust.core.accounts.oidc_client import OidcClient, OidcHttp
from noust.core.accounts.oidc_model import OidcError
from noust.core.store import NoustStore
from tests.support.oidc_support import Clock, FakeIdP

pytest.importorskip("cryptography")

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
