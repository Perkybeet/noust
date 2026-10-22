# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust variables``, ``noust secrets`` and the new ``noust env`` verbs
(checklist B-43, B-44, B-45, B-46, B-48).

They are a front end over the stores the console calls, so what these tests pin
is what an operator types: that a value does not have to be on the command line,
that secrets are not printed, that a scope is found from team, project and
environment, and what is refused.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.core.teams import TeamStore
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.app_env import read_environment_of
from noust.deployers.helpers.env_manager import EnvManager
from noust.web.auth import STATE_DIR_ENV

DOMAIN = "shop.example.com"


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore, runner: FakeRunner
) -> App:
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    TeamStore(store).create_team("Acme", slug="acme")
    project = TeamStore(store).create_project("acme", "Shop", slug="shop")
    staging = TeamStore(store).create_environment("acme", "shop", "Staging", slug="staging")
    store.create_app(App(domain=DOMAIN, app_path=str(root), app_type="nodejs", port=3000))
    TeamStore(store).move_app(DOMAIN, project.id, staging.id)
    found = store.get_app(DOMAIN)
    assert found is not None
    return found


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


def run(cli: CliRunner, *args: str, code: int = 0, stdin: str | None = None) -> str:
    result = cli.invoke(root_cli, list(args), input=stdin)
    assert result.exit_code == code, f"{result.output} {result.exception}"
    return f"{result.output}{result.exception or ''}"


class TestVariables:
    def test_a_scope_is_found_from_where_it_is(self, cli: CliRunner, app: App) -> None:
        run(
            cli,
            "variables",
            "set",
            "environment",
            "API",
            "https://staging.api",
            "--team",
            "acme",
            "--project",
            "shop",
            "--environment",
            "staging",
        )
        run(cli, "variables", "set", "server", "REGION", "eu")

        staged = json.loads(
            run(
                cli,
                "--json",
                "variables",
                "list",
                "environment",
                "--team",
                "acme",
                "--project",
                "shop",
                "--environment",
                "staging",
            )
        )
        server = json.loads(run(cli, "--json", "variables", "list", "server"))

        assert [v["name"] for v in staged["variables"]] == ["API"]
        assert [v["name"] for v in server["variables"]] == ["REGION"]
        assert staged["label"] == "environment acme/shop/staging"

    def test_a_value_can_come_from_stdin_and_a_secret_is_not_printed(
        self, cli: CliRunner, app: App
    ) -> None:
        run(cli, "variables", "set", "server", "DB_PASSWORD", "--stdin", stdin="s3cret-value\n")

        shown = run(cli, "variables", "list", "server")
        clear = run(cli, "variables", "list", "server", "--unmask")

        assert "s3cret-value" not in shown
        assert "s3cret-value" in clear

    def test_a_value_and_stdin_together_are_refused(self, cli: CliRunner, app: App) -> None:
        run(cli, "variables", "set", "server", "A", "x", "--stdin", stdin="y", code=2)

    def test_an_unset_says_who_still_uses_it(self, cli: CliRunner, app: App) -> None:
        run(cli, "variables", "set", "server", "REGION", "eu")
        run(cli, "env", "set", DOMAIN, "R={{server.REGION}}")

        out = run(cli, "variables", "unset", "server", "REGION")

        assert DOMAIN in out and "next deploy fails" in out

    def test_used_by(self, cli: CliRunner, app: App) -> None:
        run(cli, "variables", "set", "server", "REGION", "eu")
        run(cli, "env", "set", DOMAIN, "R={{server.REGION}}")

        users = json.loads(run(cli, "--json", "variables", "used-by", "server", "REGION"))

        assert users["applications"] == [DOMAIN]

    def test_a_variable_that_is_not_there_cannot_be_unset(self, cli: CliRunner, app: App) -> None:
        run(cli, "variables", "unset", "server", "NOPE", code=1)


class TestEnvVerbs:
    def test_set_keeps_the_others_and_resolves_references(
        self, cli: CliRunner, app: App, store: NoustStore
    ) -> None:
        run(cli, "variables", "set", "server", "REGION", "eu")
        run(cli, "env", "set", DOMAIN, "A=1")
        run(cli, "env", "set", DOMAIN, "R={{server.REGION}}", "B=2")

        env = json.loads(run(cli, "--json", "env", "show", DOMAIN, "--unmask"))

        assert env["variables"] == {"A": "1", "R": "eu", "B": "2"}
        assert env["references"] == {"R": "{{server.REGION}}"}
        assert env["predefined"]["NOUST_DOMAIN"] == DOMAIN

    def test_a_build_only_variable(self, cli: CliRunner, app: App) -> None:
        run(
            cli,
            "env",
            "set",
            DOMAIN,
            "--stdin",
            "--build-only",
            stdin="NPM_TOKEN=npm_abcdefghijkl\n",
        )

        found = read_environment_of(app)
        assert found.build_only == {"NPM_TOKEN": "npm_abcdefghijkl"}
        assert "npm_abcdefghijkl" not in (Path(app.app_path) / ".env").read_text()

    def test_audience_changes_a_variable_that_exists(self, cli: CliRunner, app: App) -> None:
        run(cli, "env", "set", DOMAIN, "SESSION=abc123def456")

        run(cli, "env", "audience", DOMAIN, "SESSION", "--runtime-only")

        assert read_environment_of(app).runtime_only_names == ["SESSION"]
        run(cli, "env", "audience", DOMAIN, "NOPE", "--both", code=1)

    def test_unset_removes_and_refuses_what_is_not_there(self, cli: CliRunner, app: App) -> None:
        run(cli, "env", "set", DOMAIN, "A=1", "B=2")

        run(cli, "env", "unset", DOMAIN, "A")
        run(cli, "env", "unset", DOMAIN, "A", code=1)

        assert read_environment_of(app).values == {"B": "2"}

    def test_resolve_applies_a_changed_shared_variable(self, cli: CliRunner, app: App) -> None:
        run(cli, "variables", "set", "server", "REGION", "eu")
        run(cli, "env", "set", DOMAIN, "R={{server.REGION}}")
        run(cli, "variables", "set", "server", "REGION", "us")

        out = run(cli, "env", "resolve", DOMAIN)

        assert "Resolved again: R" in out
        assert EnvManager().read_env_file(Path(app.app_path) / ".env")["R"] == "us"
        assert "Nothing changed" in run(cli, "env", "resolve", DOMAIN)

    def test_two_audience_flags_are_a_usage_error(self, cli: CliRunner, app: App) -> None:
        run(cli, "env", "set", DOMAIN, "A=1", "--build-only", "--runtime-only", code=2)

    def test_a_pair_without_an_equals_sign_is_a_usage_error(self, cli: CliRunner, app: App) -> None:
        run(cli, "env", "set", DOMAIN, "oops", code=2)

    def test_show_names_where_a_value_comes_from(self, cli: CliRunner, app: App) -> None:
        run(cli, "variables", "set", "server", "REGION", "eu")
        run(cli, "env", "set", DOMAIN, "R={{server.REGION}}")

        out = run(cli, "env", "show", DOMAIN)

        assert "from {{server.REGION}}" in out
        assert "NOUST_URL" in out


class TestSecrets:
    def test_a_provider_is_added_listed_and_removed_and_its_token_is_not_shown(
        self, cli: CliRunner, app: App
    ) -> None:
        run(
            cli,
            "secrets",
            "add",
            "doppler",
            "--kind",
            "doppler",
            "--setting",
            "project=shop",
            "--setting",
            "config=prd",
            "--token-stdin",
            stdin="dp.st.the-token\n",
        )

        listed = run(cli, "secrets", "list")
        as_json = json.loads(run(cli, "--json", "secrets", "list"))

        assert "doppler" in listed and "the-token" not in listed
        assert as_json["providers"][0]["has_token"] is True
        run(cli, "secrets", "remove", "doppler")
        assert json.loads(run(cli, "--json", "secrets", "list"))["providers"] == []
        run(cli, "secrets", "remove", "doppler", code=1)

    def test_a_kind_needs_its_settings(self, cli: CliRunner, app: App) -> None:
        out = run(
            cli, "secrets", "add", "d", "--kind", "doppler", "--token-stdin", stdin="t\n", code=1
        )

        assert "needs: project, config" in out

    def test_the_kinds_are_listed_with_what_each_takes(self, cli: CliRunner, app: App) -> None:
        out = run(cli, "secrets", "kinds")

        assert "vault" in out and "--setting project=" in out

    def test_a_setting_without_a_value_is_a_usage_error(self, cli: CliRunner, app: App) -> None:
        run(
            cli,
            "secrets",
            "add",
            "d",
            "--kind",
            "doppler",
            "--setting",
            "project",
            "--token-stdin",
            stdin="t\n",
            code=2,
        )
