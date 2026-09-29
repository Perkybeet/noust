# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A recipe's health check reaches the first deployment's gate, for every type.

The plan used to hand ``health_path`` and ``health_expect`` to ``configure``
as loose options, which only the PHP deployer read: every other type swallowed
them, so its first gate probed ``/`` accepting anything below 500, and only
the gates after it used the recipe's check. The plan now says
``initial_health``, which ``BaseDeployer`` records on the new row before the
first gate, and both callers (``wasm create --recipe`` and the console's job)
hand it over unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers.helpers.databases import DatabaseCredentials
from noust.recipes import deploy as deploy_module
from noust.recipes.deploy import plan_recipe

DOMAIN = "site.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own, installed as the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provisioning answers made-up credentials."""

    def provision(engine: str, **kwargs: Any) -> DatabaseCredentials:
        return DatabaseCredentials(
            engine="mysql" if engine in ("mysql", "mariadb") else engine,
            name=kwargs["name"],
            user=kwargs["user"],
            password="pw",
            host="localhost",
            port=5432,
        )

    monkeypatch.setattr(deploy_module, "provision_database", provision)


def test_the_plan_gives_the_recipe_health_as_initial_health(store: NoustStore) -> None:
    """Umami is a Node recipe: its check must reach the deployer as initial_health."""
    plan = plan_recipe("umami", DOMAIN, port=3000, ssl=False, logger=Logger(verbose=False))

    arguments = plan.configure_arguments()

    assert arguments["initial_health"] == ("/api/heartbeat", "200", None)
    assert "health_path" not in arguments and "health_expect" not in arguments


def test_what_the_operator_gives_wins_field_by_field(store: NoustStore) -> None:
    """A value the operator set replaces the recipe's; the rest stays the recipe's."""
    plan = plan_recipe("umami", DOMAIN, port=3000, ssl=False, logger=Logger(verbose=False))

    arguments = plan.configure_arguments(health=(None, "200-299", 30))

    assert arguments["initial_health"] == ("/api/heartbeat", "200-299", 30)


def test_a_recipe_without_a_health_check_gives_the_operator_s(store: NoustStore) -> None:
    """Without a recipe check, only what the operator gave, or nothing."""
    plan = plan_recipe("uptime-kuma", DOMAIN, port=3001, ssl=False, logger=Logger(verbose=False))
    bare = replace(plan, recipe=replace(plan.recipe, health=None))

    assert bare.configure_arguments()["initial_health"] is None
    assert bare.configure_arguments(health=("/up", None, None))["initial_health"] == (
        "/up",
        None,
        None,
    )


def test_the_console_job_hands_the_recipe_health_to_the_first_deploy(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deploy_app_job's recipe branch, with the operator's expectation over the recipe's."""
    from noust.web.jobs import Job, JobContext, JobType, deploy_app_job

    configured: dict[str, Any] = {}

    class FakeDeployer:
        last_deployment_id = 3

        def configure(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def deploy(self) -> bool:
            store.create_app(App(domain=DOMAIN, app_type="nodejs"))
            return True

    monkeypatch.setattr("noust.deployers.get_deployer", lambda *a, **k: FakeDeployer())
    job = Job(id="job-h", type=JobType.DEPLOY, name="deploy", description="")

    deploy_app_job(
        DOMAIN,
        "",
        "auto",
        port=3000,
        ssl=False,
        recipe="umami",
        health_timeout=45,
        job_context=JobContext(job, lambda _job: None),
    )

    assert configured["initial_health"] == ("/api/heartbeat", "200", 45)
    assert "health_path" not in configured


def test_the_console_job_without_a_recipe_still_passes_the_operator_s(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary path is unchanged."""
    from noust.web.jobs import Job, JobContext, JobType, deploy_app_job

    configured: dict[str, Any] = {}

    class FakeDeployer:
        last_deployment_id = 4

        def configure(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def deploy(self) -> bool:
            return True

    monkeypatch.setattr("noust.deployers.get_deployer", lambda *a, **k: FakeDeployer())
    job = Job(id="job-p", type=JobType.DEPLOY, name="deploy", description="")

    deploy_app_job(
        DOMAIN,
        "https://github.com/o/r.git",
        "nodejs",
        port=3000,
        ssl=False,
        health_path="/healthz",
        job_context=JobContext(job, lambda _job: None),
    )

    assert configured["initial_health"] == ("/healthz", None, None)


def test_wasm_create_recipe_hands_the_recipe_health_to_the_first_deploy(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI path uses the same plan, so the same initial_health."""
    configured: dict[str, Any] = {}

    class FakeDeployer:
        def configure(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def deploy(self) -> bool:
            return True

    monkeypatch.setattr("noust.cli.commands.webapp.get_deployer", lambda *a, **k: FakeDeployer())
    monkeypatch.setattr(
        "noust.cli.commands.webapp.check_deployment_ready", lambda **_k: (True, [], [])
    )

    result = CliRunner().invoke(root_cli, ["create", "--recipe", "n8n", "-d", DOMAIN, "--no-ssl"])

    assert result.exit_code == 0, result.output
    assert configured["initial_health"] == ("/healthz", "200", None)
