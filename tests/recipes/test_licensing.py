# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which licences are free software: the label a recipe carries when its licence is not."""

from __future__ import annotations

import pytest

from noust.recipes import get_recipe, list_recipes
from noust.recipes.licensing import is_free_software


@pytest.mark.parametrize(
    "expression",
    [
        "MIT",
        "Apache-2.0",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "GPL-2.0-only",
        "LGPL-3.0-or-later",
        "MPL-2.0",
        "EUPL-1.2",
        "OSL-3.0",
        "BSD-3-Clause",
        "PostgreSQL",
        "MIT AND Apache-2.0",
        "MIT OR Apache-2.0",
        "GPL-2.0-or-later WITH Classpath-exception-2.0",
        "AGPL-3.0-only OR LicenseRef-Stalwart-Enterprise-v2",
    ],
)
def test_a_licence_of_the_free_software_family_is_free(expression: str) -> None:
    """An OSI-approved or FSF-free licence, alone or with an exception or an alternative."""
    assert is_free_software(expression) is True


@pytest.mark.parametrize(
    "expression",
    [
        "BUSL-1.1",
        "FSL-1.1-Apache-2.0",
        "FSL-1.1-ALv2",
        "SSPL-1.0",
        "Elastic-2.0",
        "LicenseRef-n8n-Sustainable-Use",
        "LicenseRef-Chatwoot-MIT-and-EE",
        "MIT AND LicenseRef-Enterprise",
        "PolyForm-Shield-1.0.0",
        "",
        None,
    ],
)
def test_a_source_available_open_core_or_unknown_licence_is_not_free(
    expression: str | None,
) -> None:
    """The label is for what the operator is told is not free software, never a guess in its favour."""
    assert is_free_software(expression) is False


def test_the_summary_of_a_recipe_says_whether_its_licence_is_free() -> None:
    """The API and the console read it from the summary, so it is computed in one place."""
    assert get_recipe("vaultwarden").summary()["free_software"] is True
    assert get_recipe("n8n").summary()["free_software"] is False


def test_a_recipe_that_declares_no_licence_is_not_labelled_as_not_free() -> None:
    """Nothing is claimed, so nothing is said against the project."""
    import dataclasses

    bare = dataclasses.replace(get_recipe("hello"), license=None)

    assert bare.free_software is True and bare.summary()["free_software"] is True


def test_every_recipe_that_is_not_free_software_says_so_in_its_description() -> None:
    """The contribution rule (docs/recipes-format.md): source-available or open core, in words."""
    said = []
    for recipe in list_recipes():
        if recipe.available and recipe.license and not recipe.free_software:
            if "source-available" not in recipe.description.lower() and (
                "open core" not in recipe.description.lower()
            ):
                said.append(recipe.name)
    assert said == []


def test_the_command_line_says_on_the_list_and_on_the_page_that_a_licence_is_not_free() -> None:
    """The operator chooses from the list, so the label is on its line and not only on the page."""
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    listed = CliRunner().invoke(root_cli, ["recipe", "list", "-q", "n8n"])
    shown = CliRunner().invoke(root_cli, ["recipe", "show", "n8n"])
    free = CliRunner().invoke(root_cli, ["recipe", "list", "-q", "vaultwarden"])

    assert "[not free software]" in next(
        line for line in listed.output.splitlines() if line.strip().startswith("n8n:")
    )
    assert "not free software" in shown.output
    assert "not free software" not in free.output
