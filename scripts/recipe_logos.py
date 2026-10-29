#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The logo of every recipe: the project's own mark, with where it comes from, or Noust's monogram.

A recipe's ``logo:`` is one of three things, and this tool keeps each honest:

- ``simple-icons:<slug>``: the mark the simple-icons collection holds for the project. The file is
  kept in the package (``recipes/assets/_simple-icons/<slug>.svg``, one for however many recipes
  name it) with an ``index.json`` of what the collection says of it: the brand's colour, its
  licence (CC0-1.0, or the one the collection records where a mark is under another) and the page
  it was drawn from.
- ``logo.svg`` with a ``LOGO-NOTICE`` beside it: a mark taken from the project's own repository,
  its site or a collection of the logos of self-hosted software, rewritten by
  :func:`noust.recipes.logo_sanitize.sanitize_svg` into the closed list of elements the console's
  policy allows, with the address it was taken from, its licence and what the project's rules on its
  logo say. ``add`` is the one way such a file enters the package.
- ``logo.svg`` that is the monogram drawn for Noust: a project with no mark that can be shipped.

A licence on a file is not a right in the brand. A project whose own text forbids a third party's
use of its logo, or lets third parties use its word mark and not its logos, or gives the logo under a
non-commercial licence (Noust is also offered under a commercial one), is given the monogram:
``FORBIDDEN`` is that list, with where each rule was read.

Usage:
    scripts/recipe_logos.py report [--icons DIR]   Who has a mark, a file with its notice, or the
                                                   monogram, and why.
    scripts/recipe_logos.py apply [--icons DIR]    Write ``logo: simple-icons:<slug>`` into the
                                                   recipes the collection has a mark for.
    scripts/recipe_logos.py sync [--icons DIR]     Copy the files of the slugs recipes name and write
                                                   the index and the notice of the folder.
    scripts/recipe_logos.py check [--icons DIR]    Fail on a slug the collection does not have, a
                                                   file or an index entry that is not the
                                                   collection's, a file no recipe names, and a
                                                   mark without its notice.
    scripts/recipe_logos.py add NAME FILE --source URL --licence SPDX [--copyright LINE]
                           [--terms TEXT] [--retrieved YYYY-MM-DD] [--title TITLE]
                                                   Rewrite FILE (downloaded into a folder of its own)
                                                   as the logo of NAME, write its LOGO-NOTICE and
                                                   name it in the recipe.

``--icons`` is the unpacked ``simple-icons`` package (``npm pack simple-icons``); the default is
~/recetas-work/src/simple-icons/pkg/package.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.recipes.logo_png import PNG_SIGNATURE, sanitize_png  # noqa: E402
from noust.recipes.logo_sanitize import sanitize_svg  # noqa: E402
from noust.recipes.logos import (  # noqa: E402
    NOTICE_FILE,
    parse_logo_notice,
    simple_icon_slug,
    validate_svg,
)
from noust.recipes.model import SIMPLE_ICONS_LICENSE, RecipeError  # noqa: E402

RECIPES = ROOT / "src" / "noust" / "recipes"
ICONS = RECIPES / "assets" / "_simple-icons"
DEFAULT_PACKAGE = Path.home() / "recetas-work" / "src" / "simple-icons" / "pkg" / "package"

#: Projects whose own text does not let a third party show their logo: they get the monogram, whatever
#: a collection holds. Each says what was read and where, on the day it was read.
FORBIDDEN = {
    "plausible": "plausible.io/trademark, read 2026-10-06: the logo is not for a third party's use",
    "trek": (
        "TRADEMARKS.md of liketrek/TREK, read 2026-10-29: the word mark may describe a relationship "
        "to the software, the logos may not"
    ),
    "leantime": (
        "leantime.io/trademark-policy, read 2026-10-29: the word marks may describe a relationship "
        "to the software, the logos may not, and a logo is almost never a nominative use"
    ),
    "owncast": (
        "owncast.online/trademark, read 2026-10-29: the logo is licensed CC BY-NC 4.0, and Noust is "
        "also offered under a commercial licence"
    ),
    "typebot": (
        "LICENSE of baptisteArno/typebot.io, read 2026-10-29: no right to use its trademarks except to "
        "identify it as the origin of the software"
    ),
    "joplin-server": (
        "LICENSE of laurent22/joplin, read 2026-10-29: the logos and icons are all rights reserved and "
        "may not be used without a permission"
    ),
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
    "drizzle-gateway": "drizzle",
}

CHANGES = (
    "rewritten by scripts/recipe_logos.py into the elements the console's policy allows (editor data, "
    "styles and metadata removed, numbers rounded); the artwork is unchanged"
)


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


def collection_data(package: Path) -> dict[str, dict[str, object]]:
    """
    Args:
        package: The unpacked simple-icons package.

    Returns:
        What the collection's data file says of each mark, by slug.
    """
    data = json.loads((package / "data" / "simple-icons.json").read_text(encoding="utf-8"))
    return {entry["slug"]: entry for entry in data}


def index_entry(entry: dict[str, object]) -> dict[str, str]:
    """
    Args:
        entry: What the collection says of one mark.

    Returns:
        What the package keeps of it: the title, the brand's colour, the licence of the mark
        (CC0-1.0 unless the collection records another; a custom one as ``LicenseRef-custom`` with
        its address), the page it was drawn from and the brand's guidelines.
    """
    licence = entry.get("license")
    found: dict[str, str] = {
        "title": str(entry["title"]),
        "hex": str(entry["hex"]),
        "license": SIMPLE_ICONS_LICENSE,
    }
    if isinstance(licence, dict):
        kind = str(licence.get("type", ""))
        found["license"] = "LicenseRef-custom" if kind == "custom" else kind or SIMPLE_ICONS_LICENSE
        if licence.get("url"):
            found["license_url"] = str(licence["url"])
    if entry.get("source"):
        found["source"] = str(entry["source"])
    if entry.get("guidelines"):
        found["guidelines"] = str(entry["guidelines"])
    return found


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
        "published under CC0-1.0 (https://creativecommons.org/publicdomain/zero/1.0/), except where\n"
        "the collection records another licence for a mark: index.json says which, mark by mark.\n"
        "They are copied unchanged. The licence covers the files and not the brands: each mark is\n"
        "the trademark of its owner, shown here to say which project a recipe installs and not to\n"
        "suggest that the project made, endorses or supports the recipe or Noust.\n"
        "A project whose guidelines forbid a third party's use of its logo has no file here;\n"
        "its recipe carries the monogram drawn for Noust (scripts/recipe_logos.py, FORBIDDEN).\n",
        encoding="utf-8",
    )


def write_index(package: Path, named: set[str]) -> None:
    """
    Write what the package keeps of each mark a recipe names.

    Args:
        package: The unpacked simple-icons package.
        named: The slugs recipes name.
    """
    version = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"]
    data = collection_data(package)
    marks = {slug: index_entry(data[slug]) for slug in sorted(named) if slug in data}
    (ICONS / "index.json").write_text(
        json.dumps(
            {"collection": "simple-icons", "version": version, "marks": marks},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
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


def logo_title(title: str) -> str:
    """
    Args:
        title: A recipe's title.

    Returns:
        The name the mark carries as its ``<title>``: what the policy lets a title be.
    """
    return re.sub(r"[^A-Za-z0-9 .+-]", "", title).strip()[:40]


def render_notice(
    title: str,
    source: str,
    licence: str,
    retrieved: date,
    *,
    copyright_line: str = "",
    terms: str = "",
    changes: str = CHANGES,
) -> str:
    """
    Args:
        title: The project.
        source: The address of the file at the commit or release it was read at.
        licence: Its licence, as an SPDX expression or ``LicenseRef-...``.
        retrieved: The day it was taken.
        copyright_line: The copyright line the licence asks to travel.
        terms: What the project's rules on its logo say, and where and when they were read.
        changes: What was done to the file.

    Returns:
        The ``LOGO-NOTICE``.
    """
    lines = [f"Mark of: {title}", f"Source: {source}", f"Licence: {licence}"]
    if copyright_line:
        lines.append(f"Copyright: {copyright_line}")
    if terms:
        lines.append(f"Terms: {terms}")
    lines.append(f"Retrieved: {retrieved.isoformat()}")
    lines.append(f"Changes: {changes}")
    return "\n".join(lines) + "\n"


def add_logo(
    name: str,
    data: bytes,
    *,
    title: str,
    source: str,
    licence: str,
    retrieved: date,
    copyright_line: str = "",
    terms: str = "",
    recipes_dir: Path = RECIPES,
) -> Path:
    """
    Make a file somebody else drew the logo of a recipe, with where it comes from.

    Args:
        name: The recipe.
        data: The file as the project published it: an SVG, or a PNG of 256 pixels or more.
        title: The project, for the mark's accessible name and the notice.
        source: The address of the file at the commit or release it was read at.
        licence: Its licence.
        retrieved: The day it was taken.
        copyright_line: The copyright line the licence asks to travel.
        terms: What the project's rules on its logo say.
        recipes_dir: Where the recipes are.

    Returns:
        The written logo.

    Raises:
        RecipeError: The file cannot be made to pass the console's policy, or the notice is
            not one a mark can be shipped with.
        SystemExit: The recipe is one ``FORBIDDEN`` gives the monogram.
    """
    if name in FORBIDDEN:
        raise SystemExit(f"{name} is given the monogram: {FORBIDDEN[name]}")
    notice = render_notice(
        title, source, licence, retrieved, copyright_line=copyright_line, terms=terms
    )
    parse_logo_notice(name, notice)
    folder = recipes_dir / "assets" / name
    folder.mkdir(parents=True, exist_ok=True)
    if data.startswith(PNG_SIGNATURE):
        written = folder / "logo.png"
        written.write_bytes(sanitize_png(name, data))
        # The picture replaces the monogram, which would be a file nothing names.
        (folder / "logo.svg").unlink(missing_ok=True)
    else:
        written = folder / "logo.svg"
        written.write_text(sanitize_svg(name, data, title=logo_title(title)), encoding="utf-8")
        (folder / "logo.png").unlink(missing_ok=True)
    (folder / NOTICE_FILE).write_text(notice, encoding="utf-8")
    set_logo(recipes_dir / f"{name}.yaml", written.name)
    return written


def monogram_letters(text: str) -> str | None:
    """
    Args:
        text: A logo.

    Returns:
        The one or two letters of Noust's monogram, or None when the file is anything else.
    """
    names = re.findall(r"<(\w+)", text)
    if sorted(names) != ["rect", "svg", "text", "title"] or 'fill="#24292f"' not in text:
        return None
    found = re.search(r"<text[^>]*>([^<]{1,2})</text>", text)
    return found.group(1) if found else None


def kind_of(name: str, logo: str | None) -> str:
    """
    Args:
        name: The recipe.
        logo: Its ``logo:``.

    Returns:
        ``simple-icons``, ``mark`` (a file with its notice), ``monogram`` or ``none``.
    """
    if logo is None:
        return "none"
    if simple_icon_slug(logo) is not None:
        return "simple-icons"
    folder = RECIPES / "assets" / name
    if (folder / NOTICE_FILE).is_file():
        return "mark"
    return "monogram"


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
    parser.add_argument("command", choices=("report", "apply", "sync", "check", "add"))
    parser.add_argument("name", nargs="?", help="add: the recipe")
    parser.add_argument(
        "file", nargs="?", type=Path, help="add: the file as the project published it"
    )
    parser.add_argument("--icons", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--json", action="store_true", help="report as JSON")
    parser.add_argument(
        "--source", help="add: where the file was taken from, at its commit or release"
    )
    parser.add_argument("--licence", help="add: the file's licence, an SPDX expression")
    parser.add_argument(
        "--copyright", default="", help="add: the copyright line the licence asks for"
    )
    parser.add_argument("--terms", default="", help="add: what the project's rules on its logo say")
    parser.add_argument("--retrieved", default=date.today().isoformat())
    parser.add_argument("--title", help="add: the project's name")
    args = parser.parse_args(argv)

    if args.command == "add":
        if not (args.name and args.file and args.source and args.licence):
            parser.error("add needs NAME, FILE, --source and --licence")
        title = args.title or next((t for n, t, *_ in recipes() if n == args.name), args.name)
        try:
            written = add_logo(
                args.name,
                args.file.read_bytes(),
                title=title,
                source=args.source,
                licence=args.licence,
                retrieved=date.fromisoformat(args.retrieved),
                copyright_line=args.copyright,
                terms=args.terms,
            )
        except RecipeError as exc:
            print(exc.message)
            return 1
        print(f"{written.relative_to(ROOT)}: {written.stat().st_size} bytes")
        return 0

    collection = load_collection(args.icons)

    if args.command in ("report", "apply"):
        marks: dict[str, str] = {}
        others: dict[str, str] = {}
        for name, title, logo, path in recipes():
            slug = simple_icon_slug(logo) or slug_for(name, title, collection)
            kind = kind_of(name, logo)
            if kind == "mark":
                others[name] = "mark"
            elif slug is not None and slug in collection:
                marks[name] = slug
                if args.command == "apply":
                    set_logo(path, f"simple-icons:{slug}")
                    copy_icon(slug, collection)
                    monogram = RECIPES / "assets" / name / "logo.svg"
                    if (
                        monogram.is_file()
                        and not (RECIPES / "assets" / name / NOTICE_FILE).exists()
                    ):
                        monogram.unlink()
                        if not any((RECIPES / "assets" / name).iterdir()):
                            (RECIPES / "assets" / name).rmdir()
            else:
                others[name] = FORBIDDEN.get(name, "monogram: no mark found yet")
        if args.command == "apply":
            named = {simple_icon_slug(logo) for *_, logo, _ in recipes()} - {None}
            write_notice(args.icons)
            write_index(args.icons, {str(s) for s in named})
        monograms = {n: why for n, why in others.items() if why != "mark"}
        if args.json:
            print(
                json.dumps(
                    {
                        "simple-icons": marks,
                        "mark": sorted(n for n, w in others.items() if w == "mark"),
                        "monogram": monograms,
                    },
                    indent=1,
                )
            )
        else:
            print(
                f"{len(marks)} recipes carry a simple-icons mark, "
                f"{len(others) - len(monograms)} a mark with its notice, {len(monograms)} the monogram"
            )
            for name, reason in monograms.items():
                print(f"  {name}: {reason}")
        return 0

    named = {simple_icon_slug(logo) for *_, logo, _ in recipes()} - {None}
    if args.command == "sync":
        for slug in sorted(str(s) for s in named):
            copy_icon(slug, collection)
        write_notice(args.icons)
        write_index(args.icons, {str(s) for s in named})
        return 0

    problems = []
    data = collection_data(args.icons)
    index = (
        json.loads((ICONS / "index.json").read_text(encoding="utf-8"))
        if (ICONS / "index.json").is_file()
        else {}
    )
    for slug in sorted(str(s) for s in named):
        if slug not in collection:
            problems.append(f"{slug}: the collection has no such mark")
        elif not (ICONS / f"{slug}.svg").is_file():
            problems.append(f"{slug}: named by a recipe and not in the package")
        elif (ICONS / f"{slug}.svg").read_bytes() != Path(collection[slug]["file"]).read_bytes():
            problems.append(f"{slug}: the file is not the collection's")
        elif index.get("marks", {}).get(slug) != index_entry(data[slug]):
            problems.append(f"{slug}: index.json is not what the collection says (run sync)")
    if ICONS.is_dir():
        for entry in sorted(ICONS.glob("*.svg")):
            if entry.stem not in named:
                problems.append(f"{entry.name}: no recipe names it")
    for name, _title, logo, _path in recipes():
        if kind_of(name, logo) == "mark":
            try:
                parse_logo_notice(
                    name, (RECIPES / "assets" / name / NOTICE_FILE).read_text(encoding="utf-8")
                )
            except RecipeError as exc:
                problems.append(exc.message)
        elif (
            kind_of(name, logo) == "monogram"
            and monogram_letters(
                (RECIPES / "assets" / name / (logo or "")).read_text(encoding="utf-8")
            )
            is None
        ):
            problems.append(f"{name}: a logo that is not the monogram has no {NOTICE_FILE}")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
