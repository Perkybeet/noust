#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write ``src/noust/docs``: the documentation the site publishes, inside the Python package (item 171).

The built-in assistant looks the documentation up in files (``search_docs``), and the files used to
be where only a checkout and the RPM had them. They are now a copy in the package itself, so the wheel
(``pip install noust``), the Debian package, the RPM and the central's image all carry them without a
recipe of their own for it, and the same Noust offers the same tool whichever way it was installed.

What travels is what ``docs/site.yaml`` publishes from ``docs/``, in Markdown, without the pages that
say what changed (a changelog, an upgrade guide, the API's changes: the index skips them by name, so
they would only weigh) and without ``docs/assets``. The pages are copied byte for byte, so git keeps
one copy of each and a diff shows the edit once. ``docs/`` stays the only place a page is edited.

The copy is committed, like the console's build and the shell completions, because OBS builds from
``git archive`` with no network and no step before the build. CI and a test run ``--check`` and fail
when it is stale: after editing a page or the manifest, run this.

Usage:
    scripts/generate_packaged_docs.py          Write the copy.
    scripts/generate_packaged_docs.py --check  Exit 1 when the committed copy is not what is written.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.core.assistant.docsearch import is_history  # noqa: E402


class PackagedDocsError(Exception):
    """The manifest of the site names a page that is not there."""


def published_pages(root: Path = ROOT) -> list[str]:
    """
    Args:
        root: The repository.

    Returns:
        The file name of each page the site publishes from ``docs/`` and the package carries, in order.

    Raises:
        PackagedDocsError: A page the manifest lists does not exist.
    """
    manifest = yaml.safe_load((root / "docs" / "site.yaml").read_text(encoding="utf-8"))
    # The same traversal as scripts/build_docs.py `listed_sources`, which the site reads; not imported,
    # because that module needs the Markdown renderer and this one needs only what Noust needs.
    sources = [
        item["source"]
        for tab in manifest["tabs"]
        for group in tab["groups"]
        for item in group["pages"]
    ]
    names = []
    for source in sources:
        path = PurePosixPath(source)
        if path.parent != PurePosixPath("docs") or is_history(path.name):
            continue
        if not (root / source).is_file():
            raise PackagedDocsError(f"{source} is listed in docs/site.yaml and does not exist")
        names.append(path.name)
    return sorted(set(names))


def problems(root: Path = ROOT) -> list[str]:
    """
    Args:
        root: The repository.

    Returns:
        What differs between the committed copy and the pages as they are, one sentence each; empty
        when the copy is current.

    Raises:
        PackagedDocsError: A page the manifest lists does not exist.
    """
    packaged = root / "src" / "noust" / "docs"
    wanted = published_pages(root)
    found: list[str] = []
    for name in wanted:
        copy = packaged / name
        if not copy.is_file():
            found.append(f"{name} is not in the package")
        elif copy.read_bytes() != (root / "docs" / name).read_bytes():
            found.append(f"{name} differs from docs/{name}")
    if packaged.is_dir():
        found.extend(
            f"{path.name} is in the package and is not a published page"
            for path in sorted(packaged.iterdir())
            if path.name not in wanted
        )
    return found


def write(root: Path = ROOT) -> list[str]:
    """
    Make the copy what the pages say: copy what changed and remove what is no longer published.

    Args:
        root: The repository.

    Returns:
        The file names the package carries after it.

    Raises:
        PackagedDocsError: A page the manifest lists does not exist (nothing is written).
    """
    packaged = root / "src" / "noust" / "docs"
    wanted = published_pages(root)
    packaged.mkdir(parents=True, exist_ok=True)
    for path in sorted(packaged.iterdir()):
        if path.name not in wanted:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    for name in wanted:
        text = (root / "docs" / name).read_bytes()
        copy = packaged / name
        if not copy.is_file() or copy.read_bytes() != text:
            copy.write_bytes(text)
    return wanted


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: The arguments; ``sys.argv`` by default.

    Returns:
        0 when the copy was written or is current, 1 when ``--check`` finds it stale, 2 when the
        manifest names a page that does not exist.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="Fail when the copy is stale.")
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="The repository (default: this one)."
    )
    args = parser.parse_args(argv)
    try:
        if args.check:
            found = problems(args.root)
            for line in found:
                print(line)
            if found:
                print(
                    "The packaged documentation is stale: run python scripts/generate_packaged_docs.py"
                )
            return 1 if found else 0
        names = write(args.root)
    except PackagedDocsError as error:
        print(error, file=sys.stderr)
        return 2
    size = sum((args.root / "src" / "noust" / "docs" / name).stat().st_size for name in names)
    print(f"{len(names)} pages, {size / 1_000_000:.1f} MB, in src/noust/docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
