# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The bridge the assistant calls the API through hands the router the path as a server would (item 171).

The loop builds a path by percent-encoding each value (``postgresql@pg16`` becomes ``postgresql%40pg16``), as a
browser does. A server decodes what arrives before it routes, so the router sees ``postgresql@pg16``; the
in-process bridge handed it ``postgresql%40pg16`` as it was written, and a read of a name with an ``@`` in it
answered 404 for something that exists. The bridge decodes the path and keeps the raw one, and what the tests
call the API through is the same function, so that they see what production sees.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

from noust.web.api.openapi import api_routes
from noust.web.assistant.routing import route_of
from noust.web.mcp.gateway import call_api
from noust.web.mcp.tools import PathParameterRefused, fill_path
from noust.web.server import get_token_manager
from tests.support.assistant_lab import KEY, SHOP, ClientBridge, Lab, build_lab
from tests.support.scripted_model import ScriptedModel

pytestmark = pytest.mark.allow_sockets

OPENAPI: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "panel" / "openapi.json").read_text()
)

#: Values a model can put in a path parameter: what a router splits on, what an encoder rewrites and what a
#: browser normalises, written out and percent-encoded.
VALUES = [
    "a",
    "a/b",
    "a/b/c",
    "x%2Fy",
    "a b",
    "a;b",
    "a\\b",
    "\N{FULLWIDTH LATIN SMALL LETTER A}",
    "a?b",
    "a#b",
    "%2e%2e",
    "..%2f",
    "a%00b",
    "a.",
    " ",
    "postgresql@pg16",
]


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


class Probe:
    """An application that keeps the scope of the request it was handed and answers 204."""

    def __init__(self) -> None:
        self.scope: dict[str, Any] = {}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.scope = scope
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def seen_by(path: str) -> dict[str, Any]:
    """
    Args:
        path: What the loop built.

    Returns:
        The scope the application is handed for it through the in-process bridge.
    """
    probe = Probe()
    asyncio.run(call_api(probe, "GET", path, client_host="127.0.0.1", host="testserver"))
    return probe.scope


class TestThePathIsRoutedAsAServerRoutesIt:
    @pytest.mark.parametrize(
        ("built", "decoded"),
        [
            (
                "/api/databases/instances/postgresql%40pg16",
                "/api/databases/instances/postgresql@pg16",
            ),
            ("/api/apps/shop%2Eexample%2Ecom", "/api/apps/shop.example.com"),
            ("/api/apps/caf%C3%A9.example.com", "/api/apps/café.example.com"),
            ("/api/apps/plain.example.com", "/api/apps/plain.example.com"),
        ],
    )
    def test_the_router_is_given_the_decoded_path_and_the_raw_one_is_kept(
        self, built: str, decoded: str
    ) -> None:
        scope = seen_by(built)

        assert scope["path"] == decoded
        assert scope["raw_path"] == built.encode()

    def test_an_encoded_name_reaches_the_route_that_has_it(self, lab: Lab) -> None:
        token = get_token_manager().create_api_token(
            "reader", "read", None, owner=lab.marta.account, permissions=["apps.read"]
        )["token"]

        def status(path: str) -> int:
            return asyncio.run(
                call_api(
                    lab.app,
                    "GET",
                    path,
                    token=token,
                    client_host="127.0.0.1",
                    host="testserver",
                )
            ).status

        over_http = lab.client.get(
            f"/api/apps/{SHOP.replace('.', '%2E')}", headers={"Authorization": f"Bearer {token}"}
        ).status_code

        assert over_http == 200
        assert status(f"/api/apps/{SHOP.replace('.', '%2E')}") == over_http

    def test_the_double_of_the_tests_calls_the_api_the_way_production_does(self) -> None:
        # The bridge of the loop is `call_api` with the assistant's name; a double that went through an HTTP
        # client instead would be a router the tests have and production does not.
        probe = Probe()

        answered = ClientBridge(TestClient(probe)).call(
            "GET", "/api/databases/instances/postgresql%40pg16", token="t", query=[("a", "1")]
        )

        headers = dict(probe.scope["headers"])
        assert answered.status == 204
        assert headers[b"user-agent"] == b"noust-assistant"
        assert headers[b"authorization"] == b"Bearer t"
        assert probe.scope["path"] == "/api/databases/instances/postgresql@pg16"
        assert probe.scope["query_string"] == b"a=1"


def real_route(app: Any, method: str, scope: dict[str, Any]) -> str | None:
    """
    Args:
        app: The application.
        method: The method.
        scope: What the application was handed.

    Returns:
        The template of the route its router picks for that scope.
    """
    for route in api_routes(app.routes):
        match, _child = route.matches({**scope, "method": method})
        if match == Match.FULL:
            return str(getattr(route, "path", None))
    return None


def test_no_accepted_path_ends_in_another_operation(lab: Lab) -> None:
    # The card of a proposal is judged by the template its path matches; what the bridge then calls must be
    # that operation and no other, for every value a model can put in every parameter of every route that
    # changes something. The router is asked with the scope the bridge itself builds.
    mismatches: list[tuple[str, str, str, str | None]] = []
    accepted = 0

    async def sweep() -> None:
        nonlocal accepted
        probe = Probe()
        for path, item in OPENAPI["paths"].items():
            for method in item:
                if method.upper() in ("GET", "HEAD", "OPTIONS"):
                    continue
                params = re.findall(r"\{([^{}/:]+)(?::path)?\}", path)
                for value in VALUES:
                    try:
                        built = fill_path(path, dict.fromkeys(params, value))
                    except PathParameterRefused:
                        continue
                    if route_of(method.upper(), built) != path:
                        continue
                    accepted += 1
                    await call_api(
                        probe, method.upper(), built, client_host="127.0.0.1", host="testserver"
                    )
                    actual = real_route(lab.app, method.upper(), probe.scope)
                    if actual != path:
                        mismatches.append((method, path, built, actual))

    asyncio.run(sweep())

    assert accepted > 1000
    assert mismatches == [], mismatches[:10]
    assert unquote("postgresql%40pg16") == "postgresql@pg16"
