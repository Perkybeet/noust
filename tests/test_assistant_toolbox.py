# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tools the assistant is given (item 171).

They are the MCP endpoint's tools (one tuple, not a copy) and three of its own. What is not offered
is not on the list, a call that does not fit is answered with what is wrong in words a model can
correct, and with a fleet a tool is the same tool called on a server through the central.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.assistant.docsearch import DocIndex
from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.store import NoustStore
from noust.web.assistant.toolbox import (
    FLEET_TOOLS,
    OUTSIDE_TOOLS,
    Reach,
    Toolbox,
    ToolProblem,
    proxy_path,
)
from noust.web.mcp.tools import TOOLS
from noust.web.permissions.registry import permission_for_route
from tests.support.teams_support import World, build_world

DOMAIN = "shop.acme.example"

READS = frozenset(t.name for t in TOOLS if t.read_only)


@pytest.fixture(scope="module")
def openapi(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    # The document is the application's own and does not change: it is built once for the module.
    patch = pytest.MonkeyPatch()
    try:
        world: World = build_world(tmp_path_factory.mktemp("toolbox"), patch)
        yield world.client.app.openapi()  # type: ignore[attr-defined, no-any-return]
    finally:
        NoustStore.reset_instance()
        patch.undo()


def everything(openapi: dict[str, Any]) -> frozenset[str]:
    found = set()
    for item in openapi["paths"].values():
        for operation in item.values():
            if isinstance(operation, dict) and operation.get("x-noust-permission"):
                found.add(operation["x-noust-permission"])
    return frozenset(found)


def box(openapi: dict[str, Any], *, reach: Reach | None = None, **extra: Any) -> Toolbox:
    held = everything(openapi) - ASSISTANT_FLOOR
    return Toolbox(
        openapi,
        token_permissions=extra.pop("token", held),
        person_permissions=extra.pop("person", held),
        reach=reach or Reach(),
        **extra,
    )


def names(toolbox: Toolbox) -> list[str]:
    return [d["function"]["name"] for d in toolbox.definitions()]


class TestWhatIsOffered:
    def test_the_mcp_tools_and_the_tools_of_its_own_and_nothing_else(
        self, openapi: dict[str, Any]
    ) -> None:
        found = names(box(openapi))
        assert [t.name for t in TOOLS] == [n for n in found if n in {t.name for t in TOOLS}]
        assert {"find_operation", "call_operation"} <= set(found)
        # No documentation installed: nothing to look up, so nothing offered to fail.
        assert "search_docs" not in found

    def test_search_docs_is_offered_when_there_is_documentation(
        self, openapi: dict[str, Any], tmp_path: Path
    ) -> None:
        (tmp_path / "a.md").write_text("# Page\n\nSomething about backups.\n")
        assert "search_docs" in names(box(openapi, docs=DocIndex([tmp_path])))

    def test_a_tool_whose_route_the_token_may_not_call_is_not_offered(
        self, openapi: dict[str, Any]
    ) -> None:
        held = everything(openapi) - ASSISTANT_FLOOR - {"apps.deploy"}
        found = names(box(openapi, token=held, person=held))
        assert "deploy_application" not in found
        assert "list_applications" in found

    def test_a_change_is_offered_by_what_the_person_holds_not_the_token(
        self, openapi: dict[str, Any]
    ) -> None:
        # The token holds only reads; the person holds the rest and may be proposed a change.
        reads = frozenset(p for p in everything(openapi) if p.endswith(".read"))
        found = names(box(openapi, token=reads, person=everything(openapi) - ASSISTANT_FLOOR))
        assert "restart_application" in found

    def test_every_tool_is_one_the_mcp_never_forbids_the_floor(
        self, openapi: dict[str, Any]
    ) -> None:
        for tool in box(openapi).definitions():
            name = tool["function"]["name"]
            permission = next(
                (permission_for_route(t.method, t.path) for t in TOOLS if t.name == name), None
            )
            assert permission not in ASSISTANT_FLOOR

    def test_a_schema_does_not_offer_an_approval_number(self, openapi: dict[str, Any]) -> None:
        by_name = {d["function"]["name"]: d["function"] for d in box(openapi).definitions()}
        assert "approval" not in by_name["restart_application"]["parameters"]["properties"]
        assert by_name["restart_application"]["parameters"]["required"] == ["domain"]

    def test_the_fleet_tools_are_offered_only_for_all_the_servers(
        self, openapi: dict[str, Any]
    ) -> None:
        assert not {t.name for t in FLEET_TOOLS} & set(names(box(openapi)))
        all_of_them = names(box(openapi, reach=Reach("*", ("web-01",))))
        assert {t.name for t in FLEET_TOOLS} <= set(all_of_them)

    def test_with_all_the_servers_every_per_server_tool_asks_which(
        self, openapi: dict[str, Any]
    ) -> None:
        defs = {
            d["function"]["name"]: d["function"]
            for d in box(openapi, reach=Reach("*", ("web-01", "db-02"))).definitions()
        }
        schema = defs["restart_application"]["parameters"]
        assert "server" in schema["required"]
        assert "web-01" in schema["properties"]["server"]["description"]
        assert "server" not in defs["fleet_servers"]["parameters"]["properties"]
        assert "server" in defs["call_operation"]["parameters"]["required"]


class TestACallThatFits:
    def test_a_read_is_a_request_and_says_what_it_is(self, openapi: dict[str, Any]) -> None:
        got = box(openapi).prepare("application_logs", {"domain": DOMAIN})
        assert (got.method, got.path, got.log, got.outside) == (
            "GET",
            f"/api/apps/{DOMAIN}/logs",
            True,
            True,
        )
        assert got.step_code == "assistant.step.application_logs"
        assert got.step_params == {"domain": DOMAIN}
        assert got.operation is not None and not got.operation.changes
        assert got.needed == ("apps.read",)

    def test_a_change_knows_what_it_is_and_what_it_needs(self, openapi: dict[str, Any]) -> None:
        got = box(openapi).prepare("restart_application", {"domain": DOMAIN})
        assert (got.method, got.path, got.read_only) == (
            "POST",
            f"/api/apps/{DOMAIN}/restart",
            False,
        )
        assert got.operation is not None and got.operation.changes
        assert got.step_code == "assistant.act.restart_application"
        assert got.domain == DOMAIN
        assert got.needed == ("apps.operate",)

    def test_what_the_state_of_noust_says_is_not_from_outside(
        self, openapi: dict[str, Any]
    ) -> None:
        assert not box(openapi).prepare("list_certificates", {}).outside
        assert box(openapi).prepare("get_deployment", {"deployment_id": 3}).outside

    def test_the_outside_tools_are_the_ones_that_return_text_written_by_others(self) -> None:
        assert {
            "application_logs",
            "deployment_log",
            "job_log",
            "diagnose_application",
        } <= OUTSIDE_TOOLS
        assert READS - OUTSIDE_TOOLS >= {"list_certificates", "list_backups", "server_health"}

    def test_what_does_not_fit_is_said_for_the_model_to_correct(
        self, openapi: dict[str, Any]
    ) -> None:
        toolbox = box(openapi)
        with pytest.raises(ToolProblem, match="Missing argument"):
            toolbox.prepare("restart_application", {})
        with pytest.raises(ToolProblem, match="Unknown argument"):
            toolbox.prepare("restart_application", {"domain": DOMAIN, "force": True})
        with pytest.raises(ToolProblem, match="Unknown tool"):
            toolbox.prepare("format_disk", {})

    def test_a_destructive_tool_has_to_be_confirmed(self, openapi: dict[str, Any]) -> None:
        toolbox = box(openapi)
        with pytest.raises(ToolProblem, match="confirm=true"):
            toolbox.prepare("stop_application", {"domain": DOMAIN})
        got = toolbox.prepare("stop_application", {"domain": DOMAIN, "confirm": True})
        assert "confirm" not in got.arguments

    def test_a_server_chosen_makes_it_the_same_call_through_the_central(
        self, openapi: dict[str, Any]
    ) -> None:
        got = box(openapi, reach=Reach("web-01", ("web-01",))).prepare(
            "restart_application", {"domain": DOMAIN}
        )
        assert got.path == f"/api/nodes/web-01/api/apps/{DOMAIN}/restart"
        assert got.template == "/api/apps/{domain}/restart"
        assert got.server == "web-01"

    def test_with_a_server_chosen_the_model_does_not_name_another(
        self, openapi: dict[str, Any]
    ) -> None:
        with pytest.raises(ToolProblem, match="Unknown argument"):
            box(openapi, reach=Reach("web-01", ("web-01",))).prepare(
                "restart_application", {"domain": DOMAIN, "server": "db-02"}
            )

    def test_with_all_of_them_it_names_one_that_is_the_persons(
        self, openapi: dict[str, Any]
    ) -> None:
        toolbox = box(openapi, reach=Reach("*", ("web-01", "db-02")))
        got = toolbox.prepare("restart_application", {"domain": DOMAIN, "server": "db-02"})
        assert got.path == f"/api/nodes/db-02/api/apps/{DOMAIN}/restart"
        with pytest.raises(ToolProblem, match="Missing argument"):
            toolbox.prepare("restart_application", {"domain": DOMAIN})
        with pytest.raises(ToolProblem, match="Unknown server"):
            toolbox.prepare("restart_application", {"domain": DOMAIN, "server": "evil-99"})

    def test_a_view_of_the_fleet_is_the_centrals_own_call(self, openapi: dict[str, Any]) -> None:
        got = box(openapi, reach=Reach("*", ("web-01",))).prepare("fleet_servers", {})
        assert (got.path, got.server) == ("/api/fleet/servers", "")

    def test_a_node_path_keeps_its_own_segments_and_quotes_the_name(self) -> None:
        assert (
            proxy_path("web 01", "/api/apps/a.example/restart")
            == "/api/nodes/web%2001/api/apps/a.example/restart"
        )


class TestCallingAnOperationFound:
    def test_a_change_of_the_api_is_prepared_from_its_method_and_path(
        self, openapi: dict[str, Any]
    ) -> None:
        got = box(openapi).prepare(
            "call_operation",
            {
                "method": "POST",
                "path": "/api/apps/{domain}/restart",
                "parameters": {"domain": DOMAIN},
            },
        )
        assert (got.method, got.path) == ("POST", f"/api/apps/{DOMAIN}/restart")
        assert got.step_code == "assistant.act.operation"
        assert got.step_params["summary"]
        assert got.operation is not None and got.operation.changes

    def test_a_read_through_it_is_a_read_and_from_outside(self, openapi: dict[str, Any]) -> None:
        got = box(openapi).prepare(
            "call_operation",
            {"method": "GET", "path": "/api/apps/{domain}", "parameters": {"domain": DOMAIN}},
        )
        assert got.read_only and got.outside
        assert got.step_code == "assistant.step.operation"

    def test_an_operation_that_does_not_exist_is_said_so(self, openapi: dict[str, Any]) -> None:
        with pytest.raises(ToolProblem, match="No operation"):
            box(openapi).prepare("call_operation", {"method": "POST", "path": "/api/nope"})

    def test_the_floor_is_not_offered_even_by_name(self, openapi: dict[str, Any]) -> None:
        with pytest.raises(ToolProblem, match="not offered"):
            box(openapi).prepare("call_operation", {"method": "GET", "path": "/api/audit"})

    def test_a_persons_own_session_is_not_offered(self, openapi: dict[str, Any]) -> None:
        with pytest.raises(ToolProblem, match=r"not offered|No operation"):
            box(openapi).prepare("call_operation", {"method": "GET", "path": "/api/auth/tokens"})

    def test_a_missing_parameter_of_the_path_is_asked_for(self, openapi: dict[str, Any]) -> None:
        with pytest.raises(ToolProblem, match="Missing argument"):
            box(openapi).prepare(
                "call_operation", {"method": "POST", "path": "/api/apps/{domain}/restart"}
            )

    def test_a_body_that_is_not_an_object_is_refused(self, openapi: dict[str, Any]) -> None:
        with pytest.raises(ToolProblem, match="JSON objects"):
            box(openapi).prepare(
                "call_operation",
                {"method": "POST", "path": "/api/apps/{domain}/restart", "parameters": "x"},
            )


class TestFindingOperations:
    def test_the_operation_that_does_it_comes_with_what_it_needs_and_who_does_it(
        self, openapi: dict[str, Any]
    ) -> None:
        text, count = box(openapi).find_operation("restart an application")
        assert count >= 1
        assert "POST /api/apps/{domain}/restart" in text
        assert "apps.operate" in text

    def test_what_is_not_offered_is_not_found(self, openapi: dict[str, Any]) -> None:
        text, _count = box(openapi).find_operation("read the audit trail events")
        assert "/api/audit" not in text

    def test_a_search_of_the_documentation_gives_pages_to_cite(
        self, openapi: dict[str, Any], tmp_path: Path
    ) -> None:
        (tmp_path / "backups.md").write_text("# Backups\n\nA backup is a copy of an application.\n")
        text, count = box(openapi, docs=DocIndex([tmp_path])).search_docs("what is a backup")
        assert count == 1 and "backups.md - Backups" in text

    def test_a_search_with_nothing_to_look_in_says_so(self, openapi: dict[str, Any]) -> None:
        assert box(openapi).search_docs("anything")[1] == 0

    def test_an_empty_query_is_a_problem_to_correct(self, openapi: dict[str, Any]) -> None:
        with pytest.raises(ToolProblem, match="Missing"):
            box(openapi).prepare("find_operation", {"query": "  "})
