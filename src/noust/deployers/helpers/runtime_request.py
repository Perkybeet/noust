# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which runtime a project says it needs, from the files it already has.

A repository that pins its runtime says so where its own tools read it:
``.nvmrc`` and ``.node-version`` for Node, ``engines.node`` in ``package.json``,
``.python-version`` and Heroku's ``runtime.txt`` for Python, ``require.php`` in
``composer.json`` for PHP. Reading them is what lets a project deploy on the
version it was written for without anyone repeating it in ``noust.yaml``.

What is read here is a hint, never an order (``RuntimeRequest.explicit`` is
False): a file in a repository is as untrusted as the rest of it, and a stale
``.nvmrc`` must not break a deployment that works. The runtime manager decides
what a hint is worth (:meth:`noust.managers.runtimes.RuntimeManager.choose`).
A file that cannot be understood is reported and skipped, not an error.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from noust.core.exceptions import ValidationError
from noust.core.versions import RuntimeRequest

#: Largest version file or manifest read.
MAX_FILE_BYTES = 1024 * 1024

#: What a version file may say for "the newest": nvm's own aliases.
_NODE_ALIAS = re.compile(r"^(?:lts(?:/[\w*]+)?|node|latest|stable|current)$", re.IGNORECASE)
_PYTHON_VERSION = re.compile(r"^(?:cpython-?)?(\d+(?:\.\d+){0,2})$", re.IGNORECASE)
_RUNTIME_TXT = re.compile(r"^python-(\d+\.\d+(?:\.\d+)?)$", re.IGNORECASE)

#: A hint and the notes (files that could not be read) found looking for it.
Detected = tuple[RuntimeRequest | None, list[str]]


def _read(path: Path) -> str | None:
    """
    Read a small text file of the repository, never through a link.

    Args:
        path: The file.

    Returns:
        Its text, or None when it is not a regular file, is too large or is
        not UTF-8.
    """
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _first_line(text: str) -> str:
    """
    Take the first line of a version file that says something.

    Args:
        text: The file's content.

    Returns:
        The line, stripped; empty when there is none.
    """
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            return line
    return ""


def _json(path: Path) -> dict | None:
    """
    Read a JSON manifest.

    Args:
        path: ``package.json`` or ``composer.json``.

    Returns:
        Its object, or None.
    """
    text = _read(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _node(directory: Path) -> Detected:
    """
    Read the Node version a directory asks for.

    Args:
        directory: The project (or the root of the repository).

    Returns:
        ``.nvmrc``, ``.node-version``, then ``engines.node``, in that order:
        the files a developer edits to say it, before the manifest's range.
    """
    notes: list[str] = []
    for name in (".nvmrc", ".node-version"):
        text = _read(directory / name)
        if text is None:
            continue
        line = _first_line(text)
        if not line:
            continue
        if _NODE_ALIAS.match(line):
            return RuntimeRequest("node", line.lower(), explicit=False, source=name), notes
        bare = line.lstrip("vV")
        if re.fullmatch(r"\d+(\.\d+){0,2}", bare):
            return RuntimeRequest("node", bare, explicit=False, source=name), notes
        notes.append(f"{name} says {line!r}, which is not a Node version")
    manifest = _json(directory / "package.json")
    engines = (manifest or {}).get("engines")
    spec = engines.get("node") if isinstance(engines, dict) else None
    if isinstance(spec, str) and spec.strip():
        request = RuntimeRequest("node", spec.strip(), explicit=False, source="engines.node")
        return _readable(request, notes)
    return None, notes


def _python(directory: Path) -> Detected:
    """
    Read the Python version a directory asks for.

    Args:
        directory: The project (or the root of the repository).

    Returns:
        ``.python-version`` (the first version it lists), then Heroku's
        ``runtime.txt``.
    """
    notes: list[str] = []
    text = _read(directory / ".python-version")
    if text is not None:
        line = _first_line(text)
        match = _PYTHON_VERSION.match(line)
        if match:
            return RuntimeRequest(
                "python", match.group(1), explicit=False, source=".python-version"
            ), notes
        notes.append(f".python-version says {line!r}, which is not a CPython version")
    text = _read(directory / "runtime.txt")
    if text is not None:
        line = _first_line(text)
        match = _RUNTIME_TXT.match(line)
        if match:
            return RuntimeRequest(
                "python", match.group(1), explicit=False, source="runtime.txt"
            ), notes
        notes.append(f"runtime.txt says {line!r}, which is not python-X.Y.Z")
    return None, notes


def _php(directory: Path) -> Detected:
    """
    Read the PHP version a directory asks for.

    Args:
        directory: The project.

    Returns:
        ``require.php`` of ``composer.json``.
    """
    manifest = _json(directory / "composer.json")
    require = (manifest or {}).get("require")
    spec = require.get("php") if isinstance(require, dict) else None
    if isinstance(spec, str) and spec.strip():
        request = RuntimeRequest("php", spec.strip(), explicit=False, source="composer.json")
        return _readable(request, [])
    return None, []


def _readable(request: RuntimeRequest, notes: list[str]) -> Detected:
    """
    Keep a hint only when its range can be read.

    Args:
        request: The hint.
        notes: What was found so far.

    Returns:
        The hint, or None with a note saying why not.
    """
    try:
        request.version_range()
    except ValidationError as exc:
        return None, [*notes, f"{request.source} says {request.spec!r}: {exc.message}"]
    return request, notes


_DETECTORS: dict[str, Callable[[Path], Detected]] = {
    "node": _node,
    "python": _python,
    "php": _php,
}


def detect_request(language: str, project: Path, root: Path | None = None) -> Detected:
    """
    Find the runtime of a language that a project's files ask for.

    Args:
        language: ``node``, ``python`` or ``php``.
        project: The directory the project lives in (the base directory).
        root: The root of the repository, when the project is inside it: a
            monorepo keeps one ``.nvmrc`` at the top for every app.

    Returns:
        The hint, or None, and the files that said something unreadable.
    """
    detect = _DETECTORS[language]
    request, notes = detect(project)
    if request is None and root is not None and root != project:
        request, more = detect(root)
        notes = [*notes, *more]
    return request, notes


__all__ = ["Detected", "detect_request"]
