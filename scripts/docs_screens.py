#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The pictures of the console in the documentation, and whether they still show it.

``docs/assets/console/SCREENS.json`` records, for the pictures the README and the guides link,
the release they were taken at and the SHA-256 of each file. The console draws its own version in
its sidebar and on the Overview, so a picture says what it shows; this says it too, where a
machine can read it.

    scripts/docs_screens.py write            Record the pictures in the folder at this release.
                                             ``npm run docs:screens`` runs it after retaking them.
    scripts/docs_screens.py check            Fail when a picture was changed by hand, is not
                                             recorded, is gone, or is linked and missing.
    scripts/docs_screens.py check --release  Also fail when they show an earlier minor release.

Between releases the pictures are one minor release behind by design, so that is only a failure
when publishing (the release workflow passes ``--release``): a tag is the moment a reader is
promised that what the documentation shows is what the product is.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FOLDER = Path("docs") / "assets" / "console"
MANIFEST = FOLDER / "SCREENS.json"

#: Where the text names a picture: a raw GitHub address or a path from a page of docs/.
_LINKED = re.compile(r"(?:docs/)?assets/console/([\w.-]+\.png)")

RETAKE = "retake them with `cd panel && npm run docs:screens`, then commit docs/assets/console"


def project_line(root: Path = ROOT) -> str:
    """
    Read ``<major>.<minor>`` from ``pyproject.toml``, the version's one source.

    Args:
        root: The repository root.

    Returns:
        The minor line, such as ``3.4``.

    Raises:
        ValueError: The project version cannot be read.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.', text, re.MULTILINE)
    if match is None:
        raise ValueError("pyproject.toml has no [project] version")
    return f"{match.group(1)}.{match.group(2)}"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pictures(root: Path) -> dict[str, str]:
    folder = root / FOLDER
    return {path.name: _digest(path) for path in sorted(folder.glob("*.png"))}


def write(root: Path = ROOT, *, release: str | None = None) -> Path:
    """
    Record the pictures in the folder.

    Args:
        root: The repository root.
        release: The release the pictures show, when it is not the checkout's: the pictures of an
            earlier release being recorded for the first time say so here.

    Returns:
        The manifest written.
    """
    manifest = root / MANIFEST
    body = {
        "about": "Written by scripts/docs_screens.py. The release is the one the pictures show.",
        "release": release or project_line(root),
        "images": _pictures(root),
    }
    manifest.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return manifest


def _linked(root: Path) -> dict[str, list[str]]:
    """The pictures the text links, with the pages that link them."""
    pages = [root / "README.md", *sorted((root / "docs").glob("**/*.md"))]
    found: dict[str, list[str]] = {}
    for page in pages:
        if not page.is_file():
            continue
        for name in sorted(set(_LINKED.findall(page.read_text(encoding="utf-8")))):
            found.setdefault(name, []).append(page.relative_to(root).as_posix())
    return found


def check(root: Path = ROOT, *, publishing: bool = False) -> list[str]:
    """
    Say what is wrong with the pictures.

    Args:
        root: The repository root.
        publishing: Whether this is the release check, where pictures of an earlier minor
            release are a failure and not a note.

    Returns:
        One sentence per problem, each with what to run; empty when the pictures are in order.
    """
    manifest = root / MANIFEST
    if not manifest.is_file():
        return [
            f"{MANIFEST.as_posix()} is missing: record the pictures with `python scripts/docs_screens.py write`"
        ]
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    images: dict[str, str] = recorded.get("images", {})
    present = _pictures(root)

    problems: list[str] = []
    for name, digest in present.items():
        if name not in images:
            problems.append(f"{name} is not recorded in {MANIFEST.as_posix()}: {RETAKE}")
        elif images[name] != digest:
            problems.append(f"{name} changed since it was recorded: {RETAKE}")
    for name in images:
        if name not in present:
            problems.append(f"{name} is recorded and missing from {FOLDER.as_posix()}: {RETAKE}")
    for name, pages in _linked(root).items():
        if name not in present:
            problems.append(
                f"{name} is linked from {', '.join(pages)} and is not in {FOLDER.as_posix()}"
            )

    shown, now = str(recorded.get("release", "")), project_line(root)
    if shown != now:
        sentence = f"the pictures in the documentation show {shown or 'an unknown release'}, and this is {now}: {RETAKE}"
        if publishing:
            problems.append(sentence[0].upper() + sentence[1:])
        else:
            print(f"note: {sentence}", file=sys.stderr)
    return problems


def main(argv: list[str] | None = None) -> int:
    """
    Run the command line.

    Args:
        argv: The arguments, without the program name.

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(description="The documentation's pictures of the console.")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("write", help="record the pictures in docs/assets/console")
    record.add_argument(
        "--release", help="the release the pictures show, when it is not this checkout's (3.1)"
    )
    verify = commands.add_parser("check", help="fail when the pictures are not in order")
    verify.add_argument(
        "--release", action="store_true", help="publishing: pictures of an earlier release fail"
    )
    args = parser.parse_args(argv)

    if args.command == "write":
        manifest = write(release=args.release)
        print(
            f"Recorded {len(json.loads(manifest.read_text(encoding='utf-8'))['images'])} pictures in {MANIFEST.as_posix()}"
        )
        return 0
    problems = check(publishing=args.release)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print("The documentation's pictures are in order.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
