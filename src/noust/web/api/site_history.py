# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The earlier texts of a site's configuration file, over the API.

A thin translation of :mod:`noust.managers.site_history`, which ``noust site history`` reads
too. Looking at a version is reading the site, like the file itself; putting one back is the
one ``replace_site_config`` the editor saves through, so it is tested by the web server
before it is installed and is root-equivalent like the editor's save.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.managers.site_history import unified_diff
from noust.managers.webserver import WebServerManager
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.api.sites import SiteActionResponse, _manager_for
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class VersionModel(BaseModel):
    """
    One version of a site's file.

    Attributes:
        version: Its number for this site.
        sha256: The hash of the text.
        size: Its length in bytes.
        source: ``generated`` (a deployer rendered it), ``edit`` (saved by a person),
            ``restore`` (an earlier version put back) or ``found`` (on disk, written by
            something else, when a deploy was about to replace it).
        actor: Who saved it, when a person did.
        created_at: When, ISO 8601 UTC.
        current: Whether it is what the file says now.
    """

    version: int
    sha256: str
    size: int
    source: str
    actor: str | None = None
    created_at: str
    current: bool = False


class VersionsResponse(BaseModel):
    """
    The versions kept of a site, newest first.

    Attributes:
        site: The site.
        webserver: ``nginx`` or ``apache``.
        versions: What is kept.
        edited_outside: The file is not what Noust last wrote (it was changed by hand).
        pending_reload: The file was written after the web server last loaded its configuration.
    """

    site: str
    webserver: str
    versions: list[VersionModel] = Field(default_factory=list)
    edited_outside: bool = False
    pending_reload: bool = False


class VersionResponse(VersionModel):
    """
    One version, with its text and what differs from the file now.

    Attributes:
        site: The site.
        webserver: ``nginx`` or ``apache``.
        config: The version's text.
        diff: A unified diff from this version to the file now; empty when they are the same.
    """

    site: str
    webserver: str
    config: str
    diff: str = ""


def _site(domain: str) -> tuple[str, WebServerManager, str]:
    """
    Args:
        domain: The domain from the path.

    Returns:
        The validated name, its manager and the file's text now.

    Raises:
        HTTPException: 404 when no such site exists.
    """
    validated = strict_domain(domain)
    _name, manager = _manager_for(None)
    text = manager.get_site_config(validated)
    if text is None:
        raise HTTPException(status_code=404, detail=f"Site not found: {validated}")
    return validated, manager, text


def _model(item: Any, current_sha: str) -> VersionModel:
    """
    Args:
        item: A version of the store.
        current_sha: The hash of the file now.

    Returns:
        The response model.
    """
    return VersionModel(
        version=item.version,
        sha256=item.sha256,
        size=item.size,
        source=item.source,
        actor=item.actor,
        created_at=item.created_at,
        current=item.sha256 == current_sha,
    )


@router.get("/{domain}/config/versions", response_model=VersionsResponse)
def list_versions(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> VersionsResponse:
    """
    List the versions kept of a site's file, and say whether it has drifted.

    Args:
        domain: Domain of the site.
        session: Authenticated session, injected.

    Returns:
        The versions, newest first, and whether the file was changed outside Noust or is
        waiting for a reload.

    Raises:
        HTTPException: 404 when no such site exists.
    """
    from noust.core.site_history_store import sha256_of

    validated, manager, text = _site(domain)
    current = sha256_of(text)
    drift = manager.history.drift({validated: manager.config_path(validated)}, manager.runner)
    verdict = drift.get(validated)
    return VersionsResponse(
        site=validated,
        webserver=manager.backend.name,
        versions=[_model(item, current) for item in manager.history.versions(validated)],
        edited_outside=bool(verdict and verdict.edited_outside),
        pending_reload=bool(verdict and verdict.pending_reload),
    )


@router.get("/{domain}/config/versions/{version}", response_model=VersionResponse)
def read_version(
    domain: str,
    version: int,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> VersionResponse:
    """
    Read one version, with a diff from it to the file now.

    Args:
        domain: Domain of the site.
        version: The version's number.
        session: Authenticated session, injected.

    Returns:
        Its text and the diff.

    Raises:
        HTTPException: 404 when the site or the version does not exist.
    """
    from noust.core.site_history_store import sha256_of

    validated, manager, text = _site(domain)
    found = manager.history.version(validated, version)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Version {version} of {validated} not found")
    base = _model(found, sha256_of(text))
    return VersionResponse(
        **dump_model(base),
        site=validated,
        webserver=manager.backend.name,
        config=found.text,
        diff=unified_diff(
            found.text, text, older_label=f"version {found.version}", newer_label="now"
        ),
    )


@router.post("/{domain}/config/versions/{version}/restore", response_model=SiteActionResponse)
def restore_version(
    domain: str,
    version: int,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> SiteActionResponse:
    """
    Put an earlier version of a site's file back.

    The same write as saving it from the editor: the web server tests the text before it is
    installed (and refuses it with its own words), and the result is a version of its own.
    Needs sudo mode, like the editor's save. The web server is not reloaded.

    Args:
        domain: Domain of the site.
        version: The version to put back.
        session: Authenticated and elevated session, injected.

    Returns:
        The outcome.

    Raises:
        HTTPException: 404 when the site or the version does not exist.
        ValidationError: The web server refuses the text (400, with its own output).
    """
    validated, manager, _text = _site(domain)
    found = manager.history.version(validated, version)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Version {version} of {validated} not found")
    manager.replace_site_config(validated, found.text, source="restore")
    return SiteActionResponse(
        success=True,
        message=f"Version {version} of {validated} is back. Reload {manager.backend.name} to apply it.",
        site=validated,
    )
