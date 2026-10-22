# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The operator's own recipes: check one, add one to the folder, take one out.

A recipe of the operator's is the catalogue's recipe in every way: the same
closed schema, the same digest-pinned images, the same logo rules. What it is
not is the package's, so it lives in :func:`noust.recipes.sources.operator_directory`
and is added from a path the operator names (a file, or a folder laid out as
the catalogue's own: ``<name>.yaml`` and ``assets/<name>/...``) after it has
passed the schema, and a name the package already holds is refused.

Nothing here follows a link or accepts a file larger than a recipe can be: the
source is untrusted input even when the operator is the one handing it over,
because it often came from a colleague or a repository.
"""

from __future__ import annotations

import dataclasses
import os
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from noust.core import audit
from noust.core.fs import get_fs
from noust.recipes.assets import MAX_ASSET_BYTES, is_plain_path, set_asset_root
from noust.recipes.catalog import catalogue_files
from noust.recipes.model import NAME_PATTERN, Recipe, RecipeError, RecipeNotFoundError, set_location
from noust.recipes.schema import parse_recipe
from noust.recipes.sources import MAX_RECIPE_BYTES, operator_directory, read_regular

#: The most files one recipe's assets may be, and the most they may weigh together.
MAX_ASSET_FILES = 40
MAX_ASSET_TOTAL = 2 * 1024 * 1024


@dataclass(frozen=True)
class RecipeBundle:
    """
    A recipe read from a path, with the assets that go with it.

    Attributes:
        recipe: The validated recipe.
        text: Its document, exactly as the operator wrote it.
        assets: Asset path under ``assets/<name>/`` to its text.
    """

    recipe: Recipe
    text: str
    assets: dict[str, str]


def _candidates(source: Path) -> list[tuple[str, Path, Path]]:
    """
    Find the recipe files at a path.

    Args:
        source: A ``.yaml`` file, or a folder laid out as the catalogue is.

    Returns:
        ``(name, file, root)`` for each: ``root`` is the folder that holds
        ``assets/<name>/``.

    Raises:
        RecipeError: The path is missing, a link, or holds no recipe.
    """
    try:
        info = source.lstat()
    except OSError as exc:
        raise RecipeError(f"{source} cannot be read: {exc}", details="Name a recipe file.") from exc
    if stat.S_ISLNK(info.st_mode):
        raise RecipeError(
            f"{source} is a symbolic link",
            details="Name the file itself: a link is never followed.",
        )
    if stat.S_ISDIR(info.st_mode):
        files = sorted(
            entry for entry in source.iterdir() if entry.name.endswith(".yaml") and entry.is_file()
        )
        root = source
    elif source.name.endswith(".yaml"):
        files, root = [source], source.parent
    else:
        raise RecipeError(
            f"{source} is not a recipe file",
            details="A recipe is a <name>.yaml file, or a folder of them with their assets.",
        )
    if not files:
        raise RecipeError(f"{source} holds no <name>.yaml recipe", details="Nothing to add.")
    return [(entry.name.removesuffix(".yaml"), entry, root) for entry in files]


def _read_assets(name: str, root: Path) -> dict[str, str]:
    """
    Read the files under ``assets/<name>/`` that a recipe may use.

    Args:
        name: The recipe.
        root: The folder holding ``assets/``.

    Returns:
        Asset path to text.

    Raises:
        RecipeError: A link, an entry that is not a file, too many files or
            too many bytes, or a file that is not text.
    """
    base = root / "assets" / name
    if not base.exists() and not base.is_symlink():
        return {}
    if base.is_symlink() or not base.is_dir():
        raise RecipeError(f"assets/{name} is a link or not a folder", details="Nothing is copied.")
    assets: dict[str, str] = {}
    total = 0
    for directory, folders, names in os.walk(base, followlinks=False):
        for folder in folders:
            if (Path(directory) / folder).is_symlink():
                raise RecipeError(
                    f"{Path(directory) / folder} is a symbolic link", details="Nothing is copied."
                )
        for file_name in names:
            path = Path(directory) / file_name
            relative = path.relative_to(base).as_posix()
            try:
                text = read_regular(path)
            except (OSError, ValueError) as exc:
                raise RecipeError(
                    f"asset {relative} cannot be read as a plain text file: {exc}",
                    details="Links are never followed, and an asset is text under 256 KiB.",
                ) from exc
            if len(text.encode()) > MAX_ASSET_BYTES:
                raise RecipeError(
                    f"asset {relative} is larger than {MAX_ASSET_BYTES // 1024} KiB",
                    details="Nothing is copied.",
                )
            assets[relative] = text
            total += len(text.encode())
    if len(assets) > MAX_ASSET_FILES or total > MAX_ASSET_TOTAL:
        raise RecipeError(
            f"the assets of {name} are too many or too large",
            details=f"At most {MAX_ASSET_FILES} files and {MAX_ASSET_TOTAL // 1024} KiB.",
        )
    return assets


def read_bundles(source: Path) -> list[RecipeBundle]:
    """
    Read and validate the recipes at a path, changing nothing.

    Args:
        source: A recipe file, or a folder of them.

    Returns:
        Each recipe with its document and assets.

    Raises:
        RecipeError: A recipe is not valid, has the name of a shipped one, or
            the path holds something a recipe may not.
    """
    taken = {
        name: entry.origin
        for name, entry in catalogue_files()[0].items()
        if entry.origin != "operator"
    }
    bundles: list[RecipeBundle] = []
    for name, file, root in _candidates(source):
        if not NAME_PATTERN.match(name):
            raise RecipeError(
                f"{file.name}: a recipe's name is lower case letters, digits and dashes, up to 40",
                details="Rename the file and its name: key.",
            )
        if name in taken:
            raise RecipeError(
                f"{name} is the name of a {taken[name]} recipe",
                details="A private recipe has a name of its own. Rename the file and the "
                "name: inside it.",
            )
        try:
            text = read_regular(file)
        except (OSError, ValueError) as exc:
            raise RecipeError(f"{file} cannot be read: {exc}", details="Nothing is added.") from exc
        if len(text.encode()) > MAX_RECIPE_BYTES:
            raise RecipeError(f"{file} is too large", details="Nothing is added.")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RecipeError(
                f"{file} is not valid YAML: {exc}", details="Nothing is added."
            ) from exc
        assets = _read_assets(name, root)
        set_asset_root(name, root)
        set_location(name, str(file))
        try:
            recipe = parse_recipe(name, data)
        finally:
            # Back to what the catalogue knows: this source is not its recipe yet.
            origin = catalogue_files()[0].get(name)
            if origin is None:
                set_asset_root(name, None)
                set_location(name, None)
        recipe = dataclasses.replace(recipe, origin="operator")
        bundles.append(RecipeBundle(recipe=recipe, text=text, assets=assets))
    return bundles


def add_recipes(source: Path, *, replace: bool = False, actor: str | None = None) -> list[Recipe]:
    """
    Add the recipes at a path to the operator's folder.

    Args:
        source: A recipe file, or a folder of them.
        replace: Overwrite a private recipe of the same name.
        actor: Who is adding, for the audit trail.

    Returns:
        The recipes, as the catalogue now has them.

    Raises:
        RecipeError: A recipe is not valid, a name is taken, or a private
            recipe of that name exists and ``replace`` is not set.
    """
    bundles = read_bundles(source)
    directory = operator_directory()
    existing = {name for name, entry in catalogue_files()[0].items() if entry.origin == "operator"}
    clashes = sorted(bundle.recipe.name for bundle in bundles if bundle.recipe.name in existing)
    if clashes and not replace:
        raise RecipeError(
            f"{', '.join(clashes)} already exists in {directory}",
            details="Add it with --replace to overwrite it. Nothing was added.",
        )
    fs = get_fs()
    fs.make_dir(directory, mode=0o755)
    for bundle in bundles:
        name = bundle.recipe.name
        fs.write_text(directory / f"{name}.yaml", bundle.text, mode=0o644)
        # Whatever the folder held under this name is replaced, not merged: a
        # stale asset must not survive into the recipe that no longer names it.
        if (directory / "assets" / name).exists():
            fs.remove_tree(directory / "assets" / name)
        for asset, text in bundle.assets.items():
            fs.make_dir((directory / "assets" / name / asset).parent, mode=0o755)
            fs.write_text(directory / "assets" / name / asset, text, mode=0o644)
        audit.record(
            "recipe.private.add",
            target=f"recipe:{name}",
            details={"version": bundle.recipe.version, "replaced": name in existing},
        )
    return [bundle.recipe for bundle in bundles]


def remove_recipe(name: str) -> None:
    """
    Take a private recipe out of the operator's folder.

    Applications already installed from it keep running; they are reported as
    having no recipe to update against until it is added again.

    Args:
        name: The recipe's name.

    Raises:
        RecipeNotFoundError: No private recipe has that name.
        RecipeError: The recipe is the package's, which cannot be removed here.
    """
    files = catalogue_files()[0]
    entry = files.get(name)
    if entry is None:
        raise RecipeNotFoundError(
            f"No recipe named {name!r}", details="See: noust recipe list --origin operator"
        )
    if entry.origin != "operator":
        raise RecipeError(
            f"{name} is a {entry.origin} recipe, not one of yours",
            details="Only recipes added to the operator's folder can be removed.",
        )
    directory = operator_directory()
    fs = get_fs()
    fs.remove(directory / f"{name}.yaml")
    if (directory / "assets" / name).exists():
        fs.remove_tree(directory / "assets" / name)
    set_asset_root(name, None)
    set_location(name, None)
    audit.record("recipe.private.remove", target=f"recipe:{name}")


@contextmanager
def staged_document(document: str, assets: Mapping[str, str]) -> Iterator[Path]:
    """
    Lay a recipe that arrived as text out on disk, for checking and adding.

    The console sends a recipe as its document and its assets; every check
    and the copy into the operator's folder work from files, so there is one
    reading of a recipe whatever way it came.

    Args:
        document: The recipe's YAML.
        assets: Asset path under ``assets/<name>/`` to its text.

    Yields:
        The folder, holding ``<name>.yaml`` and ``assets/<name>/...``; it is
        deleted afterwards.

    Raises:
        RecipeError: The document is too large, is not YAML with a ``name``
            that is a valid recipe name, or an asset has a path a recipe may
            not use.
    """
    if len(document.encode()) > MAX_RECIPE_BYTES:
        raise RecipeError(
            "The recipe is too large",
            details=f"A recipe is at most {MAX_RECIPE_BYTES // 1024} KiB.",
        )
    try:
        data = yaml.safe_load(document)
    except yaml.YAMLError as exc:
        raise RecipeError(
            f"The recipe is not valid YAML: {exc}", details="Nothing is added."
        ) from exc
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise RecipeError(
            "The recipe needs a name: of lower case letters, digits and dashes (up to 40)",
            details="The name is the recipe's file name and what --recipe takes.",
            field="name",
        )
    if len(assets) > MAX_ASSET_FILES:
        raise RecipeError(
            f"A recipe has at most {MAX_ASSET_FILES} asset files", details="Nothing is added."
        )
    for path, text in assets.items():
        if not is_plain_path(path):
            raise RecipeError(
                f"asset {path!r} is not a plain relative path",
                details="Letters, digits, dots, dashes and slashes; no '..'.",
            )
        if len(text.encode()) > MAX_ASSET_BYTES:
            raise RecipeError(
                f"asset {path} is larger than {MAX_ASSET_BYTES // 1024} KiB",
                details="Nothing is added.",
            )
    fs = get_fs()
    root = Path(tempfile.mkdtemp(prefix="noust-recipe-"))
    try:
        fs.write_text(root / f"{name}.yaml", document, mode=0o600)
        for path, text in assets.items():
            fs.write_text(root / "assets" / name / path, text, mode=0o600)
        yield root
    finally:
        fs.remove_tree(root)
