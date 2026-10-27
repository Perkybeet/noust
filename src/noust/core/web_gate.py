# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one door from the rest of Noust to the console's credential store.

The token manager lives in the console's package, whose dependencies (FastAPI and friends) are
optional: it is imported when something asks for it, never when the caller loads, or ``noust
--help`` itself would fail on a machine without them. The command line, the monitor and the ENS
checks all need it and sit at different layers, so the door is here, below all of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.exceptions import DependencyError

if TYPE_CHECKING:
    from noust.web.auth import TokenManager


def missing_dependency(exc: ImportError) -> DependencyError:
    """
    Args:
        exc: The failed import.

    Returns:
        The error that tells the operator how to install the console's dependencies.
    """
    return DependencyError(
        "This command needs the console's dependencies, which are not installed",
        details=(
            "Install them with 'noust web install', your distribution's python3-fastapi, "
            "python3-uvicorn and python3-psutil packages, or pip install 'noust[web]'. "
            f"Python said: {exc}"
        ),
    )


def token_manager() -> TokenManager:
    """
    Build the token manager over the state directory the console uses.

    Returns:
        The manager, reading and writing the state directory
        :class:`~noust.web.auth.SecurityConfig` resolves by default -
        ``NOUST_WEB_STATE_DIR``, or ``/etc/noust``.

    Raises:
        DependencyError: When the console's dependencies are not installed.
    """
    try:
        from noust.web.auth import SecurityConfig, TokenManager
    except ImportError as exc:
        raise missing_dependency(exc) from exc
    return TokenManager(SecurityConfig())
