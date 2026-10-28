# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A test replaces a name where the code under test looks it up.

A module that became a package imports each of its old names in its ``__init__``, so everything
that imports them keeps working. A test that replaces one of those names on the package does not
fail and does not test what it meant to: the code reads the name from the submodule that
defines it, and the replacement sits where nobody looks (``tests/patch_scan_support.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import noust
from tests.patch_scan_support import PatchSite, patches_in_tree, patches_of

ROOT = Path(noust.__file__).resolve().parent.parent.parent

#: The packages that were modules, each with the replacements on the package that are right and
#: why. A replacement belongs here when the code under test imports the name from the package
#: when it runs (inside a function), which reads what the test put there.
SPLIT_PACKAGES: dict[str, dict[tuple[str, str], str]] = {
    "noust.deployers.base": {},
    "noust.web.auth": {
        ("tests/test_names_surfaces.py", "get_global_token_manager"): (
            "NameMigration imports it from noust.web.auth inside the function that uses it"
        ),
    },
}


def reexported(package: str, root: Path = ROOT) -> set[str]:
    """
    List the names a package's ``__init__`` imports from the modules below it.

    Args:
        package: The dotted name of the package.
        root: The repository root.

    Returns:
        The names the package re-exports, including the submodules it imports.
    """
    init = root / "src" / Path(*package.split(".")) / "__init__.py"
    names: set[str] = set()
    for node in ast.parse(init.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(package + "."):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def sites_on_the_package(package: str) -> list[PatchSite]:
    """
    Find the replacements of a name the package only re-exports.

    Args:
        package: The dotted name of the package.

    Returns:
        Each site in the tests and the scripts that replaces such a name on the package.
    """
    names = reexported(package)
    return [
        site
        for site in patches_in_tree(ROOT, ("tests", "scripts"), package)
        if site.name.split(".")[0] in names
    ]


@pytest.mark.parametrize("package", sorted(SPLIT_PACKAGES))
def test_no_test_replaces_a_name_on_a_package_that_only_re_exports_it(package: str) -> None:
    allowed = SPLIT_PACKAGES[package]
    offenders = [
        site for site in sites_on_the_package(package) if (site.path, site.name) not in allowed
    ]

    assert not offenders, (
        f"These tests replace a name of {package} that the package only re-exports; the code "
        "reads it in the module that defines it, so replace it there:\n"
        + "\n".join(f"  {site.path}:{site.line}  {package}.{site.name}" for site in offenders)
    )


@pytest.mark.parametrize("package", sorted(SPLIT_PACKAGES))
def test_the_replacements_that_are_allowed_are_still_made(package: str) -> None:
    """The list of exceptions only gets shorter: one that nobody makes any more comes off."""
    made = {(site.path, site.name) for site in sites_on_the_package(package)}

    stale = sorted(set(SPLIT_PACKAGES[package]) - made)

    assert not stale, f"Take these off SPLIT_PACKAGES in tests/test_patch_targets.py: {stale}"


class TestTheScan:
    """The reading finds the three ways a test replaces an attribute, and nothing else."""

    SOURCE = (
        "import pytest\n"
        "from pkg import mod as alias\n"
        "import pkg.mod\n"
        "def test_a(monkeypatch):\n"
        "    monkeypatch.setattr('pkg.mod.by_path', 1)\n"
        "    monkeypatch.setattr(alias, 'by_alias', 1)\n"
        "    monkeypatch.setattr(pkg.mod, 'by_dotted', 1)\n"
        "    alias.by_assignment = 1\n"
        "    mock.patch('pkg.mod.deep.name')\n"
        "    monkeypatch.setattr('pkg.other.not_this', 1)\n"
        "    monkeypatch.setattr(other, 'not_this_either', 1)\n"
        "    alias.read_only_attribute\n"
    )

    def test_it_finds_a_string_path_a_module_and_an_assignment(self) -> None:
        sites = patches_of(ast.parse(self.SOURCE), "pkg.mod", "tests/x.py")

        assert sorted(site.name for site in sites) == [
            "by_alias",
            "by_assignment",
            "by_dotted",
            "by_path",
            "deep.name",
        ]

    def test_a_package_that_only_re_exports_is_told_from_one_that_defines(
        self, tmp_path: Path
    ) -> None:
        init = tmp_path / "src" / "pkg" / "__init__.py"
        init.parent.mkdir(parents=True)
        init.write_text(
            "from pkg.sub import moved, other as renamed\nfrom os import path\ndefined_here = 1\n",
            encoding="utf-8",
        )

        assert reexported("pkg", tmp_path) == {"moved", "renamed"}
