# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An account holds one or several roles of the one list (4.1, item 96).

What it may do is the union of what they hold, read at every request. The rules that keep a server
from being locked out or from handing out everything unasked sit in ``AccountManager``, where the
accounts are written, so the console, the command line and the identity providers meet them all:
the first account is the owner, the owner role is handed on by an owner, the last account that can
sign in and holds it cannot lose it, and where a framework keeps governing and running apart the
roles of an account do not join them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.access import AccessError
from noust.core.access.builtin import ADMIN, OPERATOR, VIEWER
from noust.core.access.role_manager import RoleManager
from noust.core.access.roles import RoleStore
from noust.core.accounts import AccountManager, AuthPolicy
from noust.core.accounts.role_review import review
from noust.core.exceptions import NoustError, ValidationError
from noust.core.permission_catalog import ALL_PERMISSIONS
from noust.core.store import NoustStore
from noust.core.teams import TeamStore

PASSWORD = "correct horse battery staple"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    yield opened
    NoustStore.reset_instance()


@pytest.fixture
def accounts(store: NoustStore) -> AccountManager:
    return AccountManager(store, policy=AuthPolicy())


@pytest.fixture
def apart(store: NoustStore) -> AccountManager:
    """A server whose framework keeps governing and running apart, with the administrator split."""
    RoleManager(store).update("admin", permissions=sorted(ADMIN))
    return AccountManager(store, policy=AuthPolicy(frameworks=("iso-27001",)))


def code_of(exc: NoustError) -> str:
    assert exc.coded is not None, f"{exc!r} says nothing a console can translate"
    return exc.coded.code


def held(store: NoustStore, username: str) -> frozenset[str]:
    account = AccountManager(store).require(username)
    return RoleStore(store).permissions_for(account.roles)


# --------------------------------------------------------------- several roles


def test_an_account_holds_several_roles_and_what_they_hold_together(
    accounts: AccountManager, store: NoustStore
) -> None:
    accounts.create("ana", None, roles=["viewer", "operator"], password=PASSWORD)
    RoleManager(store).create("db-reader", ["databases.write"])

    account = accounts.set_roles("ana", ["viewer", "db-reader"])

    assert account.roles == ("viewer", "db-reader")
    # `role` is what still reads one role: the first of the five of 3.1.
    assert account.role == "viewer"
    assert held(store, "ana") == VIEWER | {"databases.write"}
    assert account.to_dict()["roles"] == ["viewer", "db-reader"]


def test_an_account_whose_roles_are_all_its_operators_own_is_known_as_a_viewer(
    accounts: AccountManager, store: NoustStore
) -> None:
    RoleManager(store).create("db-reader", ["databases.write"])

    account = accounts.create("ana", None, roles=["db-reader"], password=PASSWORD)

    assert account.role == "viewer" and account.roles == ("db-reader",)
    # It holds what that role holds and no more: the reader it is known as is not a role it has.
    assert held(store, "ana") == frozenset({"databases.write", "self"})


def test_what_an_account_holds_follows_a_role_that_changes(
    accounts: AccountManager, store: NoustStore
) -> None:
    accounts.create("ana", "operator", password=PASSWORD)
    assert "apps.deploy" in held(store, "ana")

    RoleManager(store).update("operator", permissions=["apps.read"])

    assert held(store, "ana") == frozenset({"apps.read", "self"})


def test_an_account_with_nothing_listed_holds_the_role_its_column_says(
    accounts: AccountManager, store: NoustStore
) -> None:
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO accounts (username, role, status, created_at, updated_at) "
            "VALUES ('by-hand', 'operator', 'active', 1.0, 1.0)"
        )

    account = accounts.require("by-hand")

    assert account.roles == ("operator",)
    assert held(store, "by-hand") == OPERATOR
    assert [h.username for h in RoleStore(store).holders("operator")] == ["by-hand"]


def test_a_role_the_list_does_not_have_is_refused_wherever_the_account_arrives(
    accounts: AccountManager,
) -> None:
    accounts.create("founder", "admin", password=PASSWORD)
    for attempt in (
        lambda: accounts.create("ana", "wizard", password=PASSWORD),
        lambda: accounts.invite("bea", "wizard"),
        lambda: accounts.set_roles("founder", ["admin", "wizard"]),
        lambda: accounts.set_role("founder", "wizard"),
    ):
        with pytest.raises(ValidationError) as caught:
            attempt()
        assert code_of(caught.value) == "roles.unknown"
    with pytest.raises(ValidationError) as none:
        accounts.set_roles("founder", [])
    assert code_of(none.value) == "roles.no_roles"


def test_changing_the_role_an_account_is_known_by_leaves_the_others(
    accounts: AccountManager, store: NoustStore
) -> None:
    RoleManager(store).create("db-reader", ["databases.write"])
    accounts.create("ana", None, roles=["viewer", "db-reader"], password=PASSWORD)

    account = accounts.set_role("ana", "operator")

    assert account.roles == ("operator", "db-reader")


def test_an_account_that_becomes_a_security_officer_leaves_its_teams(
    accounts: AccountManager, store: NoustStore
) -> None:
    ana = accounts.create("ana", "operator", password=PASSWORD)
    conn = store._get_connection()
    assert conn.execute(
        "SELECT COUNT(*) FROM team_members WHERE account_id = ?", (ana.id,)
    ).fetchone()[0]

    accounts.set_roles("ana", ["operator", "security"])

    # A role that only means something across every team never sits inside one.
    assert not conn.execute(
        "SELECT COUNT(*) FROM team_members WHERE account_id = ?", (ana.id,)
    ).fetchone()[0]


def test_a_role_that_never_joins_a_team_keeps_the_account_out_of_one(
    accounts: AccountManager, store: NoustStore
) -> None:
    accounts.create("ana", "operator", password=PASSWORD)
    accounts.set_roles("ana", ["operator", "security"])
    TeamStore(store).create_team("Acme", slug="acme")

    # The role it is known by is an operator's; the one it holds beside it is the security officer's.
    with pytest.raises(ValidationError, match="never join a team"):
        TeamStore(store).add_member("acme", "ana", "viewer")


# ----------------------------------------------------------------- the first owner


def test_the_first_account_is_the_owner_and_the_second_is_not(
    accounts: AccountManager, store: NoustStore
) -> None:
    first = accounts.create("maria", "admin", password=PASSWORD, first_account_is_owner=True)
    second = accounts.create("lucia", "admin", password=PASSWORD, first_account_is_owner=True)

    assert first.roles == ("admin", "owner") and second.roles == ("admin",)
    assert held(store, "maria") == ALL_PERMISSIONS


def test_an_account_made_for_somebody_else_is_given_the_roles_it_was_asked_for_and_no_more(
    accounts: AccountManager,
) -> None:
    # An identity provider, a team's onboarding and a test make accounts that nobody arrives with.
    made = accounts.create("maria", "admin", password=PASSWORD)

    assert made.roles == ("admin",)


def test_an_invited_first_account_is_the_owner_too(accounts: AccountManager) -> None:
    account, _code = accounts.invite("maria", "viewer", first_account_is_owner=True)

    assert account.roles == ("viewer", "owner")


def test_the_first_account_made_with_the_owner_role_does_not_get_it_twice(
    accounts: AccountManager,
) -> None:
    account = accounts.create(
        "maria", None, roles=["owner"], password=PASSWORD, first_account_is_owner=True
    )

    assert account.roles == ("owner",)


def test_where_duties_are_kept_apart_nobody_is_handed_everything_unasked(
    apart: AccountManager,
) -> None:
    account = apart.create("maria", "security", password=PASSWORD, first_account_is_owner=True)

    assert account.roles == ("security",)


# ----------------------------------------------------- the owner is handed on by an owner


def test_only_an_owner_gives_or_takes_the_owner_role(accounts: AccountManager) -> None:
    owner = accounts.create("maria", "admin", password=PASSWORD, first_account_is_owner=True)
    admin = accounts.create("lucia", "admin", password=PASSWORD)
    other = accounts.create("pedro", "viewer", password=PASSWORD)

    with pytest.raises(AccessError) as given:
        accounts.set_roles("pedro", ["viewer", "owner"], by=admin)
    assert code_of(given.value) == "roles.owner_by_owner"

    accounts.set_roles("pedro", ["viewer", "owner"], by=owner)
    assert accounts.require("pedro").roles == ("viewer", "owner")

    with pytest.raises(AccessError) as taken:
        accounts.set_roles("pedro", ["viewer"], by=admin)
    assert code_of(taken.value) == "roles.owner_by_owner"
    # Whoever has no account to hold anything with is root, or the master token: it can.
    accounts.set_roles("pedro", ["viewer"], by=None)
    assert other.id == accounts.require("pedro").id


# ----------------------------------------------- a server always keeps an owner it can reach


def test_the_last_owner_keeps_the_role(accounts: AccountManager) -> None:
    accounts.create("maria", "admin", password=PASSWORD, first_account_is_owner=True)

    for attempt in (
        lambda: accounts.set_roles("maria", ["admin"]),
        lambda: accounts.set_roles("maria", ["viewer"]),
        lambda: accounts.disable("maria"),
        lambda: accounts.remove("maria"),
    ):
        with pytest.raises(AccessError) as caught:
            attempt()
        assert code_of(caught.value) == "roles.last_owner"
        assert caught.value.coded.params == {"username": "maria"}
    assert accounts.require("maria").roles == ("admin", "owner")
    assert accounts.require("maria").status == "active"


def test_with_another_owner_who_can_sign_in_the_role_can_go(accounts: AccountManager) -> None:
    accounts.create("maria", "admin", password=PASSWORD, first_account_is_owner=True)
    accounts.create("lucia", "admin", password=PASSWORD)
    accounts.set_roles("lucia", ["admin", "owner"])

    accounts.set_roles("maria", ["admin"])
    assert accounts.require("maria").roles == ("admin",)
    with pytest.raises(AccessError):
        accounts.disable("lucia")


def test_an_owner_who_cannot_sign_in_does_not_count(accounts: AccountManager) -> None:
    accounts.create("maria", "admin", password=PASSWORD, first_account_is_owner=True)
    accounts.create("lucia", "admin", password=PASSWORD)
    accounts.set_roles("lucia", ["admin", "owner"])
    accounts.set_roles("maria", ["admin"])
    # lucia is the only one; an owner waiting for an invitation is not somebody who can sign in.
    pending, _code = accounts.invite("pedro", "viewer")
    accounts.set_roles("pedro", ["viewer", "owner"])

    with pytest.raises(AccessError) as caught:
        accounts.disable("lucia")
    assert code_of(caught.value) == "roles.last_owner"
    assert pending.status == "invited"


def test_a_disabled_owner_is_not_counted_among_the_owners_that_remain(
    accounts: AccountManager,
) -> None:
    accounts.create("maria", "admin", password=PASSWORD, first_account_is_owner=True)
    accounts.create("lucia", "admin", password=PASSWORD)
    accounts.create("pedro", "viewer", password=PASSWORD)
    accounts.set_roles("lucia", ["admin", "owner"])
    accounts.set_roles("pedro", ["viewer", "owner"])
    accounts.disable("lucia")
    accounts.set_roles("maria", ["admin"])

    # lucia still holds the role but cannot sign in, so pedro is the one owner who can.
    assert "owner" in accounts.require("lucia").roles
    with pytest.raises(AccessError) as caught:
        accounts.set_roles("pedro", ["viewer"])
    assert code_of(caught.value) == "roles.last_owner"


# --------------------------------------------- what a framework asks of an account's roles


def test_where_duties_are_kept_apart_roles_that_would_join_them_are_refused(
    apart: AccountManager,
) -> None:
    apart.create("officer", "security", password=PASSWORD)
    apart.create("ana", "operator", password=PASSWORD)

    with pytest.raises(AccessError) as caught:
        apart.set_roles("ana", ["operator", "security"])

    assert code_of(caught.value) == "roles.separation_account"
    assert caught.value.coded.params["username"] == "ana"
    assert "accounts.manage" in caught.value.coded.params["governing"]
    assert apart.require("ana").roles == ("operator",)


def test_where_duties_are_kept_apart_a_role_that_joins_them_alone_is_reported_not_refused(
    store: NoustStore,
) -> None:
    together = AccountManager(store, policy=AuthPolicy(frameworks=("iso-27001",)))

    # The administrator Noust ships holds both halves: the role is what to split, and the review
    # says so; refusing the account would leave a server that cannot have an administrator.
    account = together.create("maria", "admin", password=PASSWORD)

    assert account.roles == ("admin",)
    judged = review(store, together.policy().requires)
    assert [role.name for role in judged.joining] == ["admin"]
    assert judged.duties_apart and judged.suggestion is not None
    assert judged.suggestion.startswith("noust access role set admin --permission ")


def test_several_roles_that_keep_apart_are_fine_where_one_role_per_account_was_asked(
    store: NoustStore,
) -> None:
    founded_by = AccountManager(store, policy=AuthPolicy())
    founded_by.create("founder", "admin", password=PASSWORD)
    RoleManager(store).create("db-reader", ["databases.write"])
    strict = AccountManager(store, policy=AuthPolicy(frameworks=("ens-basica",)))
    assert strict.policy().requires("single_role_per_account")
    assert not strict.policy().requires("separation_of_duties")

    # What the control used to refuse by counting: a second role on the whole server.
    account = strict.create("ana", None, roles=["viewer", "db-reader"], password=PASSWORD)

    assert account.roles == ("viewer", "db-reader")


def test_an_auditor_holds_nothing_beyond_what_an_auditor_does(apart: AccountManager) -> None:
    apart.create("officer", "security", password=PASSWORD)
    apart.create("eve", "auditor", password=PASSWORD)

    with pytest.raises(AccessError) as caught:
        apart.set_roles("eve", ["auditor", "operator"])

    assert code_of(caught.value) == "roles.auditor_exclusive"
    apart.set_roles("eve", ["auditor", "viewer"])
    assert apart.require("eve").roles == ("auditor", "viewer")


def test_the_owner_is_outside_what_a_framework_asks_of_a_combination(
    apart: AccountManager,
) -> None:
    apart.create("officer", "security", password=PASSWORD)
    apart.create("ana", "operator", password=PASSWORD)

    owner = apart.set_roles("ana", ["operator", "owner"])

    # It holds everything by design, and a report says so; the account is not refused for it.
    assert owner.roles == ("operator", "owner")


def test_one_person_cannot_hold_accounts_whose_roles_together_join_the_duties(
    apart: AccountManager,
) -> None:
    apart.create("officer", "security", password=PASSWORD, person_ref="ana@example.com")
    apart.create("reader", "viewer", password=PASSWORD, person_ref="ana@example.com")

    with pytest.raises(NoustError, match="incompatible"):
        apart.set_roles("reader", ["viewer", "operator"])
    assert apart.separation_conflicts() == []
    assert apart.require("reader").roles == ("viewer",)


def test_a_role_typed_any_way_is_written_the_way_the_list_keeps_it() -> None:
    from noust.core.accounts import role_rules

    assert role_rules.normalised([" Admin ", "admin", "VIEWER"]) == ["admin", "viewer"]
    # Nothing is checked here: an empty name stays one, for the caller that refuses it.
    assert role_rules.normalised(["admin", " "]) == ["admin", ""]


def test_replacing_the_role_an_account_is_known_by_keeps_the_others_in_their_order() -> None:
    from noust.core.accounts import role_rules

    assert role_rules.with_first_role(["operator", "admin", "viewer"], "admin") == [
        "admin",
        "viewer",
    ]
    assert role_rules.with_first_role(["operator"], "viewer") == ["viewer"]
    assert role_rules.with_first_role(["operator", "viewer"], "auditor") == [
        "auditor",
        "viewer",
    ]
