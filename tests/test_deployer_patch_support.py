# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``BaseDeployer`` is a chain of classes, one module each, and a test replaces a name where its layer
looks it up.

The base deployer was one class of 4800 lines and is now ``noust/deployers/base/``: each module a
class that builds on the one before, so a layer calls what it defines or inherits and mypy checks
it. What has to hold of that is the chain itself, that a member is defined in one layer only
(otherwise a lower one is hidden by a higher), and that the tests replace names through
``tests/deployer_patch_support.py``.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

import noust
from noust.deployers.base import BaseDeployer
from tests.deployer_patch_support import layers, patch_deployer
from tests.patch_scan_support import patches_in_tree

ROOT = Path(noust.__file__).resolve().parent.parent.parent
PACKAGE = "noust.deployers.base"


def layer_classes() -> list[type]:
    """
    List the classes of the chain, the one that builds on nothing of the package first.

    Returns:
        The classes defined in the layers' modules, lowest first.
    """
    chain = [
        klass for klass in reversed(BaseDeployer.__mro__) if klass.__module__.startswith(PACKAGE)
    ]
    return [klass for klass in chain if klass is not BaseDeployer]


class TestTheChain:
    def test_each_layer_builds_on_the_one_before(self) -> None:
        chain = layer_classes()

        assert len(chain) == len(layers())
        for below, above in itertools.pairwise(chain):
            assert above.__bases__ == (below,)

    def test_the_final_class_adds_nothing_to_the_last_layer(self) -> None:
        own = {name for name in vars(BaseDeployer) if not name.startswith("_abc")} - {
            name for name in vars(BaseDeployer) if name.startswith("__")
        }

        assert own == set()
        assert BaseDeployer.__bases__ == (layer_classes()[-1],)

    def test_a_member_is_defined_in_one_layer_only(self) -> None:
        """A name in two layers hides the lower one: the higher always wins, and nobody sees it."""
        seen: dict[str, str] = {}
        twice = []
        for klass in layer_classes():
            for name, member in vars(klass).items():
                if name.startswith("__") or not (callable(member) or isinstance(member, property)):
                    continue
                if name in seen:
                    twice.append(f"{name}: {seen[name]} and {klass.__name__}")
                seen[name] = klass.__name__

        assert twice == []

    def test_the_deployer_is_still_abstract_in_what_a_type_fills_in(self) -> None:
        assert BaseDeployer.__abstractmethods__ == frozenset(
            {"detect", "get_install_command", "get_build_command", "get_start_command"}
        )

    def test_it_is_still_the_class_the_package_names(self) -> None:
        assert BaseDeployer.__module__ == PACKAGE
        assert BaseDeployer.__qualname__ == "BaseDeployer"


class TestPatchDeployer:
    def test_a_name_is_replaced_in_every_layer_that_looks_it_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.deployers.base import release, site_service

        patch_deployer(monkeypatch, "wait_until_healthy", lambda *args, **kwargs: "replaced")

        assert release.wait_until_healthy() == "replaced"
        assert site_service.wait_until_healthy() == "replaced"

    def test_the_name_is_put_back(self) -> None:
        from noust.deployers.base import release

        original = release.wait_until_healthy
        with pytest.MonkeyPatch.context() as patch:
            patch_deployer(patch, "wait_until_healthy", lambda *args, **kwargs: None)

        assert release.wait_until_healthy is original

    def test_a_name_no_layer_has_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(AttributeError, match="no_such_name"):
            patch_deployer(monkeypatch, "no_such_name", 1)


def test_no_test_replaces_a_name_on_the_package_by_hand() -> None:
    """The package holds the class and the constants; what the layers use is in the layers."""
    offenders = [
        f"{site.path}:{site.line}  {PACKAGE}.{site.name}"
        for site in patches_in_tree(ROOT, ("tests",), PACKAGE)
        if not site.name.startswith("BaseDeployer.")
    ]

    assert not offenders, "Use patch_deployer (tests/deployer_patch_support.py):\n  " + "\n  ".join(
        offenders
    )
