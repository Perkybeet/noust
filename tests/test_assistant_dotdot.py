# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A path parameter that a browser would rewrite never becomes a call (item 171).

A proposal is a call the person's browser makes with the person's own session, and what the card shows is
the path the call is classified by. A browser takes the dot segments out of a path before it sends it, so a
parameter of ``..`` makes it send another call than the one the card shows: the card says one operation,
with the permission it is judged by, and the request that leaves is another route. The defence is in three
places and this file holds the two of the server: the place that makes the path out of the template (the
MCP endpoint and the assistant share it), and the place a proposal is born, which also keeps the operation
it is for so that what is carried out is held to it. The browser's is in the console.
"""

from __future__ import annotations

import itertools
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.operation_classes import (
    HTTP_METHODS,
    NEUTRAL_PERMISSIONS,
    OWN,
    PUBLIC_CLASS,
    classify_schema,
)
from noust.web.assistant.routing import route_of
from noust.web.assistant.toolbox import Reach, Toolbox, ToolProblem
from noust.web.mcp.tools import BY_NAME, build_request

OPENAPI: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "panel" / "openapi.json").read_text()
)
HELD = (
    frozenset(
        str(operation["x-noust-permission"])
        for item in OPENAPI["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and operation.get("x-noust-permission")
    )
    - ASSISTANT_FLOOR
)

_PARAMETER = re.compile(r"\{([^{}]+)\}")
CLASSES = classify_schema(OPENAPI)


def offered(method: str, template: str) -> bool:
    """Whether the assistant is offered the operation at all (what it is not offered is refused for that reason)."""
    found = CLASSES.get((method, template))
    return (
        found is not None
        and found.kind not in (OWN, PUBLIC_CLASS)
        and found.permission not in NEUTRAL_PERMISSIONS
        and found.permission in HELD
    )


def operations() -> list[tuple[str, str, tuple[str, ...]]]:
    """Every operation of the API that takes a parameter in its path: ``(METHOD, template, names)``."""
    found = []
    for template, item in OPENAPI["paths"].items():
        names = tuple(_PARAMETER.findall(template))
        for method, operation in item.items():
            if names and method.upper() in HTTP_METHODS and isinstance(operation, dict):
                found.append((method.upper(), template, names))
    return found


OPERATIONS = operations()
OFFERED = [op for op in OPERATIONS if offered(op[0], op[1])]


def browser_path(path: str) -> str:
    """What a browser's URL parser leaves of a path: the dot segments, written out or encoded, are gone."""
    out: list[str] = []
    parts = path.split("/")
    for index, segment in enumerate(parts):
        low = segment.lower()
        last = index == len(parts) - 1
        if low in ("..", ".%2e", "%2e.", "%2e%2e"):
            if len(out) > 1:
                out.pop()
            if last:
                out.append("")
        elif low in (".", "%2e"):
            if last:
                out.append("")
        else:
            out.append(segment)
    return "/".join(out)


def matcher(template: str) -> re.Pattern[str]:
    return re.compile(re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(template)))


@pytest.fixture(scope="module")
def box() -> Toolbox:
    return Toolbox(OPENAPI, token_permissions=HELD, person_permissions=HELD, reach=Reach())


def call_of(method: str, template: str, values: dict[str, str]) -> dict[str, Any]:
    return {"method": method, "path": template, "parameters": values}


class TestTheOneThatMakesThePath:
    @pytest.mark.parametrize("value", ["..", ".", "", "a\nb", "a\x00b", "\t"])
    def test_a_value_that_cannot_be_one_part_of_a_path_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="one part of a path"):
            build_request(BY_NAME["get_application"], OPENAPI, {"domain": value})

    def test_a_name_that_is_only_odd_stays_one_segment(self) -> None:
        path, _, _ = build_request(BY_NAME["get_application"], OPENAPI, {"domain": "a b/c..d"})

        assert path == "/api/apps/a%20b%2Fc..d"

    @pytest.mark.parametrize(
        ("method", "template", "name"),
        [(m, t, n) for m, t, names in OFFERED for n in names],
        ids=lambda value: str(value),
    )
    def test_the_assistant_refuses_a_dot_segment_in_any_parameter_of_any_operation(
        self, box: Toolbox, method: str, template: str, name: str
    ) -> None:
        values = dict.fromkeys(_PARAMETER.findall(template), "x")
        values[name] = ".."

        with pytest.raises(ToolProblem, match="one part of a path"):
            box.prepare("call_operation", call_of(method, template, values))

    def test_the_operations_that_are_offered_are_enough_to_mean_something(self) -> None:
        assert len(OFFERED) > 150

    def test_a_clean_value_in_every_parameter_leaves_the_path_as_the_card_shows_it(
        self, box: Toolbox
    ) -> None:
        # What the test above refuses is the dot and nothing else: with plain values the path built is
        # one the browser does not touch and that fits the operation's own template.
        prepared = 0
        for method, template, names in OFFERED:
            try:
                got = box.prepare(
                    "call_operation",
                    call_of(method, template, dict.fromkeys(names, "x")),
                )
            except ToolProblem:
                continue  # it needs a body this test does not give: not what is measured here
            prepared += 1
            assert browser_path(got.path) == got.path, (method, template)
            assert matcher(template).fullmatch(got.path), (method, template)
        assert prepared > 50


class TestThePairsTheAuditFound:
    """Each operation that, with ``..`` in a parameter, a browser sends as another operation of the same method."""

    def reachable(self) -> list[tuple[str, str, tuple[str, ...], str]]:
        by_method: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
        for template, item in OPENAPI["paths"].items():
            for method in item:
                if method.upper() in HTTP_METHODS:
                    by_method.setdefault(method.upper(), []).append((template, matcher(template)))
        pairs = []
        for method, template, names in OFFERED:
            for size in range(1, min(len(names), 2) + 1):
                for chosen in itertools.combinations(names, size):
                    path = template
                    for name in names:
                        path = path.replace(
                            "{" + name + "}",
                            urllib.parse.quote(".." if name in chosen else "x", safe=""),
                        )
                    sent = browser_path(path)
                    if sent == path:
                        continue
                    for other, rx in by_method.get(method, []):
                        if other != template and rx.fullmatch(sent):
                            pairs.append((method, template, chosen, other))
        return pairs

    def test_there_are_pairs_and_every_one_of_them_is_refused_before_it_is_a_proposal(
        self, box: Toolbox
    ) -> None:
        pairs = self.reachable()
        assert len(pairs) >= 20, "the premise: a raw join makes the browser send another operation"

        for method, template, chosen, _other in pairs:
            values = {
                name: ".." if name in chosen else "x" for name in _PARAMETER.findall(template)
            }
            with pytest.raises(ToolProblem, match="one part of a path"):
                box.prepare("call_operation", call_of(method, template, values))

    def test_the_named_tools_refuse_it_too(self, box: Toolbox) -> None:
        with pytest.raises(ToolProblem, match="one part of a path"):
            box.prepare("get_application", {"domain": ".."})
        with pytest.raises(ToolProblem, match="one part of a path"):
            box.prepare("activate_release", {"domain": "x", "release_id": ".."})


CHANGES = [op for op in OFFERED if CLASSES[(op[0], op[1])].changes]


class TestTheRouteTheServerWillGiveIt:
    """What a change is classified by is its template, so the path built must be routed to that template."""

    def test_with_plain_values_every_change_is_routed_to_its_own_template(
        self, box: Toolbox
    ) -> None:
        checked = 0
        for method, template, names in CHANGES:
            try:
                got = box.prepare(
                    "call_operation",
                    call_of(method, template, dict.fromkeys(names, "x")),
                )
            except ToolProblem as problem:
                # It needs a body this test does not give. A refusal about the route would be a
                # legitimate operation that cannot be proposed, which is what is looked for here.
                assert "another operation" not in str(problem), (method, template)
                continue
            checked += 1
            assert route_of(got.method, got.path) == template, (method, template)
        assert checked > 30

    @pytest.mark.parametrize(
        ("method", "template", "name"),
        [(m, t, n) for m, t, names in CHANGES for n in names],
        ids=lambda value: str(value),
    )
    def test_a_value_that_splits_into_more_parts_is_not_proposed_for_any_change(
        self, box: Toolbox, method: str, template: str, name: str
    ) -> None:
        # The server routes by the path decoded, so ``x%2Fstop`` is two segments to it and one to the
        # card: the call that runs is the route of the decoded path, not the template it was made for.
        values = dict.fromkeys(_PARAMETER.findall(template), "x")
        values[name] = "x/stop"

        with pytest.raises(ToolProblem, match="another operation"):
            box.prepare("call_operation", call_of(method, template, values))

    def test_a_read_is_not_held_to_it(self, box: Toolbox) -> None:
        # A read is made and its answer given: no card, and the token that makes it holds what the
        # route it reaches needs, so nothing is classified wrongly. A route that takes a path
        # (a metric's name) must keep working.
        got = box.prepare(
            "call_operation",
            {
                "method": "GET",
                "path": "/api/metrics/{metric}",
                "parameters": {"metric": "app/cpu"},
            },
        )

        assert got.path == "/api/metrics/app%2Fcpu"

    def test_a_call_to_a_server_is_judged_by_the_servers_own_route(self) -> None:
        shown = "/api/nodes/web-2/api/apps/shop.example.com/restart"

        assert route_of("POST", shown) == "/api/apps/{domain}/restart"
        # A name that holds a slash is not a name to the server: it routes what the decoded path says.
        assert route_of("POST", "/api/nodes/web-2/api/apps/a%2Fstop/restart") is None
        # ...and a name with a slash in it can make the path another operation's: the hazard itself.
        assert route_of("GET", "/api/apps/x%2Flogs") == "/api/apps/{domain}/logs"
        assert route_of("POST", "/api/apps/a%2Frestart") == "/api/apps/{domain}/restart"
