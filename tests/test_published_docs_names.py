# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What is published as documentation names no private project of the author's.

Some tests and one optional harness scenario are built on a real application of the author's,
and may keep its name: they describe how Noust was proved. The documentation, the reference, the
manual pages and the README are read by everyone, and their examples use invented names
(``shop.example.com``, ``/opt/shop``). The name is written backwards so that this file does not
hold it; the match ignores case.
"""

from __future__ import annotations

from pathlib import Path

from tests.tree_support import tree_files

ROOT = Path(__file__).parent.parent

#: Private projects that may appear in tests but never in what is published, backwards.
REVERSED = ("tseggorp",)

#: What is published as documentation: the docs site, the manual pages and the root guides.
PUBLISHED = ("docs", "man")
ROOT_GUIDES = ("README.md", "CONTRIBUTING.md", "SECURITY.md", "obs/noust.1")


def test_the_published_documentation_names_no_private_project() -> None:
    names = [name[::-1] for name in REVERSED]
    files = tree_files(ROOT, under=PUBLISHED) + [ROOT / guide for guide in ROOT_GUIDES]
    found = []
    for path in files:
        if path.suffix in {".png", ".jpg", ".svg", ".woff2"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        found += [f"{path.relative_to(ROOT)}: {name}" for name in names if name in text]

    assert not found, "Use an invented example instead:\n" + "\n".join(found)
