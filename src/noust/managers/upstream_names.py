# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The name of the upstream Noust generates, and who else writes it down.

Blue/green activation and the Compose relay give nginx an ``upstream`` block of their own. Until
4.0 it was named ``wasm_bg_<app>``; from 4.0 it is ``noust_bg_<app>``. Noust renames what it
wrote, and only that: the files it generated (the upstream file, the application's own site)
change together, in one step that nginx has tested before it is loaded. A site the operator wrote
may say ``proxy_pass http://wasm_bg_<app>`` (a hand-written edge in front of an application
Noust runs as instances does), and that file is never rewritten, so the old name stays in force
for as long as such a site names it. :func:`scan` reads the web server's configuration for it,
:func:`rename` completes what is free to be completed, and ``noust setup doctor`` prints the
line an operator has to change, by file and line number.

Reading is by the one parser of site files (:mod:`noust.managers.siteconf`), not by a pattern
over the text: a comment that talks about the old name is not a reference to it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core import legacy_names, paths
from noust.managers.siteconf.tree import Block, Directive, Node, ParseError, parse

if TYPE_CHECKING:
    from noust.managers.webserver import WebServerManager

__all__ = [
    "NEW_PREFIX",
    "OLD_PREFIX",
    "Reference",
    "RenameOutcome",
    "Verdict",
    "defined_names",
    "legacy_references",
    "name_for",
    "references_in",
    "rename",
    "renamed_text",
    "scan",
]

_logger = logging.getLogger(__name__)

#: What the upstream Noust generates is named after, and what it was named after.
NEW_PREFIX = "noust_bg_"
OLD_PREFIX = legacy_names.UPSTREAM_PREFIX

#: The identifiers of the old spelling in a generated file: the prefix and what nginx takes in a name.
_OLD_NAME = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(OLD_PREFIX)}[A-Za-z0-9_]+")

#: A generated file is at most this large; a bigger one is not read (nothing Noust writes is).
_MAX_BYTES = 1024 * 1024

#: The mode a site file is written with.
_FILE_MODE = 0o644


@dataclass(frozen=True)
class Reference:
    """
    A line of a file somebody else wrote that names an upstream.

    Attributes:
        path: The file.
        line: Its 1-based line number.
        text: The directive as written, on one line.
    """

    path: Path
    line: int
    text: str


@dataclass
class Verdict:
    """
    One old upstream name found in files Noust generated, and whether it can go.

    Attributes:
        name: The old name (``wasm_bg_shop_example_com``).
        new_name: The name it becomes.
        files: The generated files that carry it.
        references: The lines of other files that name it; while there are any it stays.
    """

    name: str
    new_name: str
    files: list[Path] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)

    @property
    def free(self) -> bool:
        """Whether no file somebody else wrote names the old name."""
        return not self.references


@dataclass
class RenameOutcome:
    """
    What :func:`rename` did about one name.

    Attributes:
        name: The old name.
        new_name: The name it became, or would have.
        status: ``renamed``; ``kept`` (an operator's site names the old one); ``failed``
            (nginx refused the result, which was put back); ``rehearsed`` (nothing written).
        files: The files that were changed.
        references: The lines that keep the old name.
        detail: What nginx said, verbatim, when it failed.
    """

    name: str
    new_name: str
    status: str
    files: list[Path] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    detail: str = ""


# -- Text ----------------------------------------------------------------------------------------


def _word_in(name: str, value: str) -> bool:
    """
    Tell whether a token names an identifier, not merely contains it.

    Args:
        name: The identifier.
        value: A directive argument.

    Returns:
        True when ``name`` appears in ``value`` with no name character on either side.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", value) is not None


def _statements(nodes: list[Node]) -> Iterator[Directive | Block]:
    """
    Walk the directives and blocks of a tree, depth first.

    Args:
        nodes: The children of a tree or a block.

    Yields:
        Each statement; comments are not statements.
    """
    for node in nodes:
        if isinstance(node, Directive):
            yield node
        elif isinstance(node, Block):
            yield node
            yield from _statements(node.children)


def references_in(text: str, name: str) -> list[tuple[int, str]]:
    """
    Find the directives of an nginx file that name an upstream.

    A block that *defines* the name (``upstream wasm_bg_shop {``) is not a reference to it: that
    is somebody giving their own block Noust's old convention, and it works as it is.

    Args:
        text: The file.
        name: The upstream's name.

    Returns:
        The line and the text of each directive that has ``name`` as a word in an argument,
        comments excluded. A file the parser cannot read is searched line by line instead,
        so a mistake in parsing never lets a name go that somebody uses.
    """
    try:
        tree = parse(text, "nginx")
    except ParseError:
        return _plain_references(text, name)
    found: list[tuple[int, str]] = []
    for statement in _statements(tree.children):
        if isinstance(statement, Block) and statement.name.value == "upstream":
            continue
        if any(_word_in(name, arg.word.value) for arg in statement.args):
            found.append((statement.line, " ".join(statement.render_head().split())))
    return found


def _plain_references(text: str, name: str) -> list[tuple[int, str]]:
    """
    Search a file the parser would not read, line by line, leaving comments out.

    Args:
        text: The file.
        name: The upstream's name.

    Returns:
        The line number and the text of each line that names it.
    """
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        code = line.split("#", 1)[0]
        if _word_in(name, code) and not re.match(r"\s*upstream\s", code):
            found.append((number, " ".join(code.split())))
    return found


def defined_names(text: str) -> list[str]:
    """
    List the upstream blocks a file defines.

    Args:
        text: An nginx file.

    Returns:
        The names, in order. Empty for a file the parser cannot read.
    """
    try:
        tree = parse(text, "nginx")
    except ParseError:
        return []
    return [
        statement.args[0].word.value
        for statement in _statements(tree.children)
        if isinstance(statement, Block) and statement.name.value == "upstream" and statement.args
    ]


def renamed_text(text: str, old: str, new: str) -> str:
    """
    Replace an identifier in a file Noust generated.

    Args:
        text: The file.
        old: The name to replace.
        new: What it becomes.

    Returns:
        The text with every whole-word occurrence replaced.
    """
    return re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, text)


def new_name_of(old: str) -> str:
    """
    Turn an old upstream name into the one Noust writes now.

    Args:
        old: A name that starts with the old prefix.

    Returns:
        The same name with the new prefix.
    """
    return NEW_PREFIX + old[len(OLD_PREFIX) :]


# -- Files ---------------------------------------------------------------------------------------


def _config_files(manager: WebServerManager) -> list[Path]:
    """
    List the files of the web server's configuration that can name an upstream.

    Args:
        manager: The web server manager.

    Returns:
        The sites, the ``conf.d`` drop-ins, the upstream files and the main file, each once.
    """
    backend = manager.backend
    root = backend.sites_available.parent
    directories = [backend.sites_available, backend.sites_enabled, root / "conf.d"]
    if backend.upstreams_dir is not None:
        directories.append(backend.upstreams_dir)
    candidates: list[Path] = []
    for directory in directories:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        candidates.extend(entry for entry in entries if entry.name not in {".", ".."})
    candidates.append(root / "nginx.conf")
    seen: set[Path] = set()
    files: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        files.append(resolved)
    return files


def _read(path: Path) -> str | None:
    """
    Read a configuration file, leaving out what is not text or is too large.

    Args:
        path: The file.

    Returns:
        Its text, or None.
    """
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _generated(text: str) -> bool:
    """
    Tell whether Noust wrote a file, by the marker on its first lines.

    Args:
        text: The file.

    Returns:
        True when the marker is in the head of the file.
    """
    return paths.carries_unit_marker(text[:2000])


def legacy_references(manager: WebServerManager, name: str) -> list[Reference]:
    """
    Find the lines of files somebody else wrote that name an old upstream.

    Args:
        manager: The web server manager.
        name: The old name.

    Returns:
        Where it is named; empty when nothing but Noust's own files does.
    """
    found: list[Reference] = []
    for path in _config_files(manager):
        text = _read(path)
        if text is None or _generated(text) or name not in text:
            continue
        found.extend(
            Reference(path, line, directive) for line, directive in references_in(text, name)
        )
    return found


def name_for(manager: WebServerManager, suffix: str) -> str:
    """
    Choose the name to give an upstream Noust is about to write.

    Args:
        manager: The web server manager.
        suffix: What follows the prefix (``shop_example_com``).

    Returns:
        The old name while a file somebody else wrote names it, else the new one.
    """
    old = OLD_PREFIX + suffix
    return old if legacy_references(manager, old) else NEW_PREFIX + suffix


def scan(manager: WebServerManager) -> list[Verdict]:
    """
    Find every old upstream name in the files Noust generated, and who else names it.

    Args:
        manager: The web server manager.

    Returns:
        One verdict per name, sorted; empty when nothing carries the old prefix.
    """
    carriers: dict[str, list[Path]] = {}
    texts: dict[Path, str] = {}
    for path in _config_files(manager):
        text = _read(path)
        if text is None or OLD_PREFIX not in text or not _generated(text):
            continue
        texts[path] = text
        for match in sorted(set(_OLD_NAME.findall(text))):
            carriers.setdefault(match, []).append(path)
    verdicts = []
    for name in sorted(carriers):
        verdicts.append(
            Verdict(
                name=name,
                new_name=new_name_of(name),
                files=carriers[name],
                references=legacy_references(manager, name),
            )
        )
    return verdicts


def rename(manager: WebServerManager) -> list[RenameOutcome]:
    """
    Give every old upstream name that nobody else names its new one.

    The files that carry a name change together; nginx tests the result before it is loaded, and
    on a refusal every file goes back as it was and the outcome says what nginx said.

    Args:
        manager: The web server manager.

    Returns:
        What happened to each name found; empty when there was none.
    """
    verdicts = scan(manager)
    outcomes: list[RenameOutcome] = []
    changes: dict[Path, tuple[str, str]] = {}
    for verdict in verdicts:
        if not verdict.free:
            outcomes.append(
                RenameOutcome(
                    verdict.name,
                    verdict.new_name,
                    "kept",
                    files=verdict.files,
                    references=verdict.references,
                )
            )
            continue
        for path in verdict.files:
            before = changes[path][1] if path in changes else _read(path)
            if before is None:
                continue
            original = changes[path][0] if path in changes else before
            changes[path] = (original, renamed_text(before, verdict.name, verdict.new_name))
        outcomes.append(
            RenameOutcome(verdict.name, verdict.new_name, "renamed", files=verdict.files)
        )
    if not changes:
        return outcomes
    if manager._rehearsing():
        for outcome in outcomes:
            if outcome.status == "renamed":
                outcome.status = "rehearsed"
        return outcomes

    written: list[Path] = []
    try:
        for path, (_, after) in changes.items():
            manager.fs.write_text(path, after, mode=_mode_of(path))
            written.append(path)
    except OSError as exc:
        _put_back(manager, changes, written)
        return _failed(outcomes, f"{exc}")
    problem = manager.config_errors()
    if problem is not None:
        _put_back(manager, changes, written)
        return _failed(outcomes, problem)
    if not manager.reload():
        _put_back(manager, changes, written)
        manager.reload()
        return _failed(outcomes, "The configuration is valid but the web server did not reload.")
    return outcomes


def _mode_of(path: Path) -> int:
    """
    Read the permissions a file has, so a rewrite keeps them.

    Args:
        path: The file.

    Returns:
        Its permission bits, or the mode site files are written with.
    """
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return _FILE_MODE


def _put_back(
    manager: WebServerManager, changes: dict[Path, tuple[str, str]], written: list[Path]
) -> None:
    """
    Restore the files a rename wrote.

    Args:
        manager: The web server manager.
        changes: Each file's text before and after.
        written: The files already written.
    """
    for path in written:
        try:
            manager.fs.write_text(path, changes[path][0], mode=_mode_of(path))
        except OSError as exc:
            _logger.warning("Could not put %s back after a refused rename: %s", path, exc)


def _failed(outcomes: list[RenameOutcome], detail: str) -> list[RenameOutcome]:
    """
    Mark the renames that were attempted as failed.

    Args:
        outcomes: The outcomes so far.
        detail: Why.

    Returns:
        The same outcomes, the renamed ones now failed.
    """
    for outcome in outcomes:
        if outcome.status == "renamed":
            outcome.status = "failed"
            outcome.detail = detail
    return outcomes
