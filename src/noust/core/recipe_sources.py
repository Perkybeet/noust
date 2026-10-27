# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the code Noust prepares from a recipe is kept, and how to tell it from any other directory.

A recipe has no repository: Noust writes the files of a template source and a Compose recipe's
document into a folder of its own, ``<store directory>/recipe-sources/<app>``, and a deployment
copies its tree from there, as does every later update. For the origins a server allows code from
(:mod:`noust.core.ens.origins`) that folder is its own kind, ``recipe``, and not "a directory on
this server": otherwise a server that names its repositories could not install anything from the
catalogue without allowing every directory it has.

The folder is beside the store, wherever the store is (``/var/lib/noust``, or ``~/.local/share/noust``
on a server that was set up that way), so the location is asked of the store and not of a constant.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from noust.core.store import NoustStore, get_store

#: The directory, beside the store, that holds the sources rendered from a recipe.
RECIPE_SOURCES_DIR = "recipe-sources"

#: What an application's name is, which names its folder (the domain with dots as dashes).
_APP_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def recipe_sources_root(store: NoustStore | None = None) -> Path:
    """
    Say where every recipe source is kept.

    Args:
        store: The store, beside which it lives; the process-wide one by default.

    Returns:
        ``<store directory>/recipe-sources``.
    """
    return (store or get_store()).db_path.parent / RECIPE_SOURCES_DIR


def recipe_source_dir(app_name: str, store: NoustStore | None = None) -> Path:
    """
    Say where the source rendered from a recipe is kept for an application.

    Args:
        app_name: The application name.
        store: The store, beside which it lives.

    Returns:
        ``<store directory>/recipe-sources/<app_name>``.
    """
    return recipe_sources_root(store) / app_name


def is_recipe_tree(path: str | os.PathLike[str], store: NoustStore | None = None) -> bool:
    """
    Tell whether a path is the folder a recipe's source was rendered into.

    Args:
        path: A path as a source records it, already resolved.
        store: The store, beside which the folders live.

    Returns:
        True when it is a direct child of the recipe sources folder named like an application.
    """
    candidate = Path(path)
    if _APP_NAME.fullmatch(candidate.name) is None:
        return False
    root = recipe_sources_root(store)
    return candidate.parent == root or candidate.parent == root.resolve()
