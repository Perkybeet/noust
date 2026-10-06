# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files a recipe ships beside its YAML: ``recipes/assets/<recipe>/...``.

Every asset a recipe names is a plain relative path that exists in the package,
checked when the recipe loads: a template source's files, PHP's ``wp-config.php``,
a Compose recipe's configuration files and a recipe's logo.
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from noust.recipes.model import recipe_error

_ASSET_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")

#: The largest asset read into memory.
MAX_ASSET_BYTES = 256 * 1024


def is_plain_path(path: Any) -> bool:
    """
    Tell whether something is a relative path with no ``..`` and no odd characters.

    Args:
        path: What a recipe wrote.

    Returns:
        True for ``a/b.txt``; False for an absolute path, ``..``, an empty
        part, a space or anything that is not a string.
    """
    return (
        isinstance(path, str)
        and bool(_ASSET_PATH.match(path))
        and ".." not in path.split("/")
        and "." not in path.split("/")
    )


def asset_resource(name: str, asset: str) -> Any:
    """
    Locate a recipe asset inside the package.

    Args:
        name: The recipe.
        asset: The asset's path under ``assets/<recipe>/``.

    Returns:
        The resource.
    """
    resource = resources.files("noust.recipes").joinpath("assets").joinpath(name)
    for part in asset.split("/"):
        resource = resource.joinpath(part)
    return resource


def checked_asset(name: str, asset: Any) -> str:
    """
    Check that an asset a recipe names is shipped.

    Args:
        name: The recipe.
        asset: The asset's path under ``assets/<recipe>/``.

    Returns:
        The asset path.

    Raises:
        RecipeError: It is not a plain relative path, or not in the package.
    """
    if not is_plain_path(asset):
        raise recipe_error(name, f"asset {asset!r} is not a plain relative path")
    if not asset_resource(name, asset).is_file():
        raise recipe_error(name, f"asset {asset!r} is not in assets/{name}/")
    return str(asset)


def read_asset(name: str, asset: str) -> str:
    """
    Read a recipe asset.

    Args:
        name: The recipe.
        asset: The asset's path under ``assets/<recipe>/``.

    Returns:
        Its text.

    Raises:
        RecipeError: It is not shipped, or larger than :data:`MAX_ASSET_BYTES`.
    """
    resource = asset_resource(name, checked_asset(name, asset))
    text = str(resource.read_text(encoding="utf-8"))
    if len(text.encode()) > MAX_ASSET_BYTES:
        raise recipe_error(name, f"asset {asset!r} is larger than {MAX_ASSET_BYTES // 1024} KiB")
    return text
