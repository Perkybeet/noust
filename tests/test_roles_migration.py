# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The move to one list of roles changes what nobody can do (item 96).

A server that has lived under 4.0 has accounts with one of five roles, custom roles bound to the
whole server, and a ``roles_split`` control that cut the administrator behind the roles' backs.
Upgrading it writes the roles as rows, and the promise is the one the owner's central depends on:
**no account gains and no account loses a permission**. These tests build such servers the way 4.0
left them (rows in ``accounts``, ``custom_roles`` and ``access_bindings``, and an empty list of
roles), run the data step, and compare what every account holds against the rule 4.0 applied.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v16
from noust.core.access import roles_backfill
from noust.core.access.builtin import SHIPPED
from noust.core.access.roles import RoleStore
from noust.core.permission_catalog import ALL_PERMISSIONS
from noust.core.store import NoustStore
from noust.web.permissions.roles import ROLE_PERMISSIONS, STANDARD_ROLE_PERMISSIONS

GOVERNING = {
    "accounts.manage",
    "accounts.read",
    "audit.manage",
    "audit.read",
    "security.manage",
}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    yield opened
    NoustStore.reset_instance()


def as_4_0_left_it(store: NoustStore) -> None:
    """Empty the list of roles, as a store is before the migration to v16 fills it."""
    with store._transaction() as cursor:
        cursor.execute("DELETE FROM account_roles")
        cursor.execute("DELETE FROM roles")


def add_account(
    store: NoustStore, username: str, role: str, *, status: str = "active", age: int = 0
) -> int:
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO accounts (username, role, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, role, status, 1_000_000.0 + age, 1_000_000.0 + age),
        )
        return int(cursor.lastrowid or 0)


def add_custom(store: NoustStore, name: str, permissions: set[str]) -> None:
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO custom_roles (name, label, description, permissions, created_at, "
            "created_by, updated_at, updated_by) VALUES (?, ?, '', ?, 1.0, 'x', 1.0, 'x')",
            (name, name, json.dumps(sorted(permissions))),
        )


def bind(store: NoustStore, account_id: int, role: str, scope: str = "instance") -> None:
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO access_bindings (account_id, scope_kind, scope_id, role, created_at) "
            "VALUES (?, ?, 0, ?, 1.0)",
            (account_id, scope, role),
        )


def migrate(store: NoustStore, *, roles_split: bool) -> None:
    with store._transaction() as cursor:
        roles_backfill.run(cursor, roles_split=roles_split, now=time.time())


def held_in_4_0(
    store: NoustStore, account_id: int, *, roles_split: bool, custom: dict[str, set[str]]
) -> frozenset[str]:
    """What 4.0 let an account do: its role's row of the table in force, and its server roles."""
    table = ROLE_PERMISSIONS if roles_split else STANDARD_ROLE_PERMISSIONS
    conn = store._get_connection()
    own = str(conn.execute("SELECT role FROM accounts WHERE id = ?", (account_id,)).fetchone()[0])
    held = set(table[own])
    for row in conn.execute(
        "SELECT role FROM access_bindings WHERE account_id = ? AND scope_kind = 'instance'",
        (account_id,),
    ):
        held |= custom.get(str(row[0]), set())
    return frozenset(held)


def held_now(store: NoustStore, account_id: int) -> frozenset[str]:
    roles = RoleStore(store)
    return roles.permissions_for(roles.roles_of(account_id))


def test_the_owners_central_keeps_every_power_and_gives_yago_the_owner_role(
    store: NoustStore,
) -> None:
    """roles_split on, `gobierno` bound to yago, aimar and nicolo plain administrators."""
    as_4_0_left_it(store)
    gobierno = GOVERNING
    add_custom(store, "gobierno", gobierno)
    yago = add_account(store, "yago", "admin", age=0)
    aimar = add_account(store, "aimar", "admin", age=1)
    nicolo = add_account(store, "nicolo", "admin", age=2)
    bind(store, yago, "gobierno")
    before = {
        account: held_in_4_0(store, account, roles_split=True, custom={"gobierno": gobierno})
        for account in (yago, aimar, nicolo)
    }
    assert before[yago] == ALL_PERMISSIONS
    assert before[aimar] == ROLE_PERMISSIONS["admin"] and not before[aimar] & GOVERNING

    migrate(store, roles_split=True)

    roles = RoleStore(store)
    assert {account: held_now(store, account) for account in before} == before
    assert roles.roles_of(yago) == ["admin", "owner"]
    assert roles.roles_of(aimar) == ["admin"]
    assert roles.roles_of(nicolo) == ["admin"]
    # The role that completed his powers is still defined, held by nobody, for him to remove.
    gone = roles.get("gobierno")
    assert gone is not None and gone.kind == "custom" and not roles.holders("gobierno")
    # The administrator is what the other two hold: edited, and without the five of governing.
    admin = roles.require("admin")
    assert admin.edited and admin.held == ROLE_PERMISSIONS["admin"]
    assert not admin.held & GOVERNING
    # Nothing is "new" to it: those five were removed on purpose, not added by a release.
    assert admin.offers == ()
    # The bindings to the whole server are gone; they are roles of the account now.
    count = store._get_connection().execute(
        "SELECT COUNT(*) FROM access_bindings WHERE scope_kind = 'instance'"
    )
    assert count.fetchone()[0] == 0


def test_a_server_without_roles_split_keeps_its_administrators_whole(store: NoustStore) -> None:
    as_4_0_left_it(store)
    first = add_account(store, "first", "admin", age=0)
    second = add_account(store, "second", "admin", age=1)
    viewer = add_account(store, "reader", "viewer", age=2)
    before = {
        account: held_in_4_0(store, account, roles_split=False, custom={})
        for account in (first, second, viewer)
    }

    migrate(store, roles_split=False)

    roles = RoleStore(store)
    assert {account: held_now(store, account) for account in before} == before
    # The administrator follows Noust, as a fresh server's does.
    assert not roles.require("admin").edited
    # Somebody holds the owner role, and it is the account that was there first.
    assert roles.roles_of(first) == ["admin", "owner"]
    assert roles.roles_of(second) == ["admin"]


def test_a_server_that_keeps_the_duties_apart_is_left_without_an_owner(store: NoustStore) -> None:
    """Nobody holds everything, so nobody is handed more than they had."""
    as_4_0_left_it(store)
    officer = add_account(store, "officer", "security", age=0)
    admin = add_account(store, "admin1", "admin", age=1)
    auditor = add_account(store, "auditor1", "auditor", age=2)
    before = {
        account: held_in_4_0(store, account, roles_split=True, custom={})
        for account in (officer, admin, auditor)
    }

    migrate(store, roles_split=True)

    roles = RoleStore(store)
    assert {account: held_now(store, account) for account in before} == before
    assert roles.holders("owner") == []


def test_a_custom_role_bound_to_the_server_stays_with_the_account_that_had_it(
    store: NoustStore,
) -> None:
    as_4_0_left_it(store)
    reviewer = {"apps.deploy", "databases.write"}
    add_custom(store, "reviewer", reviewer)
    person = add_account(store, "person", "viewer")
    bind(store, person, "reviewer")
    before = held_in_4_0(store, person, roles_split=False, custom={"reviewer": reviewer})

    migrate(store, roles_split=False)

    roles = RoleStore(store)
    assert held_now(store, person) == before
    assert roles.roles_of(person) == ["viewer", "reviewer"]
    # It is a role of the list like any other, with what it held.
    assert roles.require("reviewer").held == frozenset(reviewer)


def test_a_binding_to_a_role_that_no_longer_exists_grants_nothing_before_or_after(
    store: NoustStore,
) -> None:
    as_4_0_left_it(store)
    person = add_account(store, "person", "operator")
    bind(store, person, "deleted-by-hand")
    before = held_in_4_0(store, person, roles_split=False, custom={})

    migrate(store, roles_split=False)

    assert held_now(store, person) == before == SHIPPED["operator"].permissions
    assert RoleStore(store).roles_of(person) == ["operator"]


def test_the_roles_bound_to_a_team_stay_where_they_are(store: NoustStore) -> None:
    as_4_0_left_it(store)
    add_custom(store, "release-manager", {"apps.deploy"})
    person = add_account(store, "person", "viewer")
    bind(store, person, "release-manager", scope="team")

    migrate(store, roles_split=False)

    rows = store._get_connection().execute(
        "SELECT scope_kind, role FROM access_bindings WHERE account_id = ?", (person,)
    )
    assert [tuple(row) for row in rows] == [("team", "release-manager")]
    assert RoleStore(store).roles_of(person) == ["viewer"]


def test_only_an_account_that_can_sign_in_is_made_the_owner(store: NoustStore) -> None:
    as_4_0_left_it(store)
    gone = add_account(store, "gone", "admin", status="disabled", age=0)
    waiting = add_account(store, "waiting", "admin", status="invited", age=1)
    locked = add_account(store, "locked", "admin", status="locked", age=2)
    active = add_account(store, "active", "admin", status="active", age=3)

    migrate(store, roles_split=False)

    roles = RoleStore(store)
    assert roles.roles_of(gone) == ["admin"] and roles.roles_of(waiting) == ["admin"]
    # A lockout ends by itself, so a locked account is still somebody who will get in.
    assert roles.roles_of(locked) == ["admin", "owner"]
    assert roles.roles_of(active) == ["admin"]


def test_running_the_step_again_changes_nothing(store: NoustStore) -> None:
    as_4_0_left_it(store)
    add_custom(store, "gobierno", GOVERNING)
    yago = add_account(store, "yago", "admin")
    bind(store, yago, "gobierno")
    migrate(store, roles_split=True)
    roles = RoleStore(store)
    snapshot = ([r.to_dict() for r in roles.list_roles()], roles.roles_of(yago))

    migrate(store, roles_split=True)
    migrate(store, roles_split=False)

    assert ([r.to_dict() for r in roles.list_roles()], roles.roles_of(yago)) == snapshot


def test_a_fresh_server_is_seeded_with_the_six_roles_and_no_edit(store: NoustStore) -> None:
    roles = RoleStore(store).list_roles()

    assert [role.name for role in roles] == [
        "viewer",
        "operator",
        "admin",
        "security",
        "auditor",
        "owner",
    ]
    assert not any(role.edited for role in roles)
    assert next(r for r in roles if r.name == "admin").held == ALL_PERMISSIONS
    assert next(r for r in roles if r.name == "owner").protected


def test_the_climb_from_4_0_runs_the_step_with_the_control_in_force_on_that_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path: a v15 store, opened by this release, with roles_split on."""
    path = tmp_path / "noust.db"
    NoustStore.reset_instance()
    first = NoustStore(path)
    add_custom(first, "gobierno", GOVERNING)
    yago = add_account(first, "yago", "admin", age=0)
    aimar = add_account(first, "aimar", "admin", age=1)
    bind(first, yago, "gobierno")
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    conn.executescript(
        "DROP TABLE account_roles; DROP TABLE roles; DELETE FROM schema_version; "
        "INSERT INTO schema_version (version) VALUES (15);"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(roles_backfill, "roles_split_in_force", lambda: True)

    upgraded = NoustStore(path)

    roles = RoleStore(upgraded)
    assert roles.roles_of(yago) == ["admin", "owner"]
    assert held_now(upgraded, yago) == ALL_PERMISSIONS
    assert held_now(upgraded, aimar) == ROLE_PERMISSIONS["admin"]
    NoustStore.reset_instance()


def test_a_store_a_development_build_stamped_v16_gets_its_roles_when_it_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore.reset_instance()
    first = NoustStore(path)
    admin = add_account(first, "admin1", "admin")
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    conn.executescript("DROP TABLE account_roles; DROP TABLE roles;")
    conn.commit()
    conn.close()
    monkeypatch.setattr(roles_backfill, "roles_split_in_force", lambda: False)

    reopened = NoustStore(path)

    assert schema_v16.incomplete(reopened._get_connection().cursor()) is False
    assert RoleStore(reopened).roles_of(admin) == ["admin", "owner"]
    NoustStore.reset_instance()
