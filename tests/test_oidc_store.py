# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The single sign-in lane's part of schema v14, and what keeps it.

Providers, identities, sign-ins in flight and tickets are the SSO fragment of
:mod:`noust.core.schema_v14`; :class:`~noust.core.accounts.oidc_store.OidcStore`
is their one reader and writer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v14
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts.oidc_model import OidcError, OidcProvider
from noust.core.accounts.oidc_store import (
    MAX_PENDING_FLOWS,
    OidcStore,
    PendingFlow,
    Ticket,
    digest,
    secret_name,
)
from noust.core.store import NoustStore


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


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def data(store: NoustStore, clock: Clock) -> OidcStore:
    return OidcStore(store, clock=clock)


def columns(store: NoustStore, table: str) -> set[str]:
    cursor = store._get_connection().execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def provider(name: str = "corp", **changes: object) -> OidcProvider:
    base = OidcProvider(
        name=name,
        label="Acme",
        issuer="https://idp.example.test",
        client_id="noust",
        created_at=1.0,
        updated_at=1.0,
    )
    return OidcProvider(**{**base.__dict__, **changes})


def flow(state: str = "s", *, expires: float = 1_800_000_600.0) -> PendingFlow:
    return PendingFlow(
        state_hash=digest(state),
        provider="corp",
        nonce="n",
        code_verifier="v" * 43,
        binding_hash=digest("b"),
        redirect_uri="https://noust.example.test/api/auth/oidc/callback",
        next_path="/",
        client_ip="203.0.113.5",
        expires_at=expires,
    )


class TestTheSchema:
    def test_a_fresh_store_has_every_table_and_is_complete(self, store: NoustStore) -> None:
        for table in ("oidc_providers", "account_identities", "oidc_flows", "oidc_tickets"):
            assert columns(store, table), table
        assert "client_secret" not in columns(store, "oidc_providers")
        assert not any("secret" in name for name in columns(store, "oidc_providers"))
        assert schema_v14.incomplete(store._get_connection().cursor()) is False

    def test_a_store_missing_the_fragment_is_reported_incomplete(self, store: NoustStore) -> None:
        connection = store._get_connection()
        connection.execute("DROP TABLE oidc_tickets")
        assert schema_v14.incomplete(connection.cursor()) is True

    def test_the_identity_table_has_room_for_other_protocols(self, store: NoustStore) -> None:
        assert {"protocol", "issuer", "subject", "mapped_role"} <= columns(
            store, "account_identities"
        )


class TestProviders:
    def test_a_provider_is_saved_and_read_back_whole(self, data: OidcStore) -> None:
        saved = provider(
            allowed_domains=("acme.test",), require_amr=("mfa",), enabled=False, provisioning="auto"
        )
        data.save_provider(saved)

        found = data.get_provider("CORP")

        assert found == saved
        assert data.list_providers() == [saved]

    def test_saving_again_keeps_who_created_it_and_when(self, data: OidcStore) -> None:
        data.save_provider(provider(created_by="cli:root", created_at=5.0))

        data.save_provider(
            provider(label="New", created_by="someone", created_at=99.0, updated_at=9.0)
        )

        found = data.get_provider("corp")
        assert found is not None
        assert (found.label, found.created_by, found.created_at, found.updated_at) == (
            "New",
            "cli:root",
            5.0,
            9.0,
        )

    def test_the_database_refuses_a_default_role_a_provider_may_not_give(
        self, store: NoustStore
    ) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            store._get_connection().execute(
                "INSERT INTO oidc_providers (name, label, issuer, client_id, default_role, "
                "created_at, updated_at) VALUES ('x', 'x', 'https://x.test', 'c', 'security', 1, 1)"
            )

    def test_a_row_whose_mappings_were_edited_by_hand_gives_nothing(
        self, data: OidcStore, store: NoustStore
    ) -> None:
        data.save_provider(provider())
        store._get_connection().execute(
            "UPDATE oidc_providers SET group_mappings = ?",
            ('[{"group": "x", "role": "security"}]',),
        )
        store._get_connection().commit()

        found = data.get_provider("corp")

        assert found is not None and found.group_mappings == ()

    def test_the_secret_has_a_name_of_its_own_and_a_private_file(
        self, data: OidcStore, store: NoustStore
    ) -> None:
        assert secret_name("corp") == "oidc/corp"
        assert data.has_secret("corp") is False
        data.write_secret("corp", "value")

        assert data.has_secret("corp") is True
        assert data.read_secret("corp") == "value"
        data.delete_secret("corp")
        assert data.has_secret("corp") is False and data.read_secret("corp") is None

    def test_deleting_a_provider_takes_its_secret_and_its_sign_ins_but_not_its_people(
        self, data: OidcStore, store: NoustStore, clock: Clock
    ) -> None:
        accounts = AccountManager(store, policy=AuthPolicy(), clock=clock)
        account = accounts.create_external("maria", "viewer")
        data.save_provider(provider())
        data.write_secret("corp", "value")
        data.begin_flow(flow())
        data.link_identity(
            account.id,
            provider="corp",
            issuer="https://idp.example.test",
            subject="sub-1",
            email=None,
            created_by="test",
        )

        assert data.delete_provider("corp") is True

        assert data.get_provider("corp") is None and not data.has_secret("corp")
        assert data.get_flow(digest("s")) is None
        assert data.find_identity("https://idp.example.test", "sub-1") is not None
        assert data.delete_provider("corp") is False


class TestIdentities:
    @pytest.fixture
    def accounts(self, store: NoustStore, clock: Clock) -> AccountManager:
        return AccountManager(store, policy=AuthPolicy(), clock=clock)

    def link(self, data: OidcStore, account_id: int, subject: str = "sub-1", issuer: str = "i"):
        return data.link_identity(
            account_id,
            provider="corp",
            issuer=issuer,
            subject=subject,
            email="m@acme.test",
            created_by="test",
        )

    def test_a_person_is_one_pair_of_issuer_and_subject(
        self, data: OidcStore, accounts: AccountManager
    ) -> None:
        first = accounts.create_external("maria", "viewer")
        second = accounts.create_external("juan", "viewer")
        self.link(data, first.id)

        with pytest.raises(OidcError) as caught:
            self.link(data, second.id)
        assert caught.value.reason == "already_linked"
        # The same subject at another issuer is another person.
        assert self.link(data, second.id, issuer="other").account_id == second.id

    def test_an_account_can_be_several_people_at_several_providers(
        self, data: OidcStore, accounts: AccountManager
    ) -> None:
        account = accounts.create_external("maria", "viewer")
        self.link(data, account.id, "a", issuer="one")
        self.link(data, account.id, "b", issuer="two")

        assert [item.subject for item in data.list_identities(account.id)] == ["a", "b"]
        assert data.accounts_linked_to("one") == {account.id}
        assert accounts.require("maria").identities == 2

    def test_removing_the_account_removes_its_links(
        self, data: OidcStore, accounts: AccountManager
    ) -> None:
        account = accounts.create_external("maria", "viewer")
        self.link(data, account.id)

        accounts.remove("maria")

        assert data.list_identities() == []

    def test_a_sign_in_records_when_and_from_where(
        self, data: OidcStore, accounts: AccountManager, clock: Clock
    ) -> None:
        account = accounts.create_external("maria", "viewer")
        identity = self.link(data, account.id)

        data.touch_identity(
            identity.id, email="new@acme.test", client_ip="203.0.113.9", mapped_role="admin"
        )

        found = data.get_identity(identity.id)
        assert found is not None
        assert (found.last_login_ip, found.email, found.mapped_role) == (
            "203.0.113.9",
            "new@acme.test",
            "admin",
        )
        data.touch_identity(identity.id, email=None, client_ip="203.0.113.9")
        again = data.get_identity(identity.id)
        assert again is not None and again.email == "new@acme.test" and again.mapped_role == "admin"
        data.touch_identity(identity.id, email=None, client_ip="203.0.113.9", mapped_role=None)
        cleared = data.get_identity(identity.id)
        assert cleared is not None and cleared.mapped_role is None

    def test_unlinking_says_what_was_there(self, data: OidcStore, accounts: AccountManager) -> None:
        account = accounts.create_external("maria", "viewer")
        identity = self.link(data, account.id)

        assert data.unlink_identity(identity.id) == identity
        assert data.unlink_identity(identity.id) is None


class TestFlowsAndTickets:
    def test_a_flow_is_spent_once(self, data: OidcStore) -> None:
        data.begin_flow(flow())

        assert data.get_flow(digest("s")) is not None  # reading does not spend it
        assert data.spend_flow(digest("s")) is not None
        assert data.spend_flow(digest("s")) is None

    def test_a_flow_expires(self, data: OidcStore, clock: Clock) -> None:
        data.begin_flow(flow())
        clock.now += 601

        assert data.get_flow(digest("s")) is None and data.spend_flow(digest("s")) is None

    def test_starting_a_flow_forgets_the_ones_that_ran_out(
        self, data: OidcStore, store: NoustStore, clock: Clock
    ) -> None:
        data.begin_flow(flow("old"))
        clock.now += 601
        data.begin_flow(flow("new", expires=clock.now + 600))

        rows = store._get_connection().execute("SELECT COUNT(*) FROM oidc_flows").fetchone()
        assert rows[0] == 1

    def test_the_number_in_flight_is_bounded(self, data: OidcStore, store: NoustStore) -> None:
        connection = store._get_connection()
        connection.executemany(
            "INSERT INTO oidc_flows (state_hash, provider, nonce, code_verifier, binding_hash, "
            "redirect_uri, client_ip, created_at, expires_at) VALUES (?, 'corp', 'n', 'v', 'b', "
            "'u', 'ip', 1, 9999999999)",
            [(f"h{index}",) for index in range(MAX_PENDING_FLOWS)],
        )
        connection.commit()

        with pytest.raises(OidcError) as caught:
            data.begin_flow(flow("one more"))
        assert caught.value.reason == "too_many_flows" and caught.value.public == "unavailable"

    def test_a_ticket_is_read_without_being_spent_and_collected_once(
        self, data: OidcStore, store: NoustStore, clock: Clock
    ) -> None:
        account = AccountManager(store, policy=AuthPolicy(), clock=clock).create_external(
            "maria", "viewer"
        )
        data.issue_ticket(
            Ticket(
                ticket_hash=digest("t"),
                account_id=account.id,
                provider="corp",
                binding_hash=digest("b"),
                client_ip="203.0.113.5",
                next_path="/apps",
                amr=("pwd", "mfa"),
                detail="through Acme",
                expires_at=clock.now + 120,
            )
        )

        peeked = data.get_ticket(digest("t"))
        assert peeked is not None and peeked.amr == ("pwd", "mfa")
        assert data.spend_ticket(digest("t")) is not None
        assert data.spend_ticket(digest("t")) is None
        assert data.get_ticket(digest("t")) is None
