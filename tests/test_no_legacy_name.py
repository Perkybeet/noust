# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The product before 3.0 had another name, and nothing but a short list of files may spell it
(item 84).

Noust 4.0 does not carry the old name around. What still has to recognise a name from
before the rename (a header a script sends, a token in a CI secret, a backup made years ago)
asks :mod:`noust.core.legacy_names` for it, and the one module that refuses to start on a
server 3.x never moved, :mod:`noust.core.retired_names`, spells the old paths because that is
what it looks for. Everywhere else the word is a leftover: a comment that tells a story nobody
needs, a temporary file named after a product that is gone, a document for a release that
cannot be installed any more.

The match ignores case and does not care about word boundaries, so ``WASM``, ``wasm-web`` and
``_wasm_init`` are all caught. The files below are the only ones allowed to spell it, each
with the reason, and the list can only shrink: a file that stops needing the word must come
off it (``test_every_exception_is_still_needed``), so an exception never outlives its reason.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.tree_support import tree_files

ROOT = Path(__file__).parent.parent

#: Where the old name is refused. The tests are not here: they exercise the old spellings on
#: purpose, and each of them is held to ``legacy_names`` by the module under test.
SCANNED_DIRECTORIES = (
    "src",
    "packaging",
    "docs",
    "scripts",
    "obs",
    "rpm",
    ".github",
    "man",
)

#: Files at the top of the repository that a reader or a package build opens.
SCANNED_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "NOTICE",
    "LICENSE",
    "Makefile",
    "MANIFEST.in",
    "pyproject.toml",
    "setup.py",
    ".gitignore",
)

#: The console's own sources: what it stores in a browser, what its mock and its screenshot
#: scripts pretend to be, and the names it invents for its fixtures. The build in
#: ``src/noust/web/static`` follows from them and is not scanned itself.
CONSOLE_SOURCES: tuple[str, ...] = ("panel/src", "panel/e2e", "panel/scripts", "panel/mock")

#: The console's configuration files that sit beside those directories.
CONSOLE_FILES: tuple[str, ...] = (
    "panel/vite.config.ts",
    "panel/playwright.config.ts",
    "panel/eslint.config.js",
)

#: Tracked and not read: the console's generated build is compiled from ``panel/``, and its
#: API types are generated from the OpenAPI document, which spells the two names the API still
#: accepts from a client of before 3.0 (``legacy_names.ONLY_PARAMETER`` and
#: ``legacy_names.EXPORT_VERSION_KEY``). The console never writes or reads them. What git
#: ignores (a virtual environment, working material, a build directory) is never read either.
SKIPPED_PATHS = ("src/noust/web/static", "panel/src/api/schema.gen.ts")

OLD_NAME = re.compile("wasm", re.IGNORECASE)

LineFilter = Callable[[list[str]], set[int]]


def _all_lines(lines: list[str]) -> set[int]:
    """Allow every line of the file."""
    return set(range(len(lines)))


def _prefixed(*prefixes: str) -> LineFilter:
    """Allow the lines that start with one of ``prefixes`` (a packaging field)."""

    def allowed(lines: list[str]) -> set[int]:
        return {i for i, line in enumerate(lines) if line.startswith(prefixes)}

    return allowed


def _spec_lines(lines: list[str]) -> set[int]:
    """Allow the spec's ``Conflicts`` line, its ``%pre`` script and its ``%changelog``."""
    allowed: set[int] = set()
    section = ""
    for i, line in enumerate(lines):
        if line.startswith("%") and not line.startswith(("%if", "%else", "%endif")):
            section = line.split()[0]
        if section in ("%pre", "%changelog") or line.startswith("Conflicts:"):
            allowed.add(i)
    return allowed


#: Path -> (why it may spell the old name, which of its lines may). A file that is history is
#: allowed whole; a packaging file only where the old name is what the line is about.
ALLOWED: dict[str, tuple[str, LineFilter]] = {
    "src/noust/core/legacy_names.py": (
        "the one module that spells every name from before 4.0 that is still accepted, each "
        "with why and until when",
        _all_lines,
    ),
    "src/noust/core/retired_names.py": (
        "refuses to start on a server whose data 3.x never moved, so it looks for the old "
        "paths, and retires the links 3.x left",
        _all_lines,
    ),
    "obs/debian.preinst": (
        "stops an upgrade over data that was never moved to the new paths: it has to look "
        "for the old ones",
        _all_lines,
    ),
    "obs/debian.control": (
        "Conflicts: the package it replaced",
        _prefixed("Conflicts:"),
    ),
    "rpm/noust.spec": (
        "Conflicts, the %pre that stops an upgrade over unmoved data, and the %changelog",
        _spec_lines,
    ),
    "obs/debian.changelog": ("history of the releases, not rewritten", _all_lines),
    "src/noust/changelog.json": ("history of the releases, not rewritten", _all_lines),
    "docs/UPGRADING-4.0.md": (
        "the guide to the rename: an operator looks the old name up here to learn the new one",
        _all_lines,
    ),
    "docs/UPGRADING-2.0.md": (
        "the guide of a release of the product's previous name; it is history, out of the "
        "documentation's navigation",
        _all_lines,
    ),
}

#: Whole families of history: a release's changelog says what the release was called.
HISTORY = re.compile(r"docs/CHANGELOG-[0-9.]+\.md")


def _files() -> list[Path]:
    """Every text file the guard reads."""
    root_files = [
        ROOT / name for name in (*SCANNED_FILES, *CONSOLE_FILES) if (ROOT / name).is_file()
    ]
    return [
        *root_files,
        *tree_files(ROOT, under=[*SCANNED_DIRECTORIES, *CONSOLE_SOURCES], skip=SKIPPED_PATHS),
    ]


def _read(path: Path) -> list[str] | None:
    """The lines of a text file, or ``None`` for a binary one."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _hits(path: Path) -> list[tuple[int, str]]:
    """Line number and text of every line of ``path`` that spells the old name."""
    lines = _read(path)
    if lines is None:
        return []
    return [(i, line) for i, line in enumerate(lines) if OLD_NAME.search(line)]


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_the_old_name_is_spelled_only_where_it_is_allowed() -> None:
    """No file outside the list spells it, and a listed one only on the lines that need to."""
    offenders: list[str] = []
    for path in _files():
        relative = _relative(path)
        if HISTORY.fullmatch(relative):
            continue
        hits = _hits(path)
        if not hits:
            continue
        allowed_lines: set[int] = set()
        if relative in ALLOWED:
            allowed_lines = ALLOWED[relative][1](_read(path) or [])
        for number, text in hits:
            if number not in allowed_lines:
                offenders.append(f"{relative}:{number + 1}: {text.strip()[:110]}")
    assert not offenders, (
        "the name from before 3.0 is spelled outside legacy_names.py "
        f"({len(offenders)} lines). Ask noust.core.legacy_names for the name that must still "
        "be recognised, and say 'before 3.0' where a comment tells a story:\n"
        + "\n".join(offenders[:60])
    )


@pytest.mark.parametrize("relative", sorted(ALLOWED))
def test_every_exception_is_still_needed(relative: str) -> None:
    """A file that stopped spelling the old name comes off the list."""
    path = ROOT / relative
    assert path.is_file(), f"{relative} is listed but does not exist: remove it from ALLOWED"
    assert _hits(path), f"{relative} no longer spells the old name: remove it from ALLOWED"


def test_every_exception_has_a_reason() -> None:
    """The list is short because each entry says why."""
    for relative, (reason, _lines) in ALLOWED.items():
        assert len(reason.split()) >= 5, f"{relative} needs a reason"


def test_the_guard_reads_the_places_it_claims_to() -> None:
    """A guard that scans nothing passes forever."""
    files = {_relative(path) for path in _files()}
    assert "src/noust/core/paths.py" in files
    assert "README.md" in files
    assert "docs/compose.md" in files
    assert "rpm/noust.spec" in files
    assert "panel/src/app/theme.ts" in files
    assert "panel/mock/backend.ts" in files
    assert "panel/vite.config.ts" in files
    assert not any(name.startswith(SKIPPED_PATHS) for name in files)


@pytest.mark.parametrize("text", ["wasm", "WASM", "Wasm-web", "x_wasm_init", "a.wAsM.b"])
def test_the_match_ignores_case_and_word_boundaries(text: str) -> None:
    """``wasm-web``, ``_wasm_init`` and ``WASM`` are the same leftover."""
    assert OLD_NAME.search(text)


def test_the_spec_allows_the_conflicts_the_pre_script_and_the_changelog_only() -> None:
    """A mention anywhere else in the spec is a leftover, however close to an allowed one."""
    lines = [
        "Name:           noust",
        "Conflicts:      wasm-cli < 3.0.0~",
        "Summary: a line that is not allowed to say wasm",
        "%files",
        "%pre",
        "if [ -f /etc/wasm/config.yaml ]; then",
        "%post",
        "echo wasm",
        "%changelog",
        "- Renamed from wasm",
    ]
    allowed = _spec_lines(lines)
    with_the_word = {i for i, line in enumerate(lines) if OLD_NAME.search(line)}
    assert sorted(allowed & with_the_word) == [1, 5, 9]
