# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811 - the env endpoint tests' fixtures are imported by name, and pytest finds them so

"""
``PATCH /api/apps/{domain}/env`` with an ``audience``: changing who sees a variable without
replacing the file.

The console edits one variable by merging it (``set``/``unset``) instead of reading the whole
environment in clear to write it back with ``PUT``. The audience of a variable (the install and
the build, the running application, or both) used to travel only in ``PUT``, so a merge that moved
a variable to the build would have needed the clear read again. What is defended here is that the
merge takes it, that it says the *name* changed and never a value, and that nobody learns a value
from it: sudo mode and the manage permission guard it exactly as they guard a write.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from tests.test_env_merge import env_file, merge
from tests.test_web_apps_env_secrets import (  # noqa: F401
    app,
    client,
    deployed_env,
    elevate,
    read_audit,
    store,
)
from tests.test_web_auth import bearer


def audience_of(client: TestClient) -> dict[str, str]:
    """
    Args:
        client: A signed-in client.

    Returns:
        The audience of every variable that has one other than both, as the table reads it.
    """
    body = client.get("/api/apps/example.com/env").json()
    return dict(body["audience"])


class TestMergingAnAudience:
    def test_a_variable_set_with_an_audience_keeps_it(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="KEEP=1\n")
        elevate(client)

        response = merge(client, {"set": {"NPM_TOKEN": "t"}, "audience": {"NPM_TOKEN": "build"}})

        assert response.status_code == 200, response.text
        assert response.json()["changed"] == ["NPM_TOKEN"]
        # Only the build is given it: the running application's file does not carry it.
        assert env_file(directory) == {"KEEP": "1"}
        assert audience_of(client) == {"NPM_TOKEN": "build"}

    def test_the_audience_of_a_variable_alone_can_change(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="KEEP=1\nSESSION=abc\n")
        elevate(client)

        response = merge(client, {"audience": {"SESSION": "runtime"}})

        assert response.status_code == 200, response.text
        # The value did not move, the audience did: that is a change the caller must hear about,
        # and one the running process only picks up on a restart.
        assert response.json()["changed"] == ["SESSION"]
        assert response.json()["restart_required"] is True
        assert audience_of(client) == {"SESSION": "runtime"}
        assert env_file(directory) == {"KEEP": "1", "SESSION": "abc"}

    def test_an_audience_that_is_already_so_changes_nothing(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)

        response = merge(client, {"audience": {"A": "both"}})

        assert response.json()["changed"] == []
        assert response.json()["restart_required"] is False

    def test_a_name_left_out_keeps_the_audience_it_has(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="KEEP=1\n")
        elevate(client)
        merge(client, {"set": {"NPM_TOKEN": "t"}, "audience": {"NPM_TOKEN": "build"}})

        merge(client, {"set": {"NPM_TOKEN": "t2", "OTHER": "x"}})

        assert audience_of(client) == {"NPM_TOKEN": "build"}

    def test_an_audience_for_a_variable_that_is_not_there_is_refused_and_nothing_is_written(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)
        before = (directory / ".env").read_text()

        response = merge(client, {"set": {"B": "2"}, "audience": {"GHOST": "build"}})

        assert response.status_code in (400, 422), response.text
        assert "GHOST" in response.text
        assert (directory / ".env").read_text() == before

    def test_an_audience_that_is_not_one_is_refused(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)

        response = merge(client, {"audience": {"A": "everyone"}})

        assert response.status_code in (400, 422), response.text
        assert audience_of(client) == {}

    def test_the_audit_trail_names_the_variable_and_never_a_value(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)

        merge(
            client, {"set": {"API_KEY": "sk-audience-secret-9"}, "audience": {"API_KEY": "build"}}
        )

        recorded = [e for e in read_audit(tmp_path) if e["action"] == "apps.env.update"]
        assert recorded and "API_KEY" in recorded[-1]["detail"]
        assert "sk-audience-secret-9" not in str(recorded)


class TestAskingWithAnAudience:
    def test_a_dry_run_says_the_name_would_change_and_writes_nothing(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="SESSION=abc\n")
        elevate(client)
        before = (directory / ".env").read_text()

        response = merge(client, {"audience": {"SESSION": "build"}, "dry_run": True})

        assert response.json()["changed"] == ["SESSION"]
        assert response.json()["dry_run"] is True and response.json()["restart_required"] is False
        assert (directory / ".env").read_text() == before
        assert audience_of(client) == {}

    def test_a_dry_run_refuses_what_the_write_would_refuse(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)

        unknown = merge(client, {"audience": {"GHOST": "build"}, "dry_run": True})
        invalid = merge(client, {"audience": {"A": "everyone"}, "dry_run": True})

        assert unknown.status_code in (400, 422)
        assert invalid.status_code in (400, 422)


class TestWhoMayAsk:
    """A merge, with or without an audience, answers "same" or "different" about a secret."""

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_a_read_token_gets_nothing_from_it(
        self, client: TestClient, store: NoustStore, tmp_path: Path, dry_run: bool
    ) -> None:
        from noust.web.server import get_token_manager

        deployed_env(store, tmp_path, env_text="SECRET_KEY=abc\n")
        token = get_token_manager().create_api_token("reader", "read", None)["token"]

        response = client.patch(
            "/api/apps/example.com/env",
            json={
                "set": {"SECRET_KEY": "guess"},
                "audience": {"SECRET_KEY": "build"},
                "dry_run": dry_run,
            },
            headers=bearer(token),
        )

        assert response.status_code == 403
        assert "SECRET_KEY" not in response.text.replace("not allowed", "")
        assert "abc" not in response.text and "guess" not in response.text

    def test_without_sudo_mode_a_dry_run_with_an_audience_is_refused(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="SECRET_KEY=abc\n")

        response = merge(client, {"audience": {"SECRET_KEY": "build"}, "dry_run": True})

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_a_read_token_reading_the_table_learns_no_value_of_a_variable_it_could_not_write(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        from noust.web.server import get_token_manager

        deployed_env(store, tmp_path, env_text="SECRET_KEY=abc\nPLAIN=1\n")
        elevate(client)
        merge(client, {"audience": {"SECRET_KEY": "runtime"}})
        token = get_token_manager().create_api_token("reader", "read", None)["token"]

        body = client.get("/api/apps/example.com/env", headers=bearer(token))

        assert body.status_code == 200
        assert "abc" not in body.text
        # A masked variable is unreadable to a reader even though it has an audience.
        assert body.json()["unmasked"] is False
        unmasked = client.get("/api/apps/example.com/env?unmask=true", headers=bearer(token))
        assert unmasked.status_code == 403
        assert "abc" not in unmasked.text
