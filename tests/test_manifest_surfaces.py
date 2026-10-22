# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The manifest at the command line and in the console (J-15).

``noust manifest`` is a client of a server's API (the engine is tested against a
server that keeps state in ``test_manifest``); here are what an operator types
and reads, and the console's preview, which runs the same engine on the
server as the person asking, so what a plan can see is what that person can.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from tests.manifest_support import FakeServer
from tests.teams_support import World, build_world

SHOP = "shop.acme.test"

MANIFEST = f"""\
version: 1
applications:
  - domain: {SHOP}
    source: https://example.test/shop.git
    branch: main
    team: acme
    project: web
    env:
      NODE_ENV: production
      SECRET_KEY: {{from_env: ACME_SECRET_KEY}}
"""


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A home of the test's own, with a server in the environment and its secret."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("ACME_SECRET_KEY", "super-secret-value-123")
    monkeypatch.setattr("time.sleep", lambda seconds: None)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    """A server that keeps state, behind every command that needs one."""
    fake = FakeServer()
    monkeypatch.setattr("noust.cli.commands.manifest.api", lambda ctx: fake)
    monkeypatch.setattr("noust.cli.remote.target_of", lambda ctx: None)
    return fake


@pytest.fixture
def file(tmp_path: Path) -> Path:
    """The manifest, on disk."""
    path = tmp_path / "noust-manifest.yaml"
    path.write_text(MANIFEST)
    return path


def run(*args: str, code: int = 0, stdin: str | None = None) -> str:
    """
    Args:
        *args: The arguments after ``noust``.
        code: The exit code expected.
        stdin: What to pipe in.

    Returns:
        What it printed, and the error a refusal raised.
    """
    result = CliRunner().invoke(
        root_cli,
        ["--remote", "https://noust.test", "--token-env", "T", *args],
        input=stdin,
        env={"T": "x"},
    )
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, output
    return output


def local(*args: str, code: int = 0) -> str:
    """
    Args:
        *args: The arguments after ``noust``, with no server in play.
        code: The exit code expected.

    Returns:
        What it printed.
    """
    result = CliRunner().invoke(root_cli, list(args))
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, output
    return output


class TestValidate:
    def test_it_needs_no_server(self, file: Path) -> None:
        output = local("manifest", "validate", str(file))

        assert "Valid: 0 project(s), 1 application(s)" in output
        assert "1 variable(s) are read from this machine" in output

    def test_it_names_what_is_wrong(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 1\napplications:\n  - domain: a.test\n    memory_mb: 5\n")

        output = local("manifest", "validate", str(bad), code=1)

        assert "applications[0].memory_mb" in output

    def test_a_file_that_is_not_there_says_so(self, tmp_path: Path) -> None:
        output = local("manifest", "validate", str(tmp_path / "nope.yaml"), code=1)

        assert "Cannot read" in output

    def test_json(self, file: Path) -> None:
        body = json.loads(local("--json", "manifest", "validate", str(file)))

        assert body["valid"] and body["applications"] == [SHOP]
        assert body["secrets_read_from_here"] == [f"{SHOP}:SECRET_KEY"]


class TestPlan:
    def test_it_prints_one_line_per_change_and_no_value(
        self, server: FakeServer, file: Path
    ) -> None:
        output = run("manifest", "plan", str(file))

        assert "deploy" in output and SHOP in output
        assert "super-secret-value-123" not in output
        assert {c[0] for c in server.calls} == {"GET"}

    def test_check_exits_two_when_there_is_something_to_apply(
        self, server: FakeServer, file: Path
    ) -> None:
        run("manifest", "plan", str(file), "--check", code=2)

        server.add_app(SHOP)
        server.env[SHOP] = {"NODE_ENV": "production", "SECRET_KEY": "super-secret-value-123"}
        run("manifest", "plan", str(file), "--check", code=0)

    def test_it_says_when_nothing_is_left(self, server: FakeServer, file: Path) -> None:
        server.add_app(SHOP)
        server.env[SHOP] = {"NODE_ENV": "production", "SECRET_KEY": "super-secret-value-123"}

        output = run("manifest", "plan", str(file))

        assert "already matches the manifest" in output

    def test_json(self, server: FakeServer, file: Path) -> None:
        body = json.loads(run("--json", "manifest", "plan", str(file)))

        assert body["empty"] is False
        assert [a["kind"] for a in body["actions"]] == ["app.create"]
        assert "super-secret-value-123" not in json.dumps(body)

    def test_a_manifest_from_standard_input(self, server: FakeServer) -> None:
        output = run(
            "manifest",
            "plan",
            "-",
            stdin="version: 1\napplications:\n  - domain: a.test\n    source: s\n",
        )

        assert "a.test" in output

    def test_an_unset_secret_is_refused_before_anything_is_read_of_it(
        self, server: FakeServer, file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ACME_SECRET_KEY")

        output = run("manifest", "plan", str(file), code=1)

        assert "ACME_SECRET_KEY is not set here" in output


class TestApply:
    def test_it_asks_first_and_in_a_pipeline_it_says_yes(
        self, server: FakeServer, file: Path
    ) -> None:
        output = run("manifest", "apply", str(file), code=2)

        assert "say --yes" in output
        assert SHOP not in server.apps

    def test_it_applies_and_the_secret_arrives_without_being_printed(
        self, server: FakeServer, file: Path
    ) -> None:
        output = run("manifest", "apply", str(file), "--yes")

        assert SHOP in server.apps
        assert server.env[SHOP] == {
            "NODE_ENV": "production",
            "SECRET_KEY": "super-secret-value-123",
        }
        assert "super-secret-value-123" not in output

    def test_a_second_apply_has_nothing_to_do(self, server: FakeServer, file: Path) -> None:
        run("manifest", "apply", str(file), "--yes")
        calls = len(server.calls)

        output = run("manifest", "apply", str(file), "--yes")

        assert "already matches" in output
        assert not [
            c for c in server.calls[calls:] if c[0] != "GET" and c[2] and "dry_run" not in str(c[2])
        ]

    def test_a_failure_exits_one_and_says_what_was_left(
        self, server: FakeServer, file: Path
    ) -> None:
        server.job_error = "health gate: connection refused"

        output = run("manifest", "apply", str(file), "--yes", code=1)

        assert "connection refused" in output

    def test_restart_restarts_what_changed(self, server: FakeServer, file: Path) -> None:
        server.add_app(SHOP)
        server.env[SHOP] = {"NODE_ENV": "development"}

        run("manifest", "apply", str(file), "--yes", "--restart")

        assert ("POST", f"/apps/{SHOP}/restart", None) in server.calls

    def test_json_is_the_outcome_of_each_change(self, server: FakeServer, file: Path) -> None:
        body = json.loads(run("--json", "manifest", "apply", str(file), "--yes"))

        assert body["ok"] is True
        assert [o["kind"] for o in body["outcomes"]] == ["app.create"]
        assert "super-secret-value-123" not in json.dumps(body)


class TestExport:
    def test_it_writes_what_is_there_and_the_file_plans_to_nothing(
        self, server: FakeServer, tmp_path: Path
    ) -> None:
        server.add_app(SHOP)
        out = tmp_path / "exported.yaml"

        run("manifest", "export", "-o", str(out))
        output = run("manifest", "plan", str(out))

        assert SHOP in out.read_text()
        assert "already matches" in output


class TestLocalRefusal:
    def test_plan_needs_a_server_and_says_how_to_name_one(self, file: Path) -> None:
        output = local("manifest", "plan", str(file), code=1)

        assert "through its API" in output and "--context" in output


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    """Two teams and their people, served by the console."""
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


class TestThePreviewInTheConsole:
    TEXT = (
        "version: 1\napplications:\n"
        "  - domain: shop.acme.example\n"
        "  - domain: brand-new.acme.example\n    source: https://example.test/x.git\n    team: acme\n"
    )

    def preview(self, world: World, who: str, text: str | None = None) -> Any:
        return world.client.post(
            "/api/manifest/plan", json={"manifest": text or self.TEXT}, headers=world.as_(who)
        )

    def test_a_member_sees_a_plan_made_of_what_it_can_see(self, world: World) -> None:
        response = self.preview(world, "alice")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [a["kind"] for a in body["actions"]] == ["app.create"]
        assert body["actions"][0]["target"] == "brand-new.acme.example"
        # Another team's application is not something it can see, so it is not "unmanaged" either.
        assert "blog.globex.example" not in body["unmanaged"]

    def test_an_administrator_sees_every_application(self, world: World) -> None:
        body = self.preview(world, "admin").json()

        assert body["unmanaged"] == ["blog.globex.example"]

    def test_it_changes_nothing(self, world: World) -> None:
        before = {a.domain for a in world.store.list_apps()}

        self.preview(world, "admin")

        assert {a.domain for a in world.store.list_apps()} == before

    def test_a_mistake_in_the_manifest_is_a_400_that_names_the_field(self, world: World) -> None:
        response = self.preview(
            world, "alice", "version: 1\napplications:\n  - domain: a.test\n    oops: 1\n"
        )

        assert response.status_code == 400
        assert "applications[0].oops" in response.json()["detail"]

    def test_a_server_does_not_read_its_own_environment_for_a_document(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SERVER_ONLY_SECRET", "must-not-be-read")
        text = (
            "version: 1\napplications:\n  - domain: brand-new.acme.example\n    source: s\n    team: acme\n"
            "    env:\n      K: {from_env: SERVER_ONLY_SECRET}\n"
        )

        response = self.preview(world, "alice", text)

        assert response.status_code == 200
        assert response.json()["actions"][0]["unverified"] is True
        assert "must-not-be-read" not in response.text

    def test_a_viewer_may_look_too_it_only_reads(self, world: World) -> None:
        assert self.preview(world, "dana").status_code == 200

    def test_nobody_anonymous(self, world: World) -> None:
        response = world.client.post("/api/manifest/plan", json={"manifest": self.TEXT})

        assert response.status_code == 401
