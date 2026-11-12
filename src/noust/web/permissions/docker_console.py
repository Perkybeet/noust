# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a shell in a container needs, decided by whose the container is.

The route that opens one asks for ``docker.manage`` like everything that changes Docker. What the shell
reaches is not the same for every container, and the permission follows what it reaches: in a container
of an application it is the application's own terminal (``apps.terminal``, the permission of the shell
that route already gives), and in anything else (a container nobody of Noust made, a database engine's)
it is a shell the server's (``server.terminal``). The owner is resolved first (the gate does it) and this
is asked of the owner, so the id of a container cannot be used to reach what its name could not.
"""

from __future__ import annotations

from noust.core.docker.models import Owner, OwnerKind
from noust.web.permissions import Permission

__all__ = ["console_permission"]


def console_permission(owner: Owner) -> str:
    """
    Args:
        owner: Whose the container is.

    Returns:
        The permission a shell in it needs on top of ``docker.manage``.
    """
    if owner.kind is OwnerKind.APP:
        return Permission.APPS_TERMINAL
    return Permission.SERVER_TERMINAL
