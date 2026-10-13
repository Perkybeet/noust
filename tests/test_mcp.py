# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The MCP endpoint: a client with an API token inspects, deploys and controls (J-19, J-20, J-21).

A client's reach is its token's, and nothing more: a tool it may not call is not
offered, a team it is not in reads as absent, and what comes back never carries
a secret. What stops something being done by accident is a confirmation the
model has to give; what makes it accountable is the audit trail.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.store import NoustStore
from noust.web.jobs import Job, JobType
from noust.web.mcp import guardrails
from noust.web.mcp.tools import (
    BY_NAME,
    FORBIDDEN_PERMISSIONS,
    GROUPS,
    TOOLS,
    build_request,
    check_arguments,
    input_schema,
)
from noust.web.permissions.registry import permission_for_route
from tests.teams_support import World, build_world, make_person

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"
PROTOCOL = "2025-06-18"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    """Two teams and their people, with MCP switched on and a configuration of its own."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    built = build_world(tmp_path, monkeypatch)
    Config().set("mcp.enabled", True)
    built.people["officer"] = make_person(
        built.teams, "officer", "security", permissions=["security.manage", "settings.read"]
    )
    try:
        yield built
    finally:
        Config.reset_instance()
        NoustStore.reset_instance()


def rpc(
    world: World,
    who: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    identifier: int | None = 1,
    headers: dict[str, str] | None = None,
) -> Any:
    """
    Send one JSON-RPC message to the endpoint.

    Args:
        world: The server.
        who: One of its people, whose token is the credential.
        method: The MCP method.
        params: Its parameters.
        identifier: The request id; None sends a notification.
        headers: Extra headers.

    Returns:
        The HTTP response.
    """
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if identifier is not None:
        body["id"] = identifier
    if params is not None:
        body["params"] = params
    return world.client.post(
        "/api/mcp",
        json=body,
        headers={**world.as_(who), "MCP-Protocol-Version": PROTOCOL, **(headers or {})},
    )


def result(world: World, who: str, method: str, params: dict[str, Any] | None = None) -> Any:
    """
    Args:
        world: The server.
        who: One of its people.
        method: The MCP method.
        params: Its parameters.

    Returns:
        The ``result`` of the answer.
    """
    response = rpc(world, who, method, params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return body["result"]


def call(world: World, who: str, tool: str, **arguments: Any) -> dict[str, Any]:
    """
    Args:
        world: The server.
        who: One of its people.
        tool: A tool's name.
        **arguments: Its arguments.

    Returns:
        The tool result: ``content`` and ``isError``.
    """
    return result(world, who, "tools/call", {"name": tool, "arguments": arguments})  # type: ignore[no-any-return]


def text(outcome: dict[str, Any]) -> str:
    """
    Args:
        outcome: A tool result.

    Returns:
        What the model reads.
    """
    return str(outcome["content"][0]["text"])


class TestTheTransport:
    def test_it_answers_nothing_while_it_is_off(self, world: World) -> None:
        Config().set("mcp.enabled", False)

        response = rpc(world, "alice", "ping")

        assert response.status_code == 404
        assert response.json()["error"] == "mcp_disabled"
        assert "noust mcp enable" in response.json()["hint"]

    def test_a_session_or_the_master_token_is_not_a_clients_credential(self, world: World) -> None:
        response = world.client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=world.master
        )

        assert response.status_code == 403
        assert response.json()["error"] == "mcp_token_required"

    def test_nobody_is_not_let_in(self, world: World) -> None:
        response = world.client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

        assert response.status_code == 401

    def test_a_page_from_another_site_is_refused(self, world: World) -> None:
        response = rpc(world, "alice", "ping", headers={"Origin": "https://evil.example"})

        assert response.status_code == 403
        assert response.json()["error"] == "mcp_origin_refused"

    def test_a_protocol_version_it_does_not_speak_is_refused(self, world: World) -> None:
        response = rpc(world, "alice", "ping", headers={"MCP-Protocol-Version": "1999-01-01"})

        assert response.status_code == 400
        assert "2025-06-18" in response.json()["hint"]

    def test_a_message_that_is_not_json_is_a_parse_error(self, world: World) -> None:
        response = world.client.post(
            "/api/mcp",
            content=b"{nope",
            headers={**world.as_("alice"), "Content-Type": "application/json"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32700

    def test_a_notification_is_acknowledged_and_not_answered(self, world: World) -> None:
        response = rpc(world, "alice", "notifications/initialized", identifier=None)

        assert response.status_code == 202 and response.content == b""

    def test_a_get_opens_no_stream(self, world: World) -> None:
        response = world.client.get("/api/mcp", headers=world.as_("alice"))

        assert response.status_code == 405
        assert response.headers["allow"] == "POST"

    def test_initialize_says_what_this_is_and_agrees_a_version(self, world: World) -> None:
        answer = result(world, "alice", "initialize", {"protocolVersion": "2025-03-26"})
        newest = result(world, "alice", "initialize", {"protocolVersion": "2030-01-01"})

        assert answer["protocolVersion"] == "2025-03-26"
        assert newest["protocolVersion"] == PROTOCOL
        assert answer["serverInfo"]["name"] == "noust"
        assert {"tools", "prompts", "resources"} <= set(answer["capabilities"])
        assert "never returned" in answer["instructions"]

    def test_ping_and_an_unknown_method(self, world: World) -> None:
        assert result(world, "alice", "ping") == {}

        unknown = rpc(world, "alice", "tools/destroy").json()
        assert unknown["error"]["code"] == -32601

    def test_a_batch_is_answered_in_one_array(self, world: World) -> None:
        response = world.client.post(
            "/api/mcp",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            ],
            headers=world.as_("alice"),
        )

        assert [item["id"] for item in response.json()] == [1, 2]

    def test_not_jsonrpc_is_an_invalid_request(self, world: World) -> None:
        response = world.client.post(
            "/api/mcp", json={"hello": "world"}, headers=world.as_("alice")
        )

        assert response.json()["error"]["code"] == -32600


class TestWhatIsOffered:
    def names(self, world: World, who: str) -> set[str]:
        return {tool["name"] for tool in result(world, who, "tools/list")["tools"]}

    def test_a_viewer_is_offered_only_what_it_can_call(self, world: World) -> None:
        names = self.names(world, "dana")

        assert "list_applications" in names and "application_logs" in names
        assert not names & {"deploy_application", "restart_application", "stop_application"}

    def test_an_operator_is_offered_deploying_and_controlling(self, world: World) -> None:
        names = self.names(world, "omar")

        assert {"deploy_application", "restart_application", "create_backup"} <= names

    def test_a_group_the_operator_turned_off_is_not_offered_to_anybody(self, world: World) -> None:
        Config().set("mcp.groups", ["inspect"])

        names = self.names(world, "omar")

        assert "list_applications" in names
        assert not names & {"deploy_application", "restart_application", "stop_application"}

    def test_a_tool_that_is_not_offered_cannot_be_called_either(self, world: World) -> None:
        Config().set("mcp.groups", ["inspect"])

        refused = rpc(
            world,
            "omar",
            "tools/call",
            {"name": "restart_application", "arguments": {"domain": ACME}},
        )

        assert refused.json()["error"]["code"] == -32602
        assert "Unknown tool" in refused.json()["error"]["message"]

    def test_the_schema_is_read_from_the_routes_and_hides_what_a_client_must_not_set(
        self, world: World
    ) -> None:
        tools = {t["name"]: t for t in result(world, "omar", "tools/list")["tools"]}

        deploy = tools["deploy_application"]["inputSchema"]
        rollback = tools["rollback_application"]["inputSchema"]
        env = tools["list_environment_variables"]["inputSchema"]
        assert deploy["required"] == ["domain"] and "force" in deploy["properties"]
        assert "schema_changed_ok" not in rollback["properties"]
        assert "confirm" in rollback["properties"]
        assert "unmask" not in env["properties"]
        assert tools["list_applications"]["annotations"]["readOnlyHint"] is True
        assert tools["stop_application"]["annotations"]["destructiveHint"] is True

    def test_no_tool_reaches_what_a_client_must_never_reach(self, world: World) -> None:
        for tool in TOOLS:
            needed = permission_for_route(tool.method, tool.path)
            assert needed is not None, tool.name
            assert needed not in FORBIDDEN_PERMISSIONS, f"{tool.name} needs {needed}"

    def test_every_tool_is_a_route_that_exists_with_the_parameters_it_names(
        self, world: World
    ) -> None:
        openapi = world.client.app.openapi()  # type: ignore[attr-defined]
        for tool in TOOLS:
            schema = input_schema(openapi, tool)
            for name in tool.path_parameters:
                assert name in schema["properties"], f"{tool.name}: {name}"
            assert tool.group in GROUPS
            assert (tool.destructive is False) or ("confirm" in schema["properties"])


class TestInspecting:
    def test_applications_are_the_tokens_own_teams(self, world: World) -> None:
        body = json.loads(text(call(world, "alice", "list_applications")))

        assert {a["domain"] for a in body["applications"]} == {ACME}

    def test_another_teams_application_reads_as_absent(self, world: World) -> None:
        outcome = call(world, "alice", "get_application", domain=GLOBEX)

        assert outcome["isError"] is True
        assert "404" in text(outcome)

    def test_an_administrator_sees_every_team(self, world: World) -> None:
        body = json.loads(text(call(world, "admin", "list_applications")))

        assert {a["domain"] for a in body["applications"]} == {ACME, GLOBEX}

    def test_a_token_bound_to_a_team_sees_that_team_only(self, world: World) -> None:
        from tests.test_bound_tokens import issue

        bound = {"Authorization": issue("root-admin", team="globex")["Authorization"]}
        response = world.client.post(
            "/api/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_applications", "arguments": {}},
            },
            headers=bound,
        )

        names = {
            a["domain"]
            for a in json.loads(response.json()["result"]["content"][0]["text"])["applications"]
        }
        assert names == {GLOBEX}

    def test_variables_are_listed_by_name_and_never_by_value(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        values = {
            "DATABASE_URL": "postgres://u:hunter2@db/x",
            "API_KEY": "sk_live_abcdef123456",
            "PORT": "3000",
        }
        monkeypatch.setattr("noust.web.api.apps.read_app_env", lambda app: dict(values))

        outcome = call(world, "alice", "list_environment_variables", domain=ACME)

        body = text(outcome)
        assert "DATABASE_URL" in body and "API_KEY" in body and "PORT" in body
        for value in ("hunter2", "sk_live_abcdef123456", "3000"):
            assert value not in body
        assert "unmask" not in json.dumps(outcome)

    def test_a_secret_in_a_log_never_reaches_the_model(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        journal = (
            "listening on 3000\n"
            "DATABASE_URL=postgres://app:s3cretpass@db.internal/shop\n"
            "STRIPE_SECRET_KEY: sk_live_0123456789abcdef\n"
            "Authorization: Bearer abcdefghijklmnop12345\n"
        )

        class FakeServices:
            def __init__(self, verbose: bool = False) -> None:
                pass

            def logs(self, name: str, lines: int = 100) -> str:
                return journal

        monkeypatch.setattr("noust.web.api.apps.ServiceManager", FakeServices)

        outcome = call(world, "alice", "application_logs", domain=ACME, lines=50)

        shown = text(outcome)
        assert "listening on 3000" in shown
        for secret in ("s3cretpass", "sk_live_0123456789abcdef", "abcdefghijklmnop12345"):
            assert secret not in shown

    def test_the_audit_trail_names_the_token_and_that_it_came_through_mcp(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.web.mcp.server.audit_record",
            lambda event, **kwargs: recorded.append({"event": event, **kwargs}),
        )

        call(world, "alice", "get_application", domain=ACME)

        entry = recorded[0]
        assert entry["event"] == "mcp.call" and entry["outcome"] == "ok"
        assert entry["actor"].via == "mcp" and entry["actor"].kind == "token"
        assert entry["actor"].name == "token:alice-token"
        assert entry["details"] == {"tool": "get_application", "domain": ACME, "status": 200}

    def test_a_refused_call_is_recorded_as_refused(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.web.mcp.server.audit_record",
            lambda event, **kwargs: recorded.append({"event": event, **kwargs}),
        )

        call(world, "alice", "get_application", domain=GLOBEX)

        assert recorded[0]["outcome"] == "denied"


class TestDoingThings:
    def test_a_deploy_queues_the_job_and_says_so(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[dict[str, Any]] = []

        class Jobs:
            def create_job(self, **kwargs: Any) -> Job:
                queued.append(kwargs)
                return Job(
                    id="abc123",
                    type=JobType.UPDATE,
                    name=kwargs["name"],
                    description=kwargs["description"],
                    metadata=kwargs["metadata"],
                    actor=kwargs["actor"],
                )

        monkeypatch.setattr("noust.web.api.jobs.get_job_manager", lambda: Jobs())

        outcome = call(world, "omar", "deploy_application", domain=ACME, force=True)

        assert outcome["isError"] is False
        assert json.loads(text(outcome))["job"]["id"] == "abc123"
        assert queued[0]["metadata"] == {"domain": ACME}
        assert queued[0]["actor"] == "token:omar-token"

    def test_deploying_another_teams_application_is_refused_by_the_api(self, world: World) -> None:
        outcome = call(world, "omar", "deploy_application", domain=GLOBEX, force=True)

        assert outcome["isError"] is True and "404" in text(outcome)

    def test_stopping_needs_to_be_said_twice(self, world: World, runner: Any) -> None:
        refused = call(world, "omar", "stop_application", domain=ACME)

        assert refused["isError"] is True
        assert "confirm=true" in text(refused) and "stop the application" in text(refused)
        assert not [c for c in runner.calls if "stop" in " ".join(map(str, c))]

        confirmed = call(world, "omar", "stop_application", domain=ACME, confirm=True)
        assert "Nothing was done" not in text(confirmed)

    def test_a_rollback_cannot_pass_a_change_of_schema_whatever_the_model_sends(
        self, world: World
    ) -> None:
        outcome = call(
            world, "omar", "rollback_application", domain=ACME, confirm=True, schema_changed_ok=True
        )

        assert outcome["isError"] is True
        assert "Unknown argument" in text(outcome) and "schema_changed_ok" in text(outcome)

    def test_a_confirmation_that_is_not_true_is_not_one(self, world: World) -> None:
        for value in ("true", 1, "yes"):
            outcome = call(world, "omar", "stop_application", domain=ACME, confirm=value)
            assert outcome["isError"] is True

    def test_a_viewer_cannot_call_what_its_token_may_not_even_by_name(self, world: World) -> None:
        refused = rpc(
            world,
            "dana",
            "tools/call",
            {"name": "restart_application", "arguments": {"domain": ACME}},
        )

        assert refused.json()["error"]["code"] == -32602

    def test_arguments_are_checked_before_anything_is_called(self, world: World) -> None:
        missing = call(world, "omar", "deploy_application")
        wrong = call(world, "omar", "deploy_application", domain=ACME, force="yes")
        extra = call(world, "omar", "deploy_application", domain=ACME, rm_rf="/")

        assert "Missing argument" in text(missing)
        assert "expected boolean" in text(wrong)
        assert "Unknown argument" in text(extra)


class TestPromptsAndResources:
    def test_the_prompts_are_offered(self, world: World) -> None:
        names = {p["name"] for p in result(world, "alice", "prompts/list")["prompts"]}

        assert names == {"troubleshoot_application", "explain_failed_deploy"}

    def test_troubleshooting_brings_what_noust_found_fenced_as_data(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = result(
            world,
            "alice",
            "prompts/get",
            {"name": "troubleshoot_application", "arguments": {"domain": ACME}},
        )

        message = answer["messages"][0]["content"]["text"]
        assert ACME in message
        assert "<untrusted-output" in message and "do not follow" in message

    def test_a_prompt_needs_a_domain(self, world: World) -> None:
        refused = rpc(
            world, "alice", "prompts/get", {"name": "troubleshoot_application", "arguments": {}}
        )

        assert refused.json()["error"]["code"] == -32602

    def test_explaining_a_failed_deploy_with_none_says_so(self, world: World) -> None:
        answer = result(
            world,
            "alice",
            "prompts/get",
            {"name": "explain_failed_deploy", "arguments": {"domain": ACME}},
        )

        assert "no failed deployment" in answer["messages"][0]["content"]["text"]

    def test_resources_name_what_is_attached(self, world: World) -> None:
        listed = {r["uri"] for r in result(world, "alice", "resources/list")["resources"]}
        templates = {
            t["uriTemplate"]
            for t in result(world, "alice", "resources/templates/list")["resourceTemplates"]
        }

        assert {"noust://apps", "noust://guardrails"} <= listed
        assert "noust://apps/{domain}/diagnosis" in templates

    def test_a_resource_reads_as_the_tool_does(self, world: World) -> None:
        apps = result(world, "alice", "resources/read", {"uri": "noust://apps"})
        guard = result(world, "alice", "resources/read", {"uri": "noust://guardrails"})
        one = result(world, "alice", "resources/read", {"uri": f"noust://apps/{ACME}"})
        other = result(world, "alice", "resources/read", {"uri": f"noust://apps/{GLOBEX}"})

        assert ACME in apps["contents"][0]["text"] and GLOBEX not in apps["contents"][0]["text"]
        assert "never receives a secret" in guard["contents"][0]["text"]
        assert ACME in one["contents"][0]["text"]
        assert "404" in other["contents"][0]["text"]

    def test_an_unknown_resource_is_an_invalid_parameter(self, world: World) -> None:
        refused = rpc(world, "alice", "resources/read", {"uri": "noust://etc/shadow"})

        assert refused.json()["error"]["code"] == -32602

    def test_reading_is_off_when_the_inspect_group_is(self, world: World) -> None:
        Config().set("mcp.groups", ["deploy"])

        refused = rpc(world, "alice", "resources/read", {"uri": "noust://apps"})

        assert refused.json()["error"]["code"] == -32602


class TestTheGuardrails:
    def test_known_secret_shapes_are_scrubbed_from_text(self) -> None:
        scrubbed = guardrails.scrub_text(
            "redis://:topsecret1@cache:6379\nPASSWORD=hunter22\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        )

        assert (
            "topsecret1" not in scrubbed and "hunter22" not in scrubbed and "MIIabc" not in scrubbed
        )

    def test_a_secret_named_key_hides_its_value_in_a_structure(self) -> None:
        safe = guardrails.hide_secrets(
            {
                "name": "shop",
                "db": {"password": "p4ss", "host": "db"},
                "tokens": ["a"],
                "port": 3000,
            }
        )

        assert safe["db"] == {"password": guardrails.REDACTED, "host": "db"}
        assert safe["name"] == "shop" and safe["port"] == 3000

    def test_a_result_is_bounded_and_says_so(self) -> None:
        long = "x" * (guardrails.MAX_RESULT_CHARS + 10)

        assert "cut: 10 more characters" in guardrails.limit(long)
        assert guardrails.tail("a" * 5 + long).startswith("... (cut:")

    def test_the_nesting_is_bounded(self) -> None:
        deep: Any = "leaf"
        for _ in range(30):
            deep = {"a": deep}

        assert guardrails.REDACTED in json.dumps(guardrails.hide_secrets(deep))


class TestTheSettings:
    def test_a_security_officer_reads_and_changes_them(self, world: World) -> None:
        status = world.client.get("/api/mcp/status", headers=world.as_("officer"))
        changed = world.client.put(
            "/api/mcp/settings",
            json={"enabled": False, "groups": ["inspect"]},
            headers=world.as_("officer"),
        )

        assert status.status_code == 200 and status.json()["enabled"] is True
        assert status.json()["endpoint"].endswith("/api/mcp")
        assert {t["name"] for t in status.json()["tools"]} == set(BY_NAME)
        assert changed.status_code == 200, changed.text
        assert changed.json()["enabled"] is False and changed.json()["groups"] == ["inspect"]

    def test_nobody_else_turns_it_on(self, world: World) -> None:
        for who in ("alice", "omar", "dana"):
            refused = world.client.put(
                "/api/mcp/settings", json={"enabled": True}, headers=world.as_(who)
            )
            assert refused.status_code == 403, who

    def test_an_administrator_cannot_patch_it_through_the_config_either(self, world: World) -> None:
        refused = world.client.patch(
            "/api/config", json={"path": "mcp.enabled", "value": False}, headers=world.as_("admin")
        )

        assert refused.status_code == 403

    def test_a_group_that_does_not_exist_is_refused(self, world: World) -> None:
        refused = world.client.put(
            "/api/mcp/settings", json={"groups": ["shell"]}, headers=world.as_("officer")
        )

        assert refused.status_code == 400
        assert "Unknown MCP group" in refused.json()["detail"]

    def test_the_groups_agree_with_the_configuration(self) -> None:
        from noust.core.config import MCP_GROUPS

        assert tuple(MCP_GROUPS) == tuple(GROUPS)


class TestBuildingARequest:
    def test_path_values_are_escaped_and_the_rest_goes_where_the_route_wants_it(
        self, world: World
    ) -> None:
        openapi = world.client.app.openapi()  # type: ignore[attr-defined]

        path, query, body = build_request(
            BY_NAME["list_deployments"], openapi, {"domain": "a.example", "limit": 5}
        )
        assert (path, query, body) == (
            "/api/deployments",
            {"domain": "a.example", "limit": 5},
            None,
        )

        path, query, body = build_request(BY_NAME["get_deployment"], openapi, {"deployment_id": 7})
        assert path == "/api/deployments/7"

        path, query, body = build_request(
            BY_NAME["deploy_application"], openapi, {"domain": "a.example", "force": True}
        )
        assert (path, query, body) == (
            "/api/jobs/update",
            {},
            {"domain": "a.example", "force": True},
        )

    def test_a_slash_in_a_name_cannot_walk_to_another_route(self, world: World) -> None:
        openapi = world.client.app.openapi()  # type: ignore[attr-defined]

        path, _, _ = build_request(
            BY_NAME["get_application"], openapi, {"domain": "../../auth/accounts"}
        )

        assert path == "/api/apps/..%2F..%2Fauth%2Faccounts"

    def test_the_arguments_checker_knows_enums_and_bounds(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["a", "b"]},
                "n": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["level"],
        }

        assert check_arguments(schema, {"level": "a", "n": 2}) is None
        assert "one of a, b" in str(check_arguments(schema, {"level": "c"}))
        assert "at most 3" in str(check_arguments(schema, {"level": "a", "n": 9}))
        assert "expected integer" in str(check_arguments(schema, {"level": "a", "n": True}))
