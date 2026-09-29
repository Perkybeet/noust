# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tell an operator who still types ``wasm`` that the product is Noust now.

``wasm`` keeps working through the whole 3.x series, so no script or cron line
breaks. A person at a terminal is told once, on standard error, so they learn
the new name without anything a program reads changing: never when standard
error is not a terminal, never under ``--json``, and never again after the
first time (a marker file under the user's state directory remembers it).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TextIO

from noust.core import paths
from noust.core.fs import get_fs

logger = logging.getLogger(__name__)

#: What the operator reads.
NOTICE = (
    "WASM is now called Noust. The command is `noust`; `wasm` keeps working "
    f"throughout 3.x. See {paths.UPGRADE_NOTES_URL}"
)

#: The file whose existence means the notice was shown.
MARKER_NAME = "wasm-rename-notice-shown"


def marker_path() -> Path:
    """
    Say where the "already told" marker lives.

    Returns:
        ``~/.local/state/noust/wasm-rename-notice-shown``.
    """
    return paths.user_state_dir() / MARKER_NAME


def invoked_as_wasm(argv0: str) -> bool:
    """
    Report whether the program was started by its old name.

    Args:
        argv0: ``sys.argv[0]``.

    Returns:
        True for ``wasm``, whatever directory it was run from.
    """
    return os.path.basename(argv0) == paths.LEGACY_NAME


def maybe_announce(argv0: str, args: list[str], stream: TextIO | None = None) -> bool:
    """
    Print the rename notice once, when a person typed ``wasm``.

    Args:
        argv0: ``sys.argv[0]``.
        args: The command line after the program name.
        stream: Where to print; standard error by default.

    Returns:
        True when the notice was printed.
    """
    stream = stream or sys.stderr
    if not invoked_as_wasm(argv0) or "--json" in args:
        return False
    if not stream.isatty():
        return False
    marker = marker_path()
    if marker.exists():
        return False
    stream.write(f"{NOTICE}\n")
    stream.flush()
    if "--dry-run" in args:
        # A rehearsal changes nothing, not even this; it is shown again.
        return True
    fs = get_fs()
    try:
        fs.make_dir(marker.parent)
        fs.write_text(marker, "")
    except OSError as exc:
        # Without the marker the notice shows again next time, which is the
        # whole cost of failing here.
        logger.debug("Could not remember the rename notice: %s", exc)
    return True
