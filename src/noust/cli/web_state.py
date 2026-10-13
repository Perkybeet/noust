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

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from noust.core.exceptions import DependencyError

if TYPE_CHECKING:
    from noust.web.auth import TokenManager
    from noust.web.mcp.server import Settings as McpSettings
    from noust.web.mcp.tools import Tool
    from noust.web.permissions.access import AccessManager


def _missing(exc: ImportError) -> DependencyError:
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
        ``NOUST_WEB_STATE_DIR`` (or ``WASM_WEB_STATE_DIR``), or ``/etc/noust``.

    Raises:
        DependencyError: When the console's dependencies are not installed.
    """
    try:
        from noust.web.auth import SecurityConfig, TokenManager
    except ImportError as exc:
        raise _missing(exc) from exc
    return TokenManager(SecurityConfig())


def access_manager() -> AccessManager:
    """
    Returns:
        The manager of roles and where they are held, the one the console's
        ``/api/access`` routes call.

    Raises:
        DependencyError: When the console's dependencies are not installed.
    """
    try:
        from noust.web.permissions.access import AccessManager
    except ImportError as exc:
        raise _missing(exc) from exc
    return AccessManager()


def permission_descriptions() -> Mapping[str, str]:
    """
    Returns:
        What each permission means, in the words the console and the API use.

    Raises:
        DependencyError: When the console's dependencies are not installed.
    """
    try:
        from noust.web.permissions import DESCRIPTIONS
    except ImportError as exc:
        raise _missing(exc) from exc
    return DESCRIPTIONS


def mcp_catalog() -> tuple[tuple[Tool, ...], Callable[[str, str], str | None]]:
    """
    Returns:
        Every tool the MCP endpoint offers, and the function that says which
        permission the route behind a tool needs.

    Raises:
        DependencyError: When the console's dependencies are not installed.
    """
    try:
        from noust.web.mcp.tools import TOOLS
        from noust.web.permissions.registry import permission_for_route
    except ImportError as exc:
        raise _missing(exc) from exc
    return tuple(TOOLS), permission_for_route


def mcp_settings() -> tuple[McpSettings, tuple[str, ...]]:
    """
    Returns:
        The MCP settings in force, and the protocol revisions the endpoint speaks.

    Raises:
        DependencyError: When the console's dependencies are not installed.
    """
    try:
        from noust.web.mcp.server import SUPPORTED_VERSIONS, load_settings
    except ImportError as exc:
        raise _missing(exc) from exc
    return load_settings(), tuple(SUPPORTED_VERSIONS)
