# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server written down: the schema, the plan and the apply (J-15).

The promises: the schema is closed and names the field that is wrong; a plan
reads and prints no value and creates nothing; an apply creates what is missing,
brings what exists to the manifest and never deletes; and doing it twice finds
nothing left, which is what makes it safe to run from a pipeline on every push.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.manifest import (
    Applier,
    EnvironmentResolver,
    ManifestError,
    RefusingResolver,
    parse,
    plan,
)
from noust.manifest.export import export
from tests.manifest_support import FakeServer

SHOP = "shop.acme.test"

BASIC = f"""
version: 1
name: acme production
projects:
  - team: acme
    project: web
    environments: [production, staging]
applications:
  - domain: {SHOP}
    source: https://example.test/shop.git
    branch: main
    team: acme
    project: web
    environment: production
    port: 3000
    env:
      NODE_ENV: production
      API_URL: https://api.acme.test
    domains: [www.{SHOP}]
    limits: {{memory_max_mb: 512}}
    health: {{path: /healthz, timeout: 30}}
    databases:
      - engine: postgresql
        name: shop_db
    backups: {{schedule: daily}}
"""


def make(text: str = BASIC) -> Any:
    """
    Args:
        text: A manifest.

    Returns:
        It, parsed.
    """
    return parse(text)


class TestTheSchema:
    def test_a_valid_manifest_is_read(self) -> None:
        manifest = make()

        assert manifest.name == "acme production"
        assert [p.environments for p in manifest.projects] == [("production", "staging")]
        app = manifest.applications[0]
        assert (app.domain, app.branch, app.port, app.team) == (SHOP, "main", 3000, "acme")
        assert app.env == {"NODE_ENV": "production", "API_URL": "https://api.acme.test"}
        assert app.domains == (f"www.{SHOP}",)
        assert app.limits == {"memory_max_mb": 512, "cpu_quota_percent": None, "tasks_max": None}
        assert app.backups is not None and app.backups.retention_count == 7

    def test_json_is_yaml_too(self) -> None:
        manifest = parse(json.dumps({"version": 1, "applications": [{"domain": "a.test"}]}))

        assert manifest.applications[0].domain == "a.test"

    @pytest.mark.parametrize(
        ("text", "path"),
        [
            ("applications: []", "version"),
            ("version: 2", "version"),
            ("version: 1\nunknown: 1", "unknown"),
            ("version: 1\napplications:\n  - source: x", "applications[0].domain"),
            (
                "version: 1\napplications:\n  - domain: a.test\n    memory_mb: 512",
                "applications[0].memory_mb",
            ),
            ("version: 1\napplications:\n  - domain: not a domain", "applications[0].domain"),
            (
                "version: 1\napplications:\n  - domain: a.test\n    port: 99999",
                "applications[0].port",
            ),
            (
                "version: 1\napplications:\n  - domain: a.test\n    env:\n      bad name: x",
                "applications[0].env",
            ),
            (
                "version: 1\napplications:\n  - domain: a.test\n    limits: {memory_max_mb: 5}",
                "applications[0].limits.memory_max_mb",
            ),
            (
                "version: 1\napplications:\n  - domain: a.test\n    ssl: maybe",
                "applications[0].ssl",
            ),
            (
                "version: 1\napplications:\n  - domain: a.test\n    source: s\n    recipe: r",
                "applications[0]",
            ),
            (
                "version: 1\napplications:\n  - domain: a.test\n  - domain: a.test",
                "applications[1].domain",
            ),
            ("version: 1\nprojects:\n  - team: Acme Corp\n    project: web", "projects[0].team"),
            (
                "version: 1\napplications:\n  - domain: a.test\n    backups: {schedule: 'rm -rf /; x'}",
                "applications[0].backups.schedule",
            ),
        ],
    )
    def test_a_mistake_is_refused_and_names_the_field(self, text: str, path: str) -> None:
        with pytest.raises(ManifestError) as caught:
            parse(text)

        assert caught.value.field == path or path in str(caught.value), str(caught.value)

    def test_yaml_that_is_not_yaml_is_refused(self) -> None:
        with pytest.raises(ManifestError, match="not valid YAML"):
            parse("version: [1")

    def test_a_manifest_is_bounded(self) -> None:
        with pytest.raises(ManifestError, match="larger than"):
            parse("version: 1\n# " + "x" * (1024 * 1024))

    def test_a_secret_is_read_from_where_it_is_applied_and_is_not_text(self) -> None:
        manifest = parse(
            "version: 1\napplications:\n  - domain: a.test\n    env:\n"
            "      KEY: {from_env: ACME_KEY}\n      CERT: {from_file: certs/a.pem}\n      PLAIN: 7\n"
        )

        secrets = {(d, n): s.kind for d, n, s in manifest.secrets()}
        assert secrets == {("a.test", "KEY"): "from_env", ("a.test", "CERT"): "from_file"}
        assert manifest.applications[0].env["PLAIN"] == "7"  # type: ignore[index]

    def test_a_secret_needs_exactly_one_source(self) -> None:
        with pytest.raises(ManifestError, match="exactly one"):
            parse(
                "version: 1\napplications:\n  - domain: a.test\n    env:\n      K: {from_env: A, from_file: B}"
            )


class TestThePlan:
    def test_everything_missing_is_planned_in_the_order_it_can_be_done(self) -> None:
        server = FakeServer()

        result = plan(make(), server, EnvironmentResolver(environ={}))

        kinds = [a.kind for a in result.actions]
        assert kinds == ["environment.create", "app.create", "app.domain", "app.backups"]
        assert "staging" in result.actions[0].summary
        create = next(a for a in result.actions if a.kind == "app.create")
        assert "2 variables" in create.summary and "acme/web/production" in create.summary

    def test_the_first_database_is_made_with_the_application_the_next_after(self) -> None:
        text = BASIC.replace(
            "- engine: postgresql\n        name: shop_db",
            "- engine: postgresql\n        name: shop_db\n      - engine: redis",
        )

        result = plan(make(text), FakeServer(), EnvironmentResolver(environ={}))

        databases = [a for a in result.actions if a.kind == "app.database"]
        assert [a.summary for a in databases] == ["create a redis database for it"]

    def test_a_plan_prints_no_value(self) -> None:
        text = BASIC.replace("https://api.acme.test", "https://user:hunter2@api.acme.test")

        result = plan(make(text), FakeServer(), EnvironmentResolver(environ={}))

        shown = json.dumps(result.to_dict())
        assert "hunter2" not in shown and "api.acme.test" not in shown

    def test_a_plan_creates_nothing(self) -> None:
        server = FakeServer()

        plan(make(), server, EnvironmentResolver(environ={}))

        assert {method for method, _, _ in server.calls} == {"GET"}
        assert server.apps == {}

    def test_an_application_that_is_as_written_has_nothing_to_do(self) -> None:
        server = FakeServer()
        server.add_app(SHOP, memory_max_mb=512, health_path="/healthz", health_timeout=30)
        server.projects[("acme", "web")]["environments"] = ["production", "staging"]
        server.env[SHOP] = {"NODE_ENV": "production", "API_URL": "https://api.acme.test"}
        server.aliases[SHOP] = [f"www.{SHOP}"]
        server.databases[SHOP] = [("postgresql", "shop_db")]
        server.schedules[SHOP] = {
            "domain": SHOP,
            "schedule": "daily",
            "retention_count": 7,
            "retention_days": 30,
            "include_databases": True,
        }

        result = plan(make(), server, EnvironmentResolver(environ={}))

        assert result.empty and result.warnings == []

    def test_each_difference_is_one_action(self) -> None:
        server = FakeServer()
        server.add_app(
            SHOP,
            branch="develop",
            memory_max_mb=256,
            team="acme",
            project="web",
            environment="production",
        )
        server.projects[("acme", "web")]["environments"] = ["production", "staging"]
        server.env[SHOP] = {"NODE_ENV": "development"}

        result = plan(make(), server, EnvironmentResolver(environ={}))

        assert [a.kind for a in result.actions] == [
            "app.branch",
            "app.limits",
            "app.health",
            "app.domain",
            "app.database",
            "app.env",
            "app.backups",
        ]
        env = next(a for a in result.actions if a.kind == "app.env")
        assert env.elevated and "API_URL" in env.summary and "NODE_ENV" in env.summary

    def test_what_cannot_change_is_a_warning_not_an_action(self) -> None:
        server = FakeServer()
        server.add_app(SHOP, source="https://example.test/other.git", port=8080, app_type="python")
        text = BASIC.replace("port: 3000", "port: 3000\n    type: nodejs")

        result = plan(make(text), server, EnvironmentResolver(environ={}))

        assert len(result.warnings) == 3
        assert all("cannot change once deployed" in w for w in result.warnings)
        assert not {a.kind for a in result.actions} & {"app.create"}

    def test_it_moves_an_application_that_lives_elsewhere(self) -> None:
        server = FakeServer()
        server.add_app(SHOP, environment="staging")
        server.projects[("acme", "web")]["environments"] = ["production", "staging"]

        result = plan(make(), server, EnvironmentResolver(environ={}))

        move = next(a for a in result.actions if a.kind == "app.move")
        assert "acme/web/staging" in move.summary and "acme/web/production" in move.summary

    def test_what_the_manifest_does_not_name_is_listed_and_left_alone(self) -> None:
        server = FakeServer()
        server.add_app("old.acme.test")
        server.add_app("legacy.acme.test")

        result = plan(make(), server, EnvironmentResolver(environ={}))

        assert result.unmanaged == ["legacy.acme.test", "old.acme.test"]
        assert not [
            a for a in result.actions if a.target.endswith("acme.test") and a.kind == "app.move"
        ]

    def test_a_team_that_is_not_there_is_said_and_nothing_is_planned_in_it(self) -> None:
        server = FakeServer()
        server.teams = set()
        server.projects = {}

        result = plan(make(), server, EnvironmentResolver(environ={}))

        assert any("the team 'acme' does not exist" in w for w in result.warnings)
        assert "project.create" not in [a.kind for a in result.actions]

    def test_an_application_with_nothing_to_create_it_from_is_a_warning(self) -> None:
        text = "version: 1\napplications:\n  - domain: new.acme.test"

        result = plan(make(text), FakeServer(), EnvironmentResolver(environ={}))

        assert result.actions == [] and "no source or recipe" in result.warnings[0]

    def test_without_sudo_mode_the_variables_are_listed_unverified(self) -> None:
        server = FakeServer(elevated=False)
        server.add_app(SHOP)

        result = plan(make(), server, EnvironmentResolver(environ={}))

        env = next(a for a in result.actions if a.kind == "app.env")
        assert env.unverified and env.elevated
        assert any("sudo mode" in w for w in result.warnings)

    def test_a_server_previewing_a_manifest_never_reads_its_own_environment(self) -> None:
        text = "version: 1\napplications:\n  - domain: a.test\n    source: s\n    env:\n      K: {from_env: HOME}"

        result = plan(make(text), FakeServer(), RefusingResolver(), compare_env=False)

        create = result.actions[0]
        assert create.kind == "app.create" and create.unverified
        assert create.data["env"] is None

    def test_a_variable_that_is_not_set_where_it_is_applied_is_refused(self) -> None:
        text = "version: 1\napplications:\n  - domain: a.test\n    source: s\n    env:\n      K: {from_env: NOPE_NOT_SET}"

        with pytest.raises(ManifestError, match="NOPE_NOT_SET"):
            plan(make(text), FakeServer(), EnvironmentResolver(environ={}))

    def test_a_variable_comes_from_a_file_beside_the_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "secret.txt").write_text("from-a-file\n")
        text = "version: 1\napplications:\n  - domain: a.test\n    source: s\n    env:\n      K: {from_file: secret.txt}"

        result = plan(make(text), FakeServer(), EnvironmentResolver(environ={}, base=tmp_path))

        assert result.actions[0].data["env"] == {"K": "from-a-file"}


class TestTheApply:
    def apply(self, server: FakeServer, text: str = BASIC, **kwargs: Any) -> Any:
        manifest = make(text)
        resolver = EnvironmentResolver(environ={})
        result = plan(manifest, server, resolver)
        return Applier(server, sleep=lambda s: None, **kwargs).apply(result)

    def test_a_server_is_made_from_nothing(self) -> None:
        server = FakeServer()

        outcome = self.apply(server)

        assert outcome.ok, [o.message for o in outcome.outcomes]
        assert server.projects[("acme", "web")]["environments"] == ["production", "staging"]
        assert SHOP in server.apps
        assert server.aliases[SHOP] == [f"www.{SHOP}"]
        assert server.schedules[SHOP]["schedule"] == "daily"
        assert server.env[SHOP]["NODE_ENV"] == "production"

    def test_the_application_is_created_with_everything_the_manifest_says_of_it(self) -> None:
        server = FakeServer()

        self.apply(server)

        created = next(
            body for method, path, body in server.calls if (method, path) == ("POST", "/apps")
        )
        assert created["source"] == "https://example.test/shop.git"
        assert (created["team"], created["project"], created["environment"]) == (
            "acme",
            "web",
            "production",
        )
        assert created["env_vars"] == {"NODE_ENV": "production", "API_URL": "https://api.acme.test"}
        assert created["memory_max_mb"] == 512 and created["health_path"] == "/healthz"
        assert created["database"] == {"engine": "postgresql", "name": "shop_db"}

    def test_doing_it_twice_finds_nothing_left(self) -> None:
        server = FakeServer()
        self.apply(server)

        again = plan(make(), server, EnvironmentResolver(environ={}))

        assert again.empty, [a.summary for a in again.actions]

    def test_an_application_that_drifted_is_brought_back_and_then_matches(self) -> None:
        server = FakeServer()
        server.add_app(SHOP, branch="old", memory_max_mb=64)
        server.projects[("acme", "web")]["environments"] = ["production", "staging"]
        server.env[SHOP] = {"NODE_ENV": "development", "KEEP_ME": "yes"}

        outcome = self.apply(server)

        assert outcome.ok
        assert server.apps[SHOP]["branch"] == "main" and server.apps[SHOP]["memory_max_mb"] == 512
        assert server.env[SHOP]["KEEP_ME"] == "yes"  # variables the manifest does not name stay
        assert outcome.restart == [SHOP]
        assert plan(make(), server, EnvironmentResolver(environ={})).empty

    def test_nothing_is_ever_deleted(self) -> None:
        server = FakeServer()
        server.add_app("old.acme.test")
        server.aliases[SHOP] = ["stale." + SHOP]
        server.add_app(SHOP)

        self.apply(server)

        assert "old.acme.test" in server.apps
        assert "stale." + SHOP in server.aliases[SHOP]
        assert not [c for c in server.calls if c[0] == "DELETE"]

    def test_the_first_failure_stops_the_run_and_the_rest_wait(self) -> None:
        server = FakeServer()
        server.fail_on = {f"/apps/{SHOP}/domains"}

        outcome = self.apply(server)

        assert not outcome.ok
        statuses = [o.status for o in outcome.outcomes]
        assert statuses.index("failed") < statuses.index("skipped")
        assert outcome.outcomes[-1].status == "skipped"
        assert server.schedules == {}

    def test_after_a_failure_the_next_plan_holds_only_what_is_left(self) -> None:
        server = FakeServer()
        server.fail_on = {"/backup-schedules"}
        self.apply(server)
        server.fail_on = set()

        again = plan(make(), server, EnvironmentResolver(environ={}))

        assert [a.kind for a in again.actions] == ["app.backups"]

    def test_a_deployment_that_fails_is_reported_with_the_servers_words(self) -> None:
        server = FakeServer()
        server.job_error = "health gate: connection refused"

        outcome = self.apply(server)

        failed = next(o for o in outcome.outcomes if o.status == "failed")
        assert "connection refused" in failed.message and failed.action.kind == "app.create"

    def test_no_wait_queues_and_goes_on(self) -> None:
        server = FakeServer()

        outcome = self.apply(server, wait=False)

        create = next(o for o in outcome.outcomes if o.action.kind == "app.create")
        assert create.status == "applied" and "queued as job" in create.message
        assert not any(c[1].startswith("/jobs/") for c in server.calls)

    def test_a_variable_that_could_not_be_read_fails_the_step_instead_of_guessing(self) -> None:
        text = "version: 1\napplications:\n  - domain: a.test\n    source: s\n    env:\n      K: {from_env: X}"
        server = FakeServer()
        result = plan(make(text), server, RefusingResolver(), compare_env=False)

        outcome = Applier(server, sleep=lambda s: None).apply(result)

        assert not outcome.ok and "could not be" in outcome.outcomes[0].message
        assert server.apps == {}

    def test_the_outcome_shows_no_value(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        server.projects[("acme", "web")]["environments"] = ["production", "staging"]

        outcome = self.apply(server, BASIC.replace("https://api.acme.test", "SUPERSECRETVALUE"))

        assert "SUPERSECRETVALUE" not in json.dumps(outcome.to_dict())


class TestExport:
    def test_a_server_is_written_down_without_a_single_value(self) -> None:
        server = FakeServer()
        server.add_app(
            SHOP,
            memory_max_mb=512,
            health_path="/healthz",
            source="https://ci:token123@example.test/shop.git",
        )
        server.aliases[SHOP] = [f"www.{SHOP}"]
        server.env[SHOP] = {"SECRET": "hunter2"}

        text = export(server)

        assert "hunter2" not in text and "token123" not in text
        assert "https://example.test/shop.git" in text
        manifest = parse(text)
        assert manifest.applications[0].domain == SHOP
        assert manifest.applications[0].domains == (f"www.{SHOP}",)

    def test_what_is_exported_plans_to_nothing_on_the_same_server(self) -> None:
        server = FakeServer()
        server.add_app(SHOP, memory_max_mb=512)
        server.aliases[SHOP] = [f"www.{SHOP}"]

        result = plan(parse(export(server)), server, EnvironmentResolver(environ={}))

        assert result.empty, [a.summary for a in result.actions]
