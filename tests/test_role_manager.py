# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One list of roles, and the rules for changing it (4.1, item 96).

The five roles of every release and the owner are rows the operator edits, duplicates and puts
back; a role Noust ships that nobody touched follows the release, one that was edited receives
nothing in silence and is offered what is new; the owner is protected. The rules sit in
``RoleManager``, so the CLI and the console reach one implementation, and every refusal carries a
code the console translates.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import compliance
from noust.core.access import AccessConflictError, AccessError, AccessNotFoundError, builtin
from noust.core.access.role_manager import RoleManager
from noust.core.access.roles import replace_roles
from noust.core.exceptions import NoustError, ValidationError
from noust.core.permission_catalog import ALL_PERMISSIONS
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    yield opened
    NoustStore.reset_instance()


@pytest.fixture
def manager(store: NoustStore) -> RoleManager:
    return RoleManager(store)


def give(store: NoustStore, username: str, *roles: str) -> int:
    """An account holding these roles, written the way the data layer keeps them."""
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO accounts (username, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'active', ?, ?)",
            (username, builtin.ROLE_VIEWER, time.time(), time.time()),
        )
        account_id = int(cursor.lastrowid or 0)
        replace_roles(cursor, account_id, roles, now=time.time(), actor="test")
    return account_id


def code_of(exc: NoustError) -> str:
    assert exc.coded is not None, f"{exc!r} says nothing a console can translate"
    return exc.coded.code


def controls_on(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    real = compliance.is_on
    monkeypatch.setattr(compliance, "is_on", lambda name, config=None: name in names or real(name))


def ships_one_more(monkeypatch: pytest.MonkeyPatch, role: str, permission: str) -> None:
    """A newer release that ships ``permission`` in ``role``."""
    shipped = builtin.SHIPPED[role]
    monkeypatch.setitem(
        builtin.SHIPPED,
        role,
        builtin.Shipped(
            role, shipped.label, shipped.description, shipped.permissions | {permission}
        ),
    )


# ---------------------------------------------------------------------- the list


def test_the_list_has_the_five_roles_and_the_owner_in_their_order(manager: RoleManager) -> None:
    views = manager.views()

    assert [v.role.name for v in views] == [
        "viewer",
        "operator",
        "admin",
        "security",
        "auditor",
        "owner",
    ]
    owner = views[-1]
    assert owner.role.held == ALL_PERMISSIONS
    assert not owner.can_edit and not owner.can_delete and not owner.can_reset
    assert all(v.role.follows_noust for v in views)
    assert all(v.can_edit and not v.can_delete for v in views[:-1])


def test_a_view_says_who_holds_a_role_and_which_roles_join_duties(
    manager: RoleManager, store: NoustStore
) -> None:
    give(store, "ana", "admin")
    give(store, "bea", "viewer", "admin")

    admin = manager.view("admin")

    assert [h.username for h in admin.holders] == ["ana", "bea"]
    # The administrator ships holding both halves of what a framework keeps apart.
    assert admin.joins_duties and "accounts.manage" in admin.governing and admin.running
    assert not manager.view("security").joins_duties
    # The owner holds everything by design; it is not reported as a defect of a definition.
    assert not manager.view("owner").joins_duties


# ------------------------------------------------ roles that follow Noust, and offers


def test_a_role_nobody_edited_receives_what_a_new_release_ships(
    manager: RoleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "backups.manage" not in manager.roles.require("operator").held

    ships_one_more(monkeypatch, "operator", "backups.manage")

    operator = manager.roles.require("operator")
    assert "backups.manage" in operator.held
    assert operator.offers == ()


def test_an_edited_role_receives_nothing_and_is_offered_what_is_new(
    manager: RoleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager.update("operator", permissions=["apps.read", "apps.operate"])

    ships_one_more(monkeypatch, "operator", "backups.manage")

    operator = manager.roles.require("operator")
    assert operator.edited and not operator.follows_noust
    assert operator.held == frozenset({"apps.read", "apps.operate", "self"})
    assert operator.offers == ("backups.manage",)


def test_what_the_operator_left_out_on_purpose_is_not_an_offer(manager: RoleManager) -> None:
    manager.update("operator", permissions=["apps.read"])

    # Everything the release ships for operator beyond this was seen when it was edited.
    assert manager.roles.require("operator").offers == ()


def test_an_offer_is_taken_or_left_and_either_way_it_stops_being_made(
    manager: RoleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager.update("operator", permissions=["apps.read", "apps.operate"])
    ships_one_more(monkeypatch, "operator", "backups.manage")
    ships_one_more(monkeypatch, "operator", "databases.manage")

    taken = manager.settle_offers(
        "operator", accept=["backups.manage"], dismiss=["databases.manage"]
    )

    assert "backups.manage" in taken.held and "databases.manage" not in taken.held
    assert taken.offers == ()


def test_an_offer_that_was_not_made_is_refused_with_a_code(manager: RoleManager) -> None:
    manager.update("operator", permissions=["apps.read"])

    with pytest.raises(AccessError) as caught:
        manager.settle_offers("operator", accept=["backups.manage"])

    assert code_of(caught.value) == "roles.offer_unknown"
    assert "noust access role show operator" in (caught.value.coded.hint or "")


def test_editing_only_the_words_of_a_role_leaves_it_following_noust(
    manager: RoleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager.update("viewer", label="Reader", description="Looks, does not touch.")

    viewer = manager.roles.require("viewer")
    assert viewer.label == "Reader" and viewer.follows_noust and not viewer.edited
    ships_one_more(monkeypatch, "viewer", "backups.run")
    assert "backups.run" in manager.roles.require("viewer").held


def test_saving_a_role_with_what_noust_ships_makes_it_follow_noust_again(
    manager: RoleManager,
) -> None:
    manager.update("operator", permissions=["apps.read"])
    assert manager.roles.require("operator").edited

    manager.update("operator", permissions=sorted(builtin.OPERATOR))

    assert manager.roles.require("operator").follows_noust


def test_putting_a_role_back_makes_it_follow_noust_with_its_own_words(
    manager: RoleManager,
) -> None:
    manager.update(
        "security", permissions=["apps.read"], label="Compliance", description="Different."
    )
    assert manager.view("security").can_reset

    back = manager.reset("security")

    assert back.follows_noust and back.held == builtin.SECURITY
    assert back.label == "Security officer"
    assert back.offers == ()
    assert not manager.view("security").can_reset


# ------------------------------------------------------------------- the owner


def test_the_owner_is_not_edited_put_back_or_deleted(manager: RoleManager) -> None:
    for attempt in (
        lambda: manager.update("owner", permissions=["apps.read"]),
        lambda: manager.update("owner", label="Boss"),
        lambda: manager.reset("owner"),
        lambda: manager.delete("owner"),
        lambda: manager.settle_offers("owner", accept=["apps.read"]),
    ):
        with pytest.raises(AccessError) as caught:
            attempt()
        assert code_of(caught.value) == "roles.owner_protected"
    owner = manager.roles.require("owner")
    assert owner.held == ALL_PERMISSIONS and owner.label == "Owner"


def test_the_owner_holds_a_permission_a_release_adds_without_anyone_touching_it(
    manager: RoleManager, store: NoustStore
) -> None:
    # Whatever an older release wrote in the row, the owner is everything the code knows.
    with store._transaction() as cursor:
        cursor.execute("UPDATE roles SET permissions = '[\"apps.read\"]' WHERE name = 'owner'")

    assert manager.roles.require("owner").held == ALL_PERMISSIONS


# --------------------------------------------------------- roles of one's own


def test_a_role_of_ones_own_is_defined_changed_and_deleted(manager: RoleManager) -> None:
    created = manager.create(
        "release-manager", ["apps.read", "apps.deploy"], description="Ships releases."
    )

    assert created.kind == "custom" and created.label == "release-manager"
    assert created.held == frozenset({"apps.read", "apps.deploy", "self"})

    changed = manager.update("release-manager", permissions=["apps.read"], label="Releases")
    assert changed.label == "Releases" and changed.held == frozenset({"apps.read", "self"})
    assert changed.offers == () and not changed.follows_noust

    assert manager.delete("release-manager").name == "release-manager"
    assert manager.roles.get("release-manager") is None


def test_the_name_of_a_role_noust_ships_is_not_free(manager: RoleManager) -> None:
    for name in ("admin", "owner", "Viewer", "auditor"):
        with pytest.raises(ValidationError):
            manager.create(name, ["apps.read"])


def test_a_name_taken_and_permissions_that_do_not_exist_are_refused_with_codes(
    manager: RoleManager,
) -> None:
    manager.create("reader", ["apps.read"])

    with pytest.raises(AccessConflictError) as taken:
        manager.create("reader", ["apps.read"])
    with pytest.raises(ValidationError) as unknown:
        manager.create("other", ["apps.read", "apps.fly"])
    with pytest.raises(ValidationError) as none:
        manager.create("empty", [])

    assert code_of(taken.value) == "roles.name_taken"
    assert code_of(unknown.value) == "roles.permissions_unknown"
    assert unknown.value.coded.params["names"] == "apps.fly"
    assert code_of(none.value) == "roles.permissions_none"


def test_a_role_somebody_holds_is_not_deleted(manager: RoleManager, store: NoustStore) -> None:
    manager.create("reader", ["apps.read"])
    give(store, "ana", "viewer", "reader")

    with pytest.raises(AccessConflictError) as caught:
        manager.delete("reader")

    assert code_of(caught.value) == "roles.in_use" and caught.value.coded.params["count"] == 1
    assert manager.roles.get("reader") is not None


def test_a_role_bound_to_a_team_is_not_deleted_either(
    manager: RoleManager, store: NoustStore
) -> None:
    manager.create("reader", ["apps.read"])
    ana = give(store, "ana", "viewer")
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO access_bindings (account_id, scope_kind, scope_id, role, created_at) "
            "VALUES (?, 'team', 1, 'reader', 1.0)",
            (ana,),
        )

    with pytest.raises(AccessConflictError) as caught:
        manager.delete("reader")

    assert code_of(caught.value) == "roles.in_use"


def test_a_role_noust_ships_is_put_back_never_deleted_and_one_of_ones_own_is_not_put_back(
    manager: RoleManager,
) -> None:
    manager.create("reader", ["apps.read"])

    with pytest.raises(AccessError) as kept:
        manager.delete("viewer")
    with pytest.raises(AccessError) as mine:
        manager.reset("reader")

    assert code_of(kept.value) == "roles.seeded_kept"
    assert kept.value.coded.params["command"] == "noust access role reset viewer"
    assert code_of(mine.value) == "roles.reset_custom"


def test_a_role_is_copied_into_a_role_of_ones_own(manager: RoleManager) -> None:
    manager.update("operator", permissions=["apps.read", "apps.operate"])

    copy = manager.duplicate("operator", "night-shift")

    assert copy.kind == "custom" and copy.label == "Operator (copy)"
    assert copy.held == manager.roles.require("operator").held
    # The original is left as it was.
    assert manager.roles.require("operator").edited
    with pytest.raises(AccessConflictError):
        manager.duplicate("operator", "night-shift")
    # Even the owner can be copied: the copy is the operator's own, and is as editable as any.
    everything = manager.duplicate("owner", "everything", label="All of it")
    assert everything.held == ALL_PERMISSIONS and everything.label == "All of it"


def test_a_server_keeps_a_hundred_roles_of_its_own_at_most(manager: RoleManager) -> None:
    for number in range(100):
        manager.create(f"role-{number:03d}", ["apps.read"])

    with pytest.raises(AccessConflictError) as caught:
        manager.create("one-more", ["apps.read"])

    assert code_of(caught.value) == "roles.too_many"


# ----------------------------------------------------- what a framework asks of roles


def test_where_duties_are_kept_apart_a_role_cannot_join_them(
    manager: RoleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    controls_on(monkeypatch, "separation_of_duties")

    with pytest.raises(AccessError) as caught:
        manager.create("everything", ["accounts.manage", "apps.manage"])
    manager.create("governs", ["accounts.manage", "audit.read"])

    assert code_of(caught.value) == "roles.separation_role"
    assert caught.value.coded.params["governing"] == "accounts.manage"
    # Editing a role Noust ships is held to the same: the administrator joins them until it is split.
    with pytest.raises(AccessError):
        manager.update("admin", permissions=sorted(builtin.ADMIN_STANDARD))
    split = manager.update("admin", permissions=sorted(builtin.ADMIN))
    assert split.edited and "accounts.manage" not in split.held


def test_a_role_cannot_be_changed_to_join_duties_for_an_account_that_holds_it(
    manager: RoleManager, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    controls_on(monkeypatch, "single_role_per_account")
    manager.create("governs", ["accounts.manage"])
    give(store, "ana", "operator", "governs")
    manager.roles.require("governs")

    # The role alone is fine under this control; the account that also runs the server is not.
    with pytest.raises(AccessError) as caught:
        manager.update("governs", permissions=["accounts.manage", "apps.read"])
    assert code_of(caught.value) == "roles.separation_account"
    assert manager.roles.require("governs").held == frozenset({"accounts.manage", "self"})


def test_every_refusal_of_the_role_manager_is_a_sentence_with_a_code(
    manager: RoleManager,
) -> None:
    refusals = []
    for attempt in (
        lambda: manager.roles.require("nothing"),
        lambda: manager.delete("viewer"),
        lambda: manager.reset("owner"),
        lambda: manager.create("xx", ["apps.fly"]),
    ):
        with pytest.raises(NoustError) as caught:
            attempt()
        refusals.append(caught.value)

    assert [code_of(e) for e in refusals] == [
        "roles.unknown",
        "roles.seeded_kept",
        "roles.owner_protected",
        "roles.permissions_unknown",
    ]
    assert all(e.message and e.coded.text == e.message for e in refusals)
    with pytest.raises(AccessNotFoundError):
        manager.roles.require("nothing")
