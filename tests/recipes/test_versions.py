# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What a recipe asks of Noust's version and of the machine, and that it is enforced."""

from __future__ import annotations

import re
from typing import Any

import pytest

from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.recipes import RecipeError, get_recipe, parse_recipe
from noust.recipes import deploy as deploy_module
from noust.recipes.deploy import plan_recipe, recipe_source_dir, require_available
from noust.recipes.versions import host_architecture, require_compatible
from tests.recipes.factory import compose_recipe, native_recipe, use_demo_assets

DOMAIN = "site.example.com"


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the assets of a shipped recipe."""
    use_demo_assets(monkeypatch)


def refused(data: dict[str, Any], message: str) -> None:
    """Parse ``data`` and check that it is refused with ``message``."""
    with pytest.raises(RecipeError, match=re.escape(message)):
        parse_recipe("demo", data)


@pytest.mark.parametrize(
    ("machine", "docker"),
    [
        ("x86_64", "amd64"),
        ("AMD64", "amd64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("armv7l", "arm"),
        ("i686", "386"),
        ("riscv64", "riscv64"),
        ("s390x", "s390x"),
    ],
)
def test_the_machine_is_named_the_way_docker_names_it(
    monkeypatch: pytest.MonkeyPatch, machine: str, docker: str
) -> None:
    """platform.machine() to the architecture an image is published for."""
    monkeypatch.setattr("platform.machine", lambda: machine)

    assert host_architecture() == docker


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------


def test_min_noust_and_architectures_are_read_and_shown() -> None:
    """Both are in the summary the catalogue and the console read."""
    recipe = parse_recipe(
        "demo", compose_recipe(min_noust="3.4.0", architectures=["amd64", "arm64"])
    )

    assert (recipe.min_noust, recipe.architectures) == ("3.4.0", ("amd64", "arm64"))
    assert recipe.summary()["min_noust"] == "3.4.0"
    assert recipe.summary()["architectures"] == ["amd64", "arm64"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"min_noust": "3.4"}, "min_noust must be a version"),
        ({"min_noust": 3.4}, "min_noust must be a version"),
        ({"architectures": ["x86_64"]}, "architectures must be different ones of"),
        ({"architectures": ["amd64", "amd64"]}, "architectures must be different ones of"),
        ({"architectures": []}, "says which architectures its images are published for"),
    ],
)
def test_the_two_fields_are_closed(overrides: dict[str, Any], message: str) -> None:
    """A compose recipe must say its architectures; the names are Docker's."""
    refused(compose_recipe(**{"architectures": ["amd64"], **overrides}), message)


def test_a_compose_recipe_without_architectures_is_refused_and_a_native_one_may_leave_them_out() -> (
    None
):
    """Images are published for some platforms; a native source builds anywhere it can."""
    without = compose_recipe()
    del without["architectures"]
    refused(without, "says which architectures its images are published for")

    assert parse_recipe("demo", native_recipe()).architectures == ()


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def test_a_recipe_for_a_newer_noust_is_refused_with_the_way_to_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """min_noust is applied, not just shown."""
    monkeypatch.setattr("noust.__version__", "3.3.1")
    older = parse_recipe("demo", compose_recipe(min_noust="3.3.1", architectures=["amd64"]))
    newer = parse_recipe("demo", compose_recipe(min_noust="3.4.0", architectures=["amd64"]))
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    require_compatible(older)
    with pytest.raises(
        RecipeError, match=r"needs Noust 3\.4\.0 or newer; this is Noust 3\.3\.1"
    ) as failure:
        require_compatible(newer)

    assert "pip install --upgrade noust" in failure.value.details


def test_a_recipe_not_published_for_this_architecture_is_refused_naming_the_ones_it_runs_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """amd64 only on an arm64 server: refused, before anything is provisioned."""
    recipe = parse_recipe("demo", compose_recipe(architectures=["amd64", "386"]))
    monkeypatch.setattr("platform.machine", lambda: "aarch64")

    with pytest.raises(
        RecipeError, match=r"not published for this server's architecture \(arm64\)"
    ) as failure:
        require_compatible(recipe)

    assert "amd64, 386" in failure.value.details
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    require_compatible(recipe)


def test_no_architectures_means_any_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A native recipe says nothing."""
    monkeypatch.setattr("platform.machine", lambda: "riscv64")

    require_compatible(parse_recipe("demo", native_recipe()))


def test_the_install_and_the_update_ask_before_anything_happens(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """plan_recipe and require_available both refuse; nothing is written."""
    recipe = parse_recipe("demo", compose_recipe(architectures=["amd64"]))
    monkeypatch.setattr(deploy_module, "get_recipe", lambda name: recipe)
    monkeypatch.setattr("platform.machine", lambda: "aarch64")

    with pytest.raises(RecipeError, match="not published for this server's architecture"):
        plan_recipe("demo", DOMAIN, port=8123, ssl=True, logger=Logger(verbose=False))
    with pytest.raises(RecipeError, match="not published for this server's architecture"):
        require_available(recipe)

    assert not recipe_source_dir("site-example-com", recipe_store).exists()


def test_every_recipe_that_ships_runs_on_amd64_and_names_its_architectures() -> None:
    """The catalogue's baseline: x86-64 servers, and the others said."""
    for name in ("vaultwarden", "memos", "jellyfin", "navidrome", "homepage"):
        recipe = get_recipe(name)
        assert "amd64" in recipe.architectures and recipe.architectures, name
    assert set(get_recipe("navidrome").architectures) == {"386", "amd64", "arm", "arm64", "riscv64"}
    assert get_recipe("vaultwarden").summary()["architectures"] == ["amd64", "arm", "arm64"]
