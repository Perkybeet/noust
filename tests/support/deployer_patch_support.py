# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Replacing a name of the base deployer in a test.

``BaseDeployer`` is a chain of classes, one module each (``noust/deployers/base/``), and a name the
code uses (``wait_until_healthy``, ``NginxManager``, ``hand_over_tree``) is looked up in the module
of the layer that uses it. Replacing it on the package would change nothing the code reads, so this
replaces it in every layer that has it; ``tests/test_deployer_patch_support.py`` holds that it does.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Any

import pytest

import noust.deployers.base as base_package


def layers() -> list[ModuleType]:
    """
    Import the module of every layer of the base deployer.

    Returns:
        The modules, in the order the package lists them.
    """
    return [
        importlib.import_module(f"{base_package.__name__}.{info.name}")
        for info in pkgutil.iter_modules(base_package.__path__)
    ]


def patch_deployer(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """
    Replace a name wherever a layer of the base deployer looks it up.

    Args:
        monkeypatch: The test's patcher, so the name is put back when it ends.
        name: The attribute: an import of one of the layers, or a constant of theirs.
        value: What to put in its place.

    Raises:
        AttributeError: When no layer has the name, which is a replacement that would test
            nothing.
    """
    holders = [module for module in layers() if hasattr(module, name)]
    if not holders:
        raise AttributeError(f"no layer of {base_package.__name__} has {name!r}")
    for module in holders:
        monkeypatch.setattr(module, name, value)
