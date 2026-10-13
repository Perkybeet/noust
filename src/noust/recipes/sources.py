# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the catalogue's recipes come from: the package, a catalogue package, the operator.

Three places hold recipes, and they are read through the same closed schema:

- ``shipped``: the files in this package, which cannot change while Noust runs.
- ``catalog``: a signed package of newer recipes the operator imported
  (:mod:`noust.recipes.catalog_package`), which may supersede a shipped recipe
  of the same name.
- ``operator``: the operator's own folder (``/etc/noust/recipes``), for the
  recipes an agency or a company keeps for itself. A name the package or a
  catalogue package already has is refused: a private recipe is a recipe of
  its own, not a replacement that changes what an installed application
  updates to.

A recipe is a ``<name>.yaml`` and, beside it, ``assets/<name>/...``. Recipes
read from disk are read as root, so the folder and its files must belong to
root (or to the user running Noust) and be writable by nobody else, and a
symbolic link is never followed. A file that does not pass is reported as a
:class:`Problem` and skipped; one bad private recipe never takes the catalogue
down.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.recipes.model import NAME_PATTERN, RESERVED_NAMES

#: The largest recipe document read from disk.
MAX_RECIPE_BYTES = 512 * 1024

#: What a catalogue package's folder holds besides recipes: the manifest the
#: package was verified by and its signature.
CATALOG_FILES = frozenset({"manifest.json", "manifest.json.sig"})

#: The folder of the operator's own recipes, named by this variable when it is set.
RECIPES_DIR_ENV = "RECIPES_DIR"


@dataclass(frozen=True)
class RecipeFile:
    """
    One recipe's file and where its assets are.

    Attributes:
        name: The recipe's name (the file name without ``.yaml``).
        origin: One of :data:`noust.recipes.model.ORIGINS`.
        location: The file's path, for an error that names it.
        root: The directory holding ``<name>.yaml`` and ``assets/<name>/``
            (a package resource for a shipped recipe, a path otherwise).
    """

    name: str
    origin: str
    location: str
    root: Any

    def read(self) -> str:
        """
        Read the recipe's document.

        Returns:
            Its text.

        Raises:
            OSError: The file cannot be read, is a link, or is not a regular file.
            ValueError: It is larger than :data:`MAX_RECIPE_BYTES` or not UTF-8.
        """
        if self.origin == "shipped":
            return str(self.root.joinpath(f"{self.name}.yaml").read_text(encoding="utf-8"))
        return read_regular(Path(self.location))


@dataclass(frozen=True)
class Problem:
    """
    A file in a recipes folder that is not a usable recipe.

    Attributes:
        name: The recipe's name, or the file's, when it has no valid name.
        origin: Where it was found.
        location: Its path.
        error: What is wrong, in a sentence.
        details: What to do about it.
    """

    name: str
    origin: str
    location: str
    error: str
    details: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """
        Describe the problem for the API and the command line.

        Returns:
            Name, origin, location, error and details.
        """
        return {
            "name": self.name,
            "origin": self.origin,
            "location": self.location,
            "error": self.error,
            "details": self.details,
        }


def operator_directory() -> Path:
    """
    Say where the operator's own recipes are.

    Returns:
        ``NOUST_RECIPES_DIR`` when set, else ``recipes`` under Noust's
        configuration directory (``/etc/noust/recipes``).
    """
    named = paths.getenv(RECIPES_DIR_ENV)
    return Path(named) if named else paths.config_dir() / "recipes"


def read_regular(path: Path) -> str:
    """
    Read a small text file that must be a regular file and not a link.

    Args:
        path: The file.

    Returns:
        Its text.

    Raises:
        OSError: It cannot be opened without following a link, or is not a
            regular file.
        ValueError: It is larger than :data:`MAX_RECIPE_BYTES` or not UTF-8.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"{path} is not a regular file")
        data = handle.read(MAX_RECIPE_BYTES + 1)
    if len(data) > MAX_RECIPE_BYTES:
        raise ValueError(f"{path} is larger than {MAX_RECIPE_BYTES // 1024} KiB")
    return data.decode("utf-8")


def untrusted_reason(info: os.stat_result, *, kind: str) -> str | None:
    """
    Say why a file or folder of recipes cannot be trusted, if it cannot.

    Args:
        info: ``lstat`` of it.
        kind: ``folder`` or ``file``, for the sentence.

    Returns:
        The reason, or None when it may be read.
    """
    if stat.S_ISLNK(info.st_mode):
        return f"the {kind} is a symbolic link"
    if info.st_uid not in (0, os.getuid()):
        return f"the {kind} belongs to a user other than root"
    if info.st_mode & 0o022:
        return f"the {kind} can be written by its group or by everyone"
    return None


def shipped_files() -> dict[str, RecipeFile]:
    """
    List the recipes the package ships.

    Returns:
        Recipe name to file.
    """
    root = resources.files("noust.recipes")
    return {
        entry.name.removesuffix(".yaml"): RecipeFile(
            name=entry.name.removesuffix(".yaml"),
            origin="shipped",
            location=f"src/noust/recipes/{entry.name}",
            root=root,
        )
        for entry in root.iterdir()
        if entry.name.endswith(".yaml") and entry.is_file()
    }


def folder_files(
    directory: Path, origin: str, *, taken: dict[str, str], trust_owner: bool = False
) -> tuple[dict[str, RecipeFile], list[Problem]]:
    """
    List the recipes of a folder on disk, and the files that are not usable.

    Args:
        directory: The folder.
        origin: ``operator`` or ``catalog``.
        taken: Names already held by a source that outranks this one, to the
            origin that holds them. A name in it is a problem here.
        trust_owner: Skip the ownership and permission checks, for a folder the
            caller is only reading to lint (a contributor's checkout), never for
            one whose recipes will be deployed.

    Returns:
        The usable recipes by name, and the problems, one per rejected file. A
        folder that does not exist is empty and no problem.
    """
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return {}, []
    except OSError as exc:
        return {}, [Problem("(folder)", origin, str(directory), f"cannot be read: {exc}")]
    reason = None if trust_owner else untrusted_reason(info, kind="folder")
    if reason is not None or not stat.S_ISDIR(info.st_mode):
        return {}, [
            Problem(
                "(folder)",
                origin,
                str(directory),
                reason or "it is not a folder",
                f"Recipes in it are not read. Fix it with: chown root:root {directory} && "
                f"chmod 755 {directory}",
            )
        ]
    found: dict[str, RecipeFile] = {}
    problems: list[Problem] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.name == "assets" or entry.name.startswith("."):
            continue
        if origin == "catalog" and entry.name in CATALOG_FILES:
            continue
        if not entry.name.endswith(".yaml"):
            problems.append(
                Problem(
                    entry.name,
                    origin,
                    str(entry),
                    "only <name>.yaml files and an assets folder are recipes",
                )
            )
            continue
        name = entry.name.removesuffix(".yaml")
        if not NAME_PATTERN.match(name) or name in RESERVED_NAMES:
            problems.append(
                Problem(
                    name,
                    origin,
                    str(entry),
                    "the name must be lower case letters, digits and dashes, up to 40",
                    "Rename the file.",
                )
            )
            continue
        try:
            file_info = entry.lstat()
        except OSError as exc:
            problems.append(Problem(name, origin, str(entry), f"cannot be read: {exc}"))
            continue
        why = None if trust_owner else untrusted_reason(file_info, kind="file")
        if why is None and not stat.S_ISREG(file_info.st_mode):
            why = "it is not a regular file"
        if why is not None:
            problems.append(
                Problem(name, origin, str(entry), why, f"Fix it with: chmod 644 {entry}")
            )
            continue
        if name in taken:
            problems.append(
                Problem(
                    name,
                    origin,
                    str(entry),
                    f"the name belongs to a {taken[name]} recipe",
                    "A private recipe has a name of its own; rename the file and the name: "
                    "inside it.",
                )
            )
            continue
        found[name] = RecipeFile(
            name=name,
            origin=origin,
            location=str(entry),
            root=directory,
        )
    return found, problems
