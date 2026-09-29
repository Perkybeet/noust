# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's credential store, for the commands that manage it from a shell.

``noust token``, ``noust sessions`` and ``noust 2fa`` are front ends over the same
:class:`~noust.web.auth.TokenManager` the console's API uses, over the same
on-disk state, so a token issued in one works in the other. That manager lives
in the console's package, whose dependencies (FastAPI and friends) are
optional: it is imported here, when a command runs, never when the CLI loads,
or ``noust --help`` itself would fail on a machine without them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.exceptions import DependencyError

if TYPE_CHECKING:
    from noust.web.auth import TokenManager


def token_manager() -> TokenManager:
    """
    Build the token manager over the state directory the console uses.

    Returns:
        The manager, reading and writing the state directory
        :class:`~noust.web.auth.SecurityConfig` resolves by default -
        ``NOUST_WEB_STATE_DIR`` (or ``WASM_WEB_STATE_DIR``), or ``/etc/noust``.

    Raises:
        DependencyError: When the console's dependencies are not installed.
    """
    try:
        from noust.web.auth import SecurityConfig, TokenManager
    except ImportError as exc:
        raise DependencyError(
            "This command needs the console's dependencies, which are not installed",
            details=(
                "Install them with 'noust web install', your distribution's python3-fastapi, "
                "python3-uvicorn and python3-psutil packages, or pip install 'noust[web]'. "
                f"Python said: {exc}"
            ),
        ) from exc
    return TokenManager(SecurityConfig())
