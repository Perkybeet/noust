# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A Compose recipe's plan, read by the real Compose deployer, without Docker."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers.compose_ports import is_headless_stack, published_port
from noust.recipes import list_recipes
from noust.recipes.deploy import COMPOSE_FILE, plan_recipe
from tests.support.compose_model import compose_resolving

DOMAIN = "site.example.com"
PORT = 8123
COMPOSE_RECIPES = [
    recipe.name for recipe in list_recipes() if recipe.available and recipe.kind == "compose"
]


@pytest.mark.parametrize("name", COMPOSE_RECIPES)
def test_the_deployer_reads_what_a_compose_recipe_plans(
    recipe_store: NoustStore, tmp_path: Path, name: str
) -> None:
    """Configured with the plan, handed the written source: one web service on the port given."""
    plan = plan_recipe(
        name,
        DOMAIN,
        port=PORT,
        ssl=True,
        env_overrides=required(name),
        logger=Logger(verbose=False),
        accept_root_equivalent="operator",
    )
    app_path = tmp_path / "apps" / "site-example-com"
    shutil.copytree(plan.source, app_path)
    # The guard over the file also judges what Compose resolves it to, so the recipe is handed a
    # Compose that prints that model from the document and the variables the deployer writes to
    # its ``.env``: a recipe is let through with what Compose would make of it, not unjudged.
    runner = compose_resolving(app_path, plan.env_vars)
    deployer = DockerComposeDeployer(runner=runner)

    deployer.configure(
        domain=DOMAIN, port=PORT, webserver="nginx", ssl=True, **plan.configure_arguments()
    )
    deployer.app_path = app_path
    deployer._discover_compose_file()
    deployer._parse_compose_services()

    assert any("config" in call for call in runner.calls), "the guard never looked at the model"
    assert deployer.compose_path is not None and deployer.compose_path.name == COMPOSE_FILE
    assert deployer.env_vars["PORT"] == str(PORT)
    assert deployer._get_primary_port() == PORT
    assert deployer._is_headless() is False
    web = [service for service in deployer.services if service.is_web]
    assert len(web) >= 1
    # The document's own web port, read as the deployer reads ports: unresolved, but published.
    assert not is_headless_stack(web)
    assert deployer.source == plan.source and deployer.branch is None


def test_the_guard_judges_the_model_and_not_only_the_recipes_text(
    recipe_store: NoustStore, tmp_path: Path
) -> None:
    """A mount a variable spells out is refused through the model: the contract is not blind to it."""
    app_path = tmp_path / "apps" / "site-example-com"
    app_path.mkdir(parents=True)
    (app_path / COMPOSE_FILE).write_text(
        "services:\n  web:\n    image: nginx\n    volumes:\n      - ${SOCKET}:/sock\n"
    )
    deployer = DockerComposeDeployer(
        runner=compose_resolving(app_path, {"SOCKET": "/var/run/docker.sock"})
    )
    deployer.configure(domain=DOMAIN, port=PORT, webserver="nginx", ssl=True, source="src")
    deployer.app_path = app_path
    deployer._discover_compose_file()

    with pytest.raises(DeploymentError, match="asks for root"):
        deployer._parse_compose_services()


def required(name: str) -> dict[str, str]:
    """What an operator must give a recipe for it to install."""
    from noust.recipes import get_recipe

    recipe = get_recipe(name)
    addresses = {entry.domain_variable: f"{entry.name}.{DOMAIN}" for entry in recipe.entrypoints}
    return {
        variable.name: addresses.get(variable.name, "given@example.com")
        for variable in recipe.variables
        if variable.is_required
    }


def test_a_recipes_port_is_published_through_the_variable_and_not_at_a_known_number() -> None:
    """published_port cannot read ${PORT}: the plan's port is what the deployer proxies to."""
    import yaml

    from noust.deployers.docker_compose import parse_services
    from noust.recipes import get_recipe

    services = parse_services(yaml.safe_load(get_recipe("vaultwarden").compose))

    assert published_port(services[0]) is None
    assert services[0].is_web is True
