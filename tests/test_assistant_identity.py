# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who the built-in assistant is when it acts: a token of its own with no sudo mode (item 171).

What it may do is decided by the server as it is for a person: the token holds at most what its
owner holds and what a preset of the MCP tools needs, never what the floor forbids, sees the
owner's teams and nothing else, and dies at once when the assistant is turned off.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.exceptions import SecurityError
from noust.core.store import NoustStore
from noust.web.assistant import identity as identity_module
from noust.web.assistant.identity import (
    ASSISTANT_PRESET,
    NEVER_HELD,
    AssistantIdentity,
    assistant_actor,
    preset_permissions,
)
from noust.web.permissions import Permission
from noust.web.server import get_token_manager
from tests.support.teams_support import World, build_world

P = Permission

SHOP = "shop.acme.example"
BLOG = "blog.globex.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    def record(event: str, **fields: Any) -> None:
        seen.append((event, fields))

    monkeypatch.setattr(identity_module, "audit_record", record)
    return seen


def account(name: str) -> Any:
    found = get_token_manager().accounts.find(name)
    assert found is not None
    return found


def issue(name: str, conversation: str = "c" * 32, **options: Any) -> Any:
    return AssistantIdentity().issue(account(name), conversation, **options)


def header(issued: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {issued.token}"}


class TestWhatItHolds:
    def test_what_is_never_held_is_the_assistants_floor_and_its_own_permission(self) -> None:
        assert ASSISTANT_FLOOR <= NEVER_HELD
        assert P.ASSISTANT_USE in NEVER_HELD
        assert NEVER_HELD - ASSISTANT_FLOOR == {P.ASSISTANT_USE}

    def test_no_preset_holds_what_is_never_held(self) -> None:
        for preset in ("inspect", "deploy", "control"):
            assert not preset_permissions(preset) & NEVER_HELD
            assert "self" in preset_permissions(preset)

    def test_a_preset_edited_later_cannot_hand_over_the_floor(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Whatever a preset says and whatever the owner holds, the floor is taken off at issuing.
        monkeypatch.setattr(
            identity_module,
            "preset_permissions",
            lambda preset=ASSISTANT_PRESET: frozenset(
                {"self", P.APPS_READ, *ASSISTANT_FLOOR, P.ASSISTANT_USE}
            ),
        )
        issued = issue("root-admin")
        assert P.APPS_READ in issued.permissions
        assert not set(issued.permissions) & NEVER_HELD

    def test_what_changes_the_server_beyond_the_floor_is_not_cut_off_by_the_mcp_list(self) -> None:
        # The MCP's list keeps a client outside away from creating applications and restoring
        # copies; the assistant is asked to propose them, so they are not in its floor.
        for permission in (
            P.APPS_MANAGE,
            P.DATABASES_WRITE,
            P.DATABASES_MANAGE,
            P.BACKUPS_MANAGE,
            P.SERVER_MANAGE,
        ):
            assert permission not in ASSISTANT_FLOOR

    def test_an_administrator_gets_the_intersection_and_never_what_the_floor_forbids(
        self, world: World
    ) -> None:
        issued = issue("root-admin")
        held = get_token_manager().permissions_held_by(account("root-admin"))
        assert set(issued.permissions) == (held & preset_permissions(ASSISTANT_PRESET)) | {"self"}
        assert not set(issued.permissions) & NEVER_HELD
        for forbidden in (P.SECRETS_REVEAL, P.ROOT_EQUIVALENT, P.SETTINGS_MANAGE, P.ASSISTANT_USE):
            assert forbidden not in issued.permissions

    def test_an_operator_gets_what_an_operator_holds_of_it(self, world: World) -> None:
        issued = issue("omar")
        assert P.APPS_OPERATE in issued.permissions
        assert P.APPS_DEPLOY in issued.permissions
        assert P.APPS_MANAGE not in issued.permissions

    def test_a_person_without_the_permission_has_none(self, world: World) -> None:
        # A viewer holds no assistant.use.
        with pytest.raises(SecurityError) as caught:
            issue("aurora")
        assert "assistant.use" in caught.value.details

    def test_a_token_never_holds_more_than_its_owner(self, world: World) -> None:
        issued = issue("omar")
        held = get_token_manager().permissions_held_by(account("omar"))
        assert set(issued.permissions) <= held

    def test_it_lives_hours_and_is_never_asked_for_sudo_mode(self, world: World) -> None:
        issued = issue("omar")
        records = {r["name"]: r for r in get_token_manager().list_api_tokens()}
        record = records[issued.name]
        assert record["allow_elevated"] is False
        assert record["caller"] == "assistant"
        assert record["created_by"] == "assistant"
        assert record["owner_account_id"] == account("omar").id
        assert 3 * 3600 < record["expires_at"] - record["created_at"] <= 4 * 3600 + 1


class TestWhoCallsIsInTheRequest:
    def test_the_payload_says_assistant_for_its_token_and_token_for_any_other(
        self, world: World
    ) -> None:
        manager = get_token_manager()
        mine = manager.verify_api_token(issue("omar").token, "10.0.0.1")
        other = manager.verify_api_token(world.people["omar"].token, "10.0.0.1")
        assert mine is not None and mine["caller"] == "assistant"
        assert other is not None and other["caller"] == "token"

    def test_the_audit_trail_names_the_assistant_as_the_way_in(self, world: World) -> None:
        from noust.web.permissions.principal import actor_of

        issued = issue("omar")
        payload = get_token_manager().verify_api_token(issued.token, "10.0.0.1")
        actor = actor_of(payload)
        assert actor is not None
        assert actor.via == "assistant"
        assert actor.name == f"token:{issued.name}"
        assert "omar" in issued.name

    def test_an_ordinary_token_is_reached_by_bearer_as_ever(self, world: World) -> None:
        from noust.web.permissions.principal import actor_of

        payload = get_token_manager().verify_api_token(world.people["omar"].token, "10.0.0.1")
        actor = actor_of(payload)
        assert actor is not None and actor.via == "bearer"

    def test_no_client_can_ask_for_it_through_the_tokens_api(self) -> None:
        import inspect

        from noust.web.api import auth as auth_api

        # The request has no field for it and the route passes none: the manager is the only door.
        fields = (
            getattr(auth_api.ApiTokenRequest, "model_fields", None)
            or auth_api.ApiTokenRequest.__fields__
        )
        assert "caller" not in fields
        assert "caller=" not in inspect.getsource(auth_api.create_api_token)

    def test_the_manager_refuses_what_the_assistants_token_must_never_be(
        self, world: World
    ) -> None:
        manager = get_token_manager()
        omar = account("omar")
        with pytest.raises(SecurityError):
            manager.create_api_token(
                "a1", "read", 1, owner=omar, allow_elevated=True, caller="assistant"
            )
        with pytest.raises(SecurityError):
            manager.create_api_token("a2", "read", 1, caller="assistant")
        with pytest.raises(SecurityError):
            manager.create_api_token("a3", "read", 1, owner=omar, caller="admin")
        manager.create_api_token("a4", "read", 1, owner=omar, caller="token")
        assert {r["name"]: r for r in manager.list_api_tokens()}["a4"]["caller"] is None


class TestWhatItSees:
    def test_it_sees_the_teams_of_the_person_and_no_others(self, world: World) -> None:
        issued = issue("omar")
        listed = world.client.get("/api/apps", headers=header(issued))
        assert listed.status_code == 200, listed.text
        assert {a["domain"] for a in listed.json()["apps"]} == {SHOP}
        assert world.client.get(f"/api/apps/{BLOG}", headers=header(issued)).status_code == 404

    def test_it_cannot_reach_the_configuration_the_assistants_own_included(
        self, world: World
    ) -> None:
        headers = header(issue("root-admin"))
        assert world.client.get("/api/auth/tokens", headers=headers).status_code in (200, 403)
        assert (
            world.client.put(
                "/api/mcp/settings", headers=headers, json={"enabled": True}
            ).status_code
            == 403
        )
        assert world.client.patch("/api/config", headers=headers, json={}).status_code == 403

    def test_what_asks_for_sudo_mode_is_refused_it_even_where_the_permission_is_held(
        self, world: World
    ) -> None:
        # An operator may restart; stopping is a change a token reaches. What a token cannot do is
        # pass a route that asks for sudo mode: nothing the assistant holds opens it.
        headers = header(issue("omar"))
        response = world.client.delete(f"/api/apps/{SHOP}", headers=headers)
        assert response.status_code in (403, 404)
        assert (
            get_token_manager().verify_api_token(issue("omar", "d" * 32).token, None)[  # type: ignore[index]
                "elevation_exempt"
            ]
            is False
        )


class TestTurningItOff:
    def test_every_token_of_the_assistant_dies_and_only_those(
        self, world: World, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        first = issue("omar", "a" * 32)
        second = issue("root-admin", "b" * 32)
        assert world.client.get("/api/apps", headers=header(first)).status_code == 200
        assert AssistantIdentity().revoke_all() == 2
        assert world.client.get("/api/apps", headers=header(first)).status_code == 401
        assert world.client.get("/api/apps", headers=header(second)).status_code == 401
        # A person's own token is not the assistant's.
        assert (
            world.client.get("/api/apps", headers=world.people["omar"].headers).status_code == 200
        )
        assert (
            "assistant.off",
            {"actor": None, "target": "setting:assistant", "details": {"revoked": 2}},
        ) in events

    def test_one_conversation_can_be_taken_back_alone(self, world: World) -> None:
        keep = issue("omar", "a" * 32)
        drop = issue("omar", "b" * 32)
        assert AssistantIdentity().revoke_conversation("b" * 32) == 1
        assert world.client.get("/api/apps", headers=header(drop)).status_code == 401
        assert world.client.get("/api/apps", headers=header(keep)).status_code == 200

    def test_issuing_is_on_the_record_as_the_assistant_acting_for_the_person(
        self, world: World, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        issued = issue("omar", "e" * 32)
        name, fields = events[0]
        assert name == "assistant.token"
        assert fields["actor"].via == "assistant"
        assert fields["actor"].name == f"token:{issued.name}"
        assert fields["details"]["person"] == "omar"

    def test_the_actor_helper_gives_the_same_name(self, world: World) -> None:
        actor = assistant_actor(account("omar"), "assistant:abc:omar:1")
        assert (actor.kind, actor.via, actor.name) == (
            "token",
            "assistant",
            "token:assistant:abc:omar:1",
        )

    def test_the_token_is_not_in_its_own_repr(self, world: World) -> None:
        issued = issue("omar")
        assert issued.token not in repr(issued)
