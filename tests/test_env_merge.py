# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811 - the env endpoint tests' fixtures are imported by name, and pytest finds them so

"""
``PATCH /api/apps/{domain}/env``: setting a few variables without dropping the rest (J-15).

``PUT`` replaces the file, so a manifest or a script that owns a few variables
would erase the others. The merge keeps them, says which *names* changed and
never a value, and compares on the server with ``dry_run`` - which needs the same
sudo mode a write does, because "same" or "different" about a secret is a way to
guess it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from tests.test_web_apps_env_secrets import (  # noqa: F401
    app,
    client,
    deployed_env,
    elevate,
    read_audit,
    store,
)


def merge(client: TestClient, body: dict[str, Any]) -> Any:
    """
    Args:
        client: A signed-in client.
        body: The merge request.

    Returns:
        The response.
    """
    return client.patch("/api/apps/example.com/env", json=body)


def env_file(directory: Path) -> dict[str, str]:
    """
    Args:
        directory: The application's directory.

    Returns:
        The variables its ``.env`` holds.
    """
    pairs = (
        line.split("=", 1) for line in (directory / ".env").read_text().splitlines() if "=" in line
    )
    return {name: value.strip("'\"") for name, value in pairs}


class TestMerging:
    def test_variables_are_set_and_the_rest_stay(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="KEEP=1\nCHANGE=old\n")
        elevate(client)

        response = merge(client, {"set": {"CHANGE": "new", "ADDED": "x"}})

        assert response.status_code == 200, response.text
        assert response.json()["changed"] == ["ADDED", "CHANGE"]
        assert response.json()["restart_required"] is True
        assert env_file(directory) == {"KEEP": "1", "CHANGE": "new", "ADDED": "x"}

    def test_variables_can_be_unset(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="KEEP=1\nDROP=2\n")
        elevate(client)

        response = merge(client, {"unset": ["DROP", "NEVER_THERE"]})

        assert response.json()["changed"] == ["DROP"]
        assert env_file(directory) == {"KEEP": "1"}

    def test_setting_what_is_already_so_changes_nothing(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)
        before = (directory / ".env").read_text()

        response = merge(client, {"set": {"A": "1"}})

        assert response.json() == {
            "domain": "example.com",
            "changed": [],
            "restart_required": False,
            "dry_run": False,
        }
        assert (directory / ".env").read_text() == before

    def test_a_value_is_never_in_the_answer_or_the_audit_trail(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)

        response = merge(client, {"set": {"API_KEY": "sk-merge-secret-123"}})

        assert "sk-merge-secret-123" not in response.text
        recorded = [e for e in read_audit(tmp_path) if e["action"] == "apps.env.update"]
        assert recorded and "API_KEY" in recorded[-1]["detail"]
        assert "sk-merge-secret-123" not in str(recorded)

    def test_a_name_that_is_not_safe_for_a_unit_is_refused_and_nothing_is_written(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)

        response = merge(client, {"set": {"bad name": "x"}})

        assert response.status_code == 422
        assert env_file(directory) == {"A": "1"}

    def test_the_variables_a_unit_sets_inline_cannot_be_merged_in(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)

        assert merge(client, {"set": {"PORT": "9999"}}).status_code == 422
        assert merge(client, {"set": {"PORT": "9999"}, "dry_run": True}).status_code == 422

    def test_an_application_that_is_not_there_is_a_404(self, client: TestClient) -> None:
        elevate(client)

        assert (
            client.patch("/api/apps/nope.example/env", json={"set": {"A": "1"}}).status_code == 404
        )


class TestDryRun:
    def test_it_says_what_would_change_and_writes_nothing(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="A=1\nSECRET_KEY=abc\n")
        elevate(client)
        before = (directory / ".env").read_text()

        response = merge(client, {"set": {"A": "1", "SECRET_KEY": "def"}, "dry_run": True})

        assert response.json()["changed"] == ["SECRET_KEY"]
        assert response.json()["dry_run"] is True and response.json()["restart_required"] is False
        assert (directory / ".env").read_text() == before

    def test_asking_needs_sudo_mode_like_a_write(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="SECRET_KEY=abc\n")

        response = merge(client, {"set": {"SECRET_KEY": "guess"}, "dry_run": True})

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"


class TestVariablesMadeOfReferences:
    """
    The file holds what a reference resolved to, and the store the text it came from. A client that sets
    the same text again (a manifest, the Terraform provider) has to be told nothing changed, or it can
    never see that its variables are in place.
    """

    def test_the_same_expression_set_again_changes_nothing_and_a_different_one_does(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        from noust.core.variables.model import SERVER
        from noust.core.variables.store import VariableStore

        directory = deployed_env(store, tmp_path, env_text="KEEP=1\n")
        VariableStore(store).set(SERVER, 0, "REGION", "eu-west-1")
        elevate(client)

        first = merge(client, {"set": {"AREA": "{{server.REGION}}"}})
        again = merge(client, {"set": {"AREA": "{{server.REGION}}"}})
        asked = merge(client, {"set": {"AREA": "{{server.REGION}}"}, "dry_run": True})
        other = merge(client, {"set": {"AREA": "{{server.REGION}}-b"}, "dry_run": True})

        assert first.json()["changed"] == ["AREA"]
        assert env_file(directory) == {"KEEP": "1", "AREA": "eu-west-1"}
        assert again.json()["changed"] == [] and again.json()["restart_required"] is False
        assert asked.json()["changed"] == []
        assert other.json()["changed"] == ["AREA"]

    def test_unsetting_a_variable_made_of_references_is_still_a_change(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        from noust.core.variables.model import SERVER
        from noust.core.variables.store import VariableStore

        deployed_env(store, tmp_path, env_text="KEEP=1\n")
        VariableStore(store).set(SERVER, 0, "REGION", "eu-west-1")
        elevate(client)
        merge(client, {"set": {"AREA": "{{server.REGION}}"}})

        removed = merge(client, {"unset": ["AREA"], "dry_run": True})

        assert removed.json()["changed"] == ["AREA"]


@pytest.mark.parametrize("verb", ["put", "patch"])
def test_a_read_only_token_may_neither_replace_nor_merge(
    client: TestClient, store: NoustStore, tmp_path: Path, verb: str
) -> None:
    from noust.web.server import get_token_manager
    from tests.test_web_auth import bearer

    deployed_env(store, tmp_path, env_text="A=1\n")
    token = get_token_manager().create_api_token("reader", "read", None)["token"]

    response = getattr(client, verb)(
        "/api/apps/example.com/env",
        json={"variables": {"A": "2"}} if verb == "put" else {"set": {"A": "2"}},
        headers=bearer(token),
    )

    assert response.status_code == 403
