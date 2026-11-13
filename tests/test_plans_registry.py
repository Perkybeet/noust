# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The operations that have a plan, held to the API that serves them (item 183).

The registry (``noust.core.plans.registry``) says which operations have a plan and where it is; the
API says it again in each operation (``x-noust-plan``); this holds the two to the routes that exist.
The list only grows: an operation that had a plan and lost it fails here, and one that gains it is
added to :data:`HAD_A_PLAN`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from noust.core import operation_classes
from noust.core.plans.registry import PLAN_EXTENSION, PLANNED, plan_of, plan_routes
from noust.web.auth import SecurityConfig
from noust.web.server import create_app

#: Every operation that has had a plan, by method and route: the list only grows, and an operation
#: that is on it keeps its plan. Add a line here when an operation gains one.
HAD_A_PLAN: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/jobs/update"),
        ("POST", "/api/apps/{domain}/deploy"),
        ("POST", "/api/apps/{domain}/deployments/{deployment_id}/rollback"),
        ("POST", "/api/apps/{domain}/restart"),
        ("POST", "/api/apps/{domain}/stop"),
        ("POST", "/api/apps/{domain}/start"),
        ("PATCH", "/api/apps/{domain}/limits"),
        ("POST", "/api/certs/{domain}/renew"),
        ("POST", "/api/apps/{domain}/domains"),
        ("DELETE", "/api/apps/{domain}/domains/{name}"),
        ("DELETE", "/api/apps/{domain}"),
        ("POST", "/api/jobs/delete"),
        ("POST", "/api/apps/{domain}/migrate"),
        ("POST", "/api/docker/cleanup"),
    }
)

_PARAMETER = re.compile(r"\{([^{}]+)\}")


@pytest.fixture(scope="module")
def schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The API's own document, with the extensions the server adds."""
    state = tmp_path_factory.mktemp("state")
    return create_app(SecurityConfig(state_dir=Path(state), rate_limit_requests=5000)).openapi()


def operation(schema: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    """The operation of a route, or a failure that says which one is missing."""
    found = schema["paths"].get(path, {}).get(method.lower())
    assert found is not None, f"{method} {path} is not a route of the API"
    return found


def test_an_operation_that_had_a_plan_still_has_one() -> None:
    planned = {(item.method, item.path) for item in PLANNED}

    assert HAD_A_PLAN <= planned, sorted(HAD_A_PLAN - planned)
    assert planned <= HAD_A_PLAN, f"add to HAD_A_PLAN: {sorted(planned - HAD_A_PLAN)}"


def test_every_planned_operation_and_every_plan_is_a_route_of_the_api(schema) -> None:
    for item in PLANNED:
        operation(schema, item.method, item.path)
        operation(schema, item.plan_method, item.plan_path)


def test_the_schema_says_where_the_plan_of_each_planned_operation_is(schema) -> None:
    for item in PLANNED:
        assert operation(schema, item.method, item.path)[PLAN_EXTENSION] == {
            "method": item.plan_method,
            "path": item.plan_path,
            "operation": item.key,
        }


def test_a_plan_is_a_post_that_reads_and_asks_for_nothing(schema) -> None:
    for item in PLANNED:
        plan = operation(schema, item.plan_method, item.plan_path)
        kind = operation_classes.classify(item.plan_method, item.plan_path, plan)

        assert item.plan_method == "POST"
        assert kind.kind == operation_classes.READ, f"{item.plan_path} is a {kind.kind}"
        assert "x-noust-requires-elevation" not in plan
        assert "x-noust-requires-approval" not in plan
        assert PLAN_EXTENSION not in plan, "a plan is not itself an operation to plan"


def test_a_planned_operation_is_one_that_changes_something(schema) -> None:
    for item in PLANNED:
        kind = operation_classes.classify(
            item.method, item.path, operation(schema, item.method, item.path)
        )

        assert kind.changes, f"{item.method} {item.path} is a {kind.kind}: it needs no plan"


def test_the_plan_is_read_with_the_permission_to_read_what_the_operation_changes(schema) -> None:
    for item in PLANNED:
        # What is read is what the operation changes: an application, or Docker.
        expected = "docker.read" if item.key.startswith("docker.") else "apps.read"
        assert operation(schema, item.plan_method, item.plan_path)["x-noust-permission"] == expected


def test_every_parameter_of_a_plans_path_comes_from_the_operation_it_plans(schema) -> None:
    """The convention a client follows: a ``{name}`` is the operation's path parameter or body field."""
    for item in PLANNED:
        spec = operation(schema, item.method, item.path)
        known = set(_PARAMETER.findall(item.path))
        body = spec.get("requestBody", {}).get("content", {}).get("application/json", {})
        reference = body.get("schema", {}).get("$ref", "")
        if reference:
            model = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            known |= set(model.get("properties", {}))
        wanted = set(_PARAMETER.findall(item.plan_path))

        assert wanted <= known, f"{item.plan_path} needs {sorted(wanted - known)} from {item.path}"


def test_the_routes_that_are_plans_are_the_registrys(schema) -> None:
    assert set(plan_routes()) == {(item.plan_method, item.plan_path) for item in PLANNED}
    assert plan_of("post", "/api/jobs/update") is not None
    assert plan_of("GET", "/api/jobs/update") is None


def test_how_much_of_what_changes_something_has_a_plan(schema, capsys) -> None:
    """Not a limit: the figure the list is judged by, printed so the next lane sees it."""
    changing = [
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        for method, spec in methods.items()
        if method in {"post", "put", "patch", "delete"}
        and "x-noust-permission" in spec
        and operation_classes.classify(method, path, spec).changes
    ]
    planned = [key for key in changing if plan_of(*key) is not None]

    print(f"{len(planned)} of {len(changing)} operations that change something have a plan")
    assert planned and len(planned) <= len(changing)


def test_a_central_judges_the_plan_of_a_servers_operation_by_the_permission_to_read(schema) -> None:
    """Through a node, the plan is read with the same permission as on the node itself."""
    from noust.web.permissions.registry import match_path

    for item in PLANNED:
        concrete = _PARAMETER.sub("x.example.com", item.plan_path)
        found = match_path(item.plan_method, concrete)

        assert found is not None, item.plan_path
        template, permission = found
        assert template == item.plan_path
        planned = operation(schema, item.plan_method, item.plan_path)
        assert permission == planned["x-noust-permission"]


def test_only_the_operations_a_schema_has_get_the_extension() -> None:
    """A 4.2 node runs no such code, so its schema never carries the extension: the console offers no plan there."""
    from noust.web.api.openapi import annotate_plans

    old = {"paths": {"/api/apps/{domain}/restart": {"post": {}}}}

    annotated = annotate_plans(old)

    assert annotated == {
        "paths": {
            "/api/apps/{domain}/restart": {
                "post": {
                    PLAN_EXTENSION: {
                        "method": "POST",
                        "path": "/api/apps/{domain}/restart/plan",
                        "operation": "app.restart",
                    }
                }
            }
        }
    }
    # An operation a schema does not have is not invented into it.
    assert "/api/jobs/update" not in annotated["paths"]
