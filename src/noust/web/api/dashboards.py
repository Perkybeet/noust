# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The panels of the console's Home: ``/api/auth/dashboards``.

A thin client of :class:`noust.core.account_dashboards.AccountDashboards`: the document's rules (the
closed version 1 layout, the 12-column grid, the 40 widgets, the 32 KB) and the limit of 20 panels
are the store's and the validator's, not repeated here. This module only says whose panels they are.

Mounted under ``/api/auth`` (by :mod:`noust.web.api.router`) like the rest of one's own credentials
and settings, so a central never forwards it to a node and a fleet token never reaches it
(``noust.web.auth.FLEET_REFUSED_PREFIXES``): a panel belongs to the operator, one set across the
whole fleet, and not to each server. Every principal that is an account keeps its own (``self``,
like the team it works in); the master token has no account to keep them for and is answered 400,
as ``PUT /api/teams/active`` answers it, and the console keeps its panels in the browser instead.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.account_dashboards import AccountDashboards, Dashboard
from noust.core.exceptions import ValidationError
from noust.core.teams import ResourceNotFoundError
from noust.validators import dashboards as rules
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, caller_account

router = APIRouter(route_class=NoustErrorRoute)


class DashboardLimits(BaseModel):
    """
    What a panel and an account may hold, so a client need not hard-code it.

    Attributes:
        dashboards: Panels an account keeps.
        widgets: Widgets in one panel.
        columns: Columns of the grid a widget is placed on.
        document_bytes: Bytes of a panel's layout, as it is stored.
        settings_bytes: Bytes of one widget's settings.
    """

    dashboards: int = rules.MAX_DASHBOARDS
    widgets: int = rules.MAX_WIDGETS
    columns: int = rules.GRID_COLUMNS
    document_bytes: int = rules.MAX_DOCUMENT_BYTES
    settings_bytes: int = rules.MAX_SETTINGS_BYTES


class DashboardOut(BaseModel):
    """
    One panel.

    Attributes:
        id: The id the person gave it.
        name: What it is called.
        scope: ``server`` (follows the server in the selector) or ``fleet``.
        position: Its place among the account's panels, from 0.
        layout: The document: ``{version: 1, widgets: [{id, type, x, y, w, h, settings}]}``.
        created_at: When it was first saved, UNIX seconds.
        updated_at: When it was last saved, UNIX seconds.
    """

    id: str
    name: str
    scope: str
    position: int
    layout: dict[str, Any]
    created_at: float
    updated_at: float

    @classmethod
    def of(cls, panel: Dashboard) -> DashboardOut:
        """
        Args:
            panel: A panel of the store.

        Returns:
            Its answer.
        """
        return cls(
            id=panel.id,
            name=panel.name,
            scope=panel.scope,
            position=panel.position,
            layout=panel.layout,
            created_at=panel.created_at,
            updated_at=panel.updated_at,
        )


class DashboardsResponse(BaseModel):
    """
    The panels of the account asking.

    Attributes:
        dashboards: In the order the account keeps them; empty when it never saved one (the
            console then draws its own defaults).
        limits: What a panel and an account may hold.
    """

    dashboards: list[DashboardOut]
    limits: DashboardLimits = Field(default_factory=DashboardLimits)


class DashboardBody(BaseModel):
    """
    What a panel is saved as.

    Attributes:
        name: What it is called, 1 to 60 characters.
        scope: ``server`` or ``fleet``.
        layout: The document, version 1. Judged in full by the server; see
            :mod:`noust.validators.dashboards`.
    """

    name: str
    scope: str
    layout: dict[str, Any]


class DashboardOrder(BaseModel):
    """
    The order the panels are kept in.

    Attributes:
        order: The id of every panel the account has, each once.
    """

    order: list[str]


def _account_id(session: dict[str, Any]) -> int:
    """
    Args:
        session: The authenticated session.

    Returns:
        The account the request acts as: the signed-in one, or an API token's owner.

    Raises:
        ValidationError: 400 for the master token, which is no account.
    """
    account = caller_account(session)
    if account is None:
        raise ValidationError(
            "Only an account keeps panels",
            details="Sign in with an account; the console keeps the panels of the master token "
            "in this browser.",
            field="account",
        )
    return int(account.id)


def _answer(account_id: int) -> DashboardsResponse:
    return DashboardsResponse(
        dashboards=[DashboardOut.of(panel) for panel in AccountDashboards().for_account(account_id)]
    )


@router.get("", response_model=DashboardsResponse)
def list_dashboards(
    session: Annotated[dict, Depends(get_current_session)],
) -> DashboardsResponse:
    """
    List the panels of the account asking.

    Args:
        session: The authenticated session.

    Returns:
        Its panels in order, and the limits.

    Raises:
        ValidationError: 400 when the principal is not an account.
    """
    return _answer(_account_id(session))


@router.post("/order", response_model=DashboardsResponse)
def order_dashboards(
    body: DashboardOrder, session: Annotated[dict, Depends(get_current_session)]
) -> DashboardsResponse:
    """
    Put the account's panels in the order given.

    Args:
        body: The id of every panel, each once.
        session: The authenticated session.

    Returns:
        The panels in their new order.

    Raises:
        ValidationError: 400 when the principal is not an account, or the list does not name
            every panel of the account exactly once.
    """
    account_id = _account_id(session)
    AccountDashboards().reorder(account_id, body.order)
    return _answer(account_id)


@router.put("/{dashboard_id}", response_model=DashboardOut)
def save_dashboard(
    dashboard_id: str, body: DashboardBody, session: Annotated[dict, Depends(get_current_session)]
) -> DashboardOut:
    """
    Create a panel, or replace the one the account has under that id.

    A new panel goes last; one that exists keeps its place.

    Args:
        dashboard_id: The panel's id, the person's own.
        body: Its name, scope and layout.
        session: The authenticated session.

    Returns:
        The panel as it is now.

    Raises:
        ValidationError: 400 when the principal is not an account, anything in the body is not
            valid (the error's ``fields`` names it), or the account keeps 20 panels already and
            this one is new.
    """
    panel = AccountDashboards().save(
        _account_id(session),
        dashboard_id,
        name=body.name,
        scope=body.scope,
        layout=body.layout,
    )
    return DashboardOut.of(panel)


@router.delete("/{dashboard_id}", response_model=DashboardsResponse)
def delete_dashboard(
    dashboard_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> DashboardsResponse:
    """
    Delete one of the account's panels.

    Args:
        dashboard_id: The panel's id.
        session: The authenticated session.

    Returns:
        The panels that are left.

    Raises:
        ValidationError: 400 when the principal is not an account.
        ResourceNotFoundError: 404 when the account has no panel by that id, whoever has one.
    """
    account_id = _account_id(session)
    if not AccountDashboards().delete(account_id, dashboard_id):
        raise ResourceNotFoundError(
            f"No panel called {dashboard_id!r}",
            details="List yours with GET /api/auth/dashboards.",
            field="dashboard",
        )
    return _answer(account_id)
