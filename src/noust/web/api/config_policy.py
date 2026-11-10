# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/config/policy``: how this server behaves, as one setting (item 166).

Whether it asks for new releases, where certificate notices go, how many backups it keeps of each
application and how long a deployment may build are the four settings of "General" that are policy
and not the machine. They are one typed route so that a central that sets them for the fleet writes
one request per server, each judged by the rule of its own key, and so that the push of a setting
of the fleet has a route of its own to be admitted on: a loose ``PATCH /api/config`` is not one it
may use. Nothing here writes configuration itself (:class:`~noust.core.config.Config` is the single
writer, through :mod:`noust.fleet.settings.policy`), and what sits beside these keys in the same
sections (the backup folder, the builds that run at once, the certificate provider) is left as it
was.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from noust.core import delivery
from noust.fleet.settings import policy
from noust.web.api.auth import get_current_session
from noust.web.api.config import MessageResponse, _audit_write, load_config, persist
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class ServerPolicyConfig(BaseModel):
    """The four settings of a server's policy."""

    updates_check: bool = Field(..., description="Whether this server asks for new releases")
    ssl_email: str = Field(..., description="Where certificate notices are sent; empty for nowhere")
    backup_max_per_app: int = Field(
        ..., description="How many backups are kept of each application"
    )
    delivery_max_minutes: int = Field(
        ..., description="The longest a deployment may build, in minutes; 0 is no limit"
    )


class ServerPolicyResponse(ServerPolicyConfig):
    """
    A server's policy and whether anybody chose it.

    Attributes:
        customized: Some field is something other than what the server does of itself: its own
            operator chose it, so a central that sets the policy asks before taking it over.
    """

    customized: bool


@router.get("", response_model=ServerPolicyResponse)
def get_policy(session: dict = Depends(get_current_session)) -> ServerPolicyResponse:
    """
    Get this server's policy.

    Args:
        session: Authenticated session, injected by the dependency.

    Returns:
        The four fields, read the way the server itself reads each key, and whether its operator
        chose any of them.
    """
    values = policy.read(load_config())
    return ServerPolicyResponse(**values, customized=policy.is_customized(values))


@router.put("", response_model=MessageResponse)
def update_policy(
    body: ServerPolicyConfig,
    request: Request,
    session: dict = Depends(require_elevated),
) -> MessageResponse:
    """
    Set this server's policy: the four fields are written together, and nothing else.

    A value out of range is refused as a whole, with the words of the key that refused it, before
    anything is written. The queue obeys the new time limit from its next look, with no restart.

    Args:
        body: The four fields.
        request: The incoming request, for the audit record.
        session: Authenticated session, injected by the dependency.

    Returns:
        Confirmation message.

    Raises:
        ConfigError: 400 when a key refuses its value.
        HTTPException: If the configuration cannot be written.
    """
    config = load_config()
    before = policy.read(config)
    values = dump_model(body)
    policy.write(config, values)
    persist(config)
    delivery.forget()
    # The sections whose keys moved, as the other typed routes name what they wrote.
    changed = sorted(
        {
            policy.KEYS[field].split(".")[0]
            for field in policy.FIELDS
            if before[field] != values[field]
        }
    )
    _audit_write(request, session, changed=changed)
    return MessageResponse(message="Server policy updated")
