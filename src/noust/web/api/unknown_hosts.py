# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What answers a name no site serves, over the API.

A thin translation of :mod:`noust.managers.unknown_hosts`, which ``noust site
unknown-hosts`` calls too. It is the web server's own default, ahead of every
application's site, so reading it is a read of the server and changing it needs sudo
mode. The manager decides everything: which server may be the default, what is
switched off, and what the web server's own refusal says.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.managers.unknown_hosts import UnknownHosts, UnknownHostsStatus, web_manager
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)


class HolderModel(BaseModel):
    """
    A server that already declares itself the default for a port.

    Attributes:
        file: The configuration file it is in.
        port: The port.
        distribution: Whether it is the web server's own default site, which can be switched off.
    """

    file: str
    port: int
    distribution: bool


class UnknownHostsResponse(BaseModel):
    """
    What answers an unknown name, and what stands in the way of changing it.

    Attributes:
        webserver: ``nginx`` or ``apache``.
        enabled: Whether Noust's site is in place.
        action: ``close``, ``refuse`` or ``redirect``; empty when not enabled.
        redirect_to: The redirect's address.
        tls: Whether a TLS handshake for an unknown name is refused too.
        file: Where the site is kept.
        disabled_distribution_site: The distribution site switched off to make room.
        holders: Other servers that declare themselves the default.
        version: The web server's version.
        notes: Things the operator should know.
    """

    webserver: str
    enabled: bool
    action: str = ""
    redirect_to: str = ""
    tls: bool = False
    file: str = ""
    disabled_distribution_site: str = ""
    holders: list[HolderModel] = Field(default_factory=list)
    version: str = ""
    notes: list[str] = Field(default_factory=list)


class UnknownHostsRequest(BaseModel):
    """
    What to answer an unknown name with.

    Attributes:
        action: ``close`` (nothing), ``refuse`` (503) or ``redirect`` (301).
        redirect_to: The address a redirect goes to; only for ``redirect``.
        switch_off_distribution_site: Switch off the web server's own default site when
            it is the one in the way; it is switched on again when this is removed.
        webserver: ``nginx`` or ``apache``; the one installed by default.
    """

    action: str
    redirect_to: str = ""
    switch_off_distribution_site: bool = False
    webserver: str | None = None


def _response(status: UnknownHostsStatus) -> UnknownHostsResponse:
    """
    Args:
        status: What the manager reported.

    Returns:
        The response model.
    """
    data: dict[str, Any] = status.to_dict()
    return UnknownHostsResponse(**data)


@router.get("", response_model=UnknownHostsResponse)
def get_unknown_hosts(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    webserver: str | None = None,
) -> UnknownHostsResponse:
    """
    Say what answers a request for a name no site serves.

    Args:
        session: Authenticated session, injected.
        webserver: ``nginx`` or ``apache``; the one installed by default.

    Returns:
        The status, with any other server that claims to be the default.
    """
    return _response(UnknownHosts(web_manager(webserver)).status())


@router.put("", response_model=UnknownHostsResponse)
def put_unknown_hosts(
    data: UnknownHostsRequest,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> UnknownHostsResponse:
    """
    Make the web server answer an unknown name as asked.

    Needs sudo mode: it changes what the whole server answers on ports 80 and 443. The
    site is tested by the web server before it is reloaded, and put away when refused.

    Args:
        data: The answer and, for a redirect, its address.
        session: Authenticated and elevated session, injected.

    Returns:
        The status after the change.

    Raises:
        ValidationError: The action or address is not acceptable, another server is the
            default for a port (400, naming its file), or the web server refused the site
            (400, with its own output).
    """
    hosts = UnknownHosts(web_manager(data.webserver))
    return _response(
        hosts.set(
            data.action,
            data.redirect_to,
            switch_off_distribution_site=data.switch_off_distribution_site,
        )
    )


@router.delete("", response_model=UnknownHostsResponse)
def delete_unknown_hosts(
    session: Annotated[dict[str, Any], Depends(require_elevated)],
    webserver: str | None = None,
) -> UnknownHostsResponse:
    """
    Take the site away: the first site answers an unknown name again.

    Needs sudo mode. The web server's own default site, if it was switched off to make
    room, is switched on again.

    Args:
        session: Authenticated and elevated session, injected.
        webserver: ``nginx`` or ``apache``; the one installed by default.

    Returns:
        The status after the change.
    """
    return _response(UnknownHosts(web_manager(webserver)).reset())
