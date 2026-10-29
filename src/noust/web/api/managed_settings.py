# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central manages on this server: ``/api/managed-settings``.

A thin layer over :class:`~noust.fleet.settings.managed.ManagedSettings` (rule 3). A server that a
central manages keeps, for each setting the central pushed, which version it applied and a
fingerprint of what it held then; whenever it is asked, it looks at what it holds and says which
settings are still the fleet's (``managed``) and which its own operator changed (``overridden``).
It also says which centrals call it and when each last did (:mod:`noust.fleet.central_calls`), so
its console can show whether the central that manages it is still there.
Nothing here carries a value: a setting is read where it lives - the channels, the rules, the
destinations, the registries and the variables - and its credentials are never read back.

Writing is the central's alone. It writes a setting by the server's own typed route, signed with
the person and the change (:mod:`noust.web.auth.fleet_sync`), and then records here that it did.
The server's own operator can only ask for a setting to be put back (``restore``): a server cannot
call its central, so the request waits for the next time the central applies the fleet's settings.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.server_text import ServerText, say
from noust.fleet.central_calls import CentralCalls, central_of_token
from noust.fleet.settings.kinds import AVAILABLE, kind_of
from noust.fleet.settings.managed import ManagedRow, ManagedSettings
from noust.fleet.settings.signature import SyncSignature
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, require_auth
from noust.web.auth.fleet_sync import is_sync

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(require_auth)]


class ManagedSettingOut(BaseModel):
    """
    One setting a central manages here.

    Attributes:
        resource: The kind (``channel``, ``alert_rule``, ``backup_destination``, ``registry``,
            ``variable``).
        name: The setting.
        central: The central that pushed it.
        version: The version of the central's row that was applied.
        fingerprint: A handle of what this server held right after (a prefix of a keyed hash).
        state: ``managed``, or ``overridden`` when this server's own operator changed it.
        change: The change the central pushed it for.
        applied_by: The person who approved that change.
        applied_at: When it was applied.
        overridden_at: When this server found it changed.
        restore_requested_at: When this server's operator asked the central to put it back.
    """

    resource: str
    name: str
    central: str
    version: int
    fingerprint: str
    state: str
    change: str
    applied_by: str
    applied_at: str
    overridden_at: str | None = None
    restore_requested_at: str | None = None


class CentralCallOut(BaseModel):
    """
    One central that calls this server, and when it last did.

    Attributes:
        central: The central, as its fleet token names it.
        first_call_at: When this server first noted a call of it.
        last_call_at: When it last did (noted to the minute).
        quiet_for_s: Seconds since then.
        keeps_watch: It said it keeps watch (its monitor probes every server every half minute),
            so this server expects to hear from it again and says so when it does not.
        heartbeat_s: How often it said it calls, when it did.
    """

    central: str
    first_call_at: str
    last_call_at: str
    quiet_for_s: float
    keeps_watch: bool
    heartbeat_s: int | None = None


class ManagedSettingsOut(BaseModel):
    """
    Every setting a central manages here.

    Attributes:
        items: Them, by kind and name, after looking at what this server holds.
        kinds: The kinds of setting this server can be managed for.
        centrals: The centrals that call this server, with when each last did.
    """

    items: list[ManagedSettingOut]
    kinds: list[str]
    centrals: list[CentralCallOut] = Field(default_factory=list)


class ManagedSettingAppliedIn(BaseModel):
    """
    What the central says it applied.

    Attributes:
        version: The version of its row that it pushed.
    """

    version: int = Field(ge=1)


class ManagedSettingReleasedOut(BaseModel):
    """
    The answer to letting a setting go.

    Attributes:
        released: Whether this server managed it.
    """

    released: bool


def _centrals() -> list[CentralCallOut]:
    """
    Returns:
        The centrals that have called this server, and when.
    """
    now = time.time()
    return [
        CentralCallOut(
            central=call.central,
            first_call_at=datetime.fromtimestamp(call.first_call_at, timezone.utc).isoformat(),
            last_call_at=datetime.fromtimestamp(call.last_call_at, timezone.utc).isoformat(),
            quiet_for_s=max(now - call.last_call_at, 0.0),
            keeps_watch=call.heartbeat_s is not None,
            heartbeat_s=call.heartbeat_s,
        )
        for call in CentralCalls().list()
    ]


def _out(row: ManagedRow) -> ManagedSettingOut:
    """
    Args:
        row: A managed setting.

    Returns:
        Its API shape.
    """
    return ManagedSettingOut(**row.to_dict())


def _refusal(code: str, text: ServerText) -> HTTPException:
    """
    Args:
        code: The answer's ``error``.
        text: The sentence.

    Returns:
        The 403 in the API's error contract, carrying the sentence as a code.
    """
    return HTTPException(
        status_code=403,
        detail={
            "error": code,
            "detail": text.text,
            "hint": text.hint,
            "fields": None,
            "coded": text.to_dict(),
        },
    )


def _require_sync(session: dict[str, Any]) -> SyncSignature:
    """
    Args:
        session: The authenticated payload.

    Returns:
        The person and the change the request is signed with.

    Raises:
        HTTPException: 403 ``fleet_sync_required`` for anything that is not a central's push.
    """
    if not is_sync(session):
        raise _refusal("fleet_sync_required", say("fleet_settings.sync_required"))
    return SyncSignature(**session["fleet_sync"])


def central_of(session: dict[str, Any]) -> str:
    """
    Args:
        session: The authenticated payload of a central.

    Returns:
        The central, as its token's name says it (``fleet-nas.2`` is ``nas``).
    """
    token = str(session.get("token_name") or "central")
    return central_of_token(token) or token


@router.get("", response_model=ManagedSettingsOut)
def list_managed(session: Session) -> ManagedSettingsOut:
    """
    List the settings a central manages here, after looking at what this server holds.

    Args:
        session: The authenticated session.

    Returns:
        Each with its state: ``managed``, or ``overridden`` where this server's own operator
        changed it.
    """
    return ManagedSettingsOut(
        items=[_out(row) for row in ManagedSettings().verified()],
        kinds=list(AVAILABLE),
        centrals=_centrals(),
    )


@router.put("/{resource}/{name}", response_model=ManagedSettingOut)
def record_applied(
    resource: str, name: str, body: ManagedSettingAppliedIn, session: Session
) -> ManagedSettingOut:
    """
    Record that the central wrote a setting here, and fingerprint what this server holds of it.

    Only the central's push can do this (it is signed with the person and the change).

    Args:
        resource: The kind.
        name: The setting.
        body: The version the central pushed.
        session: The authenticated session.

    Returns:
        The setting, ``managed`` again also if it had been ``overridden``.

    Raises:
        HTTPException: 403 ``fleet_sync_required`` for anyone else.
        ValidationError: 400 for a kind this server cannot be managed for.
        ResourceNotFoundError: 404 when this server holds no such setting.
    """
    signature = _require_sync(session)
    kind_of(resource)
    row = ManagedSettings().record_applied(
        resource,
        name,
        central=central_of(session),
        version=body.version,
        signature=signature,
        actor=session_actor(session),
    )
    return _out(row)


@router.delete("/{resource}/{name}", response_model=ManagedSettingReleasedOut)
def release(resource: str, name: str, session: Session) -> ManagedSettingReleasedOut:
    """
    Stop treating a setting as the fleet's. This server keeps it as its own.

    Args:
        resource: The kind.
        name: The setting.
        session: The authenticated session.

    Returns:
        Whether this server managed it.

    Raises:
        HTTPException: 403 ``fleet_sync_required`` for anyone but the central.
    """
    _require_sync(session)
    return ManagedSettingReleasedOut(
        released=ManagedSettings().release(resource, name, actor=session_actor(session))
    )


@router.post("/{resource}/{name}/restore", response_model=ManagedSettingOut)
def ask_restore(resource: str, name: str, session: Session) -> ManagedSettingOut:
    """
    Ask the central to put a setting back as the fleet's.

    This server cannot call its central: the request waits until the central next applies the
    fleet's settings here, and shows as ``restore_requested_at``.

    Args:
        resource: The kind.
        name: The setting.
        session: The authenticated session.

    Returns:
        The setting, still ``overridden`` until then.

    Raises:
        ResourceNotFoundError: 404 when no central manages it here.
    """
    managed = ManagedSettings()
    managed.verified()
    row = managed.request_restore(resource, name)
    audit.record(
        "fleet.setting.restore",
        actor=session_actor(session),
        target=f"{resource}:{name}",
        details={"central": row.central, "version": row.version},
    )
    return _out(row)
