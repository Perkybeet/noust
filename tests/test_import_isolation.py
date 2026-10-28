# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A module of the console imports on its own, whichever module an operator's first import is.

``TestImportable::test_no_module_fails_to_import`` imports every module in one process, so a
module that works only after another has been imported passes it. ``web.fleet_placement_actions``
was one: its group of modules imported each other at the end of their files and only worked in the
order they were first loaded in. ``tests/test_layering.py`` now holds that no module imports one
that imports it back, which is the cause; this holds the symptom, in an interpreter where nothing
else is loaded (``tests/support/isolated_import_child.py``).

Every module below ``noust/web`` takes more than ten minutes of processor time (about three
hundred modules, each with the closure it imports), which a suite that runs on every push should
not spend, so the default is the ones directly in ``web/`` and the
``__init__`` of each package below it: the entry points, and where the fleet's group was.
``NOUST_IMPORT_ALL=1`` imports all of them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import noust

SRC = Path(noust.__file__).resolve().parent.parent
CHILD = Path(__file__).resolve().parent / "support" / "isolated_import_child.py"
WEB = Path(noust.__file__).resolve().parent / "web"


def web_modules(everything: bool) -> list[str]:
    """
    Name the modules of the console to import alone.

    Args:
        everything: Every module below ``noust/web`` rather than the entry points.

    Returns:
        Dotted names, sorted.
    """
    names = []
    for path in sorted(WEB.rglob("*.py")):
        relative = path.relative_to(SRC).with_suffix("")
        if "static" in relative.parts or "__pycache__" in relative.parts:
            continue
        direct = path.parent == WEB or path.name == "__init__.py"
        if direct or everything:
            parts = relative.parts[:-1] if path.name == "__init__.py" else relative.parts
            names.append(".".join(parts))
    return names


def test_the_modules_to_import_are_the_entry_points_of_the_console() -> None:
    names = web_modules(everything=False)

    assert "noust.web.fleet_placement_actions" in names
    assert "noust.web.fleet_jobs" in names
    assert "noust.web.api" in names
    assert "noust.web.api.apps" not in names
    assert len(names) < 40


@pytest.mark.allow_subprocess
def test_each_module_of_the_console_imports_with_nothing_else_loaded() -> None:
    names = web_modules(everything=bool(os.environ.get("NOUST_IMPORT_ALL")))
    inherited = os.environ.get("PYTHONPATH")
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(SRC), inherited] if inherited else [str(SRC)]),
    }

    result = subprocess.run(
        [sys.executable, str(CHILD), *names],
        env=environment,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )

    assert result.returncode == 0, (
        "These modules do not import when they are the first thing imported:\n" + result.stdout
    )
