# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who the built-in assistant is when it acts: a token of its own, never the person's session
(item 171).

A conversation acts with a token whose owner is the person who asks, whose permissions are the
**intersection** of theirs and one of the presets of the MCP tools (:mod:`noust.core.mcp_presets`,
the one definition of what a client that deploys or controls may do), minus a floor nobody lifts,
whose scope is the owner's teams, which lasts hours and which is never asked for sudo mode and never
holds ``allow_elevated``. So what the assistant may do is decided by the server, as it is for a
person: the model proposes, the API answers, and what needs a person's confirmation (sudo mode, a
second approver) is done by the person in the console, with their own session.

**The floor** is :data:`noust.core.assistant_floor.ASSISTANT_FLOOR`, a closed list of its own that
a test pins: nothing that reveals a secret, acts as root or on the host, opens a terminal, touches
accounts, the audit trail, the security or the configuration (its own settings, the policy of
approval and the channels of notification, a way out for data), the fleet or the teams. It is not
the MCP's list of what a client outside is kept from: the assistant is asked to do what Noust knows
how to do, so creating an application or restoring a copy is not in its floor; it is *proposed*, and
done by the person in the console, with their own session, where sudo mode and a second approver
are asked. The token never holds a permission of the floor, whatever its owner holds, and never the
assistant's own permission, so that it can neither read nor delete a conversation nor start another
assistant.

**The audit trail** names it as an actor of its own: ``via: assistant``, with the person behind it
in the token's name (``assistant:<conversation>:<person>:<n>``) and in the event's details, never
the conversation.

**Turning the assistant off** revokes every token it holds at once, and what a conversation tries
next fails with the sentence ``assistant.off``.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.audit import Actor
from noust.core.audit import record as audit_record
from noust.core.exceptions import SecurityError
from noust.core.mcp_presets import SELF_PERMISSION, permissions_of_preset
from noust.core.server_text import ServerText, say
from noust.web.auth.records import ASSISTANT_CALLER
from noust.web.mcp.tools import TOOLS
from noust.web.permissions import Permission
from noust.web.permissions.registry import permission_for_route

if TYPE_CHECKING:
    from noust.core.accounts.model import Account
    from noust.web.auth.tokens import TokenManager

__all__ = [
    "ASSISTANT_PRESET",
    "NEVER_HELD",
    "TOKEN_HOURS",
    "AssistantIdentity",
    "AssistantToken",
    "assistant_actor",
    "off_sentence",
    "preset_permissions",
]

#: The preset of tools a conversation's token is cut from: the widest there is, and what it may
#: really do is the intersection with the person's own permissions and what the policy lets it
#: propose. The assistant's loop never offers a tool the token cannot use.
ASSISTANT_PRESET = "control"

#: How long a conversation's token lives, in hours.
TOKEN_HOURS = 4.0

#: What no assistant's token holds, whatever a preset or a person holds: the floor, and the
#: assistant's own permission.
NEVER_HELD: frozenset[str] = frozenset({*ASSISTANT_FLOOR, Permission.ASSISTANT_USE})

_NAME_PREFIX = "assistant:"


@dataclass(frozen=True)
class AssistantToken:
    """
    A token issued for one conversation.

    Attributes:
        token: The token itself, shown once: the loop keeps it in memory for the turn.
        name: Its name, which the audit trail shows.
        token_id: Its record's id.
        expires_at: When it dies, as a UNIX timestamp.
        permissions: What it holds.
        account_id: The person it acts for.
    """

    token: str
    name: str
    token_id: int
    expires_at: float
    permissions: tuple[str, ...]
    account_id: int

    def __repr__(self) -> str:
        return f"AssistantToken(name={self.name!r}, token=***)"


def preset_permissions(preset: str = ASSISTANT_PRESET) -> frozenset[str]:
    """
    Say what an assistant of a preset may hold at most, whoever asks.

    Args:
        preset: One of the MCP presets (``inspect``, ``deploy``, ``control``).

    Returns:
        The permissions the routes behind its tools need, without what is never held, and ``self``.
    """
    needed = permissions_of_preset(preset, TOOLS, permission_for_route)
    return (frozenset(needed) - NEVER_HELD) | {SELF_PERMISSION}


def off_sentence() -> ServerText:
    """
    Returns:
        What a conversation is told when it tries something after the assistant was turned off.
    """
    return say("assistant.off")


def assistant_actor(account: Account, token_name: str) -> Actor:
    """
    The actor the audit trail names for what the assistant does.

    Args:
        account: The person it acts for.
        token_name: The token it acts with.

    Returns:
        An actor of its own: the token, reached ``via`` the assistant, with the person's role.
    """
    return Actor(
        kind="token",
        id=token_name,
        name=f"token:{token_name}",
        role=account.role,
        via="assistant",
    )


class AssistantIdentity:
    """The issuer of the assistant's tokens, and the one that takes them back."""

    def __init__(self, manager: TokenManager | None = None) -> None:
        """
        Args:
            manager: The console's token manager; the running one when None.
        """
        self._manager = manager

    @property
    def manager(self) -> TokenManager:
        """The token manager, resolved on first use."""
        if self._manager is None:
            from noust.web.server import get_token_manager

            self._manager = get_token_manager()
        return self._manager

    def issue(
        self,
        account: Account,
        conversation_id: str,
        *,
        preset: str = ASSISTANT_PRESET,
        hours: float = TOKEN_HOURS,
        bound: tuple[str, int] | None = None,
    ) -> AssistantToken:
        """
        Issue the token a conversation acts with.

        Args:
            account: The person who asks. The token is theirs and holds at most what they hold.
            conversation_id: The conversation it is for.
            preset: What the token is cut from.
            hours: How long it lives, at most the server's longest token life.
            bound: A team, project or environment to bind it to, as ``(kind, id)``; the person's
                own teams when None.

        Returns:
            The token.

        Raises:
            SecurityError: The account cannot have one (the person does not hold the assistant's
                permission, or does not reach the scope), or the policy refuses a token.
        """
        manager = self.manager
        held = manager.permissions_held_by(account, bound)
        if Permission.ASSISTANT_USE not in held:
            raise SecurityError(
                "This account is not allowed to use the assistant",
                details="An administrator can give it a role that holds assistant.use.",
            )
        # The floor is taken off again here, whatever the preset says: a preset edited later must
        # not hand over what is never held.
        granted = sorted(((held & preset_permissions(preset)) - NEVER_HELD) | {SELF_PERMISSION})
        name = f"{_NAME_PREFIX}{conversation_id[:8]}:{account.username[:20]}:{secrets.token_hex(3)}"
        issued = manager.create_api_token(
            name,
            "read",
            hours,
            owner=account,
            permissions=granted,
            created_by="assistant",
            bound=bound,
            caller=ASSISTANT_CALLER,
        )
        audit_record(
            "assistant.token",
            actor=assistant_actor(account, name),
            target=f"conversation:{conversation_id[:8]}",
            details={"person": account.username, "expires_in_hours": hours},
        )
        return AssistantToken(
            token=issued["token"],
            name=name,
            token_id=int(issued["id"]),
            expires_at=float(issued["expires_at"] or time.time() + hours * 3600),
            permissions=tuple(granted),
            account_id=int(account.id),
        )

    def revoke_conversation(self, conversation_id: str) -> int:
        """
        Take back the tokens of one conversation.

        Args:
            conversation_id: The conversation.

        Returns:
            How many were revoked.
        """
        return self.manager.sessions.revoke_caller_tokens(
            ASSISTANT_CALLER, name_prefix=f"{_NAME_PREFIX}{conversation_id[:8]}:"
        )

    def revoke_all(self, *, actor: Actor | None = None) -> int:
        """
        Take back every token the assistant holds: what turning it off does, at once.

        Args:
            actor: Who turned it off, for the record.

        Returns:
            How many were revoked.
        """
        revoked = self.manager.sessions.revoke_caller_tokens(ASSISTANT_CALLER)
        audit_record(
            "assistant.off", actor=actor, target="setting:assistant", details={"revoked": revoked}
        )
        return revoked
