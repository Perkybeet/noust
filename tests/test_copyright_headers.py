# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every Python source file carries the same copyright line and licence identifier.

The credit is what a fork or a reseller has to keep (the AGPL's section 5(d) asks an
interactive program to show it, and `noust --version` and the console's About do), so it
is spelled one way everywhere: the line of :data:`noust.__copyright__`, under a shebang
when the file has one. Fixtures are data a test deploys, not Noust's source, and empty
package markers carry nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from noust import __copyright__
from tests.tree_support import tree_files

REPO_ROOT = Path(__file__).resolve().parent.parent

SPDX = "# SPDX-License-Identifier: AGPL-3.0-or-later"

#: Where Noust's own Python lives. The SDK is generated, with the same header.
ROOTS = ("src/", "scripts/", "tests/", "sdk/")

#: Data, not source: a test's application, a recipe's file.
NOT_SOURCE = ("tests/fixtures/", "tests/integration/fixtures/", "src/noust/recipes/assets/")

#: Any spelling of the line, so a variant is caught rather than missed.
ANY_COPYRIGHT = re.compile(r"Copyright( \(c\)| ©)? [0-9]{4}(-[0-9]{4})? Yago L[oó]pez Prado")


def _sources() -> list[Path]:
    return [
        path
        for path in tree_files(REPO_ROOT, under=[root.rstrip("/") for root in ROOTS])
        if path.suffix == ".py"
        and not path.relative_to(REPO_ROOT).as_posix().startswith(NOT_SOURCE)
    ]


def _head(text: str) -> list[str]:
    lines = text.splitlines()
    while lines and lines[0].startswith("#!"):
        lines.pop(0)
    return lines[:2]


def test_every_source_file_opens_with_the_copyright_and_the_licence() -> None:
    wrong = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            continue
        if _head(text) != [f"# {__copyright__}", SPDX]:
            wrong.append(path.relative_to(REPO_ROOT).as_posix())
    assert not wrong, (
        f"These files do not open with '# {__copyright__}' and '{SPDX}': " + ", ".join(wrong[:20])
    )


def test_the_name_is_spelled_one_way_in_every_tracked_file() -> None:
    variants = {}
    for path in tree_files(REPO_ROOT):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in ANY_COPYRIGHT.finditer(text):
            if match.group(0) != __copyright__:
                variants[path.relative_to(REPO_ROOT).as_posix()] = match.group(0)
    assert not variants, f"Spelled another way: {dict(list(variants.items())[:10])}"
