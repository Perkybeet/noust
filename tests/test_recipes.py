# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for recipes: the shipped files, the strict loader, the plan, and the three front doors.

Nothing reaches the network or a database: provisioning is replaced where the
plan looks it up, and deployers are replaced where the CLI and the job look
them up.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wasm.cli.app import cli as root_cli
from wasm.core.exceptions import DeploymentError, ValidationError
from wasm.core.logger import Logger
from wasm.core.runner import FakeRunner
from wasm.core.store import App, Database, WASMStore
from wasm.deployers.helpers.databases import DatabaseCredentials
from wasm.managers.source_manager import SourceManager, split_archive_checksum
from wasm.recipes import (
    RecipeError,
    RecipeNotFoundError,
    get_recipe,
    list_recipes,
    parse_recipe,
    read_asset,
)
from wasm.recipes import deploy as deploy_module
from wasm.recipes.deploy import finish_recipe, plan_recipe, recipe_source_dir, refuse_conflicts
from wasm.recipes.render import render_value, secret
from wasm.validators.source import validate_source
from wasm.web.auth import CSRF_HEADER_NAME, SecurityConfig
from wasm.web.server import create_app, get_token_manager

DOMAIN = "site.example.com"
PASSWORD = "Pass" + "word123abc"

SHIPPED = {"wordpress", "uptime-kuma", "umami", "n8n", "ghost", "plausible"}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
    """A store of this test's own, installed as the process-wide one."""
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    WASMStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        WASMStore.reset_instance()


@pytest.fixture
def provisioned(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace database provisioning with a record of what was asked."""
    calls: list[dict[str, Any]] = []

    def provision(engine: str, **kwargs: Any) -> DatabaseCredentials:
        calls.append({"engine": engine, **kwargs})
        canonical = "mysql" if engine in ("mysql", "mariadb") else engine
        return DatabaseCredentials(
            engine=canonical,
            name=kwargs["name"],
            user=kwargs["user"],
            password=PASSWORD,
            host="localhost",
            port=3306 if canonical == "mysql" else 5432,
        )

    monkeypatch.setattr(deploy_module, "provision_database", provision)
    monkeypatch.setattr(
        deploy_module,
        "database_identifiers",
        lambda app_name, engine: (app_name.replace("-", "_") + "_db", "u_" + app_name[:8]),
    )
    return calls


# ---------------------------------------------------------------------------
# The shipped recipes
# ---------------------------------------------------------------------------


def test_every_shipped_recipe_loads() -> None:
    """A typo in a recipe file fails here, not on an operator's server."""
    recipes = {recipe.name: recipe for recipe in list_recipes()}

    assert set(recipes) == SHIPPED
    available = [name for name, recipe in recipes.items() if recipe.available]
    assert sorted(available) == ["n8n", "umami", "uptime-kuma", "wordpress"]
    assert [recipe.available for recipe in list_recipes()][:4] == [True] * 4


@pytest.mark.parametrize("name", ["ghost", "plausible"])
def test_the_recipes_left_out_of_2_3_say_why(name: str) -> None:
    """Listed, not deployable, with the reason."""
    recipe = get_recipe(name)

    assert recipe.available is False
    assert recipe.unavailable_reason and "Not available in 2.3" in recipe.unavailable_reason
    assert recipe.summary()["app_type"] is None


def test_ghost_names_mysql_8_and_plausible_names_clickhouse() -> None:
    """The reasons are the real ones."""
    assert "MySQL 8" in (get_recipe("ghost").unavailable_reason or "")
    assert "ClickHouse" in (get_recipe("plausible").unavailable_reason or "")


def test_wordpress_is_verified_against_the_published_sha1() -> None:
    """The archive's checksum travels in the source, where every download verifies it."""
    recipe = get_recipe("wordpress")
    source = recipe.source
    assert source is not None

    url, expected = split_archive_checksum(source.deploy_source())

    assert url == "https://wordpress.org/latest.tar.gz"
    assert expected is not None and expected.algorithm == "sha1"
    assert expected.url == "https://wordpress.org/latest.tar.gz.sha1"
    assert validate_source(source.deploy_source())[0] == "archive"


def test_wordpress_keeps_wp_content_and_reads_its_config_from_the_environment() -> None:
    """The salts are generated, wp-config.php reads getenv(), wp-content is shared."""
    recipe = get_recipe("wordpress")

    assert recipe.app_type == "php-fpm"
    assert recipe.database_engine == "mysql"
    assert recipe.php["shared_from_release"] == ["wp-content"]
    assert "wp-config.php" in recipe.php["deny"]
    salts = [name for name in recipe.env if name.endswith(("_KEY", "_SALT"))]
    assert len(salts) == 8
    config = read_asset("wordpress", "wp-config.php")
    for name in ("WORDPRESS_DB_PASSWORD", "WORDPRESS_NONCE_SALT"):
        assert f"wasm_env( '{name}' )" in config
    assert "WP_CONTENT_DIR" in config


def test_git_recipes_pin_a_tag() -> None:
    """A release tag, never a moving branch."""
    kuma = get_recipe("uptime-kuma").source
    umami = get_recipe("umami").source
    assert kuma is not None and umami is not None
    assert re.match(r"^1\.23\.\d+$", kuma.ref or "")
    assert re.match(r"^v2\.\d+\.\d+$", umami.ref or "")


def test_n8n_is_a_pinned_package_rendered_from_the_recipe() -> None:
    """n8n is not built from its repository: a package.json names the version."""
    recipe = get_recipe("n8n")
    assert recipe.source is not None and recipe.source.kind == "template"

    package = json.loads(read_asset("n8n", "package.json"))

    assert re.match(r"^\d+\.\d+\.\d+$", package["dependencies"]["n8n"])
    assert package["scripts"]["start"] == "n8n start"


# ---------------------------------------------------------------------------
# The strict loader
# ---------------------------------------------------------------------------


def valid(**overrides: Any) -> dict[str, Any]:
    """A minimal valid recipe named ``demo``."""
    data: dict[str, Any] = {
        "name": "demo",
        "title": "Demo",
        "description": "A demo.",
        "homepage": "https://example.com",
        "app_type": "nodejs",
        "source": {"git": "https://github.com/o/r.git", "ref": "v1.0.0"},
    }
    data.update(overrides)
    return data


def test_a_minimal_recipe_is_valid() -> None:
    """Defaults: releases, no database, no variables."""
    recipe = parse_recipe("demo", valid())

    assert (recipe.layout, recipe.database_engine, recipe.env) == ("releases", None, {})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"colour": "blue"}, "unknown keys: colour"),
        ({"name": "other"}, "name must be the file's name"),
        ({"app_type": "cobol"}, "app_type 'cobol'"),
        ({"source": {"git": "https://github.com/o/r"}}, "fixed ref"),
        ({"source": {"git": "http://github.com/o/r", "ref": "v1"}}, "https"),
        ({"source": {"archive": "https://h/a.tar.gz"}}, "exactly one of sha256"),
        (
            {
                "source": {
                    "archive": "https://h/a.tar.gz",
                    "sha256": "ab" * 32,
                    "checksum_url": "https://h/a.sha1",
                }
            },
            "exactly one of sha256",
        ),
        ({"source": {"archive": "https://h/a.tar.gz", "sha256": "xyz"}}, "64 hexadecimal"),
        ({"source": {"archive": "https://h/a.tar.gz", "checksum_url": "https://h/a.md5"}}, ".sha1"),
        ({"source": {"template": {"package.json": "missing.json"}}}, "not in assets/demo/"),
        (
            {"source": {"git": "https://g/r", "ref": "v1", "archive": "https://h/a.tgz"}},
            "exactly one",
        ),
        ({"env": {"1BAD": "x"}}, "not a valid variable name"),
        ({"env": {"PORT_NUMBER": 3000}}, "must be a string"),
        ({"env": {"DB": "{{ database.url }}"}}, "does not ask for"),
        ({"env": {"X": "{{ unclosed"}}, "not a valid template"),
        ({"database": {"engine": "oracle"}}, "database.engine"),
        ({"port": 80}, "1024 to 65535"),
        ({"layout": "sideways"}, "layout must be one of"),
        ({"php": {"webroot": "."}}, "php settings are for app_type php-fpm"),
        ({"health": {"path": "healthz"}}, "health.path must start with /"),
        ({"available": "yes"}, "available must be true or false"),
        ({"available": False}, "unavailable recipe has no"),
        ({"unavailable_reason": "x"}, "only for an unavailable recipe"),
        ({"homepage": "http://example.com"}, "homepage must be an https"),
    ],
)
def test_the_loader_refuses_what_it_does_not_understand(
    overrides: dict[str, Any], message: str
) -> None:
    """Every refusal names the problem and the file."""
    with pytest.raises(RecipeError, match=re.escape(message)) as failure:
        parse_recipe("demo", valid(**overrides))

    assert "src/wasm/recipes/demo.yaml" in failure.value.details


def test_an_unknown_recipe_lists_the_known_ones() -> None:
    """With the command that lists them."""
    with pytest.raises(RecipeNotFoundError) as failure:
        get_recipe("drupal")

    assert "wordpress" in failure.value.details
    assert "wasm recipe list" in failure.value.details


def test_names_that_are_not_file_names_are_not_looked_up() -> None:
    """No path is ever built from what a client sends."""
    with pytest.raises(RecipeNotFoundError):
        get_recipe("../wordpress")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_secrets_are_letters_and_digits_and_never_repeat() -> None:
    """Safe in a .env, a URL and a pool file."""
    first, second = secret(64), secret(64)

    assert re.match(r"^[A-Za-z0-9]{64}$", first)
    assert first != second
    with pytest.raises(ValidationError):
        secret(8)


def test_an_unknown_name_in_a_value_is_an_error() -> None:
    """StrictUndefined: never an empty string in a variable."""
    with pytest.raises(ValidationError, match="does not have"):
        render_value("{{ database.url }}", {"domain": DOMAIN}, what="X")


def test_the_sandbox_refuses_python_internals() -> None:
    """A template cannot reach past the values it is given."""
    with pytest.raises(ValidationError):
        render_value("{{ ''.__class__.__mro__ }}", {}, what="X")


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def test_wordpress_plans_a_database_salts_and_php_settings(
    store: WASMStore, provisioned: list[dict[str, Any]]
) -> None:
    """Everything the deployer is configured with."""
    plan = plan_recipe(
        "wordpress",
        DOMAIN,
        port=3007,
        ssl=True,
        env_overrides={"WORDPRESS_DEBUG": "true"},
        logger=Logger(verbose=False),
    )

    assert provisioned[0]["engine"] == "mysql"
    assert provisioned[0]["domain"] == DOMAIN
    env = plan.env_vars
    assert env["WORDPRESS_DB_NAME"] == "site_example_com_db"
    assert env["WORDPRESS_DB_PASSWORD"] == PASSWORD
    assert env["WORDPRESS_DB_HOST"] == "localhost"
    assert env["WORDPRESS_DEBUG"] == "true"
    salts = [env[name] for name in env if name.endswith(("_KEY", "_SALT"))]
    assert len(set(salts)) == 8 and all(len(value) == 64 for value in salts)
    arguments = plan.configure_arguments()
    assert arguments["source"].startswith("https://wordpress.org/latest.tar.gz#checksum=")
    assert arguments["layout"] == "releases"
    assert arguments["php_shared_from_release"] == ["wp-content"]
    assert "wasm_env" in arguments["php_files"]["wp-config.php"]
    assert set(arguments["persistent_paths"]) >= {"wp-content", "wp-config.php"}
    assert arguments["health_expect"] == "200-399"
    assert plan.notes[0].startswith(f"Open https://{DOMAIN}/wp-admin/install.php")


def test_an_override_wins_over_a_generated_value(
    store: WASMStore, provisioned: list[dict[str, Any]]
) -> None:
    """The operator's value, literally: it is not a template."""
    plan = plan_recipe(
        "umami",
        DOMAIN,
        port=3000,
        ssl=False,
        env_overrides={"APP_SECRET": "{{ literal }}"},
        logger=Logger(verbose=False),
    )

    assert plan.env_vars["APP_SECRET"] == "{{ literal }}"
    assert plan.env_vars["DATABASE_URL"] == (
        f"postgresql://u_site-exa:{PASSWORD}@localhost:5432/site_example_com_db"
    )
    assert plan.branch == "v2.20.2"


def test_n8n_renders_its_package_json_beside_the_store(
    store: WASMStore, provisioned: list[dict[str, Any]]
) -> None:
    """A local source every later update copies from, and its variables from the domain."""
    plan = plan_recipe("n8n", DOMAIN, port=5680, ssl=False, logger=Logger(verbose=False))

    directory = recipe_source_dir("site-example-com", store)
    assert plan.source == str(directory)
    assert json.loads((directory / "package.json").read_text())["dependencies"]["n8n"]
    assert plan.env_vars["N8N_PORT"] == "5680"
    assert plan.env_vars["N8N_PROTOCOL"] == "http"
    assert plan.env_vars["N8N_SECURE_COOKIE"] == "false"
    assert plan.env_vars["WEBHOOK_URL"] == f"http://{DOMAIN}/"
    assert plan.env_vars["N8N_USER_FOLDER"].endswith("/site-example-com/shared/data")
    assert provisioned == []
    assert validate_source(plan.source)[0] == "local"


def test_a_recipe_does_not_redeploy_an_existing_application(
    store: WASMStore, provisioned: list[dict[str, Any]]
) -> None:
    """Nothing is provisioned for a domain that is already taken."""
    store.create_app(App(domain=DOMAIN, app_type="nodejs"))

    with pytest.raises(DeploymentError, match="already deployed"):
        plan_recipe("wordpress", DOMAIN, port=None, ssl=True, logger=Logger(verbose=False))

    assert provisioned == []


def test_an_unavailable_recipe_is_refused_with_its_reason(store: WASMStore) -> None:
    """Before anything happens."""
    with pytest.raises(RecipeError, match="not available") as failure:
        plan_recipe("ghost", DOMAIN, port=None, ssl=True, logger=Logger(verbose=False))

    assert "MySQL 8" in failure.value.details


def test_finishing_links_the_database_and_sets_the_health_check(
    store: WASMStore, provisioned: list[dict[str, Any]]
) -> None:
    """The row the deployment created learns about both."""
    plan = plan_recipe("umami", DOMAIN, port=3000, ssl=True, logger=Logger(verbose=False))
    store.create_database(Database(name="site_example_com_db", engine="postgresql"))
    store.create_app(App(domain=DOMAIN, app_type="nodejs"))

    notes = finish_recipe(plan, logger=Logger(verbose=False))

    app = store.get_app(DOMAIN)
    assert app is not None
    assert (app.health_path, app.health_expect) == ("/api/heartbeat", "200")
    assert store.get_database("site_example_com_db", "postgresql").app_id == app.id
    assert notes and "admin" in notes[0]


def test_a_source_or_type_with_a_recipe_is_refused() -> None:
    """The recipe brings both."""
    refuse_conflicts(source=None, app_type="auto")
    with pytest.raises(RecipeError, match="a source and a type"):
        refuse_conflicts(source="https://x/y.git", app_type="nextjs")


# ---------------------------------------------------------------------------
# A tag as the ref of a repository cache
# ---------------------------------------------------------------------------


GIT = ["git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never"]


def test_an_update_of_a_tag_pinned_cache_follows_the_tag(tmp_path: Path) -> None:
    """A tag is not under refs/heads: the cache is put on the tag, detached."""
    cache = tmp_path / "repo"
    (cache / ".git").mkdir(parents=True)
    runner = FakeRunner()
    url = "https://github.com/louislam/uptime-kuma.git"
    runner.script([*GIT, "remote", "get-url", "origin"], stdout=url + "\n")
    runner.script([*GIT, "rev-parse", "--abbrev-ref", "HEAD"], stdout="HEAD\n")
    runner.script([*GIT, "rev-parse", "HEAD"], stdout="b" * 40 + "\n")
    runner.script(
        [*GIT, "fetch", "origin", "+refs/heads/1.23.17:refs/remotes/origin/1.23.17"],
        exit_code=128,
        stderr="no ref",
    )

    commit = SourceManager(runner=runner).sync_cache(url, cache, "1.23.17")

    calls = [call[5:] for call in runner.calls_to("git")]
    assert commit == "b" * 40
    assert ("fetch", "--no-tags", "origin", "+refs/tags/1.23.17:refs/tags/1.23.17") in calls
    assert ("checkout", "--force", "--detach", "refs/tags/1.23.17") in calls
    assert not any(call[:2] == ("reset", "--hard") for call in calls)


def test_a_ref_that_is_neither_branch_nor_tag_reports_the_branch_failure(tmp_path: Path) -> None:
    """The original error, not the fallback's."""
    cache = tmp_path / "repo"
    (cache / ".git").mkdir(parents=True)
    runner = FakeRunner()
    url = "https://github.com/o/r.git"
    runner.script([*GIT, "remote", "get-url", "origin"], stdout=url + "\n")
    runner.script([*GIT, "rev-parse", "--abbrev-ref", "HEAD"], stdout="main\n")
    runner.script([*GIT, "fetch"], exit_code=128, stderr="couldn't find remote ref nope")

    with pytest.raises(Exception, match="Git fetch of nope failed"):
        SourceManager(runner=runner).sync_cache(url, cache, "nope")


# ---------------------------------------------------------------------------
# wasm recipe and wasm create --recipe
# ---------------------------------------------------------------------------


def test_recipe_list_json_lists_every_recipe_with_its_availability() -> None:
    """What the console's gallery and scripts read."""
    result = CliRunner().invoke(root_cli, ["recipe", "list", "--json"])

    assert result.exit_code == 0, result.output
    items = {item["name"]: item for item in json.loads(result.output)["items"]}
    assert set(items) == SHIPPED
    assert items["ghost"]["available"] is False
    assert items["wordpress"]["app_type"] == "php-fpm"


def test_recipe_show_describes_one() -> None:
    """Human and JSON."""
    human = CliRunner().invoke(root_cli, ["recipe", "show", "uptime-kuma"])
    as_json = CliRunner().invoke(root_cli, ["recipe", "show", "uptime-kuma", "--json"])

    assert human.exit_code == 0, human.output
    assert "wasm create --recipe uptime-kuma" in human.output
    described = json.loads(as_json.output)
    assert described["source"]["ref"].startswith("1.23.")
    assert described["persistent_paths"] == ["data"]


def test_create_with_a_recipe_and_a_source_is_refused(store: WASMStore) -> None:
    """Before anything is checked or provisioned."""
    result = CliRunner().invoke(
        root_cli,
        ["create", "--recipe", "wordpress", "-d", DOMAIN, "-s", "https://github.com/o/r.git"],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, RecipeError)
    assert "brings its own source" in str(result.exception)


def test_create_without_source_or_recipe_is_a_usage_error() -> None:
    """--source is required unless --recipe is given."""
    result = CliRunner().invoke(root_cli, ["create", "-d", DOMAIN])

    assert result.exit_code == 2
    assert "--recipe" in result.output


def test_create_with_a_recipe_deploys_the_plan_and_prints_the_notes(
    store: WASMStore,
    provisioned: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary deployment, configured from the plan, then the next steps."""
    configured: dict[str, Any] = {}

    class FakeDeployer:
        """Records how it was configured."""

        def configure(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def deploy(self) -> bool:
            return True

    monkeypatch.setattr("wasm.cli.commands.webapp.get_deployer", lambda *a, **k: FakeDeployer())
    monkeypatch.setattr(
        "wasm.cli.commands.webapp.check_deployment_ready", lambda **_k: (True, [], [])
    )

    result = CliRunner().invoke(
        root_cli,
        ["create", "--recipe", "uptime-kuma", "-d", DOMAIN, "--no-ssl", "--env", "TZ=UTC"],
    )

    assert result.exit_code == 0, result.output
    assert configured["source"] == "https://github.com/louislam/uptime-kuma.git"
    assert configured["branch"].startswith("1.23.")
    assert configured["env_vars"] == {"UPTIME_KUMA_HOST": "127.0.0.1", "TZ": "UTC"}
    assert configured["persistent_paths"] == ["data"]
    assert configured["layout"] == "releases"
    assert "Next steps" in result.output
    assert f"http://{DOMAIN}" in result.output


# ---------------------------------------------------------------------------
# The API and the job
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path, store: WASMStore, runner: object) -> FastAPI:
    """The application, over this test's store."""
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """A signed-in client carrying the CSRF header."""
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the deployment instead of queueing a real job."""
    captured: list[dict[str, Any]] = []

    class Queued:
        """A job that was accepted but never run."""

        id = "job-1"
        status = type("Status", (), {"value": "pending"})()

        def to_dict(self) -> dict[str, Any]:
            return {"id": self.id}

    def create_job(**kwargs: Any) -> Queued:
        captured.append(kwargs)
        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("wasm.web.api.apps.get_job_manager", lambda: manager)
    return captured


def test_the_api_lists_and_describes_recipes(client: TestClient) -> None:
    """GET /api/recipes and /api/recipes/{name}, 404 for an unknown one."""
    listed = client.get("/api/recipes")
    one = client.get("/api/recipes/wordpress")
    missing = client.get("/api/recipes/drupal")

    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()["items"]]
    assert set(names) == SHIPPED
    assert one.json()["source"]["checksum_url"].endswith(".sha1")
    assert {"name": "WORDPRESS_AUTH_KEY", "generated": True} in one.json()["env"]
    assert missing.status_code == 404


def test_post_apps_with_a_recipe_queues_it_with_the_recipe_type(
    client: TestClient, queued: list[dict[str, Any]]
) -> None:
    """No source needed; the type and the preferred port come from the recipe."""
    response = client.post(
        "/api/apps", json={"domain": DOMAIN, "recipe": "uptime-kuma", "ssl": False}
    )

    assert response.status_code == 202, response.text
    kwargs = queued[0]["kwargs"]
    assert (kwargs["recipe"], kwargs["app_type"]) == ("uptime-kuma", "nodejs")
    assert queued[0]["metadata"]["recipe"] == "uptime-kuma"


@pytest.mark.parametrize(
    "body",
    [
        {"recipe": "wordpress", "source": "https://github.com/o/r.git"},
        {"recipe": "wordpress", "app_type": "nextjs"},
        {"recipe": "ghost"},
        {"recipe": "drupal"},
        {},
    ],
    ids=["source", "type", "unavailable", "unknown", "neither"],
)
def test_post_apps_refuses_recipe_conflicts(
    client: TestClient, queued: list[dict[str, Any]], body: dict[str, Any]
) -> None:
    """400, nothing queued."""
    response = client.post("/api/apps", json={"domain": DOMAIN, **body})

    assert response.status_code == 400, response.text
    assert queued == []


def test_the_job_deploys_the_plan_and_returns_the_notes(
    store: WASMStore,
    provisioned: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same plan as the CLI; the notes are in the job's result."""
    from wasm.web.jobs import Job, JobContext, JobType, deploy_app_job

    configured: dict[str, Any] = {}

    class FakeDeployer:
        """Records how it was configured and registers the row a deploy would."""

        last_deployment_id = 7

        def configure(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def deploy(self) -> bool:
            store.create_app(App(domain=DOMAIN, app_type="php-fpm"))
            return True

    monkeypatch.setattr("wasm.deployers.get_deployer", lambda *a, **k: FakeDeployer())
    job = Job(id="job-r", type=JobType.DEPLOY, name="deploy", description="")

    result = deploy_app_job(
        DOMAIN,
        "",
        "auto",
        port=3005,
        ssl=True,
        env_vars={"WORDPRESS_DEBUG": "true"},
        recipe="wordpress",
        job_context=JobContext(job, lambda _job: None),
    )

    assert result["recipe"] == "wordpress"
    assert result["app_type"] == "php-fpm"
    assert result["notes"][0].startswith(f"Open https://{DOMAIN}/wp-admin/install.php")
    assert configured["source"].startswith("https://wordpress.org/latest.tar.gz#checksum=")
    assert configured["env_vars"]["WORDPRESS_DEBUG"] == "true"
    assert configured["php_shared_from_release"] == ["wp-content"]


def test_every_shipped_recipe_file_is_plain_yaml() -> None:
    """No tags, no anchors that could smuggle in Python objects."""
    directory = Path(__file__).resolve().parents[1] / "src" / "wasm" / "recipes"
    for path in directory.glob("*.yaml"):
        assert isinstance(yaml.safe_load(path.read_text()), dict), path.name
