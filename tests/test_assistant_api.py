# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The built-in assistant's routes: settings with a key that never comes back, the probe, turning it
off, usage, and a person's own conversations (item 171).

The provider is a scripted model on this machine (``tests/support/scripted_model.py``); nothing
reaches a real one.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.assistant.client import Usage
from noust.core.assistant.conversations import Conversations
from noust.core.assistant.limits import Limits, UsageMeter
from noust.core.config import Config
from noust.core.fs import RealFileSystem, set_fs
from noust.core.store import NoustStore
from noust.web.api import assistant as assistant_api
from noust.web.assistant.identity import AssistantIdentity
from noust.web.permissions.access import AccessManager
from noust.web.server import get_token_manager
from tests.support.scripted_model import Reply, ScriptedModel
from tests.support.teams_support import World, build_world, make_person

pytestmark = pytest.mark.allow_sockets

KEY = "sk-provider-9d8c7b6a5f4e3d2c1b0a"
GOOD = Reply.call("get_status", {"service": "probe"})


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    target = tmp_path / "etc" / "config.yaml"
    target.parent.mkdir()
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", target)
    set_fs(RealFileSystem())
    Config.reset_instance()
    built = build_world(tmp_path, monkeypatch)
    # An operator of the whole server, beside omar, who belongs to one team only.
    built.people["ops"] = make_person(built.teams, "ops", "operator")
    AccessManager().bind("ops", "operator", scope="instance")
    try:
        yield built
    finally:
        NoustStore.reset_instance()
        Config.reset_instance()
        set_fs(None)


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
        yield scripted


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(assistant_api, "audit_record", lambda event, **f: seen.append((event, f)))
    return seen


def admin(world: World) -> dict[str, str]:
    return world.people["admin"].headers


def configure(world: World, model: ScriptedModel, **extra: Any) -> Any:
    model.script(GOOD)
    return world.client.put(
        "/api/assistant/settings",
        headers=admin(world),
        json={
            "base_url": model.base_url,
            "model": "tiny",
            "api_key": KEY,
            "allow_private_host": True,
            **extra,
        },
    )


def turn_on(world: World, model: ScriptedModel) -> None:
    response = configure(world, model, enabled=True)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True


def session_of(world: World, who: str) -> dict[str, Any]:
    response = world.client.get("/api/auth/session", headers=world.people[who].headers)
    assert response.status_code == 200, response.text
    return response.json()["assistant"]


class TestSettings:
    def test_nothing_is_chosen_and_it_is_off_and_the_key_is_not_even_a_field(
        self, world: World
    ) -> None:
        response = world.client.get("/api/assistant/settings", headers=admin(world))
        assert response.status_code == 200
        document = response.json()
        assert document["enabled"] is False
        assert document["configured"] is False
        assert document["has_key"] is False
        assert "api_key" not in document
        assert document["limits"]["steps_per_turn"] == 20

    def test_changing_it_is_for_those_who_manage_settings_in_sudo_mode(self, world: World) -> None:
        # An operator may read how it is set up and may not change it.
        response = world.client.put(
            "/api/assistant/settings", headers=world.people["ops"].headers, json={"model": "x"}
        )
        assert response.status_code == 403
        # A session that never confirmed it is its owner is refused too: that is sudo mode.
        unconfirmed = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=get_token_manager().accounts.find("root-admin")
        )
        response = world.client.put(
            "/api/assistant/settings",
            headers={"Authorization": f"Bearer {unconfirmed['token']}"},
            json={"model": "x"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_choosing_a_provider_probes_it_and_never_shows_the_key(
        self, world: World, model: ScriptedModel
    ) -> None:
        response = configure(world, model)
        assert response.status_code == 200, response.text
        assert KEY not in response.text
        document = response.json()
        assert document["has_key"] is True
        assert document["key_set_at"]
        assert document["location"] == "machine"
        assert document["probe"]["reachable"] is True
        assert document["probe"]["tools"] is True
        assert document["calls_tools"] is True
        assert document["probe"]["sentences"][0]["code"] == "assistant.probe.answers"
        assert document["enabled"] is False
        again = world.client.get("/api/assistant/settings", headers=admin(world))
        assert KEY not in again.text
        # The key went to the provider as the header it is for, and nowhere else.
        assert model.chat_requests()[0].headers["authorization"] == f"Bearer {KEY}"
        assert KEY not in model.everything_received()

    def test_the_host_is_listed_only_with_the_operators_consent(
        self, world: World, model: ScriptedModel
    ) -> None:
        response = world.client.put(
            "/api/assistant/settings",
            headers=admin(world),
            json={"base_url": model.base_url, "model": "tiny"},
        )
        assert response.status_code == 400
        assert response.json()["coded"]["code"] == "assistant.provider.destination_refused"
        assert "127.0.0.1" not in (Config().get("security.allow_private_hosts") or [])
        assert model.requests == []

    def test_it_is_turned_on_after_a_probe_found_the_provider_answering(
        self, world: World, model: ScriptedModel
    ) -> None:
        refused = world.client.put(
            "/api/assistant/settings", headers=admin(world), json={"enabled": True}
        )
        assert refused.status_code == 400
        turn_on(world, model)
        document = world.client.get("/api/assistant/settings", headers=admin(world)).json()
        assert document["enabled"] is True

    def test_a_limit_is_changed_and_one_that_is_no_limit_is_refused(self, world: World) -> None:
        ok = world.client.put(
            "/api/assistant/settings", headers=admin(world), json={"limits": {"steps_per_turn": 9}}
        )
        assert ok.status_code == 200
        assert ok.json()["limits"]["steps_per_turn"] == 9
        assert ok.json()["limits"]["actions_per_hour"] == 10
        refused = world.client.put(
            "/api/assistant/settings", headers=admin(world), json={"limits": {"steps_per_turn": 0}}
        )
        assert refused.status_code == 400

    def test_a_hub_turns_it_on_like_any_other(self, world: World, model: ScriptedModel) -> None:
        # A central that deploys nothing has servers to look at: the assistant is the console's.
        configure(world, model)
        Config().set("central.role", "hub")
        Config().write()
        response = world.client.put(
            "/api/assistant/settings", headers=admin(world), json={"enabled": True}
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    def test_what_is_recorded_names_what_changed_and_never_the_key(
        self, world: World, model: ScriptedModel, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        configure(world, model, enabled=True)
        names = [event for event, _ in events]
        assert names[:2] == ["assistant.settings", "assistant.probe"]
        assert names[-1] == "assistant.enable"
        settings_event = events[0][1]
        assert settings_event["details"]["key_set"] is True
        assert KEY not in str(events)


class TestTheProbe:
    def test_it_says_what_it_found_and_keeps_it(self, world: World, model: ScriptedModel) -> None:
        configure(world, model)
        model.script(Reply.say("I would rather talk."))
        response = world.client.post("/api/assistant/probe", headers=admin(world))
        assert response.status_code == 200
        document = response.json()
        assert document["reachable"] is True
        assert document["tools"] is False
        assert "assistant.probe.no_tools" in [s["code"] for s in document["sentences"]]
        kept = world.client.get("/api/assistant/settings", headers=admin(world)).json()
        assert kept["calls_tools"] is False

    def test_there_is_nothing_to_probe_before_a_provider_is_chosen(self, world: World) -> None:
        response = world.client.post("/api/assistant/probe", headers=admin(world))
        assert response.status_code == 400

    def test_an_operator_cannot_ask_it_to_call_out(self, world: World) -> None:
        response = world.client.post("/api/assistant/probe", headers=world.people["ops"].headers)
        assert response.status_code == 403


class TestTurningItOff:
    def test_it_is_off_and_every_token_it_held_is_dead(
        self, world: World, model: ScriptedModel
    ) -> None:
        turn_on(world, model)
        omar = get_token_manager().accounts.find("omar")
        issued = AssistantIdentity().issue(omar, "a" * 32)
        live = {"Authorization": f"Bearer {issued.token}"}
        assert world.client.get("/api/apps", headers=live).status_code == 200
        response = world.client.post("/api/assistant/off", headers=admin(world))
        assert response.status_code == 200, response.text
        assert response.json()["revoked"] == 1
        assert response.json()["settings"]["enabled"] is False
        assert world.client.get("/api/apps", headers=live).status_code == 401
        assert session_of(world, "omar")["can_use"] is False

    def test_only_those_who_manage_settings_turn_it_off(self, world: World) -> None:
        response = world.client.post("/api/assistant/off", headers=world.people["ops"].headers)
        assert response.status_code == 403


class TestPresets:
    def test_they_come_with_the_places_and_the_default_limits(self, world: World) -> None:
        document = world.client.get("/api/assistant/presets", headers=admin(world)).json()
        assert {p["id"] for p in document["presets"]} >= {"ollama", "mistral"}
        assert "eu" in document["locations"]
        assert document["limits"]["input_tokens_per_conversation"] == 400_000


class TestUsage:
    def test_the_month_and_one_conversation_with_the_limits(self, world: World) -> None:
        omar = get_token_manager().accounts.find("omar")
        chat = Conversations().create(omar.id)
        meter = UsageMeter(Limits(), {}, "USD")
        meter.record_step(chat.id, omar.id, "tiny", Usage(1000, 100))
        meter.record_step("someone-elses", 999, "tiny", Usage(500, 50, estimated=True))
        document = world.client.get(
            "/api/assistant/usage",
            headers=world.people["omar"].headers,
            params={"conversation": chat.id},
        ).json()
        assert document["conversation"]["steps"] == 1
        assert document["conversation"]["prompt_tokens"] == 1000
        assert document["month"]["steps"] == 2
        assert document["limits"]["spend_per_month"] == 20.0
        assert document["actions_last_hour"] == 0

    def test_a_conversation_that_is_not_the_callers_is_not_shown(self, world: World) -> None:
        other = Conversations().create(get_token_manager().accounts.find("root-admin").id)
        document = world.client.get(
            "/api/assistant/usage",
            headers=world.people["omar"].headers,
            params={"conversation": other.id},
        ).json()
        assert document["conversation"] is None

    def test_a_viewer_does_not_see_it(self, world: World) -> None:
        response = world.client.get("/api/assistant/usage", headers=world.people["aud"].headers)
        assert response.status_code == 403

    def test_the_master_token_is_not_a_person(self, world: World) -> None:
        response = world.client.get("/api/assistant/usage", headers=world.master)
        assert response.status_code in (400, 403)


class TestConversations:
    def make(self, world: World, who: str) -> tuple[Conversations, Any, str]:
        account = get_token_manager().accounts.find(who)
        chats = Conversations()
        chat = chats.create(account.id)
        chats.append(chat.id, account.id, "user", "Why is shop down from <ip-1>?")
        chats.save_markers(chat.id, account.id, {"<ip-1>": "203.0.113.7"})
        return chats, account, chat.id

    def test_a_person_lists_reads_and_deletes_their_own(self, world: World) -> None:
        _chats, _account, identifier = self.make(world, "omar")
        headers = world.people["omar"].headers
        listed = world.client.get("/api/assistant/conversations", headers=headers).json()
        assert [c["id"] for c in listed] == [identifier]
        read = world.client.get(f"/api/assistant/conversations/{identifier}", headers=headers)
        assert read.status_code == 200
        assert read.json()["messages"][0]["content"] == "Why is shop down from 203.0.113.7?"
        gone = world.client.delete(f"/api/assistant/conversations/{identifier}", headers=headers)
        assert gone.json() == {"deleted": True}
        assert world.client.get("/api/assistant/conversations", headers=headers).json() == []

    def test_another_person_gets_what_a_conversation_that_does_not_exist_gets(
        self, world: World
    ) -> None:
        _chats, _account, identifier = self.make(world, "omar")
        headers = world.people["admin"].headers
        theirs = world.client.get(f"/api/assistant/conversations/{identifier}", headers=headers)
        nothing = world.client.get("/api/assistant/conversations/nope", headers=headers)
        assert theirs.status_code == nothing.status_code == 404
        assert theirs.json()["error"] == nothing.json()["error"]
        assert world.client.delete(
            f"/api/assistant/conversations/{identifier}", headers=headers
        ).json() == {"deleted": False}
        assert world.client.get("/api/assistant/conversations", headers=headers).json() == []

    def test_deleting_one_takes_back_its_token_and_is_on_the_record(
        self, world: World, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        _chats, account, identifier = self.make(world, "omar")
        issued = AssistantIdentity().issue(account, identifier)
        live = {"Authorization": f"Bearer {issued.token}"}
        assert world.client.get("/api/apps", headers=live).status_code == 200
        world.client.delete(
            f"/api/assistant/conversations/{identifier}", headers=world.people["omar"].headers
        )
        assert world.client.get("/api/apps", headers=live).status_code == 401
        assert events[-1][0] == "assistant.conversation.delete"


class TestWhatTheSessionSays:
    def test_off_for_everybody_it_is_a_reason_and_nothing_to_draw(self, world: World) -> None:
        seen = session_of(world, "omar")
        assert seen["enabled"] is False
        assert seen["can_use"] is False
        assert seen["reason"]["code"] == "assistant.unavailable.off"
        assert seen["reason"]["text"]

    def test_on_it_is_offered_to_those_who_may_use_it_and_to_nobody_else(
        self, world: World, model: ScriptedModel
    ) -> None:
        turn_on(world, model)
        omar = session_of(world, "omar")
        assert omar == {
            "enabled": True,
            "can_use": True,
            "tools": True,
            "reason": None,
            "model": "tiny",
            "location": "machine",
            "mask_personal": True,
        }
        viewer = session_of(world, "aud")
        assert viewer["enabled"] is True
        assert viewer["can_use"] is False
        assert viewer["reason"]["code"] == "assistant.unavailable.permission"

    def test_a_model_that_does_not_call_tools_is_said_to_only_explain(
        self, world: World, model: ScriptedModel
    ) -> None:
        model.script(Reply.say("no"))
        world.client.put(
            "/api/assistant/settings",
            headers=admin(world),
            json={
                "base_url": model.base_url,
                "model": "tiny",
                "api_key": KEY,
                "allow_private_host": True,
            },
        )
        world.client.put("/api/assistant/settings", headers=admin(world), json={"enabled": True})
        seen = session_of(world, "omar")
        assert seen["can_use"] is True
        assert seen["tools"] is False

    def test_the_master_token_is_not_a_person_so_it_is_not_offered_it(
        self, world: World, model: ScriptedModel
    ) -> None:
        turn_on(world, model)
        response = world.client.get("/api/auth/session", headers=world.master)
        assert response.status_code == 200
        seen = response.json()["assistant"]
        assert seen["can_use"] is False
        assert seen["reason"]["code"] == "assistant.unavailable.account"

    def test_on_a_hub_it_is_offered_as_anywhere_else(
        self, world: World, model: ScriptedModel
    ) -> None:
        turn_on(world, model)
        Config().set("central.role", "hub")
        Config().write()
        seen = session_of(world, "omar")
        assert seen["can_use"] is True
        assert seen["reason"] is None
