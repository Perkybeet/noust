# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking up the documentation, to cite it (item 171).

``search_docs`` gives the assistant passages of the documentation that ships with Noust, each with
the page and the heading it is under, so that an answer can say where it read what it says. A page
is cut at its headings; a search is a BM25 ranking over the passages (:mod:`noust.core.assistant.bm25`).

The documentation is not inside the Python package: the RPM installs ``docs/`` and a source checkout
has it. Where no page is found there is nothing to look up and the tool is not offered: what cannot
be done is not offered, instead of being offered to fail.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from noust.core.assistant.bm25 import Index

__all__ = ["DocIndex", "Passage", "split_markdown"]

#: Pages that say what changed, not what is true: history is not to be cited as documentation.
_HISTORY = re.compile(r"(?:CHANGELOG|UPGRADING|API-CHANGELOG)", re.IGNORECASE)
_HEADING = re.compile(r"(#{1,4})[ \t]+(.+?)[ \t#]*")
_FENCE = re.compile(r"^(```|~~~)")

#: The longest a passage is, so that a long section does not use a request on its own.
MAX_PASSAGE = 1_400
#: The most documents read from one folder.
_MAX_PAGES = 400
#: The largest page read.
_MAX_PAGE_BYTES = 1_500_000


@dataclass(frozen=True)
class Passage:
    """
    A piece of a page.

    Attributes:
        page: The page's file name (``assistant.md``).
        title: Its headings from the page's title down (``The assistant > Limits``).
        text: What it says, up to :data:`MAX_PASSAGE` characters.
    """

    page: str
    title: str
    text: str


def split_markdown(page: str, text: str) -> list[Passage]:
    """
    Cut a Markdown page at its headings.

    Args:
        page: The page's file name.
        text: Its text.

    Returns:
        A passage for each heading with something under it. Lines inside a code block are never a
        heading.
    """
    passages: list[Passage] = []
    trail: list[tuple[int, str]] = []
    body: list[str] = []
    fenced = False

    def close() -> None:
        content = "\n".join(body).strip()
        if content and trail:
            title = " > ".join(name for _level, name in trail)
            passages.append(Passage(page, title, content[:MAX_PASSAGE]))
        body.clear()

    for line in text.splitlines():
        if _FENCE.match(line.strip()):
            fenced = not fenced
        heading = None if fenced else _HEADING.fullmatch(line)
        if heading:
            close()
            level = len(heading.group(1))
            while trail and trail[-1][0] >= level:
                trail.pop()
            trail.append((level, heading.group(2).strip()))
        else:
            body.append(line)
    close()
    return passages


class DocIndex:
    """The passages of the documentation found in some folders, ranked on demand."""

    def __init__(self, folders: Iterable[Path]) -> None:
        """
        Args:
            folders: Where pages may be (the first that holds any is used).
        """
        self._passages: list[Passage] = []
        for folder in folders:
            pages = _pages(folder)
            if pages:
                for path in pages:
                    try:
                        self._passages.extend(
                            split_markdown(
                                path.name, path.read_text(encoding="utf-8", errors="replace")
                            )
                        )
                    except OSError:
                        continue
                break
        self._index: Index[int] = Index(
            (number, f"{p.title} {p.title} {p.page} {p.text}")
            for number, p in enumerate(self._passages)
        )

    @property
    def available(self) -> bool:
        """Whether there is any documentation to look up."""
        return bool(self._passages)

    def search(self, question: str, limit: int = 4) -> list[Passage]:
        """
        Args:
            question: What is looked for, in words.
            limit: The most passages to return.

        Returns:
            The passages that fit best, the best first.
        """
        return [self._passages[number] for number, _score in self._index.search(question, limit)]


def _pages(folder: Path) -> Sequence[Path]:
    """
    Args:
        folder: A folder that may hold documentation.

    Returns:
        Its Markdown pages (not the pages of changes), at its top level only, so that what is
        under it (plans, notes) is never taken for documentation.
    """
    try:
        found = sorted(
            path
            for path in folder.glob("*.md")
            if path.is_file()
            and not _HISTORY.search(path.name)
            and path.stat().st_size <= _MAX_PAGE_BYTES
        )
    except OSError:
        return []
    return found[:_MAX_PAGES]
