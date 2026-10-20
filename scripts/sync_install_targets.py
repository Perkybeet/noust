#!/usr/bin/env python3
"""
Keep the installer's platform table, the sizes it checks and the documents equal to the code's.

The table of releases Noust installs on is ``TARGETS`` in
``noust/managers/server/platforms.py``, and the memory, processors and free disk a server needs
are ``REQUIREMENTS`` in ``noust/managers/server/requirements.py``. ``packaging/install.sh``
cannot import either (it runs before Noust is installed), so it carries a generated copy of
each between two marker lines, and ``docs/PLATFORMS.md`` and ``docs/requirements.md`` show the
same rows as a table between their own markers. This script rewrites all four, or with
``--check`` fails when any differs, which is what CI runs.

Usage:
    scripts/sync_install_targets.py           Rewrite the installer and the document.
    scripts/sync_install_targets.py --check   Exit 1 when either is out of date.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.managers.server import platforms, requirements  # noqa: E402

INSTALLER = ROOT / "packaging" / "install.sh"
DOCUMENT = ROOT / "docs" / "PLATFORMS.md"
REQUIREMENTS_DOCUMENT = ROOT / "docs" / "requirements.md"

DOC_BEGIN = "<!-- BEGIN TARGETS (generated: scripts/sync_install_targets.py) -->"
DOC_END = "<!-- END TARGETS -->"


def replace_document_table(text: str) -> str:
    """
    Put the current table into the platform document.

    Args:
        text: The document.

    Returns:
        The same text with the table between the markers regenerated.

    Raises:
        ValueError: When the markers are missing or out of order.
    """
    begin = text.find(DOC_BEGIN)
    end = text.find(DOC_END)
    if begin < 0 or end < begin:
        raise ValueError("docs/PLATFORMS.md has no BEGIN TARGETS / END TARGETS markers")
    return f"{text[:begin]}{DOC_BEGIN}\n\n{platforms.markdown_table()}\n{text[end:]}"


def main(argv: list[str] | None = None) -> int:
    """
    Rewrite or check the two generated blocks.

    Args:
        argv: Arguments; ``sys.argv`` by default.

    Returns:
        0 when everything is in step (or was written), 1 when ``--check`` finds a difference.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="fail instead of writing")
    args = parser.parse_args(argv)

    pairs = (
        (INSTALLER, platforms.replace_installer_block),
        (INSTALLER, requirements.replace_installer_block),
        (DOCUMENT, replace_document_table),
        (REQUIREMENTS_DOCUMENT, requirements.replace_document_table),
    )
    stale = []
    for path, render in pairs:
        current = path.read_text(encoding="utf-8")
        wanted = render(current)
        if wanted == current:
            continue
        if args.check:
            name = path.relative_to(ROOT).as_posix()
            if name not in stale:
                stale.append(name)
        else:
            path.write_text(wanted, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    if stale:
        print(
            "Out of date: " + ", ".join(stale) + ". Run scripts/sync_install_targets.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
