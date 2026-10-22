# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The shipped catalogue: every recipe loads, every pin holds its shape, every logo serves."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

import pytest
import yaml

from noust.recipes import (
    CATEGORIES,
    RecipeError,
    categories,
    get_recipe,
    list_recipes,
    logo_svg,
    read_asset,
    search_recipes,
)
from noust.recipes.deploy import render_notes
from noust.recipes.logos import validate_svg
from noust.recipes.model import Recipe
from noust.recipes.variables import DeploymentContext, resolve_variables

#: The recipes of the 2.3 catalogue, now in the 4.0 format.
MIGRATED = {"wordpress", "uptime-kuma", "umami", "n8n"}
#: Listed, not deployable, with the reason.
UNAVAILABLE = {
    "appwrite",
    "bookstack",
    "cal-com",
    "coder",
    "excalidraw",
    "hermes-agent",
    "joplin-server",
    "minio",
    "openclaw",
    "pocketbase",
    "portainer",
    "stalwart",
    "strapi",
    "supabase",
    "vault",
}
#: Wave one (checklist D-29) that ship as Docker recipes.
WAVE_ONE = {
    "vaultwarden",
    "memos",
    "syncthing",
    "stirling-pdf",
    "grafana",
    "meilisearch",
    "code-server",
    "jellyfin",
    "mailpit",
    "open-webui",
    "ollama",
    "ntfy",
    "navidrome",
    "linkding",
    "freshrss",
    "homepage",
}
#: Compose recipes whose images carry a tag but no digest read from a registry.
#: Each one is a debt the report names; the list only ever shrinks.
UNVERIFIED_DIGESTS: set[str] = set()

FLOATING = re.compile(r":(latest|stable|edge|nightly|main|master|dev|develop|canary|next)(@|$|\s)")


def shipped() -> list[Recipe]:
    """Every recipe of the package."""
    return list_recipes()


def compose_recipes() -> list[Recipe]:
    """The recipes that run in Docker."""
    return [recipe for recipe in shipped() if recipe.available and recipe.kind == "compose"]


def test_the_catalogue_holds_the_migrated_recipes_and_the_first_wave() -> None:
    """Nothing the 2.3 catalogue had is gone, and wave one is there."""
    names = {recipe.name for recipe in shipped()}

    assert MIGRATED | UNAVAILABLE | WAVE_ONE <= names
    available = {recipe.name for recipe in shipped() if recipe.available}
    assert MIGRATED | WAVE_ONE <= available
    assert not UNAVAILABLE & available


def test_every_recipe_of_wave_one_is_deployable_or_listed_with_the_reason() -> None:
    """The 19 of the checklist's D-29: none is missing without an explanation."""
    wave = WAVE_ONE | {"pocketbase", "excalidraw", "vault"}
    assert len(wave) == 19

    for name in wave:
        recipe = get_recipe(name)
        assert recipe.available or recipe.unavailable_reason, name


def test_every_shipped_recipe_is_written_in_the_current_format() -> None:
    """Nothing ships in the 2.3 format: it is only still read."""
    assert {recipe.name for recipe in shipped() if recipe.schema != 2} == set()


@pytest.mark.parametrize("name", sorted(UNAVAILABLE))
def test_the_recipes_left_out_say_why(name: str) -> None:
    """Listed, not deployable, with the reason."""
    recipe = get_recipe(name)

    assert recipe.available is False
    assert (
        recipe.unavailable_reason and "Not available in this release" in recipe.unavailable_reason
    )
    assert recipe.summary()["app_type"] is None
    assert recipe.logo is not None


def test_ghost_and_plausible_carry_the_databases_they_needed() -> None:
    """Listed as unavailable until 4.0, they now run with MySQL 8 and with PostgreSQL and ClickHouse."""
    ghost, plausible = get_recipe("ghost"), get_recipe("plausible")

    assert ghost.available and plausible.available
    assert {image.repository for image in ghost.images} == {"ghost", "mysql"}
    assert any(image.tag.startswith("8.") for image in ghost.images if image.repository == "mysql")
    assert {image.repository for image in plausible.images} == {
        "ghcr.io/plausible/community-edition",
        "postgres",
        "clickhouse/clickhouse-server",
    }


def test_the_file_names_are_the_recipe_names() -> None:
    """The one name a recipe has."""
    files = {
        entry.name.removesuffix(".yaml")
        for entry in resources.files("noust.recipes").iterdir()
        if entry.name.endswith(".yaml")
    }

    assert files == {recipe.name for recipe in shipped()}


@pytest.mark.parametrize("recipe", shipped(), ids=lambda recipe: recipe.name)
def test_every_recipe_has_what_a_catalogue_card_shows(recipe: Recipe) -> None:
    """Title, slogan, a real category, tags, a logo, links over https."""
    assert recipe.title and recipe.slogan and recipe.description
    assert recipe.category in CATEGORIES
    assert recipe.logo == "logo.svg"
    assert recipe.homepage.startswith("https://")
    if recipe.available:
        assert recipe.category != "other"
        assert recipe.tags, "a recipe needs tags to be found"
        assert recipe.version and recipe.updated is not None
        assert recipe.docs and recipe.repository and recipe.license


@pytest.mark.parametrize("recipe", shipped(), ids=lambda recipe: recipe.name)
def test_every_logo_passes_the_policy_that_serves_it(recipe: Recipe) -> None:
    """No script, no style, no link: it works under the console's CSP."""
    svg = logo_svg(recipe.name)

    assert validate_svg(recipe.name, svg) == svg
    assert svg.lstrip().startswith("<svg") and "<script" not in svg and "style" not in svg


def test_every_svg_in_the_package_is_valid_even_if_no_recipe_names_it() -> None:
    """A stray file in assets/ cannot become an XSS by being served some day."""
    root = resources.files("noust.recipes").joinpath("assets")
    found = 0
    for directory in root.iterdir():
        for entry in directory.iterdir():
            if entry.name.endswith(".svg"):
                validate_svg(directory.name, entry.read_text(encoding="utf-8"))
                found += 1
    assert found >= len(shipped())


# ---------------------------------------------------------------------------
# Pinning: the policy that Coolify's catalogue does not keep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe", compose_recipes(), ids=lambda recipe: recipe.name)
def test_every_compose_image_is_pinned_by_tag_and_digest(recipe: Recipe) -> None:
    """No `latest`, no missing tag, no floating tag; and a digest read from the registry."""
    document = yaml.safe_load(recipe.compose or "")
    images = [service["image"] for service in document["services"].values()]

    assert images, "a compose recipe runs an image"
    for image in images:
        repository_and_tag = image.split("@", 1)[0]
        assert ":" in repository_and_tag.rsplit("/", 1)[-1], f"{image} has no tag"
        assert not FLOATING.search(image + " "), f"{image} has a floating tag"
        assert "${" not in image
        if recipe.name not in UNVERIFIED_DIGESTS:
            assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"{image} has no digest"
    assert [image.reference for image in recipe.images] == images
    assert recipe.digest_verified is (recipe.name not in UNVERIFIED_DIGESTS)


def test_no_compose_recipe_publishes_anything_but_loopback_and_declared_ports() -> None:
    """nginx is the way in; the rest is said in public_ports."""
    for recipe in compose_recipes():
        document = yaml.safe_load(recipe.compose or "")
        declared = {(port.port, port.protocol) for port in recipe.public_ports}
        for service in document["services"].values():
            for entry in service.get("ports") or ():
                if entry.startswith("127.0.0.1:"):
                    continue
                port, _, protocol = entry.partition("/")
                number = int(port.split(":")[0])
                assert (number, protocol or "tcp") in declared, (recipe.name, entry)


def test_a_compose_recipe_says_it_needs_docker() -> None:
    """The catalogue's "runs in Docker" badge, and the sentence an operator reads."""
    for recipe in compose_recipes():
        assert recipe.runs_in_docker is True
        assert any("Docker" in need for need in recipe.needs), recipe.name
        assert recipe.summary()["runs_in_docker"] is True


def test_every_available_recipe_has_a_health_check_and_notes() -> None:
    """The health gate has something to ask, and the operator something to do next."""
    for recipe in shipped():
        if recipe.available:
            assert recipe.health is not None and recipe.health.path.startswith("/"), recipe.name
            assert recipe.notes, recipe.name


@pytest.mark.parametrize(
    "recipe", [recipe for recipe in shipped() if recipe.available], ids=lambda r: r.name
)
def test_every_notes_render_with_the_values_an_install_has(recipe: Recipe) -> None:
    """A note cannot reach for a secret or a name the deployment does not give."""
    context = DeploymentContext(
        domain="app.example.com",
        ssl=True,
        port=4000,
        app_name="app-example-com",
        app_path="/var/www/apps/app-example-com",
    )
    provisioned = {
        requirement: dict.fromkeys(("url", "host", "port", "name", "user", "password"), "x")
        for requirement in recipe.requires
    }
    given = {variable.name: "given" for variable in recipe.variables if variable.is_required}
    resolved = resolve_variables(
        recipe.variables, context=context, provisioned=provisioned, overrides=given
    )

    notes = render_notes(recipe, context, resolved.values, resolved.secrets)

    assert len(notes) == len(recipe.notes)
    assert all("{{" not in note for note in notes)
    for note in notes:
        for name in resolved.secrets:
            assert resolved.values[name] not in note or len(resolved.values[name]) < 8


# ---------------------------------------------------------------------------
# Compatibility of the four that were there
# ---------------------------------------------------------------------------


def test_the_migrated_recipes_set_the_variables_they_always_did() -> None:
    """The names an application's code and an operator's notes depend on."""
    expected = {
        "wordpress": {
            "WORDPRESS_DB_NAME",
            "WORDPRESS_DB_USER",
            "WORDPRESS_DB_PASSWORD",
            "WORDPRESS_DB_HOST",
            "WORDPRESS_AUTH_KEY",
            "WORDPRESS_SECURE_AUTH_KEY",
            "WORDPRESS_LOGGED_IN_KEY",
            "WORDPRESS_NONCE_KEY",
            "WORDPRESS_AUTH_SALT",
            "WORDPRESS_SECURE_AUTH_SALT",
            "WORDPRESS_LOGGED_IN_SALT",
            "WORDPRESS_NONCE_SALT",
        },
        "uptime-kuma": {"UPTIME_KUMA_HOST"},
        "umami": {"DATABASE_URL", "APP_SECRET", "DISABLE_TELEMETRY"},
        "n8n": {
            "N8N_PORT",
            "N8N_LISTEN_ADDRESS",
            "N8N_HOST",
            "N8N_PROTOCOL",
            "N8N_EDITOR_BASE_URL",
            "WEBHOOK_URL",
            "N8N_SECURE_COOKIE",
            "N8N_USER_FOLDER",
            "N8N_ENCRYPTION_KEY",
            "N8N_DIAGNOSTICS_ENABLED",
        },
    }

    for name, names in expected.items():
        assert names <= {variable.name for variable in get_recipe(name).variables}, name


def test_the_migrated_recipes_keep_their_database_source_and_settings() -> None:
    """Only the format changed."""
    wordpress, umami, kuma, n8n = (
        get_recipe(name) for name in ("wordpress", "umami", "uptime-kuma", "n8n")
    )

    assert (wordpress.app_type, wordpress.database_engine) == ("php-fpm", "mysql")
    assert wordpress.php["shared_from_release"] == ["wp-content"]
    assert (umami.app_type, umami.database_engine) == ("nodejs", "postgresql")
    assert umami.source is not None and umami.source.ref == "v2.20.2"
    assert kuma.source is not None and kuma.source.ref == "1.23.17"
    assert kuma.persistent_paths == ("data",) and n8n.persistent_paths == ("data",)
    assert n8n.source is not None and n8n.source.kind == "template"


def test_the_n8n_recipe_version_is_the_version_its_package_json_installs() -> None:
    """One number in two places would be two numbers one day."""
    package = json.loads(read_asset("n8n", "package.json"))

    assert package["dependencies"]["n8n"] == get_recipe("n8n").version


def test_a_git_recipe_pins_a_tag_never_a_branch() -> None:
    """A release tag."""
    for name in ("uptime-kuma", "umami"):
        source = get_recipe(name).source
        assert source is not None and re.match(r"^v?\d+\.\d+\.\d+$", source.ref or "")


# ---------------------------------------------------------------------------
# Search and filters
# ---------------------------------------------------------------------------


def test_the_catalogue_lists_what_can_be_deployed_first() -> None:
    """Available recipes, then the unavailable ones, each by title."""
    recipes = list_recipes()
    flags = [recipe.available for recipe in recipes]

    assert flags == sorted(flags, reverse=True)
    available = [recipe.title.lower() for recipe in recipes if recipe.available]
    assert available == sorted(available)


def test_a_search_finds_by_every_word_in_name_title_slogan_tags_and_category() -> None:
    """password manager finds Vaultwarden; a word nothing has finds nothing."""
    assert [recipe.name for recipe in search_recipes(query="password manager")] == ["vaultwarden"]
    assert "grafana" in {recipe.name for recipe in search_recipes(query="DASHBOARDS")}
    assert "mailpit" in {recipe.name for recipe in search_recipes(query="smtp")}
    assert search_recipes(query="zzzz nothing") == []
    assert len(search_recipes(query="")) == len(shipped())


def test_filters_combine_and_the_closed_lists_are_enforced() -> None:
    """category, tag and kind each narrow the result; a made-up value is an error."""
    media = {recipe.name for recipe in search_recipes(category="media")}
    assert {"jellyfin", "navidrome"} <= media
    assert {recipe.name for recipe in search_recipes(category="media", tag="subsonic")} == {
        "navidrome"
    }
    assert all(recipe.kind == "native" for recipe in search_recipes(kind="native"))
    assert {recipe.name for recipe in search_recipes(kind="native")} >= MIGRATED
    assert search_recipes(category="media", kind="native") == []
    with pytest.raises(RecipeError, match="not a recipe category"):
        search_recipes(category="Networking")
    with pytest.raises(RecipeError, match="not a recipe kind"):
        search_recipes(kind="docker")


def test_categories_count_the_recipes_that_are_in_them() -> None:
    """Only categories with a recipe, in the order of the closed list."""
    counted = categories()

    assert [entry["category"] for entry in counted] == [
        category for category in CATEGORIES if any(c["category"] == category for c in counted)
    ]
    assert sum(entry["count"] for entry in counted) == len(shipped())


def test_a_logo_the_recipe_does_not_have_is_not_found() -> None:
    """An unknown recipe is a not found, not a crash."""
    with pytest.raises(RecipeError):
        logo_svg("drupal")


def test_the_nginx_settings_a_recipe_ships_are_valid_for_the_builder_that_reads_them(
    tmp_path: Path,
) -> None:
    """Upload size and read timeout, in the file the Compose deployer's site is built from."""
    from noust.deployers.helpers.nginx_config import NginxConfigBuilder

    shipped_any = 0
    for recipe in compose_recipes():
        if "noust.nginx.yaml" not in recipe.files:
            continue
        shipped_any += 1
        path = tmp_path / f"{recipe.name}.yaml"
        path.write_text(read_asset(recipe.name, recipe.files["noust.nginx.yaml"]))
        builder = NginxConfigBuilder()

        config = builder.parse(path)

        assert builder.validate(config) == [], recipe.name
        assert config.proxies_the_app, recipe.name
        assert config.max_body_size, recipe.name
        context = builder.proxy_context(config, "site.example.com")
        assert context["max_body_size"] == config.max_body_size
    assert shipped_any >= 10


def test_the_applications_that_take_uploads_ship_a_body_size() -> None:
    """Vaultwarden's attachments, Stirling's PDFs, Meilisearch's batches: nginx's 1m would refuse them."""
    for name in ("vaultwarden", "stirling-pdf", "meilisearch", "open-webui", "memos"):
        assert "noust.nginx.yaml" in get_recipe(name).files, name
