# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/recipes/test_operator_recipes.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.recipes.factory import compose_recipe

LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Acme">'
    "<title>Acme</title>"
    '<rect width="64" height="64" rx="14" fill="#24292f"/></svg>'
)


def write_recipe(directory: Path, name: str = "acme-wiki", **overrides: object) -> Path:
    """Write a valid private recipe, with its logo, and return its file."""
    document = compose_recipe(name=name, title="Acme Wiki", **overrides)
    file = directory / f"{name}.yaml"
    file.write_text(yaml.safe_dump(document, sort_keys=False))
    file.chmod(0o644)
    assets = directory / "assets" / name
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.svg").write_text(LOGO)
    return file
