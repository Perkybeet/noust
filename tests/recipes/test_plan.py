# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Planning a recipe: what is provisioned, written and handed to the deployer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers.helpers.databases import DatabaseCredentials
from noust.recipes import RecipeError, get_recipe, parse_recipe
from noust.recipes import deploy as deploy_module
from noust.recipes.deploy import (
    COMPOSE_FILE,
    finish_recipe,
    plan_recipe,
    recipe_source_dir,
)
from noust.recipes.installed import get_installed
from tests.recipes.factory import (
    compose_document,
    compose_recipe,
    native_recipe,
    use_demo_assets,
)

DOMAIN = "site.example.com"
APP = "site-example-com"
LOG = Logger(verbose=False)


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the assets of a shipped recipe."""
    use_demo_assets(monkeypatch)


@pytest.fixture
def provisioned(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace SQL provisioning with a record of what was asked."""
    calls: list[dict[str, Any]] = []

    def provision(engine: str, **kwargs: Any) -> DatabaseCredentials:
        calls.append({"engine": engine, **kwargs})
        canonical = "mysql" if engine in ("mysql", "mariadb") else engine
        return DatabaseCredentials(
            engine=canonical,
            name=kwargs["name"],
            user=kwargs["user"],
            password="Pass" + "word123abc",
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


def test_a_recipe_whose_first_deploy_fails_says_its_database_was_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recipe provisions before it deploys; the error says what is left and how to drop it.

    The real-machine harness found a failed Umami install leave its PostgreSQL database and
    account behind without a word, while a first deploy with a database of its own names the
    commands that drop them.
    """
    from noust.core.exceptions import DeploymentError
    from noust.managers.database import service as database_service

    kept: list[Any] = []

    class Service:
        def __init__(self, **_: Any) -> None: ...

        def keep_after_failed_deploy(self, prepared: Any, error: Any) -> None:
            kept.append(prepared)
            error.details = f"{error.details}\nkept {prepared.database}"

    monkeypatch.setattr(database_service, "DatabaseService", Service)
    plan = deploy_module.RecipePlan(
        recipe=parse_recipe("demo", native_recipe()),
        domain=DOMAIN,
        app_type="nodejs",
        source="https://example.com/app.git",
        branch=None,
        port=3000,
        env_vars={},
        layout="releases",
        persistent_paths=[],
        database=DatabaseCredentials(
            engine="postgresql", name="site_db", user="site_user", password="x", port=5432
        ),
    )
    error = DeploymentError("Build failed", details="pnpm exited 1")

    deploy_module.keep_after_failed_deploy(plan, error, logger=LOG)

    [prepared] = kept
    assert (prepared.domain, prepared.engine, prepared.database, prepared.username) == (
        DOMAIN,
        "postgresql",
        "site_db",
        "site_user",
    )
    assert error.details == "pnpm exited 1\nkept site_db"


def use(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> None:
    """Make ``demo`` a recipe of the catalogue for the plan."""
    recipe = parse_recipe("demo", data)
    monkeypatch.setattr(deploy_module, "get_recipe", lambda name: recipe)


# ---------------------------------------------------------------------------
# A Compose recipe
# ---------------------------------------------------------------------------


def test_a_compose_recipe_plans_its_document_its_port_and_its_variables(
    recipe_store: NoustStore,
) -> None:
    """Vaultwarden, as shipped: the stack is written, the variables resolved, the port given."""
    plan = plan_recipe("vaultwarden", DOMAIN, port=8123, ssl=True, logger=LOG)

    recipe = get_recipe("vaultwarden")
    directory = recipe_source_dir(APP, recipe_store)
    assert plan.app_type == "docker-compose" and plan.layout == "inplace"
    assert plan.source == str(directory) and plan.branch is None
    assert (directory / COMPOSE_FILE).read_text() == (recipe.compose or "").rstrip("\n") + "\n"
    assert sorted(path.name for path in directory.iterdir()) == [COMPOSE_FILE, "noust.nginx.yaml"]
    assert (directory / "noust.nginx.yaml").read_text().startswith("# How nginx proxies")
    env = plan.env_vars
    assert env["PORT"] == "8123"
    assert env["DOMAIN"] == f"https://{DOMAIN}"
    assert env["SIGNUPS_ALLOWED"] == "true"
    assert len(env["ADMIN_TOKEN"]) == 64
    assert plan.secret_names == {"ADMIN_TOKEN"}
    assert plan.configure_arguments()["initial_health"] == ("/alive", "200", None)
    assert plan.notes[0].startswith(f"Open https://{DOMAIN} and create your account")
    assert "ADMIN_TOKEN" in plan.notes[2] and env["ADMIN_TOKEN"] not in " ".join(plan.notes)


def test_the_stack_directory_never_holds_the_secrets(recipe_store: NoustStore) -> None:
    """The .env is written into the application by the deployment, not into the source."""
    plan_recipe("vaultwarden", DOMAIN, port=8123, ssl=True, logger=LOG)

    directory = recipe_source_dir(APP, recipe_store)
    assert not (directory / ".env").exists()
    assert all(
        "ADMIN_TOKEN=" not in path.read_text() for path in directory.rglob("*") if path.is_file()
    )


def test_a_compose_recipe_needs_a_port_to_publish_on(recipe_store: NoustStore) -> None:
    """The document publishes 127.0.0.1:${PORT}."""
    with pytest.raises(RecipeError, match="needs a port"):
        plan_recipe("vaultwarden", DOMAIN, port=None, ssl=True, logger=LOG)


def test_the_operators_value_wins_and_adds_what_the_recipe_does_not_name(
    recipe_store: NoustStore,
) -> None:
    """--var SIGNUPS_ALLOWED=false, and a variable of the operator's own."""
    plan = plan_recipe(
        "vaultwarden",
        DOMAIN,
        port=8123,
        ssl=False,
        env_overrides={"SIGNUPS_ALLOWED": "false", "SMTP_HOST": "mail.example.com"},
        logger=LOG,
    )

    assert plan.env_vars["SIGNUPS_ALLOWED"] == "false"
    assert plan.env_vars["SMTP_HOST"] == "mail.example.com"
    assert plan.env_vars["DOMAIN"] == f"http://{DOMAIN}"


def test_a_replan_replaces_what_the_directory_held(recipe_store: NoustStore) -> None:
    """An update writes the new document over the old one, and leaves nothing of it."""
    directory = recipe_source_dir(APP, recipe_store)
    directory.mkdir(parents=True)
    (directory / "stale.conf").write_text("from an older version")

    plan_recipe("vaultwarden", DOMAIN, port=8123, ssl=True, logger=LOG)

    assert not (directory / "stale.conf").exists()


def test_files_a_compose_recipe_ships_are_written_beside_the_document(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verbatim, from the assets, at the paths the document mounts."""
    text = compose_document().replace(
        "- data:/data", "- data:/data\n      - ./conf/mark.svg:/m.svg:ro"
    )
    use(monkeypatch, compose_recipe(compose=text, files={"conf/mark.svg": "logo.svg"}))

    plan_recipe("demo", DOMAIN, port=8123, ssl=True, logger=LOG)

    directory = recipe_source_dir(APP, recipe_store)
    assert (directory / "conf" / "mark.svg").read_text().startswith("<svg")


# ---------------------------------------------------------------------------
# Required variables: ${VAR:?required}
# ---------------------------------------------------------------------------


def test_a_required_variable_stops_the_install_before_anything_exists(
    recipe_store: NoustStore, provisioned: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No database, no directory, no secret generated until the operator has answered."""
    use(
        monkeypatch,
        native_recipe(
            requires=["mysql"],
            variables={
                "LICENSE": {"required": True, "description": "Your licence key"},
                "DATABASE_URL": {"from": "mysql.url"},
            },
        ),
    )

    with pytest.raises(RecipeError, match="needs a value for LICENSE") as failure:
        plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG)

    assert "Your licence key" in failure.value.details
    assert provisioned == []
    assert not recipe_source_dir(APP, recipe_store).exists()

    plan = plan_recipe(
        "demo", DOMAIN, port=3000, ssl=True, env_overrides={"LICENSE": "k-123"}, logger=LOG
    )
    assert plan.env_vars["LICENSE"] == "k-123" and len(provisioned) == 1


def test_a_demanded_variable_in_the_document_is_required_too(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """${LICENSE:?Your key} in the Compose file is the same demand."""
    text = compose_document().replace(
        'SECRET: "${SECRET}"', 'SECRET: "${SECRET}"\n      L: "${LICENSE:?Your key}"'
    )
    use(monkeypatch, compose_recipe(compose=text))

    with pytest.raises(RecipeError, match="needs a value for LICENSE"):
        plan_recipe("demo", DOMAIN, port=8123, ssl=True, logger=LOG)

    plan = plan_recipe(
        "demo", DOMAIN, port=8123, ssl=True, env_overrides={"LICENSE": "x"}, logger=LOG
    )
    assert plan.env_vars["LICENSE"] == "x"


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


def test_a_native_recipe_gets_its_sql_database_and_its_variables_from_it(
    recipe_store: NoustStore, provisioned: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """requires: [postgres] through the existing provisioning."""
    use(
        monkeypatch,
        native_recipe(
            requires=["postgres"],
            variables={
                "DATABASE_URL": {"from": "postgres.url"},
                "DB_HOST": {"from": "postgres.host"},
                "DB_NAME": {"from": "postgres.name"},
                "DB_USER": {"from": "postgres.user"},
                "DB_PASSWORD": {"from": "postgres.password"},
            },
        ),
    )

    plan = plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG)

    assert provisioned[0]["engine"] == "postgresql" and provisioned[0]["domain"] == DOMAIN
    assert plan.database is not None and plan.database.name == "site_example_com_db"
    password = "Pass" + "word123abc"
    assert plan.env_vars["DATABASE_URL"] == (
        f"postgresql://u_site-exa:{password}@localhost:5432/site_example_com_db"
    )
    assert plan.env_vars["DB_PASSWORD"] == password
    assert plan.secret_names == {"DATABASE_URL", "DB_PASSWORD"}


class FakeDatabaseService:
    """The database service, answering for Redis and recording the link."""

    prepared: list[dict[str, Any]] = []
    linked: list[Any] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def prepare_for_new_app(self, domain: str, engine: str, **kwargs: Any) -> Any:
        record = {"domain": domain, "engine": engine, **kwargs}
        self.prepared.append(record)

        class Prepared:
            """What the service hands back."""

            env_var = kwargs["env_var"]
            values = {kwargs["env_var"]: "redis://:s3cr3t%2Fx@127.0.0.1:6379/3"}

        return Prepared()

    def link_new_app(self, prepared: Any) -> None:
        self.linked.append(prepared)


def test_redis_valkey_and_mongodb_go_through_the_database_service(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service provisions them; the recipe reads the connection string it answers."""
    FakeDatabaseService.prepared, FakeDatabaseService.linked = [], []
    monkeypatch.setattr("noust.managers.database.service.DatabaseService", FakeDatabaseService)
    use(
        monkeypatch,
        native_recipe(
            requires=["redis"],
            variables={
                "REDIS_URL": {"from": "redis.url"},
                "REDIS_HOST": {"from": "redis.host"},
                "REDIS_PORT": {"from": "redis.port"},
                "REDIS_PASSWORD": {"from": "redis.password"},
            },
        ),
    )

    plan = plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG)

    assert FakeDatabaseService.prepared[0]["engine"] == "redis"
    assert FakeDatabaseService.prepared[0]["env_var"] == "REDIS_URL"
    assert plan.env_vars["REDIS_URL"] == "redis://:s3cr3t%2Fx@127.0.0.1:6379/3"
    assert (plan.env_vars["REDIS_HOST"], plan.env_vars["REDIS_PORT"]) == ("127.0.0.1", "6379")
    assert plan.env_vars["REDIS_PASSWORD"] == "s3cr3t/x"
    assert len(plan.keyed) == 1


def test_s3_is_not_provisioned_it_is_asked_for(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Noust manages no object store: the operator names one, and install stops without it."""
    use(
        monkeypatch,
        native_recipe(
            requires=["s3"],
            variables={
                "S3_ENDPOINT": {"from": "s3.endpoint"},
                "S3_BUCKET": {"from": "s3.bucket"},
                "S3_ACCESS_KEY": {"from": "s3.access_key"},
                "S3_SECRET_KEY": {"from": "s3.secret_key"},
            },
        ),
    )

    with pytest.raises(RecipeError, match="S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY"):
        plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG)

    given = {
        "S3_ENDPOINT": "https://s3.example.com",
        "S3_BUCKET": "assets",
        "S3_ACCESS_KEY": "AK",
        "S3_SECRET_KEY": "SK",
    }
    plan = plan_recipe("demo", DOMAIN, port=3000, ssl=True, env_overrides=given, logger=LOG)
    assert plan.env_vars == given
    assert plan.secret_names == {"S3_ACCESS_KEY", "S3_SECRET_KEY"}


# ---------------------------------------------------------------------------
# The native build block, and the 2.3 format
# ---------------------------------------------------------------------------


def test_the_build_block_travels_with_the_plan(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commands of a native recipe, for the deployer that can run them."""
    use(monkeypatch, native_recipe(build={"install": "npm ci", "start": "node server.js"}))

    plan = plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG)

    assert plan.build is not None and plan.build.start == "node server.js"
    assert plan.configure_arguments()["build_block"] == {
        "install": "npm ci",
        "start": "node server.js",
    }
    use(monkeypatch, native_recipe())
    assert (
        "build_block"
        not in plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG).configure_arguments()
    )


def test_a_native_template_source_is_written_verbatim_in_the_current_format(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version 2 files are copied as they are; version 1 files were Jinja templates."""
    use(monkeypatch, native_recipe(source={"template": {"mark.svg": "logo.svg"}}))

    plan = plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG)

    assert (Path(plan.source) / "mark.svg").read_text().startswith("<svg")


def test_a_recipe_in_the_2_3_format_still_plans_with_its_jinja_values(
    recipe_store: NoustStore, provisioned: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version 1: env templates, one database, the same notes."""
    legacy = {
        "name": "demo",
        "title": "Demo",
        "description": "A demo.",
        "homepage": "https://example.com",
        "requires": ["Node.js 20"],
        "app_type": "nodejs",
        "source": {"git": "https://github.com/o/r.git", "ref": "v1.0.0"},
        "database": {"engine": "postgresql"},
        "env": {
            "DATABASE_URL": "{{ database.url }}",
            "SITE": "{{ url }}/{{ app_name }}",
            "KEY": "{{ secret(48) }}",
        },
        "notes": ["Open {{ url }}."],
    }
    use(monkeypatch, legacy)

    plan = plan_recipe("demo", DOMAIN, port=3000, ssl=False, logger=LOG)

    assert plan.recipe.schema == 1 and plan.recipe.needs == ("Node.js 20",)
    assert plan.env_vars["SITE"] == f"http://{DOMAIN}/{APP}"
    assert plan.env_vars["DATABASE_URL"].startswith("postgresql://u_site-exa:")
    assert len(plan.env_vars["KEY"]) == 48
    assert plan.notes == (f"Open http://{DOMAIN}.",)
    assert plan.secret_names == frozenset()


def test_a_hub_installs_nothing_and_provisions_nothing(
    recipe_store: NoustStore, provisioned: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A central that is only a hub deploys no applications: refused before a database exists."""
    from noust.core.exceptions import NoustError

    monkeypatch.setattr("noust.central.is_hub", lambda config=None: True)

    with pytest.raises(NoustError, match="not available on this central"):
        plan_recipe("wordpress", DOMAIN, port=3000, ssl=True, logger=LOG)

    assert provisioned == []
    assert not recipe_source_dir(APP, recipe_store).exists()


def test_a_dollar_in_a_value_for_a_compose_stack_is_refused_and_a_native_one_takes_it(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose reads a dollar sign in a stack's .env as a variable; a native .env is read as it is."""
    with pytest.raises(RecipeError, match=r"reads a \$ in SMTP_PASSWORD") as failure:
        plan_recipe(
            "vaultwarden",
            DOMAIN,
            port=8123,
            ssl=True,
            env_overrides={"SMTP_PASSWORD": "pa$$word", "OTHER": "fine"},
            logger=LOG,
        )

    assert failure.value.field == "variables"
    assert not recipe_source_dir(APP, recipe_store).exists()
    use(monkeypatch, native_recipe())
    plan = plan_recipe(
        "demo", DOMAIN, port=3000, ssl=True, env_overrides={"SMTP_PASSWORD": "pa$$word"}, logger=LOG
    )
    assert plan.env_vars["SMTP_PASSWORD"] == "pa$$word"


# ---------------------------------------------------------------------------
# Finishing
# ---------------------------------------------------------------------------


def test_finishing_records_the_recipe_and_marks_the_secrets(recipe_store: NoustStore) -> None:
    """Which recipe, which version, and what is secret: after the application exists."""
    plan = plan_recipe("vaultwarden", DOMAIN, port=8123, ssl=True, logger=LOG)
    assert get_installed(DOMAIN, store=recipe_store) is None
    recipe_store.create_app(App(domain=DOMAIN, app_type="docker-compose"))

    notes = finish_recipe(plan, logger=LOG, default_backups=False)

    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None
    assert (installed.recipe, installed.kind, installed.version) == (
        "vaultwarden",
        "compose",
        get_recipe("vaultwarden").version,
    )
    app = recipe_store.get_app(DOMAIN)
    assert app is not None and app.env_secret_marks == {"ADMIN_TOKEN": True}
    assert notes == list(plan.notes)


def test_finishing_without_an_application_records_nothing(recipe_store: NoustStore) -> None:
    """A deployment that failed leaves no row, and so no record."""
    plan = plan_recipe("vaultwarden", DOMAIN, port=8123, ssl=True, logger=LOG)

    assert finish_recipe(plan, logger=LOG, default_backups=False) == list(plan.notes)
    assert get_installed(DOMAIN, store=recipe_store) is None


def test_finishing_links_a_keyed_database_through_the_service(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link the service needs once the application row exists."""
    FakeDatabaseService.prepared, FakeDatabaseService.linked = [], []
    monkeypatch.setattr("noust.managers.database.service.DatabaseService", FakeDatabaseService)
    use(
        monkeypatch,
        native_recipe(requires=["redis"], variables={"REDIS_URL": {"from": "redis.url"}}),
    )
    plan = plan_recipe("demo", DOMAIN, port=3000, ssl=True, logger=LOG)
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs"))

    finish_recipe(plan, logger=LOG, default_backups=False)

    assert len(FakeDatabaseService.linked) == 1
