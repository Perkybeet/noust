# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What OBS is given, and the entries that say so, name things that exist.

A line that excludes a file nobody has is not harmless: it reads as the reason a file is not
there, and the next person looks for the file that is missing instead of the one that is not.
And a directory the tarball leaves out must be one no recipe reads, or the package that builds
today stops building in OBS, where nobody is watching, on the day somebody adds a line to a recipe.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from tests.tree_support import tree_files

REPO = Path(__file__).resolve().parent.parent

#: What `packaging/obs/stage.sh` leaves out of the tarball, besides the `debian/` it makes itself.
STAGE_EXCLUDES = ("sdk", "scripts")

#: What builds the packages from the tarball: the recipes and the files setuptools reads.
RECIPES = (
    "rpm/noust.spec",
    "obs/debian.rules",
    "obs/debian.dirs",
    "obs/debian.docs",
    "obs/debian.postinst",
    "obs/debian.preinst",
    "obs/debian.prerm",
    "obs/debian.postrm",
    "obs/noust.dsc",
    "obs/source/options",
    "packaging/obs/build.sh",
    "setup.py",
    "MANIFEST.in",
)


def read(path: str) -> str:
    """
    Read a repository file.

    Args:
        path: Path relative to the repository root.

    Returns:
        Its text.
    """
    return (REPO / path).read_text(encoding="utf-8")


def tracked_paths() -> list[str]:
    """
    List the repository's files, relative to its root.

    Returns:
        The paths git does not ignore, with ``/`` separators.
    """
    return [path.relative_to(REPO).as_posix() for path in tree_files(REPO)]


def names_something(pattern: str, paths: list[str]) -> bool:
    """
    Tell whether a pattern of ``.gitattributes`` or ``MANIFEST.in`` names a file of the tree.

    Args:
        pattern: A file, a directory (trailing ``/``) or a glob.
        paths: The repository's files.

    Returns:
        True when at least one file is named. A directory is read as one at the root, which is
        what every entry means; a glob with no slash matches a name at any depth.
    """
    if pattern.endswith("/"):
        return any(path.startswith(pattern) for path in paths)
    glob = pattern.replace("**", "*")
    if "/" in glob:
        return any(fnmatch.fnmatchcase(path, glob) for path in paths)
    return any(fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], glob) for path in paths)


def attribute_lines(attribute: str) -> list[str]:
    """
    List the patterns of ``.gitattributes`` that set one attribute.

    Args:
        attribute: ``export-ignore``, ``linguist-generated=true``...

    Returns:
        The patterns, set or unset (a leading ``-`` on the attribute).
    """
    found = []
    for line in read(".gitattributes").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].removeprefix("-") == attribute:
            found.append(parts[0])
    return found


class TestWhatOBSIsGiven:
    """The tarball the packages are built from."""

    def test_there_is_one_copy_of_the_manual(self):
        """The packages install ``man/noust.1``; a second copy in ``obs/`` drifted from it once."""
        assert [path for path in tracked_paths() if path.endswith("noust.1")] == ["man/noust.1"]

    def test_stage_leaves_out_what_it_says_and_no_recipe_reads_it(self):
        stage = read("packaging/obs/stage.sh")

        assert set(re.findall(r"':!([\w.-]+)'", stage)) == {"debian", *STAGE_EXCLUDES}
        for recipe in RECIPES:
            text = read(recipe).split("\n%changelog\n")[0]
            code = "\n".join(re.sub(r"#.*", "", line) for line in text.splitlines())
            for name in STAGE_EXCLUDES:
                assert not re.search(rf"(?<![\w/.-]){name}/", code), (
                    f"{recipe} reads {name}/, which the OBS tarball leaves out"
                )

    def test_what_stage_leaves_out_is_still_in_the_repository(self):
        """It is the tarball that is smaller, not the project: the generators and the console stay."""
        paths = tracked_paths()

        for name in STAGE_EXCLUDES:
            assert any(path.startswith(f"{name}/") for path in paths), name


class TestEntriesThatNameFiles:
    """Every exclusion and every marker points at something."""

    def test_every_export_ignore_names_a_file_that_exists(self):
        paths = tracked_paths()
        dead = [p for p in attribute_lines("export-ignore") if not names_something(p, paths)]

        assert not dead, f".gitattributes excludes what is not in the tree: {dead}"

    def test_every_generated_marker_names_a_file_that_exists(self):
        paths = tracked_paths()
        dead = [
            p for p in attribute_lines("linguist-generated=true") if not names_something(p, paths)
        ]

        assert not dead, f".gitattributes marks as generated what is not in the tree: {dead}"

    def test_every_manifest_entry_names_a_file_that_exists(self):
        paths = tracked_paths()
        dead = []
        for line in read("MANIFEST.in").splitlines():
            words = line.split("#")[0].split()
            if not words or words[0] == "global-exclude":
                continue
            command, args = words[0], words[1:]
            if command in {"include", "exclude"}:
                dead += [a for a in args if not names_something(a, paths)]
            elif command in {"recursive-include", "recursive-exclude"}:
                if not names_something(args[0].rstrip("/") + "/", paths):
                    dead.append(args[0])

        assert not dead, f"MANIFEST.in names what is not in the tree: {dead}"

    def test_the_matching_sees_a_dead_entry(self):
        """The three checks above are only as good as this."""
        paths = ["src/noust/a.py", "docs/guide.md", "Dockerfile.dev"]

        assert names_something("src/", paths)
        assert names_something("docs/*.md", paths)
        assert names_something("Dockerfile*", paths)
        assert not names_something("docker/", paths)
        assert not names_something("pytest.ini", paths)
        assert not names_something("build-and-upload-obs.sh", paths)


class TestTheMakefile:
    """`make` is a convenience; one that runs a tool nobody installs teaches nobody to trust it."""

    def test_it_names_real_targets_and_runs_the_tools_the_project_uses(self):
        makefile = read("Makefile")
        targets = set(re.findall(r"^([a-z][\w-]*):", makefile, re.MULTILINE))
        declared = re.search(r"^\.PHONY:(.*)$", makefile, re.MULTILINE)
        assert declared, "the Makefile declares no .PHONY targets"
        commands = " ".join(line for line in makefile.splitlines() if line.startswith("\t"))

        assert set(declared.group(1).split()) == targets
        # Formatting is `ruff format` (CONTRIBUTING.md, "Commands"); black and isort are not in
        # the development extras, so `make format` was a missing command.
        assert not re.search(r"\b(black|isort)\b", commands)
        assert "-n auto" not in commands, "with psutil `auto` counts physical cores: use logical"


def test_the_consoles_source_goes_with_its_build() -> None:
    """The packages ship the console's compiled build, so the source package carries panel/."""
    assert "':!panel'" not in read("packaging/obs/stage.sh")
