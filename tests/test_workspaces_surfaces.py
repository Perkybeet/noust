# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust workspace`` and ``/api/workspaces`` (checklist A-53).

The surfaces name workspaces and domains and nothing else: the build block and the watch paths
are made from what the repository says, and everything is checked before any application is
created, so a typo in the second workspace does not leave the first one deployed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.deployers import workspaces as workspaces_module
from noust.deployers.workspaces import WorkspaceReport, detect_workspaces
from tests.test_workspaces import turbo  # noqa: F401 - the fixture

SOURCE = "https://github.com/acme/mono.git"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def report(turbo: Path) -> WorkspaceReport:  # noqa: F811
    return detect_workspaces(turbo)


@pytest.fixture(autouse=True)
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from noust.core.config import Config

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()


def invoke(*args: str) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli

    return CliRunner().invoke(cli, list(args))


class TestTheCommands:
    @pytest.fixture
    def cli_report(
        self, monkeypatch: pytest.MonkeyPatch, report: WorkspaceReport
    ) -> WorkspaceReport:
        monkeypatch.setattr(
            "noust.cli.commands.workspace.inspect_workspaces", lambda *a, **k: report
        )
        return report

    @pytest.fixture
    def plans(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        seen: list[Any] = []
        monkeypatch.setattr(
            "noust.cli.commands.workspace.run_import",
            lambda ctx, plan, yes=False: seen.append((plan, yes)),
        )
        return seen

    def test_detect_lists_applications_and_libraries(self, cli_report: WorkspaceReport) -> None:
        shown = invoke("workspace", "detect", SOURCE)

        assert shown.exit_code == 0, shown.output
        assert "turborepo (pnpm)" in shown.output
        assert "apps/web  web  [nextjs]" in shown.output
        assert "packages/ui  @acme/ui  (not an application: a library" in shown.output
        assert "builds with: pnpm exec turbo run build --filter=web..." in shown.output
        assert "noust workspace create" in shown.output

    def test_detect_in_json(self, cli_report: WorkspaceReport) -> None:
        shown = invoke("workspace", "detect", SOURCE, "--json")

        body = json.loads(shown.output)
        assert body["tool"] == "turborepo"
        assert [w["directory"] for w in body["workspaces"]][:2] == ["apps/api", "apps/web"]

    def test_a_repository_without_workspaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "noust.cli.commands.workspace.inspect_workspaces", lambda *a, **k: WorkspaceReport()
        )

        shown = invoke("workspace", "detect", SOURCE)

        assert shown.exit_code == 0
        assert "declares no workspaces" in shown.output

    def test_create_plans_one_application_per_workspace_on_releases(
        self, store: NoustStore, cli_report: WorkspaceReport, plans: list[Any]
    ) -> None:
        done = invoke(
            "workspace",
            "create",
            SOURCE,
            "--app",
            "web=web.example.com",
            "--app",
            "apps/api=api.example.com",
            "--branch",
            "main",
            "--yes",
        )

        assert done.exit_code == 0, done.output
        assert [plan.domain for plan, _yes in plans] == ["web.example.com", "api.example.com"]
        web = plans[0][0]
        assert web.create.layout == "releases" and web.create.branch == "main"
        assert web.create.app_type == "nextjs"
        assert "base_directory: apps/web" in (web.create.build_document or "")
        assert web.document["source_options"]["watch_paths"][0] == "apps/web/**"
        assert all(yes for _plan, yes in plans)

    def test_an_unknown_workspace_names_the_ones_there_are(
        self, store: NoustStore, cli_report: WorkspaceReport, plans: list[Any]
    ) -> None:
        refused = invoke("workspace", "create", SOURCE, "--app", "nope=x.example.com")

        assert refused.exit_code != 0
        assert refused.exception.field == "app"
        assert "apps/api, apps/web" in (refused.exception.details or "")
        assert plans == []

    def test_a_library_is_not_deployed_and_nothing_else_is_either(
        self, store: NoustStore, cli_report: WorkspaceReport, plans: list[Any]
    ) -> None:
        refused = invoke(
            "workspace",
            "create",
            SOURCE,
            "--app",
            "web=web.example.com",
            "--app",
            "@acme/ui=ui.example.com",
        )

        assert refused.exit_code != 0
        assert "not an application" in str(refused.exception)
        assert plans == []

    @pytest.mark.parametrize(
        "pairs",
        [
            ("web",),
            ("web=",),
            ("=web.example.com",),
            ("web=a.example.com", "apps/api=a.example.com"),
        ],
    )
    def test_a_pair_that_is_not_workspace_equals_domain(
        self,
        store: NoustStore,
        cli_report: WorkspaceReport,
        plans: list[Any],
        pairs: tuple[str, ...],
    ) -> None:
        args = [arg for pair in pairs for arg in ("--app", pair)]

        refused = invoke("workspace", "create", SOURCE, *args)

        assert refused.exit_code != 0
        assert plans == []

    def test_dry_run_shows_the_plans_and_creates_nothing(
        self, store: NoustStore, cli_report: WorkspaceReport
    ) -> None:
        shown = invoke("workspace", "create", SOURCE, "--app", "web=web.example.com", "--dry-run")

        assert shown.exit_code == 0, shown.output
        assert "--- web.example.com" in shown.output
        assert "Rehearsal: nothing was created" in shown.output


class TestTheApi:
    @pytest.fixture
    def queued(self, monkeypatch: pytest.MonkeyPatch, report: WorkspaceReport) -> list[Any]:
        seen: list[Any] = []

        def fake(plan: Any, request: Any, session: Any) -> Any:
            from noust.web.api.deps import JobAcceptedResponse

            seen.append(plan)
            return JobAcceptedResponse(
                job_id=f"job-{len(seen)}", status="queued", message=plan.domain, job={}
            )

        monkeypatch.setattr("noust.web.api.workspaces.queue_import", fake)
        monkeypatch.setattr("noust.web.api.workspaces.inspect_workspaces", lambda *a, **k: report)
        return seen

    @pytest.fixture
    def client(self, store: NoustStore, queued: list[Any]) -> Any:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import workspaces
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers, require_elevated

        server = FastAPI()
        install_error_handlers(server)
        server.include_router(workspaces.router, prefix="/api/workspaces")
        session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
        server.dependency_overrides[get_current_session] = lambda: session
        server.dependency_overrides[require_elevated] = lambda: session
        return TestClient(server)

    def test_the_permissions_and_scopes(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_workspaces import ROUTES
        from noust.web.permissions.scopes import INSTANCE, SCOPED, rule_for

        assert ROUTES[("POST", "/api/workspaces/inspect")] == Permission.APPS_MANAGE
        assert ROUTES[("POST", "/api/workspaces/deploy")] == Permission.APPS_MANAGE
        inspect_rule = rule_for("POST", "/api/workspaces/inspect")
        deploy_rule = rule_for("POST", "/api/workspaces/deploy")
        assert inspect_rule is not None and inspect_rule.scoping == SCOPED
        assert deploy_rule is not None and deploy_rule.scoping == INSTANCE

    def test_inspect(self, client: Any) -> None:
        got = client.post("/api/workspaces/inspect", json={"source": SOURCE})

        assert got.status_code == 200, got.text
        body = got.json()
        assert body["tool"] == "turborepo" and body["package_manager"] == "pnpm"
        web = next(w for w in body["workspaces"] if w["name"] == "web")
        assert web["deployable"] is True and web["watch_paths"][0] == "apps/web/**"
        assert web["build"][:3] == ["pnpm", "exec", "turbo"]

    def test_deploy_queues_a_job_per_application_in_order(
        self, client: Any, queued: list[Any]
    ) -> None:
        got = client.post(
            "/api/workspaces/deploy",
            json={
                "source": SOURCE,
                "branch": "main",
                "apps": [
                    {"workspace": "web", "domain": "web.example.com"},
                    {"workspace": "apps/api", "domain": "api.example.com"},
                ],
            },
        )

        assert got.status_code == 202, got.text
        assert [job["job_id"] for job in got.json()["jobs"]] == ["job-1", "job-2"]
        assert [plan.domain for plan in queued] == ["web.example.com", "api.example.com"]
        assert queued[0].create.layout == "releases"

    def test_a_mistake_in_the_second_queues_nothing(self, client: Any, queued: list[Any]) -> None:
        got = client.post(
            "/api/workspaces/deploy",
            json={
                "source": SOURCE,
                "apps": [
                    {"workspace": "web", "domain": "web.example.com"},
                    {"workspace": "@acme/ui", "domain": "ui.example.com"},
                ],
            },
        )

        assert got.status_code == 400
        assert "not an application" in got.text
        assert queued == []

    @pytest.mark.parametrize(
        ("apps", "message"),
        [
            ([], "between 1 and 20"),
            ([{"workspace": "ghost", "domain": "g.example.com"}], "There is no workspace"),
            (
                [
                    {"workspace": "web", "domain": "same.example.com"},
                    {"workspace": "apps/api", "domain": "same.example.com"},
                ],
                "given for two workspaces",
            ),
        ],
    )
    def test_what_is_refused_before_anything_is_queued(
        self, client: Any, queued: list[Any], apps: list[dict[str, str]], message: str
    ) -> None:
        got = client.post("/api/workspaces/deploy", json={"source": SOURCE, "apps": apps})

        assert got.status_code == 400
        assert message in got.text
        assert queued == []

    def test_a_repository_without_workspaces(
        self, client: Any, queued: list[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.web.api.workspaces.inspect_workspaces", lambda *a, **k: WorkspaceReport()
        )

        got = client.post(
            "/api/workspaces/deploy",
            json={"source": SOURCE, "apps": [{"workspace": "web", "domain": "w.example.com"}]},
        )

        assert got.status_code == 400 and "declares no workspaces" in got.text
        assert queued == []


def test_the_sparse_patterns_read_workspaces_two_and_three_levels_down() -> None:
    assert "/*/*/package.json" in workspaces_module.SPARSE_PATTERNS
    assert "/pnpm-workspace.yaml" in workspaces_module.SPARSE_PATTERNS
