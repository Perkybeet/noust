# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sites Noust generated that still ask nginx to staple OCSP responses (item 172).

Let's Encrypt turned its OCSP service off on 2025-08-06 and its certificates carry no OCSP address
since, so nginx with ``ssl_stapling on`` logs ``"ssl_stapling" ignored, no OCSP responder URL in
the certificate`` at every reload, for every site. Noust's templates no longer write the two
directives; a site it generated earlier changes the next time Noust writes it (a deployment, a
change of its domains), and ``noust setup doctor`` names the ones that still carry them.

A site somebody else wrote is never read for this, let alone changed: the marker Noust puts on the
first lines of what it generates decides, the same way it does everywhere
(:func:`noust.core.paths.carries_unit_marker`). A certificate from another authority that does carry
an OCSP address is stapled by whoever wrote that site's file.
"""

from __future__ import annotations

from pathlib import Path

from noust.core import paths
from noust.managers.siteconf.tree import Block, Directive, Node, ParseError, parse

#: The directives that ask nginx to staple.
_STAPLING = ("ssl_stapling", "ssl_stapling_verify")

#: A site file is at most this large; a bigger one is not read (nothing Noust writes is).
_MAX_BYTES = 1024 * 1024


def _directives(nodes: list[Node]) -> list[Directive]:
    """
    Args:
        nodes: The children of a tree or of a block.

    Returns:
        Every directive in them, at any depth. Comments are not directives.
    """
    found: list[Directive] = []
    for node in nodes:
        if isinstance(node, Directive):
            found.append(node)
        elif isinstance(node, Block):
            found.extend(_directives(node.children))
    return found


def staples(text: str) -> bool:
    """
    Tell whether a site turns stapling on.

    Args:
        text: An nginx site file.

    Returns:
        True when ``ssl_stapling`` or ``ssl_stapling_verify`` is set to ``on``. A file the parser
        cannot read is judged on its lines instead, so a mistake in parsing never hides a site.
    """
    try:
        tree = parse(text, "nginx")
    except ParseError:
        return any(
            line.strip().startswith(directive) and line.strip().rstrip(";").endswith(" on")
            for line in text.splitlines()
            for directive in _STAPLING
        )
    return any(
        directive.name.value in _STAPLING and any(arg.word.value == "on" for arg in directive.args)
        for directive in _directives(tree.children)
    )


def generated_sites_with_stapling(sites_dir: Path) -> list[str]:
    """
    Name the sites Noust generated that still turn stapling on.

    Args:
        sites_dir: Where the web server's site files are (``sites-available``).

    Returns:
        The names of the files, sorted; empty when there is none or the directory is not there.
    """
    try:
        entries = sorted(sites_dir.iterdir())
    except OSError:
        return []
    found: list[str] = []
    for entry in entries:
        try:
            if not entry.is_file() or entry.stat().st_size > _MAX_BYTES:
                continue
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if paths.carries_unit_marker(text[:2000]) and staples(text):
            found.append(entry.name)
    return found
