#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Say whether the Noust installed here carries its documentation and can search it (item 171).

The built-in assistant looks pages up in files, and a package that left them out would offer the
tool on one distribution and not on another. This runs against an *installed* Noust, in the gate of
each way of installing it (the wheel, the Debian package, the RPM, the central's image), with that
installation's own interpreter and nothing of the repository on its path::

    python3 packaging/check_documentation.py            # a package, in a container with the checkout
    docker run --rm -i --entrypoint python IMAGE - < packaging/check_documentation.py

It needs only what ``noust --version`` needs: the console's dependencies are not installed on the
machines it checks. Exit status 0 when the pages are there and a question finds one, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: A question per page that every release of the documentation answers, and a word of its page.
QUESTIONS = (
    ("zero downtime", "relay"),
    ("restore a backup", "restore"),
)


def say(line: str) -> None:
    """
    Args:
        line: What to tell whoever reads the log of the gate.
    """
    sys.stdout.write(line + "\n")


def main() -> int:
    """
    Returns:
        0 when the installed package holds its pages and the search answers; 1 with the reason.
    """
    import noust
    from noust.core.assistant.docsearch import DocIndex
    from noust.core.paths import docs_directories

    packaged = Path(noust.__file__).resolve().parent / "docs"
    pages = sorted(packaged.glob("*.md"))
    say(f"noust {noust.__version__} at {Path(noust.__file__).resolve().parent}")
    say(f"{len(pages)} pages in {packaged}")
    if not pages:
        say("The package carries no documentation: the assistant would not offer to search it.")
        return 1
    if packaged not in docs_directories():
        say(f"{packaged} is not among the places the assistant looks in.")
        return 1
    index = DocIndex(docs_directories())
    if not index.available:
        say("The index found nothing to read in any of those places.")
        return 1
    failed = False
    for question, word in QUESTIONS:
        found = index.search(question)
        sentence = ", ".join(sorted({passage.page for passage in found})) or "nothing"
        say(f"search {question!r}: {sentence}")
        if not found or not any(word in f"{p.title} {p.text}".lower() for p in found):
            say(f"  expected a passage that speaks of {word!r}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
