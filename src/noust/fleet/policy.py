# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central needs before it may register a node.

Whoever signs in to a central reaches every node it manages, so the central's
sign-in must be at least as strong as the nodes' own: two-factor sign-in has
to be on before the first node is added. :meth:`noust.fleet.nodes.NodeManager.add`
calls :func:`node_registration_blockers` itself - the CLI and the API both go
through it - so the rule has one door.
"""

from __future__ import annotations

from noust.core.exceptions import SecurityError


def node_registration_blockers() -> list[str]:
    """
    List what stops this central from registering a node.

    Returns:
        One actionable sentence per blocker; empty when a node may be added.
    """
    try:
        from noust.web.auth import SecurityConfig, TokenManager, get_global_token_manager
    except ImportError as exc:
        return [
            "The console's dependencies are not installed, so this central has no sign-in "
            "to protect with two-factor authentication. Install them (noust web install), "
            f"then enable two-factor sign-in. Python said: {exc}"
        ]
    try:
        manager = get_global_token_manager() or TokenManager(SecurityConfig())
        enabled = manager.totp_enabled()
    except SecurityError as exc:
        return [
            f"The two-factor sign-in state could not be read ({exc.message}). {exc.details}".strip()
        ]
    if not enabled:
        return [
            "Two-factor sign-in is not enabled on this central, and whoever signs in here "
            "reaches every node it manages. Enable it first: 'noust 2fa enroll', then "
            "'noust 2fa confirm <code>' (or Settings, Security in the console)."
        ]
    return []
