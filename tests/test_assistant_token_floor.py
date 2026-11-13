# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The assistant's token against every route it must never pass (item 171).

The assistant acts with a token of its own, and what that token cannot do is decided by the server, not by
anything the model says. Here the token of an administrator's conversation (who holds every permission) is
presented to **every route of the API** whose permission is on the assistant's floor, and to every route that
asks for sudo mode or for a second person, with a body that would be valid if the call went through. Each is
refused by construction: what a token that was never issued for sudo mode, and never holds what the floor
holds, does not reach, however it asks.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.operation_classes import (
    APPROVAL_EXTENSION,
    ELEVATION_EXTENSION,
    PERMISSION_EXTENSION,
)
from noust.web.assistant.identity import AssistantIdentity
from tests.support.assistant_lab import KEY, Lab, build_lab
from tests.support.scripted_model import ScriptedModel

pytestmark = pytest.mark.allow_sockets

_PARAMETER = re.compile(r"\{[^{}]+\}")
_HTTP = ("get", "post", "put", "patch", "delete")


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
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)


def attacked(lab: Lab) -> dict[str, list[tuple[str, str]]]:
    """The routes to try, by why the assistant must not pass them."""
    found: dict[str, list[tuple[str, str]]] = {"floor": [], "sudo": [], "second person": []}
    for path, item in lab.app.openapi()["paths"].items():
        for method in _HTTP:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            permission = operation.get(PERMISSION_EXTENSION)
            if permission in ASSISTANT_FLOOR:
                found["floor"].append((method.upper(), path))
            elif operation.get(ELEVATION_EXTENSION):
                found["sudo"].append((method.upper(), path))
            elif (operation.get(APPROVAL_EXTENSION) or {}).get("when") == "always":
                found["second person"].append((method.upper(), path))
    return found


def test_no_route_of_the_floor_or_of_sudo_mode_lets_the_assistants_token_through(lab: Lab) -> None:
    lab.configure()
    token = AssistantIdentity().issue(lab.marta.account, "0123456789abcdef").token
    headers = {"Authorization": f"Bearer {token}"}
    routes = attacked(lab)
    assert len(routes["floor"]) > 100 and len(routes["sudo"]) > 100
    passed: list[str] = []
    tried = 0
    statuses: dict[int, int] = {}
    for why, operations in routes.items():
        for method, path in operations:
            tried += 1
            target = _PARAMETER.sub("x", path)
            response = lab.client.request(
                method, target, headers=headers, json={} if method != "GET" else None
            )
            statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
            if not (response.status_code in (401, 403) or response.status_code == 404):
                passed.append(f"{why}: {method} {path} -> {response.status_code}")
    assert passed == []
    # The refusals are the guard's own (a permission it never holds, or sudo mode it was never given), not
    # a route that was not found.
    assert statuses.get(403, 0) >= tried * 0.95, statuses
    print(f"\n[token against the floor and sudo mode] routes={tried} statuses={statuses}")


def test_the_token_never_holds_what_the_floor_holds_nor_sudo(lab: Lab) -> None:
    lab.configure()
    issued = AssistantIdentity().issue(lab.marta.account, "fedcba9876543210")
    assert not set(issued.permissions) & ASSISTANT_FLOOR
