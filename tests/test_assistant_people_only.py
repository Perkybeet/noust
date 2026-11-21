# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The assistant and the approval policy are for people (items 171 and 184).

A token acts for its owner but is narrowed: to some teams, to some permissions, and to what the approval policy
says a token may do. The assistant is the owner's own conversation, so a token that could use it would read through
it what the token cannot, and act through it where the policy says a token never acts. And the table of that policy
is edited by a person: a token that the policy forbids would otherwise lift its own refusal.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute

from noust.core.accounts import passwords
from noust.core.callers import PERSON, caller_of
from noust.core.teams import Scope
from noust.web import events
from noust.web.api import assistant, assistant_chat
from noust.web.api.deps import require_person
from noust.web.auth import SESSION_COOKIE_NAME, verify_credential
from tests.support.assistant_lab import KEY, SHOP, Lab, build_lab
from tests.support.policy_people import issue
from tests.support.scripted_model import Reply, ScriptedModel
from tests.support.teams_support import make_app

pytestmark = pytest.mark.allow_sockets

GLOBEX = "blog.globex.example"


@pytest.fixture(autouse=True)
def cheap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def real_config(real_config: Any) -> Any:
    return real_config


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
        yield scripted


@pytest.fixture
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)


class TestTheAssistantIsForPeople:
    def test_a_token_that_asks_for_the_permission_to_use_it_does_not_hold_it(
        self, lab: Lab
    ) -> None:
        lab.configure()
        headers = issue(lab.marta.account, "chat-only", "assistant.use")

        for method, path in (
            ("post", "/api/assistant/conversations"),
            ("get", "/api/assistant/conversations"),
            ("get", "/api/assistant/usage?conversation=abc"),
        ):
            response = getattr(lab.client, method)(path, headers=headers)
            assert response.status_code == 403, (path, response.text)

    def test_what_a_token_cannot_read_it_cannot_read_through_the_assistant_either(
        self, lab: Lab
    ) -> None:
        # A token bound to its owner's team that holds only the assistant's permission: the logs of an
        # application of another team are out of its reach, and are not given to the model for it.
        from noust.web.assistant.turns import registry  # noqa: F401 - the turn would run there

        lab.configure()
        lab.teams.create_team("Globex", slug="globex")
        target = lab.teams.resolve_target(Scope.everything(), team="globex")
        make_app(lab.store, GLOBEX, target, lab.root)
        headers = issue(lab.marta.account, "chat-only", "assistant.use")
        lab.model.script(Reply.say("hello"))

        began = lab.client.post("/api/assistant/conversations", json={}, headers=headers)

        assert began.status_code == 403
        assert lab.requests() == []

    def test_a_token_the_policy_forbids_does_not_act_through_the_assistant(self, lab: Lab) -> None:
        lab.configure()
        lab.policy(
            ("token", "production", "apps.operate", "never"),
            ("assistant", "production", "apps.operate", "alone"),
        )
        headers = issue(lab.marta.account, "ci", "apps.operate", "apps.read", "assistant.use")

        direct = lab.client.post(f"/api/apps/{SHOP}/restart", headers=headers)
        began = lab.client.post("/api/assistant/conversations", json={}, headers=headers)

        assert direct.status_code == 403
        assert began.status_code == 403
        assert lab.restarts == []

    def test_a_token_still_reads_the_settings_and_the_spending(self, lab: Lab) -> None:
        lab.configure()
        headers = issue(lab.marta.account, "reader", "settings.read")

        assert lab.client.get("/api/assistant/settings", headers=headers).status_code == 200
        assert lab.client.get("/api/assistant/usage", headers=headers).status_code == 200
        assert lab.client.get("/api/assistant/presets", headers=headers).status_code == 200

    def test_a_person_still_talks_to_it(self, lab: Lab) -> None:
        lab.configure()

        assert lab.marta.post("/api/assistant/conversations", {}).status_code == 200

    def test_the_session_of_a_token_does_not_say_it_can_use_it(self, lab: Lab) -> None:
        lab.configure()
        headers = issue(lab.marta.account, "chat-only", "assistant.use", "self")

        session = lab.client.get("/api/auth/session", headers=headers)

        assert session.status_code == 200, session.text
        assert "assistant.use" not in session.json()["permissions"]
        assert session.json()["assistant"]["can_use"] is False

    def test_every_route_that_converses_asks_for_a_person(self) -> None:
        # What a route added to the conversation tomorrow needs to be covered too: the router asks, not
        # each handler. The settings, the probe, the presets and the totals are not a conversation.
        conversational = {
            ("GET", "/conversations"),
            ("GET", "/conversations/{conversation_id}"),
            ("DELETE", "/conversations/{conversation_id}"),
        }
        uncovered = []
        for module in (assistant, assistant_chat):
            for route in module.router.routes:
                assert isinstance(route, APIRoute)
                key_paths = {(m, route.path) for m in route.methods}
                if module is assistant and not key_paths & conversational:
                    continue
                if not _asks_for_a_person(route):
                    uncovered.append(sorted(key_paths))
        assert uncovered == []

    def test_a_credential_that_is_not_a_person_is_refused_where_the_router_asks(self) -> None:
        payload = {"type": "api_token", "owner_account_id": 1, "permissions": frozenset()}
        assert caller_of(payload) != PERSON

        import asyncio

        with pytest.raises(Exception, match="person") as raised:
            asyncio.run(require_person(payload))
        assert getattr(raised.value, "status_code", 403) == 403

        assert asyncio.run(require_person({"account_id": 1, "type": "session"})) == {
            "account_id": 1,
            "type": "session",
        }


class _Listener:
    """The part of a request the event stream asks about: whether the client is still there."""

    async def is_disconnected(self) -> bool:
        return False


async def _heard(
    session: dict[str, Any], name: str, account: int, *, wait: float = 1.0
) -> list[dict[str, Any]]:
    """
    Open the event stream as a credential, publish one frame addressed to an account, and read what arrived.

    Args:
        session: The payload the stream is authenticated with.
        name: The event the frame is published as.
        account: The account the frame is for.
        wait: How long to keep listening.

    Returns:
        The payloads of the frames of that event the stream carried.
    """
    stream = events._stream(_Listener(), session)  # type: ignore[arg-type]
    carried: list[dict[str, Any]] = []
    await asyncio.wait_for(stream.__anext__(), 2)  # ": connected"
    events.hub.publish(name, {"for": account, "type": "text", "delta": "the shop is fine"})
    deadline = asyncio.get_running_loop().time() + wait
    while (left := deadline - asyncio.get_running_loop().time()) > 0:
        try:
            frame = await asyncio.wait_for(stream.__anext__(), left)
        except (asyncio.TimeoutError, StopAsyncIteration):
            break
        head, _, rest = frame.partition("\n")
        if head == f"event: {name}":
            carried.append(json.loads(rest.removeprefix("data: ").strip()))
    await stream.aclose()
    return carried


def _published_with_an_audience() -> dict[str, list[str]]:
    """
    Returns:
        For each event the code publishes to one account (``hub.publish("<name>", {"for": ...})``), where.
    """
    found: dict[str, list[str]] = {}
    root = Path(events.__file__).resolve().parents[1]
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "publish"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and isinstance(node.args[1], ast.Dict)
            ):
                continue
            keys = [k.value for k in node.args[1].keys if isinstance(k, ast.Constant)]
            if "for" in keys:
                found.setdefault(node.args[0].value, []).append(f"{path.name}:{node.lineno}")
    return found


class TestTheStreamIsForPeople:
    @pytest.fixture(autouse=True)
    def quiet_machine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # What the stream says of the machine is not what is asked here, and reading it runs systemctl.
        monkeypatch.setattr(events, "machine_frame", lambda: None)
        monkeypatch.setattr(events, "metrics_frame", lambda: None)

    def person_session(self, lab: Lab) -> dict[str, Any]:
        session = verify_credential(
            lab.marta.client.cookies[SESSION_COOKIE_NAME],
            "testclient",
            resource="/events",
            source="cookie",
        )
        assert session is not None
        assert caller_of(session) == PERSON
        return session

    def token_session(self, lab: Lab, *permissions: str) -> dict[str, Any]:
        headers = issue(lab.marta.account, "listener", *permissions)
        session = verify_credential(
            headers["Authorization"].removeprefix("Bearer "),
            "127.0.0.1",
            resource="/events",
            source="bearer",
        )
        assert session is not None
        assert caller_of(session) != PERSON
        return session

    @pytest.mark.parametrize("name", sorted(events.PERSONAL_EVENTS))
    def test_a_token_does_not_hear_what_its_owner_is_told(self, lab: Lab, name: str) -> None:
        owner = int(lab.marta.account.id)
        session = self.token_session(lab, "apps.read")
        assert session["owner_account_id"] == owner

        heard = asyncio.run(_heard(session, name, owner))

        assert heard == []

    @pytest.mark.parametrize("name", sorted(events.PERSONAL_EVENTS))
    def test_the_person_does_hear_it(self, lab: Lab, name: str) -> None:
        owner = int(lab.marta.account.id)

        heard = asyncio.run(_heard(self.person_session(lab), name, owner))

        assert [h["delta"] for h in heard] == ["the shop is fine"]

    @pytest.mark.parametrize("name", sorted(events.PERSONAL_EVENTS))
    def test_a_person_does_not_hear_what_another_is_told(self, lab: Lab, name: str) -> None:
        other = int(lab.marta.account.id) + 1000

        assert asyncio.run(_heard(self.person_session(lab), name, other)) == []

    def test_every_event_that_has_an_audience_is_listed_as_personal(self) -> None:
        # What a channel added tomorrow needs to be covered too: an event published to one account is
        # delivered by the stream to a person and to no token, and it is the list that says which are.
        published = _published_with_an_audience()
        assert sorted(published) == sorted(events.PERSONAL_EVENTS), published


def _asks_for_a_person(route: APIRoute) -> bool:
    def walk(dependant: Any) -> bool:
        return dependant.call is require_person or any(walk(d) for d in dependant.dependencies)

    return walk(route.dependant) or any(
        d.dependency is require_person for d in route.dependencies if hasattr(d, "dependency")
    )


class TestThePolicyIsEditedByPeople:
    def forbidden_token(self, lab: Lab) -> dict[str, str]:
        lab.policy(("token", "production", "apps.operate", "never"))
        return issue(
            lab.marta.account,
            "ci-admin",
            "apps.operate",
            "apps.read",
            "security.manage",
            "settings.read",
            elevated=True,
        )

    def test_a_token_cannot_lift_its_own_never(self, lab: Lab) -> None:
        headers = self.forbidden_token(lab)
        before = lab.client.post(f"/api/apps/{SHOP}/restart", headers=headers)
        change = {
            "cells": [],
            "reset": [
                {"caller": "token", "environment": "production", "permission": "apps.operate"}
            ],
        }

        lifted = lab.client.put("/api/config/approval-policy", headers=headers, json=change)
        after = lab.client.post(f"/api/apps/{SHOP}/restart", headers=headers)

        assert before.status_code == 403
        assert lifted.status_code == 403
        assert "person" in lifted.text
        assert after.status_code == 403
        assert lab.restarts == []

    def test_nor_can_it_ask_what_the_change_would_do(self, lab: Lab) -> None:
        headers = self.forbidden_token(lab)

        previewed = lab.client.post(
            "/api/config/approval-policy/preview",
            headers=headers,
            json={"cells": [], "reset": []},
        )

        assert previewed.status_code == 403

    def test_a_token_still_reads_the_table(self, lab: Lab) -> None:
        headers = self.forbidden_token(lab)

        assert lab.client.get("/api/config/approval-policy", headers=headers).status_code == 200

    def test_a_person_still_edits_it(self, lab: Lab) -> None:
        lab.marta.elevate()
        change = {
            "cells": [
                {
                    "caller": "token",
                    "environment": "production",
                    "permission": "apps.operate",
                    "value": "propose",
                }
            ],
            "reset": [],
        }

        assert lab.marta.put("/api/config/approval-policy", change).status_code == 200
        assert (
            lab.marta.post(
                "/api/config/approval-policy/preview", {"cells": [], "reset": []}
            ).status_code
            == 200
        )


def test_the_dependency_reads_the_class_of_the_caller_and_nothing_the_caller_says() -> None:
    assert "caller_of" in inspect.getsource(require_person)
