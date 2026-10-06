# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The catalogue's second wave (checklist D-01, D-30, D-31, D-32): stacks with their own databases.

A recipe of this wave runs an application together with the PostgreSQL, MySQL, MongoDB,
ClickHouse or Valkey it needs, as services of one Compose stack. What these tests hold on
to is what makes such a stack safe to install and to update: every database keeps its data
in a named volume, is an image Noust knows how to dump, starts before what uses it, is never
published, and no secret is written in the document.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.managers.database.instances import image_engine
from noust.managers.stack_databases import declared_databases
from noust.managers.stack_databases import image_engine as dumpable_engine
from noust.recipes import get_recipe, list_recipes, read_asset
from noust.recipes.model import Recipe

#: The recipes this wave added, which run an application with databases of its own.
STACKS = {
    "activepieces",
    "authentik",
    "baserow",
    "directus",
    "docmost",
    "forgejo",
    "ghost",
    "gitea",
    "immich",
    "keycloak",
    "langflow",
    "listmonk",
    "litellm",
    "matomo",
    "mattermost",
    "metabase",
    "miniflux",
    "nextcloud",
    "nocodb",
    "paperless-ngx",
    "plausible",
    "rocketchat",
    "twenty",
    "wikijs",
}
#: The recipes this wave added that are a single container or keep data in a file.
SINGLES = {
    "actual-budget",
    "audiobookshelf",
    "gotify",
    "healthchecks",
    "searxng",
    "trilium",
    "vikunja",
}
#: Applications whose first password is chosen by whoever opens them first and cannot be set
#: from the environment: they generate no secret, and the first note says to open them now.
FIRST_VISIT = {"actual-budget", "audiobookshelf", "trilium"}
#: Static applications that keep nothing on the server: no volume, no copy to make.
STATELESS = {"cyberchef"}
WAVE_TWO = STACKS | SINGLES | STATELESS
#: Databases with no accounts: a MongoDB replica set needs a key file to have any, and this
#: stack has one member that nothing outside its network can reach.
WITHOUT_ACCOUNTS = {"rocketchat"}

#: Where each engine keeps its data, for the volume a stack must mount there.
DATA_DIRECTORIES = {
    "postgres": "/var/lib/postgresql/data",
    "mysql": "/var/lib/mysql",
    "mariadb": "/var/lib/mysql",
    "mongo": "/data/db",
}
#: Images that are a database or a store but not one of the four Noust dumps.
OTHER_STORES = {"clickhouse/clickhouse-server": "/var/lib/clickhouse", "valkey/valkey": "/data"}

#: What a variable's name says it holds, so that a literal there is a default credential.
SECRET_NAME = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|_KEY|_PASS$|_PWD$)", re.IGNORECASE)


def compose_of(recipe: Recipe) -> dict[str, Any]:
    """The recipe's Compose document, parsed."""
    document = yaml.safe_load(recipe.compose or "")
    assert isinstance(document, dict)
    return document


def services_of(recipe: Recipe) -> dict[str, dict[str, Any]]:
    """Its services by name."""
    return dict(compose_of(recipe)["services"])


def mounts(service: dict[str, Any]) -> dict[str, str]:
    """A service's named volumes: volume name to mount point."""
    found: dict[str, str] = {}
    for entry in service.get("volumes") or ():
        source, _, rest = str(entry).partition(":")
        if not source.startswith(("./", "$", "/")):
            found[source] = rest.split(":")[0]
    return found


def environment_of(service: dict[str, Any]) -> dict[str, str]:
    """A service's environment as a mapping, whichever way it was written."""
    raw = service.get("environment") or {}
    if isinstance(raw, list):
        return dict(item.partition("=")[::2] for item in map(str, raw))
    return {str(key): "" if value is None else str(value) for key, value in raw.items()}


def installable() -> list[Recipe]:
    """Every recipe a server can install."""
    return [recipe for recipe in list_recipes() if recipe.available]


def compose_recipes() -> list[Recipe]:
    """The installable recipes that run in Docker."""
    return [recipe for recipe in installable() if recipe.kind == "compose"]


def test_the_catalogue_reaches_the_credibility_floor_of_forty_installable_recipes() -> None:
    """Checklist D-01: 40 to 60 curated recipes, of which this wave is a part."""
    assert len(installable()) >= 40
    assert WAVE_TWO <= {recipe.name for recipe in installable()}


@pytest.mark.parametrize("name", sorted(WAVE_TWO))
def test_a_recipe_of_the_wave_is_a_pinned_docker_recipe_with_everything_a_card_shows(
    name: str,
) -> None:
    """Closed schema, tag and digest on every image, and the metadata of the catalogue's card."""
    recipe = get_recipe(name)

    assert recipe.available and recipe.kind == "compose" and recipe.runs_in_docker
    assert recipe.digest_verified is True
    assert recipe.logo == "logo.svg" and recipe.tags and recipe.category != "other"
    assert recipe.version and recipe.license and recipe.docs and recipe.repository
    assert recipe.architectures and recipe.health is not None and recipe.notes
    assert all(image.digest and image.tag for image in recipe.images)


@pytest.mark.parametrize("recipe", compose_recipes(), ids=lambda recipe: recipe.name)
def test_every_database_of_a_stack_keeps_its_data_in_a_named_volume(recipe: Recipe) -> None:
    """The volume is declared, so a volume backup covers it and an update does not lose it."""
    for service_name, service in services_of(recipe).items():
        engine = image_engine(service["image"])
        reference = service["image"].split("@", 1)[0].rsplit(":", 1)[0]
        expected = (
            DATA_DIRECTORIES.get(engine.flavour) if engine is not None and engine.official else None
        ) or OTHER_STORES.get(reference)
        if expected is None:
            continue
        assert expected in mounts(service).values(), (recipe.name, service_name)


def declared_dumps(recipe: Recipe) -> list[Any]:
    """The databases the recipe's own noust.yaml declares for Noust to dump."""
    if "noust.yaml" not in recipe.files:
        return []
    with tempfile.TemporaryDirectory() as directory:
        text = read_asset(recipe.name, recipe.files["noust.yaml"])
        (Path(directory) / "noust.yaml").write_text(text)
        declared = declared_databases(Path(directory))
    assert isinstance(declared, tuple)
    return list(declared)


@pytest.mark.parametrize("name", sorted(STACKS))
def test_a_database_of_a_stack_is_one_noust_dumps_before_an_update(name: str) -> None:
    """Either the official image, with account and database in its environment, or declared."""
    recipe = get_recipe(name)
    services = services_of(recipe)
    databases = 0
    declared_services = {declared.service for declared in declared_dumps(recipe)}
    for service_name, service in services.items():
        engine = image_engine(service["image"])
        if engine is None or engine.flavour not in DATA_DIRECTORIES:
            continue
        databases += 1
        if service_name in declared_services:
            # An image Noust recognises as an engine but not as the official one (pgvector's):
            # the recipe's noust.yaml names it, which is what makes the dump happen.
            continue
        assert engine.official and dumpable_engine(service["image"]) is not None, service_name
        environment = environment_of(service)
        if engine.flavour == "postgres":
            assert environment.get("POSTGRES_USER") and environment.get("POSTGRES_DB")
        elif engine.flavour in ("mysql", "mariadb"):
            prefix = "MARIADB_" if engine.flavour == "mariadb" else "MYSQL_"
            assert environment.get(prefix + "USER") and environment.get(prefix + "DATABASE")
        elif engine.flavour == "mongo" and name not in WITHOUT_ACCOUNTS:
            assert environment.get("MONGO_INITDB_ROOT_USERNAME"), service_name
    for declared in declared_dumps(recipe):
        databases += 1
        assert declared.service in services, (name, declared.service)
        assert mounts(services[declared.service]), (name, declared.service)
    assert databases >= 1, name


def test_a_database_image_noust_does_not_recognise_is_declared_for_the_dump() -> None:
    """Immich's PostgreSQL carries the vector extensions: its recipe declares it in noust.yaml."""
    recipe = get_recipe("immich")
    image = services_of(recipe)["database"]["image"]

    assert image_engine(image) is None
    assert [(d.service, d.engine, d.database, d.user) for d in declared_dumps(recipe)] == [
        ("database", "postgres", "immich", "postgres")
    ]


@pytest.mark.parametrize("name", sorted(STACKS))
def test_what_uses_a_database_waits_for_it_to_answer(name: str) -> None:
    """depends_on with service_healthy for every database that has a health check."""
    services = services_of(get_recipe(name))
    for service_name, service in services.items():
        waits = service.get("depends_on") or {}
        for dependency in waits:
            target = services[dependency]
            if target.get("healthcheck"):
                assert isinstance(waits, dict), (name, service_name)
                assert waits[dependency] == {"condition": "service_healthy"}, (
                    name,
                    service_name,
                    dependency,
                )


@pytest.mark.parametrize("recipe", compose_recipes(), ids=lambda recipe: recipe.name)
def test_nothing_but_the_web_port_and_the_declared_ones_is_published(recipe: Recipe) -> None:
    """A database, a cache or a worker is never reachable from the network."""
    declared = {f"{port.port}:{port.port}" for port in recipe.public_ports}
    for service_name, service in services_of(recipe).items():
        for entry in service.get("ports") or ():
            text = str(entry).split("/", 1)[0]
            assert text.startswith("127.0.0.1:") or text in declared, (
                recipe.name,
                service_name,
                entry,
            )
        if image_engine(service["image"]) is not None:
            assert not service.get("ports"), (recipe.name, service_name)


@pytest.mark.parametrize("recipe", compose_recipes(), ids=lambda recipe: recipe.name)
def test_no_credential_is_written_in_a_document(recipe: Recipe) -> None:
    """A secret reaches the stack through a variable Noust generated, never as a literal."""
    for service_name, service in services_of(recipe).items():
        for key, value in environment_of(service).items():
            if not SECRET_NAME.search(key) or not value:
                continue
            assert "${" in value, (recipe.name, service_name, key)


@pytest.mark.parametrize("name", sorted(FIRST_VISIT))
def test_an_application_that_asks_its_first_visitor_for_a_password_says_so_first(name: str) -> None:
    """No default account: the note that comes first tells the operator to open it and choose."""
    recipe = get_recipe(name)

    assert recipe.notes[0].startswith("Open {{ url }} now")
    assert "first" in recipe.notes[0]


@pytest.mark.parametrize("name", sorted(WAVE_TWO - STATELESS - FIRST_VISIT))
def test_the_secrets_of_a_recipe_are_generated_and_marked_secret(name: str) -> None:
    """A generated password, hex or base64 is a secret, so it is masked wherever it is shown."""
    recipe = get_recipe(name)
    generated = [variable for variable in recipe.variables if variable.generate]

    assert generated, "a recipe of this wave has something to generate"
    assert all(variable.secret for variable in generated)
    names = {variable.name for variable in recipe.variables}
    document = recipe.compose or ""
    for variable in generated:
        assert f"${{{variable.name}}}" in document or any(
            f"${{{variable.name}}}" in (other.value or "") for other in recipe.variables
        ), variable.name
    assert "PORT" not in names


@pytest.mark.parametrize("name", sorted(WAVE_TWO - STATELESS))
def test_a_recipe_says_where_its_data_is_and_that_it_needs_a_copy(name: str) -> None:
    """Notes name a Docker volume of this application, as the contribution rules ask."""
    recipe = get_recipe(name)
    volumes = set(compose_of(recipe).get("volumes") or ())
    text = " ".join(recipe.notes)

    assert volumes, name
    assert any(volume in text for volume in volumes), name
    assert "back" in text.lower() or "copy" in text.lower(), name


@pytest.mark.parametrize("name", sorted(WAVE_TWO))
def test_a_recipe_never_asks_for_the_servers_databases(name: str) -> None:
    """A Compose stack cannot reach 127.0.0.1 of the host: its databases are its services."""
    assert get_recipe(name).requires == ()


def test_the_stacks_do_not_share_a_published_port() -> None:
    """Only the ports a recipe declares are opened; two recipes that open one cannot be installed together."""
    opened: dict[tuple[int, str], list[str]] = {}
    for recipe in compose_recipes():
        for port in recipe.public_ports:
            opened.setdefault((port.port, port.protocol), []).append(recipe.name)

    shared = {key: names for key, names in opened.items() if len(names) > 1}
    # Gitea and Forgejo are the same kind of server and both offer Git over SSH on 2222: the
    # descriptions say so, and an operator who wants both changes one of them.
    assert shared == {(2222, "tcp"): ["forgejo", "gitea"]} or shared == {
        (2222, "tcp"): ["gitea", "forgejo"]
    }
    for name in ("gitea", "forgejo"):
        assert "2222" in get_recipe(name).description


def test_the_recipes_that_were_listed_as_unavailable_until_now_are_installable() -> None:
    """Ghost waited for MySQL 8 and Plausible for ClickHouse: their stacks carry them."""
    assert get_recipe("ghost").available and get_recipe("plausible").available
