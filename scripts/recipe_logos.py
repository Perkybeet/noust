#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The logo of every recipe: the project's mark in the simple-icons collection, or Noust's monogram.

A recipe's ``logo:`` is ``simple-icons:<slug>`` (the collection's mark for the project, CC0-1.0) or
a file of the recipe (the monogram drawn for Noust). This tool finds which recipes the collection
has a mark for, writes that into the recipes, and keeps the mark's file in the package
(``recipes/assets/_simple-icons/<slug>.svg``, one file for however many recipes name it), checked
against the closed list of SVG elements the console's CSP allows (:mod:`noust.recipes.logos`).

The collection's licence covers its files and not anybody's rights in a brand. A project whose own
guidelines forbid a third party's use of its logo is given the monogram, whatever the collection
holds: ``FORBIDDEN`` is that list, with where each was read.

Usage:
    scripts/recipe_logos.py report [--icons DIR]   Who has a mark, who has the monogram and why.
    scripts/recipe_logos.py apply [--icons DIR]    Write ``logo: simple-icons:<slug>`` into the
                                                   recipes that have a mark and copy the files.
    scripts/recipe_logos.py sync [--icons DIR]     Copy the files of the slugs recipes name.
    scripts/recipe_logos.py check [--icons DIR]    Fail on a slug the collection does not have, a
                                                   file that is not the collection's, a file no
                                                   recipe names.

``--icons`` is the unpacked ``simple-icons`` package (``npm pack simple-icons``); the default is
~/recetas-work/src/simple-icons/pkg/package.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.recipes.logos import simple_icon_slug, validate_svg  # noqa: E402

RECIPES = ROOT / "src" / "noust" / "recipes"
ICONS = RECIPES / "assets" / "_simple-icons"
DEFAULT_PACKAGE = Path.home() / "recetas-work" / "src" / "simple-icons" / "pkg" / "package"

#: Projects whose own guidelines forbid a third party's use of their logo: they get the monogram.
FORBIDDEN = {
    "plausible": "plausible.io/trademark, read 2026-10-06: the logo is not for a third party's use",
}

#: A recipe's name to the collection's slug, where the two are not spelled alike.
ALIASES = {
    "rocketchat": "rocketdotchat",
    "rocket-chat": "rocketdotchat",
    "wikijs": "wikidotjs",
    "wiki-js": "wikidotjs",
    "couchdb": "apachecouchdb",
    "owncloud-ocis": "owncloud",
    "drawio": "diagramsdotnet",
}


def norm(text: str) -> str:
    """
    Args:
        text: A name.

    Returns:
        Lower case letters and digits only, which is how the collection spells a slug.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_collection(package: Path) -> dict[str, dict[str, str]]:
    """
    Args:
        package: The unpacked simple-icons package.

    Returns:
        Its icons by slug, each with ``title`` and the file's path.
    """
    data = json.loads((package / "data" / "simple-icons.json").read_text(encoding="utf-8"))
    return {
        entry["slug"]: {
            "title": entry["title"],
            "file": str(package / "icons" / f"{entry['slug']}.svg"),
        }
        for entry in data
        if (package / "icons" / f"{entry['slug']}.svg").is_file()
    }


def slug_for(name: str, title: str, collection: dict[str, dict[str, str]]) -> str | None:
    """
    Find the collection's mark for a project.

    Args:
        name: The recipe's name.
        title: Its title.
        collection: What :func:`load_collection` read.

    Returns:
        The slug, or None when the collection has no mark under that name.
    """
    if name in FORBIDDEN:
        return None
    by_title = {norm(entry["title"]): slug for slug, entry in collection.items()}
    for candidate in (ALIASES.get(name), norm(name), norm(title)):
        if candidate and candidate in collection:
            return candidate
        if candidate and candidate in by_title:
            return by_title[candidate]
    return None


def recipes() -> list[tuple[str, str, str | None, Path]]:
    """
    Returns:
        Every recipe of the catalogue's folder: name, title, its ``logo:`` and its file.
    """
    found = []
    for path in sorted(RECIPES.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        title = re.search(r'^title: "?(.*?)"?\s*$', text, re.MULTILINE)
        logo = re.search(r"^logo: (\S+)\s*$", text, re.MULTILINE)
        found.append(
            (
                path.stem,
                title.group(1) if title else path.stem,
                logo.group(1) if logo else None,
                path,
            )
        )
    return found


def copy_icon(slug: str, collection: dict[str, dict[str, str]]) -> None:
    """
    Put the collection's file for a slug in the package, after the console's policy has read it.

    Args:
        slug: The mark.
        collection: What :func:`load_collection` read.
    """
    text = Path(collection[slug]["file"]).read_text(encoding="utf-8")
    validate_svg(slug, text)
    ICONS.mkdir(parents=True, exist_ok=True)
    (ICONS / f"{slug}.svg").write_text(text, encoding="utf-8")


def write_notice(package: Path) -> None:
    """
    Record where the marks come from, in the folder that holds them.

    Args:
        package: The unpacked simple-icons package.
    """
    version = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"]
    ICONS.mkdir(parents=True, exist_ok=True)
    (ICONS / "NOTICE").write_text(
        "The files in this folder are marks from the simple-icons collection\n"
        f"(https://github.com/simple-icons/simple-icons, package simple-icons {version}),\n"
        "published under CC0-1.0 (https://creativecommons.org/publicdomain/zero/1.0/).\n"
        "They are copied unchanged. The licence covers the files and not the brands: each mark is\n"
        "the trademark of its owner, shown here to say which project a recipe installs and not to\n"
        "suggest that the project made, endorses or supports the recipe or Noust.\n"
        "A project whose guidelines forbid a third party's use of its logo has no file here;\n"
        "its recipe carries the monogram drawn for Noust (scripts/recipe_logos.py, FORBIDDEN).\n",
        encoding="utf-8",
    )


def set_logo(path: Path, logo: str) -> None:
    """
    Args:
        path: A recipe's file.
        logo: The new value of ``logo:``.
    """
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"^logo: \S+$", f"logo: {logo}", text, count=1, flags=re.MULTILINE), "utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0, or 1 when ``check`` finds a problem.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("command", choices=("report", "apply", "sync", "check"))
    parser.add_argument("--icons", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--json", action="store_true", help="report as JSON")
    args = parser.parse_args(argv)
    collection = load_collection(args.icons)

    if args.command in ("report", "apply"):
        marks: dict[str, str] = {}
        monograms: dict[str, str] = {}
        for name, title, logo, path in recipes():
            slug = simple_icon_slug(logo) or slug_for(name, title, collection)
            if slug is not None and slug in collection:
                marks[name] = slug
                if args.command == "apply":
                    set_logo(path, f"simple-icons:{slug}")
                    copy_icon(slug, collection)
                    monogram = RECIPES / "assets" / name / "logo.svg"
                    if (
                        monogram.is_file()
                        and not (RECIPES / "assets" / name / "LOGO-NOTICE").exists()
                    ):
                        monogram.unlink()
                        if not any((RECIPES / "assets" / name).iterdir()):
                            (RECIPES / "assets" / name).rmdir()
            else:
                monograms[name] = FORBIDDEN.get(name, "the collection has no mark under this name")
        if args.command == "apply":
            write_notice(args.icons)
        if args.json:
            print(json.dumps({"simple-icons": marks, "monogram": monograms}, indent=1))
        else:
            print(f"{len(marks)} recipes carry a simple-icons mark, {len(monograms)} the monogram")
            for name, reason in monograms.items():
                print(f"  {name}: {reason}")
        return 0

    named = {simple_icon_slug(logo) for *_, logo, _ in recipes()} - {None}
    if args.command == "sync":
        for slug in sorted(str(s) for s in named):
            copy_icon(slug, collection)
        write_notice(args.icons)
        return 0

    problems = []
    for slug in sorted(str(s) for s in named):
        if slug not in collection:
            problems.append(f"{slug}: the collection has no such mark")
        elif not (ICONS / f"{slug}.svg").is_file():
            problems.append(f"{slug}: named by a recipe and not in the package")
        elif (ICONS / f"{slug}.svg").read_bytes() != Path(collection[slug]["file"]).read_bytes():
            problems.append(f"{slug}: the file is not the collection's")
    if ICONS.is_dir():
        for entry in sorted(ICONS.glob("*.svg")):
            if entry.stem not in named:
                problems.append(f"{entry.name}: no recipe names it")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
