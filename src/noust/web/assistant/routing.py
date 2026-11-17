# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which operation a path is, the way the server will route it (item 171).

A call the assistant prepares is classified, shown on a card and judged by the policy through its
operation's *template*; what runs is whatever the server routes the *path* to. The two must be one
operation. The server routes by the path **decoded** (``%2F`` is a slash to it), with the more specific
route winning, and that is what :func:`noust.web.permissions.registry.match_path` already answers for
the guard of the node proxy: this module asks it, and adds nothing of its own to the routing.
"""

from __future__ import annotations

from urllib.parse import unquote

from noust.web.permissions.registry import match_path, proxied_path

__all__ = ["route_of", "route_of_decoded", "runs_as"]


def route_of_decoded(method: str, path: str) -> str | None:
    """
    Args:
        method: The HTTP method.
        path: A path as the server holds it once received (decoded, as the audit trail records it);
            through a node's proxy when the call is for a server (the node's own route is the one
            the call is judged by there).

    Returns:
        The template of the route the server gives the request to, or None when it is none of the
        routes every route declares a permission for.
    """
    inner = proxied_path(path)
    matched = match_path(method, inner if inner is not None else path)
    return matched[0] if matched is not None else None


def route_of(method: str, path: str) -> str | None:
    """
    Args:
        method: The HTTP method.
        path: A path as it is sent, percent-encoded.

    Returns:
        The template of the route the server gives the request to, as :func:`route_of_decoded` for
        the path once decoded; None when there is none.
    """
    return route_of_decoded(method, unquote(path))


def runs_as(method: str, path: str, template: str) -> bool:
    """
    Args:
        method: The HTTP method.
        path: A path as it is sent.
        template: The operation's template the call was classified by.

    Returns:
        Whether the server routes the path to that very operation.
    """
    return route_of(method, path) == template
