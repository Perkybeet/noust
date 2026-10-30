# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/roles`` and the roles of an account over HTTP (4.1, item 96).

What is pinned is what the console's screen of roles and people draws from: one list with the
five roles of every release and the protected owner, who holds each, what a framework makes of
them, what a newer release offers an edited role, the actions each admits; the roles of an account
with its effective permissions; and, for every refusal, a code (``coded``) the console translates.
Reading is ``accounts.read``; changing is ``accounts.manage`` in sudo mode.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.access import builtin
from noust.core.accounts import AuthPolicy, passwords
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.permissions import ALL_PERMISSIONS
from noust.web.server import create_app, get_token_manager

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


@pytest.fixture
def app(sandbox: Path, real_config: Any) -> Any:
    return create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
        )
    )


class Person:
    """A signed-in account in its own browser."""

    def __init__(self, app: Any, username: str, *roles: str) -> None:
        manager = get_token_manager().accounts
        account = manager.create(username, None, roles=list(roles), password=PASSWORD)
        secret = manager.begin_totp(account.id)
        codes = manager.confirm_totp(account.id, totp.totp_now(secret))
        assert codes is not None
        self.codes = list(codes)
        self.client = TestClient(app, client=("testclient", 50000))
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": PASSWORD, "totp_code": self.codes.pop()},
        )
        assert response.status_code == 200, response.text
        self.csrf = response.json()["csrf_token"]

    def headers(self) -> dict[str, str]:
        return {CSRF_HEADER_NAME: self.csrf}

    def elevate(self) -> None:
        response = self.client.post(
            "/api/auth/elevate",
            json={"password": PASSWORD, "code": self.codes.pop()},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text

    def get(self, path: str) -> Any:
        return self.client.get(path)

    def post(self, path: str, body: Any = None) -> Any:
        return self.client.post(path, json=body, headers=self.headers())

    def patch(self, path: str, body: Any) -> Any:
        return self.client.patch(path, json=body, headers=self.headers())

    def put(self, path: str, body: Any) -> Any:
        return self.client.put(path, json=body, headers=self.headers())

    def delete(self, path: str) -> Any:
        return self.client.delete(path, headers=self.headers())


def code_of(response: Any) -> str:
    """The code a refusal carries for the console to translate."""
    body = response.json()
    assert body.get("coded"), body
    return str(body["coded"]["code"])


@pytest.fixture
def owner(app: Any) -> Person:
    person = Person(app, "olga", "admin")
    person.elevate()
    return person


# ------------------------------------------------------------------- reading the list


def test_the_list_is_the_five_roles_and_the_owner_with_what_a_screen_draws(
    app: Any, owner: Person
) -> None:
    Person(app, "vera", "viewer")

    body = owner.get("/api/roles").json()

    names = [role["name"] for role in body["roles"]]
    assert names == ["viewer", "operator", "admin", "security", "auditor", "owner"]
    viewer = body["roles"][0]
    assert viewer["kind"] == "seeded" and viewer["follows_noust"] and not viewer["edited"]
    assert viewer["holders"] == ["vera"] and viewer["holder_count"] == 1
    assert viewer["shipped_permissions"] == sorted(builtin.VIEWER)
    assert viewer["can_edit"] and viewer["can_reset"] is False and viewer["can_delete"] is False
    admin = body["roles"][2]
    # The administrator Noust ships holds both halves of what a framework keeps apart.
    assert admin["joins_duties"] and "accounts.manage" in admin["governing"] and admin["running"]
    final = body["roles"][-1]
    assert final["protected"] and final["permissions"] == sorted(ALL_PERMISSIONS)
    assert not final["can_edit"] and not final["can_delete"] and not final["can_reset"]
    assert final["joins_duties"] is False
    assert body["permissions"].keys() == ALL_PERMISSIONS
    assert body["owners"] == 0 and body["duties_apart"] is False


def test_reading_the_list_is_for_whoever_reads_accounts(app: Any) -> None:
    viewer = Person(app, "vera", "viewer")
    auditor = Person(app, "aurora", "auditor")

    assert viewer.get("/api/roles").status_code == 403
    assert viewer.get("/api/roles/admin").status_code == 403
    assert auditor.get("/api/roles").status_code == 200


def test_changing_a_role_needs_the_security_role_and_sudo_mode(app: Any) -> None:
    auditor = Person(app, "aurora", "auditor")
    admin = Person(app, "adela", "admin")

    assert (
        auditor.post("/api/roles", {"name": "ci", "permissions": ["apps.read"]}).status_code == 403
    )
    # An administrator holds accounts.manage unless a framework split it; sudo mode is still asked.
    assert admin.post("/api/roles", {"name": "ci", "permissions": ["apps.read"]}).status_code == 403
    admin.elevate()
    assert admin.post("/api/roles", {"name": "ci", "permissions": ["apps.read"]}).status_code == 201


def test_one_role_is_read_by_name_or_is_not_found_with_a_code(owner: Person) -> None:
    found = owner.get("/api/roles/operator")
    missing = owner.get("/api/roles/wizard")

    assert found.status_code == 200 and found.json()["name"] == "operator"
    assert missing.status_code == 404 and code_of(missing) == "roles.unknown"
    assert missing.json()["coded"]["params"]["command"] == "noust access role list"


# ------------------------------------------------------------ defining, changing, deleting


def test_a_role_of_ones_own_is_defined_changed_and_deleted(owner: Person) -> None:
    created = owner.post(
        "/api/roles",
        {
            "name": "release-manager",
            "permissions": ["apps.read", "apps.deploy"],
            "label": "Releases",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "custom" and body["label"] == "Releases"
    assert body["can_delete"] and not body["can_reset"] and body["shipped_permissions"] is None

    changed = owner.patch("/api/roles/release-manager", {"permissions": ["apps.read"]})
    assert changed.status_code == 200 and changed.json()["permissions"] == ["apps.read", "self"]

    removed = owner.delete("/api/roles/release-manager")
    assert removed.status_code == 200
    assert owner.get("/api/roles/release-manager").status_code == 404


def test_every_refusal_of_the_roles_carries_a_code_and_a_fix(owner: Person) -> None:
    owner.post("/api/roles", {"name": "reader", "permissions": ["apps.read"]})

    taken = owner.post("/api/roles", {"name": "reader", "permissions": ["apps.read"]})
    system = owner.post("/api/roles", {"name": "admin", "permissions": ["apps.read"]})
    invalid = owner.post("/api/roles", {"name": "Not A Slug", "permissions": ["apps.read"]})
    unknown = owner.post("/api/roles", {"name": "other", "permissions": ["apps.fly"]})
    empty = owner.post("/api/roles", {"name": "empty", "permissions": []})
    protected = owner.patch("/api/roles/owner", {"label": "Boss"})
    kept = owner.delete("/api/roles/viewer")
    custom_reset = owner.post("/api/roles/reader/reset")

    assert [
        (r.status_code, code_of(r))
        for r in (taken, system, invalid, unknown, empty, protected, kept, custom_reset)
    ] == [
        (409, "roles.name_taken"),
        (400, "roles.name_system"),
        (400, "roles.name_invalid"),
        (400, "roles.permissions_unknown"),
        (400, "roles.permissions_none"),
        (400, "roles.owner_protected"),
        (400, "roles.seeded_kept"),
        (400, "roles.reset_custom"),
    ]
    assert taken.json()["coded"]["params"] == {
        "name": "reader",
        "command": "noust access role show reader",
    }
    assert all(r.json()["hint"] for r in (taken, system, protected, kept))


def test_a_role_somebody_holds_is_not_deleted(app: Any, owner: Person) -> None:
    owner.post("/api/roles", {"name": "reader", "permissions": ["apps.read"]})
    Person(app, "vera", "reader")

    refused = owner.delete("/api/roles/reader")

    assert refused.status_code == 409 and code_of(refused) == "roles.in_use"
    assert refused.json()["coded"]["params"]["count"] == 1


def test_a_role_is_duplicated_and_the_copy_is_the_operators_own(owner: Person) -> None:
    owner.patch("/api/roles/operator", {"permissions": ["apps.read", "apps.operate"]})

    copy = owner.post("/api/roles/operator/duplicate", {"name": "night-shift"})

    assert copy.status_code == 201
    assert copy.json()["kind"] == "custom" and copy.json()["label"] == "Operator (copy)"
    assert copy.json()["permissions"] == ["apps.operate", "apps.read", "self"]
    again = owner.post("/api/roles/operator/duplicate", {"name": "night-shift"})
    assert again.status_code == 409 and code_of(again) == "roles.name_taken"


# ------------------------------------------------------------ editing a shipped role, offers


def test_a_role_noust_ships_is_edited_put_back_and_follows_noust_again(owner: Person) -> None:
    edited = owner.patch("/api/roles/operator", {"permissions": ["apps.read", "apps.operate"]})
    assert edited.status_code == 200
    body = edited.json()
    assert body["edited"] and not body["follows_noust"] and body["can_reset"]
    assert body["shipped_permissions"] == sorted(builtin.OPERATOR)

    back = owner.post("/api/roles/operator/reset")

    assert back.status_code == 200
    assert back.json()["follows_noust"] and back.json()["permissions"] == sorted(builtin.OPERATOR)
    assert not back.json()["can_reset"]


def test_what_a_newer_release_offers_an_edited_role_is_taken_or_left(
    owner: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner.patch("/api/roles/operator", {"permissions": ["apps.read", "apps.operate"]})
    shipped = builtin.SHIPPED["operator"]
    monkeypatch.setitem(
        builtin.SHIPPED,
        "operator",
        builtin.Shipped(
            "operator",
            shipped.label,
            shipped.description,
            shipped.permissions | {"backups.manage", "databases.manage"},
        ),
    )

    offered = owner.get("/api/roles/operator").json()
    assert offered["offers"] == ["backups.manage", "databases.manage"]

    settled = owner.post(
        "/api/roles/operator/offers",
        {"accept": ["backups.manage"], "dismiss": ["databases.manage"]},
    )

    assert settled.status_code == 200
    assert "backups.manage" in settled.json()["permissions"] and settled.json()["offers"] == []
    refused = owner.post("/api/roles/operator/offers", {"accept": ["apps.manage"]})
    assert refused.status_code == 400 and code_of(refused) == "roles.offer_unknown"


# --------------------------------------------------------------------- what a framework makes


def test_the_review_names_the_role_that_joins_the_duties_a_framework_keeps_apart(
    app: Any, owner: Person, real_config: Any
) -> None:
    real_config.set("security.separation_of_duties", True)

    body = owner.get("/api/roles/review").json()

    assert body["duties_apart"] and body["controls"] == ["separation_of_duties"]
    joining = [role["name"] for role in body["roles"] if role["joins_duties"]]
    assert joining == ["admin"]
    assert body["suggestion"].startswith("noust access role set admin --permission ")
    # The owner is listed, as protected, and never counted among the roles that join them.
    assert next(r for r in body["roles"] if r["name"] == "owner")["protected"]


def test_where_a_framework_keeps_duties_apart_a_role_cannot_be_defined_joining_them(
    owner: Person, real_config: Any
) -> None:
    real_config.set("security.separation_of_duties", True)

    refused = owner.post(
        "/api/roles", {"name": "both", "permissions": ["accounts.manage", "apps.manage"]}
    )

    assert refused.status_code == 400 and code_of(refused) == "roles.separation_role"
    assert refused.json()["coded"]["params"]["governing"] == "accounts.manage"


# --------------------------------------------------------------- the roles of an account


def test_an_account_shows_its_roles_and_what_they_hold_together(app: Any, owner: Person) -> None:
    owner.post("/api/roles", {"name": "db-reader", "permissions": ["databases.write"]})
    Person(app, "vera", "viewer")

    changed = owner.put("/api/auth/accounts/vera/roles", {"roles": ["viewer", "db-reader"]})

    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["roles"] == ["viewer", "db-reader"] and body["role"] == "viewer"
    assert "databases.write" in body["permissions"] and "apps.read" in body["permissions"]
    assert body["owner"] is False
    listed = {a["username"]: a for a in owner.get("/api/auth/accounts").json()["accounts"]}
    assert listed["vera"]["roles"] == ["viewer", "db-reader"]
    assert listed["olga"]["permissions"] == sorted(builtin.ADMIN_STANDARD)


def test_the_roles_of_an_account_apply_at_its_next_request(app: Any, owner: Person) -> None:
    vera = Person(app, "vera", "viewer")
    assert vera.get("/api/auth/accounts").status_code == 403

    owner.put("/api/auth/accounts/vera/roles", {"roles": ["viewer", "auditor"]})

    assert vera.get("/api/auth/accounts").status_code == 200
    assert vera.get("/api/auth/session").json()["roles"] == ["viewer", "auditor"]


def test_a_role_the_list_does_not_have_and_no_roles_are_refused_with_codes(
    app: Any, owner: Person
) -> None:
    Person(app, "vera", "viewer")

    unknown = owner.put("/api/auth/accounts/vera/roles", {"roles": ["viewer", "wizard"]})
    none = owner.put("/api/auth/accounts/vera/roles", {"roles": []})

    assert unknown.status_code == 400 and code_of(unknown) == "roles.unknown"
    assert none.status_code == 422 or code_of(none) == "roles.no_roles"


def test_the_owner_role_is_given_by_an_owner(app: Any) -> None:
    founder = Person(app, "fran", "admin", "owner")
    founder.elevate()
    other = Person(app, "olga", "admin")
    other.elevate()
    Person(app, "vera", "viewer")

    by_a_non_owner = other.put("/api/auth/accounts/vera/roles", {"roles": ["viewer", "owner"]})
    by_the_owner = founder.put("/api/auth/accounts/vera/roles", {"roles": ["viewer", "owner"]})

    assert by_a_non_owner.status_code == 400 and code_of(by_a_non_owner) == "roles.owner_by_owner"
    assert by_the_owner.status_code == 200 and by_the_owner.json()["owner"] is True
    assert by_the_owner.json()["permissions"] == sorted(ALL_PERMISSIONS)
    # Taking it is the same decision.
    taken = other.put("/api/auth/accounts/vera/roles", {"roles": ["viewer"]})
    assert taken.status_code == 400 and code_of(taken) == "roles.owner_by_owner"


def test_the_last_owner_who_can_sign_in_cannot_be_disabled_or_removed(app: Any) -> None:
    Person(app, "fran", "admin", "owner")
    helper = Person(app, "hana", "admin", "owner")
    helper.elevate()
    other = Person(app, "olga", "admin")
    other.elevate()

    # Two owners can sign in: one may go. Then hana is the last.
    assert helper.post("/api/auth/accounts/fran/disable", {"reason": "left"}).status_code == 200
    disabled = other.post("/api/auth/accounts/hana/disable", {"reason": "x"})
    removed = other.delete("/api/auth/accounts/hana")
    reduced = other.put("/api/auth/accounts/hana/roles", {"roles": ["admin"]})

    assert (disabled.status_code, code_of(disabled)) == (400, "roles.last_owner")
    assert (removed.status_code, code_of(removed)) == (400, "roles.last_owner")
    # Taking the role is an owner's decision first, and the guard is the same either way.
    assert reduced.status_code == 400
    assert code_of(reduced) in {"roles.owner_by_owner", "roles.last_owner"}


def test_the_first_account_made_through_the_console_is_the_owner(app: Any) -> None:
    from tests.support.web_accounts import elevate_master, master_sign_in

    client = TestClient(app, client=("testclient", 50000))
    token, csrf = master_sign_in(client)
    elevate_master(client, token, csrf)

    created = client.post(
        "/api/auth/accounts",
        json={"username": "first", "role": "admin", "password": PASSWORD},
        headers={CSRF_HEADER_NAME: csrf},
    )

    assert created.status_code == 201, created.text
    assert created.json()["roles"] == ["admin", "owner"] and created.json()["owner"] is True
    second = client.post(
        "/api/auth/accounts",
        json={"username": "second", "roles": ["viewer"], "password": PASSWORD},
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert second.status_code == 201 and second.json()["roles"] == ["viewer"]


def test_the_owner_role_is_given_by_an_owner_through_every_door(app: Any) -> None:
    founder = Person(app, "fran", "admin", "owner")
    founder.elevate()
    other = Person(app, "olga", "admin")
    other.elevate()
    Person(app, "vera", "viewer")
    accounts = get_token_manager().accounts
    TeamStore(accounts.store).create_team("Acme", slug="acme")
    binding = {"username": "vera", "role": "owner", "scope": "instance"}
    account = {"username": "nora", "roles": ["owner"], "password": PASSWORD}
    invitation = {"username": "ivan", "roles": ["owner"]}
    team_invitation = {"username": "tim", "role": "owner", "team": "acme"}

    for refused in (
        other.post("/api/access/bindings", binding),
        other.post("/api/auth/accounts", account),
        other.post("/api/auth/invitations", invitation),
        other.post("/api/auth/invitations", team_invitation),
    ):
        assert refused.status_code == 400 and code_of(refused) == "roles.owner_by_owner", (
            refused.text
        )
    assert all(accounts.find(name) is None for name in ("nora", "ivan", "tim"))
    assert accounts.require("vera").roles == ("viewer",)

    # An owner can, through each of them.
    assert founder.post("/api/access/bindings", binding).status_code == 201
    assert founder.post("/api/auth/accounts", account).status_code == 201
    assert founder.post("/api/auth/invitations", invitation).status_code == 201
    assert founder.post("/api/auth/invitations", team_invitation).status_code == 201
