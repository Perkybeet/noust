# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
With a fleet the assistant is the central's, and works on its servers (item 171).

A central that manages other servers (and the owner's is a hub that deploys nothing) turns the assistant on
once. What it looks at is the server the person chose in the console, reached **through the central like
everything else the console shows of that server**: the same tool, called through the proxy to the node with
the assistant's token, so the central re-implements nothing a node does. What this holds to:

- the node's own answer is what the model reads, and what a node does not have (a node of 4.2) is said in words;
- what asks for sudo mode does not pass, because the central answers for its operator's sudo mode only when a
  person confirmed it, and the assistant's token never does;
- the node's audit sees the assistant and, in its token's name, the person behind it;
- the policy that rules is the central's, where the person is, and a change on a server is judged as production;
- a member of a team reaches only the servers of their team.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from noust.core.accounts import passwords
from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.web.api import node_proxy
from noust.web.api import nodes as nodes_api
from noust.web.api.openapi import ELEVATION_EXTENSION
from noust.web.assistant.identity import AssistantIdentity
from tests.support.assistant_lab import KEY, SHOP, Lab, build_lab
from tests.support.node_proxy import FakeManager, FakeTunnels, ServedNode
from tests.support.policy_people import Person
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

NODE = "web-2"
OUR_APP = "ours.example.com"


class Node:
    """A server of the fleet: a small console of its own that records what it is asked."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.app = FastAPI()
        node = self

        def note(request: Request) -> None:
            node.seen.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                }
            )

        @self.app.get("/api/openapi.json")
        def schema() -> Response:
            return JSONResponse(
                {
                    "openapi": "3.1.0",
                    "info": {"title": "node", "version": "4.3.0"},
                    "paths": {
                        "/api/apps/{domain}/logs": {"get": {}},
                        "/api/apps/{domain}/restart": {"post": {}},
                        "/api/apps/{domain}/limits": {"patch": {ELEVATION_EXTENSION: True}},
                    },
                }
            )

        @self.app.get("/api/apps/{domain}/logs")
        def logs(domain: str, request: Request) -> Response:
            note(request)
            return JSONResponse(
                {
                    "domain": domain,
                    "logs": "node says: out of memory\nrestart counter 5\n",
                    "lines": 100,
                }
            )

        @self.app.post("/api/apps/{domain}/restart")
        def restart(domain: str, request: Request) -> Response:
            note(request)
            return JSONResponse(
                {"success": True, "message": f"Application restarted: {domain}", "domain": domain}
            )

        @self.app.patch("/api/apps/{domain}/limits")
        def limits(domain: str, request: Request) -> Response:
            note(request)
            return JSONResponse({"domain": domain, "memory_max_mb": 512})


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
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
def node() -> Iterator[tuple[Node, ServedNode]]:
    fake = Node()
    with ServedNode(fake) as served:  # type: ignore[arg-type]
        yield fake, served


@pytest.fixture
def lab(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: ScriptedModel,
    node: tuple[Node, ServedNode],
) -> Iterator[Lab]:
    manager = FakeManager(FakeTunnels(node[1].port))
    monkeypatch.setattr(nodes_api, "node_manager", lambda: manager)
    monkeypatch.setattr(nodes_api, "tunnels", lambda: manager.tunnels)
    node_proxy.node_schemas.forget()
    yield from build_lab(sandbox, monkeypatch, model)


def last_tool_text(lab: Lab, request: int = -1) -> str:
    body = lab.requests()[request].body
    assert body is not None
    return str(body["messages"][-1]["content"])


class TestTheServerThePersonChose:
    def test_a_read_is_the_same_tool_called_on_the_server_through_the_central(
        self, lab: Lab, node: tuple[Node, ServedNode]
    ) -> None:
        lab.configure()
        lab.model.script(
            Reply.call("application_logs", {"domain": OUR_APP}), Reply.say("It runs out of memory.")
        )
        turn = lab.ask(lab.conversation(), "why?", server=NODE)

        assert turn["state"] == "done" and turn["server"] == NODE
        assert turn["steps"][0]["server"] == NODE
        assert turn["steps"][0]["tool"] == "application_logs"
        # The tool's answer is the node's own words.
        assert "node says: out of memory" in last_tool_text(lab, 1)
        # The node was asked as the central's fleet token, on behalf of the assistant, for a person.
        [seen] = node[0].seen
        assert (seen["method"], seen["path"]) == ("GET", f"/api/apps/{OUR_APP}/logs")
        actor = seen["headers"]["x-noust-actor"]
        assert "assistant:" in actor and "marta" in actor
        assert seen["headers"].get("x-noust-elevated", "false").lower() != "true"
        assert lab.bridge.calls == [("GET", f"/api/nodes/{NODE}/api/apps/{OUR_APP}/logs")]

    def test_a_change_on_a_server_is_a_proposal_and_names_the_path_through_the_central(
        self, lab: Lab, node: tuple[Node, ServedNode]
    ) -> None:
        lab.configure()
        lab.model.script(
            Reply.call("restart_application", {"domain": OUR_APP}), Reply.say("proposed")
        )
        turn = lab.ask(lab.conversation(), "restart it", server=NODE)

        [proposal] = turn["proposals"]
        assert (proposal["klass"], proposal["state"], proposal["server"]) == (
            "ask",
            "waiting",
            NODE,
        )
        assert proposal["call"]["path"] == f"/api/nodes/{NODE}/api/apps/{OUR_APP}/restart"
        assert proposal["environment"] == "production"
        assert node[0].seen == []
        assert {"name": "server", "value": NODE} in proposal["facts"]

    def test_where_the_centrals_policy_says_alone_it_is_made_on_the_server(
        self, lab: Lab, node: tuple[Node, ServedNode]
    ) -> None:
        lab.configure()
        # A call on a server is judged as production: the central holds no record of its applications.
        lab.policy(("assistant", "production", "apps.operate", "alone"))
        lab.model.script(Reply.call("restart_application", {"domain": OUR_APP}), Reply.say("done"))
        turn = lab.ask(lab.conversation(), f"restart {OUR_APP}", server=NODE)

        [proposal] = turn["proposals"]
        assert (proposal["klass"], proposal["state"]) == ("alone", "done")
        [seen] = node[0].seen
        assert (seen["method"], seen["path"]) == ("POST", f"/api/apps/{OUR_APP}/restart")
        assert "assistant:" in seen["headers"]["x-noust-actor"]

    def test_a_server_that_does_not_have_what_is_asked_says_so_in_words(self, lab: Lab) -> None:
        lab.configure()
        lab.model.script(Reply.call("server_health", {}), Reply.say("the node is old"))
        turn = lab.ask(lab.conversation(), "how is it?", server=NODE)

        assert turn["steps"][0]["state"] == "failed"
        assert "does not have this" in last_tool_text(lab, 1)
        assert "older version of Noust" in last_tool_text(lab, 1)

    def test_a_server_that_is_not_one_of_the_persons_is_refused_before_anything_is_asked(
        self, lab: Lab
    ) -> None:
        lab.configure()
        response = lab.begin(lab.conversation(), "hello", server="evil-99")
        assert response.status_code == 400
        assert response.json()["fields"]["server"]
        assert lab.requests() == []


class TestWhatTheCentralNeverVouches:
    def test_a_route_that_asks_for_sudo_mode_is_not_passed_by_the_assistants_token(
        self, lab: Lab, node: tuple[Node, ServedNode]
    ) -> None:
        lab.configure()
        token = AssistantIdentity().issue(lab.marta.account, "0123456789abcdef").token
        response = lab.client.patch(
            f"/api/nodes/{NODE}/api/apps/{OUR_APP}/limits",
            json={"memory_max_mb": 512},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert node[0].seen == []

    def test_the_floor_holds_through_a_server_too(
        self, lab: Lab, node: tuple[Node, ServedNode]
    ) -> None:
        lab.configure()
        token = AssistantIdentity().issue(lab.marta.account, "0123456789abcdef").token
        for path in ("/api/audit", "/api/accounts"):
            response = lab.client.get(
                f"/api/nodes/{NODE}/api{path[4:]}", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code in (403, 404)
        assert node[0].seen == []
        assert not set(
            AssistantIdentity().issue(lab.marta.account, "fedcba9876543210").permissions
        ) & (ASSISTANT_FLOOR)


class TestWithAllTheServers:
    def test_every_tool_of_a_server_asks_which_and_the_views_of_the_fleet_are_offered(
        self, lab: Lab, node: tuple[Node, ServedNode]
    ) -> None:
        lab.configure()
        lab.model.script(
            Reply.call("application_logs", {"domain": OUR_APP}),
            Reply.call("application_logs", {"domain": OUR_APP, "server": NODE}),
            Reply.say("it is on web-2"),
        )
        turn = lab.ask(lab.conversation(), "why?", server="*")

        tools = {t["function"]["name"]: t["function"] for t in lab.requests()[0].body["tools"]}  # type: ignore[index]
        assert "fleet_servers" in tools and "fleet_apps" in tools
        assert "server" in tools["application_logs"]["parameters"]["required"]
        assert (
            NODE in tools["application_logs"]["parameters"]["properties"]["server"]["description"]
        )
        system = lab.requests()[0].body["messages"][0]["content"]  # type: ignore[index]
        assert NODE in system and "which server" in system.lower()
        # The call that named no server was sent back to the model; the one that named it was made.
        assert "Missing argument(s): server" in last_tool_text(lab, 1)
        assert [s["tool"] for s in turn["steps"]] == ["application_logs"]
        assert [s["server"] for s in turn["steps"]] == [NODE]
        assert len(node[0].seen) == 1

    def test_the_views_of_the_fleet_are_read_through_the_centrals_own_routes(
        self, lab: Lab
    ) -> None:
        lab.configure()
        lab.model.script(Reply.call("fleet_servers", {}), Reply.say("one server"))
        turn = lab.ask(lab.conversation(), "what servers do I have?", server="*")

        assert turn["state"] == "done"
        assert [s["tool"] for s in turn["steps"]] == ["fleet_servers"]
        assert turn["steps"][0]["what"]["code"] == "assistant.step.fleet_servers"
        assert lab.bridge.calls == [("GET", "/api/fleet/servers")]

    def test_a_central_that_deploys_nothing_is_all_the_servers_unless_one_is_chosen(
        self, lab: Lab, real_config: Any
    ) -> None:
        real_config.set("central.role", "hub")
        lab.configure()
        lab.model.script(Reply.say("hello"))
        turn = lab.ask(lab.conversation(), "what servers do I have?")
        assert turn["server"] == "*"
        assert NODE in lab.requests()[0].body["messages"][0]["content"]  # type: ignore[index]


class TestATeamReachesItsOwnServersOnly:
    def a_member(self, lab: Lab) -> Person:
        # A team of their own: the default team is the whole instance, whose members see everything.
        lab.teams.create_team("Acme", slug="acme")
        return Person(lab.app, "omar", "operator", team="acme")

    def test_a_member_of_a_team_does_not_see_a_server_that_is_not_assigned_to_theirs(
        self, lab: Lab
    ) -> None:
        lab.configure()
        omar = self.a_member(lab)
        response = lab.begin(lab.conversation(omar), "hello", omar, server=NODE)
        assert response.status_code == 400
        assert response.json()["fields"]["server"]
        assert lab.requests() == []

    def test_and_what_is_another_teams_application_reads_as_one_that_does_not_exist(
        self, lab: Lab
    ) -> None:
        lab.configure()
        omar = self.a_member(lab)
        lab.model.script(Reply.call("application_logs", {"domain": SHOP}), Reply.say("not found"))
        turn = lab.ask(lab.conversation(omar), "look at the shop", omar)
        assert turn["steps"][0]["state"] == "failed"
        assert "listening on 3000" not in last_tool_text(lab, 1)
