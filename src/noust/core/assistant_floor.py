# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the built-in assistant never holds, whatever its owner holds and whatever it is allowed to
propose (item 171).

One closed list, in one place, in a module that imports nothing but the catalogue of permissions:
the assistant's own module is not loaded until it is turned on, and the policy of approval, which
decides per kind of caller what may be carried out, reads this list for the rows that never change,
so the list cannot live where loading it means loading the assistant.

**It is not the MCP's floor.** The MCP endpoint is for clients outside, and keeps them away from
everything that changes the server (:data:`noust.web.mcp.tools.FORBIDDEN_PERMISSIONS`: creating an
application, writing to a database, restoring a copy). The assistant is asked to do what Noust
knows how to do, with an approval the operator configures, so those are not in its floor: what it
holds is cut from a preset of the tools and from what its owner holds, and what changes the server
beyond that is **proposed**, and carried out by the person in the console, with their own session,
where sudo mode and a second approver are asked.

What is here is what no proposal can carry out for it either: reading a secret, acting as root or
on the host, opening a terminal, accounts, the audit trail, the security and the configuration
(its own settings, the policy of approval and the channels of notification live there, and a
channel is a way out for data), the fleet and the teams.
"""

from __future__ import annotations

from noust.core.permission_catalog import Permission

__all__ = ["ASSISTANT_FLOOR"]

P = Permission

#: The permissions the assistant's token never holds. A test pins the list: a permission is added to
#: it on purpose, in this file, and never found in it by accident.
ASSISTANT_FLOOR: frozenset[str] = frozenset(
    {
        P.SECRETS_REVEAL,
        P.ROOT_EQUIVALENT,
        P.SERVER_HOST_ACCESS,
        P.SERVER_TERMINAL,
        P.APPS_TERMINAL,
        P.ACCOUNTS_MANAGE,
        P.ACCOUNTS_READ,
        P.AUDIT_MANAGE,
        P.AUDIT_READ,
        P.SECURITY_MANAGE,
        P.SETTINGS_MANAGE,
        P.FLEET_MANAGE,
        P.TEAMS_MANAGE,
    }
)
