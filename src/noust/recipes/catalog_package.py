# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A signed package of recipes that grows the catalogue between releases of Noust."""

from __future__ import annotations

from pathlib import Path

from noust.core import paths


def catalog_directory() -> Path:
    """
    Say where the imported catalogue package is unpacked.

    Returns:
        ``recipe-catalog/current`` under Noust's state directory.
    """
    return paths.state_dir() / "recipe-catalog" / "current"
