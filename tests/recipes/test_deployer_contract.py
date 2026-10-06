# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A Compose recipe's plan, read by the real Compose deployer, without Docker."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers.compose_ports import is_headless_stack, published_port
from noust.recipes import list_recipes
from noust.recipes.deploy import COMPOSE_FILE, plan_recipe

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
    )
    app_path = tmp_path / "apps" / "site-example-com"
    shutil.copytree(plan.source, app_path)
    deployer = DockerComposeDeployer()

    deployer.configure(
        domain=DOMAIN, port=PORT, webserver="nginx", ssl=True, **plan.configure_arguments()
    )
    deployer.app_path = app_path
    deployer._discover_compose_file()
    deployer._parse_compose_services()

    assert deployer.compose_path is not None and deployer.compose_path.name == COMPOSE_FILE
    assert deployer.env_vars["PORT"] == str(PORT)
    assert deployer._get_primary_port() == PORT
    assert deployer._is_headless() is False
    web = [service for service in deployer.services if service.is_web]
    assert len(web) >= 1
    # The document's own web port, read as the deployer reads ports: unresolved, but published.
    assert not is_headless_stack(web)
    assert deployer.source == plan.source and deployer.branch is None


def required(name: str) -> dict[str, str]:
    """What an operator must give a recipe for it to install."""
    from noust.recipes import get_recipe

    return {
        variable.name: "given@example.com"
        for variable in get_recipe(name).variables
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
