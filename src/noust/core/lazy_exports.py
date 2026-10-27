# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Names a package offers that load their module when somebody asks for them (PEP 562).

``import noust`` and ``import noust.core.anything`` run the package's ``__init__``, and an
``__init__`` that imports the configuration, the store and the notifier to re-export their
classes makes every command line, ``noust --version`` included, pay for ninety modules it never
touches. A package that wants to keep offering ``from noust import Config`` declares the names
here instead, and the module behind each is imported by the first one who reads it.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from typing import Any


def lazy_exports(
    namespace: dict[str, Any], exports: Mapping[str, str]
) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """
    Build the ``__getattr__`` and ``__dir__`` of a package whose names load on first use.

    The caller assigns both in its own namespace, and lists the same names under ``TYPE_CHECKING``
    so that a type checker and an editor still see them as what they are.

    Args:
        namespace: The package's ``globals()``. The value is stored there on its first read, so
            the hook runs once per name and a later read is an ordinary attribute.
        exports: Each name the package offers, to the dotted module that defines it.

    Returns:
        The two functions, in the order a module defines them.
    """
    package = str(namespace["__name__"])

    def module_getattr(name: str) -> Any:
        module = exports.get(name)
        if module is None:
            # Not a name of ours: the import system goes on to try it as a submodule, which is
            # how ``from noust.core import runner`` keeps working.
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(module), name)
        namespace[name] = value
        return value

    def module_dir() -> list[str]:
        return sorted({*namespace, *exports})

    return module_getattr, module_dir
