# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust recipe list|show|install|outdated|update``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.audit_policy import is_read_only
from noust.core.store import App, NoustStore
from noust.recipes import get_recipe
from noust.recipes.installed import record_install
from noust.recipes.updates import RecipeUpdate

DOMAIN = "site.example.com"


def run(*args: str) -> Any:
    """Invoke ``noust`` with these arguments."""
    return CliRunner().invoke(root_cli, list(args))


def test_list_prints_the_catalogue_with_its_docker_badge_and_slogan() -> None:
    """One line per recipe: name, title, version, badge, slogan."""
    result = run("recipe", "list")

    assert result.exit_code == 0, result.output
    assert "Vaultwarden 1.37.4 [Docker]" in result.output
    assert "WordPress" in result.output and "WordPress 7" in result.output
    assert "[Docker]" not in next(
        line for line in result.output.splitlines() if "Uptime Kuma" in line
    )
    assert "not available in this release" in next(
        line for line in result.output.splitlines() if "Vault" in line and "Vaultwarden" not in line
    )


def test_list_json_carries_the_catalogue_fields_and_filters() -> None:
    """Search, category, tag and kind, as the API's."""
    everything = json.loads(run("recipe", "list", "--json").output)
    found = json.loads(run("recipe", "list", "--json", "-q", "password manager").output)
    media = json.loads(run("recipe", "list", "--json", "--category", "media").output)
    tagged = json.loads(
        run("recipe", "list", "--json", "--category", "media", "--tag", "subsonic").output
    )
    native = json.loads(run("recipe", "list", "--json", "--kind", "native").output)

    assert everything["total"] == len(everything["items"]) >= 22
    assert [item["name"] for item in found["items"]] == ["vaultwarden"]
    assert {item["name"] for item in media["items"]} >= {"jellyfin", "navidrome"}
    assert [item["name"] for item in tagged["items"]] == ["navidrome"]
    assert {item["name"] for item in native["items"]} >= {"wordpress", "n8n"}
    item = found["items"][0]
    assert item["slogan"] and item["category"] == "security" and item["runs_in_docker"] is True
    assert item["version"] and item["updated"] and item["logo"] == "logo.svg"


def test_list_says_when_nothing_matches_and_refuses_a_category_that_does_not_exist() -> None:
    """The closed lists are click choices: the error names them."""
    assert "No recipe matches." in run("recipe", "list", "-q", "zzzz").output
    result = run("recipe", "list", "--category", "Networking")
    assert result.exit_code == 2 and "media" in result.output


def test_show_describes_a_compose_recipe_as_an_operator_decides() -> None:
    """What it runs, what it opens, what to give, what is set for them."""
    result = run("recipe", "show", "syncthing")

    assert result.exit_code == 0, result.output
    output = result.output
    assert "Kind" in output and "compose: runs in Docker" in output
    assert "ghcr.io/syncthing/syncthing:2.1.6" in output and "sha256:4d0281bdcb4e" in output
    assert "Opens 22000/tcp to the network" in output and "Opens 22000/udp to the network" in output
    assert "Asked for at install (--var NAME=VALUE):" in output
    assert "PUID (default: '1000')" in output
    assert "Install it with: noust recipe install syncthing -d <domain>" in output


def test_show_lists_what_noust_sets_without_any_value() -> None:
    """Generated and provisioned variables by name and origin, never a value."""
    output = run("recipe", "show", "vaultwarden").output

    assert "Set by Noust" in output
    assert "ADMIN_TOKEN: generated (password64), secret" in output
    assert "DOMAIN: from the deployment (url)" in output
    assert "required" not in output.split("Asked for at install")[0]


def test_show_marks_required_variables() -> None:
    """The operator sees what install will stop for."""
    output = run("recipe", "show", "freshrss").output

    assert "ADMIN_EMAIL (required)" in output


def test_show_describes_a_native_recipe_with_its_source_and_database() -> None:
    """The 2.3 view, plus the catalogue fields."""
    output = run("recipe", "show", "umami").output

    assert "Version" in output and "2.20.2" in output
    assert "https://github.com/umami-software/umami.git at v2.20.2" in output
    assert "Provisions" in output and "postgres" in output
    assert "DATABASE_URL: from postgres.url, secret" in output


def test_show_of_an_unavailable_recipe_says_why_and_nothing_more() -> None:
    """Listed, not deployable."""
    output = run("recipe", "show", "vault").output

    assert "Available" in output and "No" in output and "sealed" in output
    assert "Install it with" not in output


def test_show_warns_when_an_image_has_no_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """digest_verified: false is visible."""
    import dataclasses

    recipe = dataclasses.replace(get_recipe("memos"), digest_verified=False)
    monkeypatch.setattr("noust.cli.commands.recipe.get_recipe", lambda name: recipe)

    assert "pinned by tag only: no digest was verified" in run("recipe", "show", "memos").output


def test_show_json_is_the_full_description() -> None:
    """Variables, images and ports, for scripts."""
    described = json.loads(run("recipe", "show", "syncthing", "--json").output)

    assert described["kind"] == "compose" and described["images"][0]["digest"].startswith("sha256:")
    assert {entry["name"] for entry in described["variables"]} == {"PUID", "PGID"}
    assert described["public_ports"][0]["port"] == 22000
    assert run("recipe", "show", "drupal").exit_code != 0


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_is_the_deployment_create_with_a_recipe_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same function, called with the recipe, the domain and the variables."""
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("noust.cli.commands.webapp._create_from_recipe", fake)
    values = Path("values.env")

    result = CliRunner().invoke(
        root_cli,
        [
            "recipe",
            "install",
            "freshrss",
            "-d",
            DOMAIN,
            "--var",
            "ADMIN_EMAIL=me@example.com",
            "--var",
            "TZ=Europe/Madrid",
            "--port",
            "8123",
            "--no-ssl",
            "--www",
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["recipe"] == "freshrss" and captured["domain"] == DOMAIN
    assert captured["env_vars"] == {"ADMIN_EMAIL": "me@example.com", "TZ": "Europe/Madrid"}
    assert (captured["port"], captured["ssl"], captured["www"], captured["replace_existing"]) == (
        8123,
        False,
        True,
        True,
    )
    assert (captured["source"], captured["app_type"], captured["branch"]) == (None, "auto", None)
    assert captured["env_file"] is None and values.name == "values.env"


def test_install_passes_a_failure_exit_code_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system that is not ready exits non-zero, as create does."""
    monkeypatch.setattr("noust.cli.commands.webapp._create_from_recipe", lambda **_: 1)

    assert run("recipe", "install", "memos", "-d", DOMAIN).exit_code == 1


def test_install_needs_a_domain_and_validates_variable_syntax() -> None:
    """Usage errors, before anything."""
    assert run("recipe", "install", "memos").exit_code == 2
    result = run("recipe", "install", "memos", "-d", DOMAIN, "--var", "NOEQUALS")
    assert result.exit_code == 2 and "NAME=VALUE" in result.output


def test_install_stops_on_a_missing_required_variable_before_provisioning(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FreshRSS needs an e-mail: the plan says so, and nothing is deployed."""
    monkeypatch.setattr(
        "noust.cli.commands.webapp.check_deployment_ready", lambda **_k: (True, [], [])
    )
    deployed: list[Any] = []
    monkeypatch.setattr(
        "noust.cli.commands.webapp.get_deployer", lambda *a, **k: deployed.append(a) or None
    )

    result = run("recipe", "install", "freshrss", "-d", DOMAIN, "--no-ssl")

    assert result.exit_code != 0
    assert "needs a value for ADMIN_EMAIL" in str(result.exception)
    assert deployed == [] and recipe_store.get_app(DOMAIN) is None


# ---------------------------------------------------------------------------
# outdated and update
# ---------------------------------------------------------------------------


def install(store: NoustStore, name: str, version: str) -> None:
    """An application installed from a recipe at a version."""
    import dataclasses

    store.create_app(App(domain=DOMAIN, app_type="docker-compose", port=8123))
    record_install(DOMAIN, dataclasses.replace(get_recipe(name), version=version), store=store)


def test_outdated_lists_the_applications_the_catalogue_is_ahead_of(
    recipe_store: NoustStore,
) -> None:
    """Domain, recipe, installed version to catalogue version."""
    install(recipe_store, "vaultwarden", "1.0.0")

    result = run("recipe", "outdated")

    assert result.exit_code == 0, result.output
    assert f"{DOMAIN}" in result.output
    assert "vaultwarden 1.0.0 -> 1.37.4" in result.output
    assert "Update one with: noust recipe update DOMAIN" in result.output
    as_json = json.loads(run("recipe", "outdated", "--json").output)
    assert as_json["items"][0]["update_available"] is True
    assert as_json["items"][0]["installed_version"] == "1.0.0"


def test_outdated_with_nothing_behind_says_everything_is_current(recipe_store: NoustStore) -> None:
    """And --all shows them anyway."""
    install(recipe_store, "memos", get_recipe("memos").version)

    assert "up to date" in run("recipe", "outdated").output
    listed = run("recipe", "outdated", "--all")
    assert "memos 0.31.0 (up to date)" in listed.output


def test_update_runs_the_recipe_update_and_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one function, with the variables and force."""
    seen: dict[str, Any] = {}

    def fake(domain: str, **kwargs: Any) -> RecipeUpdate:
        seen.update(domain=domain, **kwargs)
        kwargs["on_phase"](1, 2, "Backing up")
        kwargs["on_step"]("Building")
        return RecipeUpdate(
            domain=domain,
            recipe="memos",
            from_version="0.30.0",
            to_version="0.31.0",
            updated=True,
            added_variables=("NEW_KEY",),
            warnings="a hook warned",
        )

    monkeypatch.setattr("noust.recipes.updates.update_recipe", fake)

    result = run("recipe", "update", DOMAIN, "--var", "LICENSE=abc", "--force")

    assert result.exit_code == 0, result.output
    assert seen["overrides"] == {"LICENSE": "abc"} and seen["force"] is True
    assert f"{DOMAIN} now runs memos 0.31.0." in result.output
    assert "Variables added: NEW_KEY" in result.output and "a hook warned" in result.output


def test_update_of_a_current_application_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to do is not an error."""
    monkeypatch.setattr(
        "noust.recipes.updates.update_recipe",
        lambda domain, **_: RecipeUpdate(
            domain=domain, recipe="memos", from_version="0.31.0", to_version="0.31.0", updated=False
        ),
    )

    result = run("recipe", "update", DOMAIN)

    assert result.exit_code == 0 and f"{DOMAIN} already runs memos 0.31.0." in result.output


# ---------------------------------------------------------------------------
# What is audited
# ---------------------------------------------------------------------------


def test_browsing_is_read_only_and_installing_and_updating_are_not() -> None:
    """The CLI audits what changes something and nothing else."""
    import click

    group = root_cli.get_command(click.Context(root_cli), "recipe")
    assert isinstance(group, click.Group)

    def read_only(name: str) -> bool:
        command = group.commands[name]
        return is_read_only(command, f"recipe {name}", {})

    assert [read_only(name) for name in ("list", "show", "outdated")] == [True, True, True]
    assert [read_only(name) for name in ("install", "update")] == [False, False]
