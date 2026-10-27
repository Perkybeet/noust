# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The packages that load their re-exports on first use still offer every name they always did.

``noust``, ``noust.core``, ``noust.managers``, ``noust.deployers``, ``noust.deployers.helpers``
and ``noust.cli.commands`` re-export classes from their submodules. Doing it with imports at the
top made every import of anything below them load all of it, ``noust --version`` included; they
now answer through ``lazy_exports`` (PEP 562). What a script wrote, ``from noust import Config``,
must keep working, and the three places that list a name (the runtime table, the ``TYPE_CHECKING``
block that the type checker reads and ``__all__``) must agree.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import pytest

import noust
from noust.core.lazy_exports import lazy_exports

SRC = Path(noust.__file__).resolve().parent

PACKAGES = (
    "noust",
    "noust.core",
)


def _type_checking_names(package: str) -> dict[str, str]:
    """
    Read the names a package's ``TYPE_CHECKING`` block imports.

    Args:
        package: The dotted name of a package.

    Returns:
        Each name to the module it comes from.
    """
    init = SRC.parent / Path(*package.split(".")) / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            for statement in node.body:
                assert isinstance(statement, ast.ImportFrom) and statement.module
                for alias in statement.names:
                    found[alias.name] = statement.module
    return found


class TestWhatScriptsImport:
    """The names that were imports before are still importable from the same place."""

    def test_the_names_of_the_top_package(self) -> None:
        """``from noust import Config, Logger, NoustError`` returns the classes."""
        from noust import Config, Logger, NoustError
        from noust.core.config import Config as RealConfig
        from noust.core.exceptions import NoustError as RealError
        from noust.core.logger import Logger as RealLogger

        assert (Config, Logger, NoustError) == (RealConfig, RealLogger, RealError)

    def test_the_names_of_the_core_package(self) -> None:
        """``from noust.core import get_store, Notifier`` returns what the store defines."""
        from noust.core import Config, Notifier, get_store
        from noust.core.config import Config as RealConfig
        from noust.core.notifier import Notifier as RealNotifier
        from noust.core.store import get_store as real_get_store

        assert (Config, Notifier, get_store) == (RealConfig, RealNotifier, real_get_store)

    @pytest.mark.parametrize("package", PACKAGES)
    def test_every_name_of_all_is_the_object_of_its_module(self, package: str) -> None:
        """What ``__all__`` lists resolves to the very object its defining module has."""
        module = importlib.import_module(package)
        origins = _type_checking_names(package)
        names = [name for name in module.__all__ if not name.startswith("__")]
        assert sorted(origins) == sorted(names), (
            f"{package}: __all__ and the TYPE_CHECKING block name different things"
        )
        for name in names:
            real = getattr(importlib.import_module(origins[name]), name)
            assert getattr(module, name) is real, f"{package}.{name}"

    @pytest.mark.parametrize("package", PACKAGES)
    def test_a_star_import_still_works(self, package: str) -> None:
        """``from package import *`` loads every name ``__all__`` lists."""
        namespace: dict[str, Any] = {}
        exec(f"from {package} import *", namespace)  # noqa: S102
        module = importlib.import_module(package)
        assert set(module.__all__) <= set(namespace)

    @pytest.mark.parametrize("package", PACKAGES)
    def test_the_names_are_listed_by_dir(self, package: str) -> None:
        """An editor or a REPL completes the names before they are loaded."""
        module = importlib.import_module(package)
        assert set(module.__all__) <= set(dir(module))


class TestWhatItDoesNotHide:
    """The hook answers for its own names and for nothing else."""

    @pytest.mark.parametrize("package", PACKAGES)
    def test_an_unknown_name_is_an_attribute_error(self, package: str) -> None:
        """``hasattr`` and ``getattr`` with a default see a missing name as missing."""
        module = importlib.import_module(package)
        assert not hasattr(module, "no_such_name_anywhere")
        assert getattr(module, "no_such_name_anywhere", 7) == 7

    def test_a_submodule_is_still_taken_by_a_from_import(self) -> None:
        """``from noust.core import runner`` finds the module, not an attribute error."""
        from noust.core import runner

        assert runner.__name__ == "noust.core.runner"

    def test_the_value_is_kept_after_the_first_read(self) -> None:
        """The hook runs once for a name: the second read is a plain attribute."""
        namespace: dict[str, Any] = {"__name__": "example"}
        hook, _listing = lazy_exports(namespace, {"sep": "os.path"})
        assert "sep" not in namespace
        value = hook("sep")
        assert namespace["sep"] == value

    def test_the_error_names_the_package_and_the_name(self) -> None:
        """A typo says what was asked and of whom."""
        namespace: dict[str, Any] = {"__name__": "example"}
        hook, _listing = lazy_exports(namespace, {})
        with pytest.raises(AttributeError, match=r"example.*nothing"):
            hook("nothing")

    def test_dir_lists_the_loaded_and_the_waiting_names(self) -> None:
        """The listing is the namespace plus the table, sorted."""
        namespace: dict[str, Any] = {"__name__": "example", "z": 1}
        _hook, listing = lazy_exports(namespace, {"a": "os"})
        assert listing() == ["__name__", "a", "z"]
