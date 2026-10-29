#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Keep the credit of the recipes that come from another catalogue where the licences ask for it.

A recipe whose header says "Derived in part from Coolify's template ..." (Apache-2.0) or "Derived in
part from Dokploy's template ..." (MIT) carries that project's licence terms: its authors' copyright
line and the licence have to travel with every copy. ``NOTICE`` lists the recipes under each, the
Debian copyright file names them in a ``Files:`` paragraph of their own, and
``tests/test_third_party_notice.py`` fails when a list is not the recipes that say so. With hundreds
of recipes the lists are written by this tool from the headers, never by hand:

    scripts/recipe_notice.py           Rewrite the lists in NOTICE and obs/debian.copyright.
    scripts/recipe_notice.py --check   Fail if either is out of date (the tests run this).

The marks of the simple-icons collection (CC0-1.0, and the licence the collection records for the
few that are under another) are declared in the same files in a paragraph of their own: CC0 asks for
nothing to travel, and the paragraph says where they come from.

The marks taken from the projects themselves, from their brand kits or from collections of the logos
of self-hosted software are listed by licence in both files, written from each recipe's ``LOGO-NOTICE``
(and from the collection's ``index.json`` for the marks under a licence of their own): a licence that
asks for the author's credit to travel (MIT, BSD, CC-BY) is kept by the notice beside each file, whose
``Copyright`` line is the credit, and by these lists, which name every file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "src" / "noust" / "recipes"
NOTICE = ROOT / "NOTICE"
COPYRIGHT = ROOT / "obs" / "debian.copyright"

#: The sentence a recipe's header starts a credit with, by the catalogue it comes from.
COOLIFY_MARK = "Derived in part from Coolify's template"
DOKPLOY_MARK = "Derived in part from Dokploy's template"

#: What NOTICE and the Debian copyright file say about each, and the markers around the list.
NOTICE_COOLIFY = re.compile(
    r"(header which template it comes from and what was changed:\n\n)(.*?)(\n\nPlausible Community Edition)",
    re.DOTALL,
)
NOTICE_DOKPLOY = re.compile(
    r"(Dokploy templates\n-----------------\n.*?which template it comes from\s+and what was "
    r"changed:\n\n)(.*?)(\n\nThe tests of the Dokploy template converter)",
    re.DOTALL,
)
COPYRIGHT_COOLIFY = re.compile(
    r"(\nFiles:\n)((?:(?!\n\n).)*?)(\nCopyright: 2025 Andras Bacsai\n)", re.DOTALL
)
COPYRIGHT_DOKPLOY = re.compile(
    r"(\nFiles:\n)((?:(?!\n\n).)*?)(\nCopyright: 2024 Dokploy and Carlos Ortiz\n)", re.DOTALL
)


ASSETS = RECIPES / "assets"
MARKS_BEGIN = "# marks: begin (written by scripts/recipe_notice.py)"
MARKS_END = "# marks: end"
NOTICE_MARKS = re.compile(
    r"(Marks of the projects\n----------------------\n.*?by licence:\n\n)(.*?)(\n\nFonts of the console)",
    re.DOTALL,
)
COPYRIGHT_MARKS = re.compile(
    rf"({re.escape(MARKS_BEGIN)}\n)(.*?)(\n{re.escape(MARKS_END)}\n)", re.DOTALL
)
#: Debian's short names for the licences marks come under; a name not here is spelled as it is.
DEBIAN_NAMES = {
    "MIT": "Expat",
    "Apache-2.0": "Apache-2.0",
    "GPL-3.0-only": "GPL-3",
    "GPL-2.0-only": "GPL-2",
    "AGPL-3.0-only": "AGPL-3",
    "AGPL-3.0-or-later": "AGPL-3+",
    "LGPL-3.0-only": "LGPL-3",
    "BSD-2-Clause": "BSD-2-clause",
    "BSD-3-Clause": "BSD-3-clause",
    "MPL-2.0": "MPL-2.0",
}
#: Licences whose text the base system ships; every other is pointed at by its SPDX page.
COMMON_LICENSES = {"GPL-3", "GPL-2", "LGPL-3", "Apache-2.0", "MPL-2.0", "AGPL-3"}


def marks() -> dict[str, list[str]]:
    """
    Returns:
        The files of the marks that are not Noust's monogram or an unchanged mark of the collection
        under CC0, by licence expression: each recipe's logo (from its ``LOGO-NOTICE``) and the
        collection's marks that are under a licence of their own (from ``index.json``). Paths are
        repository paths, sorted.
    """
    found: dict[str, list[str]] = defaultdict(list)
    for notice in sorted(ASSETS.glob("*/LOGO-NOTICE")):
        licence = next(
            (
                line.split(": ", 1)[1].strip()
                for line in notice.read_text(encoding="utf-8").splitlines()
                if line.startswith("Licence: ")
            ),
            "",
        )
        logo = "logo.png" if (notice.parent / "logo.png").is_file() else "logo.svg"
        found[licence].append(f"src/noust/recipes/assets/{notice.parent.name}/{logo}")
    index = ASSETS / "_simple-icons" / "index.json"
    if index.is_file():
        for slug, entry in sorted(json.loads(index.read_text(encoding="utf-8"))["marks"].items()):
            if entry["license"] != "CC0-1.0":
                found[entry["license"]].append(f"src/noust/recipes/assets/_simple-icons/{slug}.svg")
    return {licence: sorted(paths) for licence, paths in sorted(found.items())}


def render_marks_notice() -> str:
    """
    Returns:
        The list NOTICE carries: each licence and the files that are under it.
    """
    blocks = []
    for licence, paths in marks().items():
        blocks.append(f"  {licence}\n" + "\n".join(f"    {path}" for path in paths))
    return "\n".join(blocks)


def debian_name(licence: str) -> str:
    """
    Args:
        licence: A licence expression of a ``LOGO-NOTICE``.

    Returns:
        The expression in the Debian copyright format's short names.
    """
    atoms = re.split(r"( AND | OR )", licence)
    out = []
    for atom in atoms:
        if atom == " AND ":
            out.append(" and ")
        elif atom == " OR ":
            out.append(" or ")
        elif atom.startswith("LicenseRef-"):
            out.append("custom-" + atom[len("LicenseRef-") :].lower())
        else:
            out.append(DEBIAN_NAMES.get(atom, atom))
    return "".join(out)


def render_marks_copyright(text: str) -> str:
    """
    Args:
        text: The Debian copyright file as it is (to know which licences it already has a text for).

    Returns:
        The paragraphs between the markers: one per licence of a mark, the files under it, and
        for each licence the copyright file has no paragraph of its own for, one that says where the
        text is.
    """
    out: list[str] = []
    outside = COPYRIGHT_MARKS.sub("", text)
    defined = set(re.findall(r"^License: ([^\n]+)\n ", outside, re.MULTILINE))
    used: list[str] = []
    for licence, paths in marks().items():
        short = debian_name(licence)
        out.append(
            "Files:\n"
            + "\n".join(f" {path}" for path in paths)
            + "\nCopyright: the authors each mark's LOGO-NOTICE names (src/noust/recipes/assets/<recipe>/LOGO-NOTICE)\n"
            "Comment: A mark of a project, taken from its repository, its brand kit or a collection of the\n"
            " logos of self-hosted software. The licence covers the file and not the brand: the LOGO-NOTICE\n"
            " says where the file comes from and what the project's own rules on its logo say.\n"
            f"License: {short}"
        )
        for atom in re.split(r" and | or ", short):
            if atom not in used:
                used.append(atom)
    for atom in used:
        if atom in defined or atom == "CC0-1.0":
            continue
        if atom.startswith("custom-"):
            body = (
                f"Terms the project publishes for third parties' use of its logo ({atom[7:]}); the LOGO-NOTICE of\n"
                " each file says where they were read."
            )
        elif atom in COMMON_LICENSES:
            body = f"On Debian systems, the full text of the licence is in /usr/share/common-licenses/{atom.split('+')[0]}."
        else:
            body = f"The text of the licence is at https://spdx.org/licenses/{atom}.html."
        out.append(f"License: {atom}\n {body}")
    return "\n\n".join(out)


def derived(mark: str) -> list[str]:
    """
    Args:
        mark: One of the marks a header starts a credit with.

    Returns:
        The recipes whose file says it, as repository paths, sorted.
    """
    return sorted(
        f"src/noust/recipes/{path.name}"
        for path in RECIPES.glob("*.yaml")
        if mark in path.read_text(encoding="utf-8")
    )


def indented(paths: Sequence[str], indent: str) -> str:
    """
    Args:
        paths: Repository paths.
        indent: What starts each line.

    Returns:
        One line per path.
    """
    return "\n".join(f"{indent}{path}" for path in paths)


def render_notice(text: str) -> str:
    """
    Args:
        text: NOTICE as it is.

    Returns:
        NOTICE with the lists of derived recipes written from the recipes' headers.

    Raises:
        SystemExit: A paragraph the lists belong to is not where it is looked for.
    """
    for pattern, mark in ((NOTICE_COOLIFY, COOLIFY_MARK), (NOTICE_DOKPLOY, DOKPLOY_MARK)):
        found = pattern.search(text)
        if found is None:
            raise SystemExit(f"NOTICE has no paragraph for {mark!r}")
        paths = derived(mark)
        body = indented(paths, "  ") if paths else "  (none yet)"
        text = text[: found.start(2)] + body + text[found.end(2) :]
    found = NOTICE_MARKS.search(text)
    if found is None:
        raise SystemExit("NOTICE has no paragraph for the marks of the projects")
    return text[: found.start(2)] + render_marks_notice() + text[found.end(2) :]


def render_copyright(text: str) -> str:
    """
    Args:
        text: The Debian copyright file as it is.

    Returns:
        The file with the ``Files:`` paragraphs of the derived recipes written from the headers.

    Raises:
        SystemExit: A paragraph the lists belong to is not where it is looked for.
    """
    for pattern, mark in ((COPYRIGHT_COOLIFY, COOLIFY_MARK), (COPYRIGHT_DOKPLOY, DOKPLOY_MARK)):
        found = pattern.search(text)
        if found is None:
            raise SystemExit(f"obs/debian.copyright has no paragraph for {mark!r}")
        paths = derived(mark)
        body = indented(paths, " ") if paths else " src/noust/recipes/none-yet.yaml"
        text = text[: found.start(2)] + body + text[found.end(2) :]
    found = COPYRIGHT_MARKS.search(text)
    if found is None:
        raise SystemExit("obs/debian.copyright has no markers for the marks of the projects")
    return text[: found.start(2)] + render_marks_copyright(text) + text[found.end(2) :]


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0, or 1 when ``--check`` finds a file out of date.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for path, render in ((NOTICE, render_notice), (COPYRIGHT, render_copyright)):
        current = path.read_text(encoding="utf-8")
        wanted = render(current)
        if wanted != current:
            stale.append(path.relative_to(ROOT))
            if not args.check:
                path.write_text(wanted, encoding="utf-8")
    if args.check and stale:
        print("out of date, run scripts/recipe_notice.py:", *stale)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
