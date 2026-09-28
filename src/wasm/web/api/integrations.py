# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Integrations with code hosts: this server's GitHub App (2.2).

A thin layer over :mod:`wasm.integrations.github`: every endpoint translates
HTTP to one call there and back. Creating the App, recording an installation
and removing the App change what this server trusts, so they require sudo
mode; reading the status and listing repositories do not.

The manifest flow, as the console runs it:

1. ``POST /github/manifest`` with the console's origin: the manifest and the
   URL to post it to (``state`` included).
2. The browser posts ``manifest`` (the JSON, as a form field) to that URL.
3. GitHub sends the browser to ``<origin>/integrations/github/callback?code=
   &state=``; the console calls ``POST /github/manifest/conversions``.
4. Installing sends the browser to the same page with ``installation_id`` and
   ``setup_action``; the console calls ``POST /github/installations``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from wasm.integrations.github import manifest, service
from wasm.web.api.auth import get_current_session
from wasm.web.api.deps import WASMErrorRoute, require_elevated

router = APIRouter(route_class=WASMErrorRoute)


class InstallationOut(BaseModel):
    """An account the App is installed on."""

    installation_id: int
    account: str
    account_type: str | None = None
    repository_selection: str | None = None
    settings_url: str | None = None


class GitHubStatusOut(BaseModel):
    """The GitHub integration of this server."""

    configured: bool
    app_id: int | None = None
    slug: str | None = None
    name: str | None = None
    owner: str | None = None
    html_url: str | None = None
    settings_url: str | None = None
    install_url: str | None = None
    installations: list[InstallationOut] = []
    hooks_url: str | None = None
    hooks_active: bool = False


class ManifestRequest(BaseModel):
    """
    Start creating the App.

    Attributes:
        origin: The console's origin as the browser sees it
            (``location.origin``), which GitHub sends the browser back to.
        organization: Create the App owned by this organisation; omitted for
            the operator's personal account.
    """

    origin: str
    organization: str | None = None


class ManifestOut(BaseModel):
    """
    What the console posts to GitHub.

    Attributes:
        manifest: Send as the form field ``manifest``, JSON-encoded.
        post_url: The form's action (``state`` included).
        state: Handed back by GitHub with the code.
    """

    manifest: dict[str, Any]
    post_url: str
    state: str


class ConversionRequest(BaseModel):
    """The ``code`` and ``state`` GitHub's callback carried."""

    code: str
    state: str


class InstallationRequest(BaseModel):
    """The ``installation_id`` GitHub's setup callback carried."""

    installation_id: int


class RepositoryOut(BaseModel):
    """
    A repository the App's installations cover.

    Attributes:
        source: What to deploy it as (``github:owner/repo``).
    """

    full_name: str
    private: bool
    default_branch: str | None = None
    clone_url: str | None = None
    source: str
    installation_id: int


class RepositoryListOut(BaseModel):
    """Every repository the App reaches."""

    items: list[RepositoryOut]
    total: int


class BranchOut(BaseModel):
    """A branch of a repository, with its head commit."""

    name: str
    protected: bool = False
    commit: str | None = None


class BranchListOut(BaseModel):
    """A repository's branches."""

    items: list[BranchOut]
    total: int


class InstallationListOut(BaseModel):
    """The App's installations."""

    items: list[InstallationOut]
    total: int


class RemovalOut(BaseModel):
    """
    What removing the integration did.

    Attributes:
        removed: Whether there was an App to forget.
        settings_url: The App's page on GitHub, where it is uninstalled and
            deleted; WASM cannot do that itself.
    """

    removed: bool
    settings_url: str | None = None


def _status_out() -> GitHubStatusOut:
    """
    Build the status response.

    Returns:
        The integration's status.
    """
    return GitHubStatusOut(**service.status().to_dict())


@router.get("/github", response_model=GitHubStatusOut)
def github_status(session: Annotated[dict, Depends(get_current_session)]) -> GitHubStatusOut:
    """
    Describe this server's GitHub App, its installations and its webhook.

    Args:
        session: The authenticated session.

    Returns:
        The status; ``configured`` false when there is no App yet.
    """
    return _status_out()


@router.post("/github/manifest", response_model=ManifestOut)
def github_manifest(
    data: ManifestRequest, session: Annotated[dict, Depends(require_elevated)]
) -> ManifestOut:
    """
    Start creating the App: its manifest, and where to post it.

    Args:
        data: The console's origin and, optionally, the organisation.
        session: An elevated session.

    Returns:
        The manifest, the form's action and its state (valid ten minutes).
    """
    started = manifest.start(
        data.origin, hooks_url=service.github_hooks_url(), organization=data.organization
    )
    return ManifestOut(manifest=started.manifest, post_url=started.post_url, state=started.state)


@router.post("/github/manifest/conversions", response_model=GitHubStatusOut)
def github_manifest_conversion(
    data: ConversionRequest, session: Annotated[dict, Depends(require_elevated)]
) -> GitHubStatusOut:
    """
    Finish creating the App: exchange GitHub's code for its credentials.

    Args:
        data: The callback's code and state.
        session: An elevated session.

    Returns:
        The integration's status, now configured.
    """
    manifest.convert(data.code, data.state)
    return _status_out()


@router.post("/github/installations", response_model=InstallationOut)
def github_add_installation(
    data: InstallationRequest, session: Annotated[dict, Depends(require_elevated)]
) -> InstallationOut:
    """
    Record an installation after GitHub confirms it is the App's.

    Args:
        data: The setup callback's installation id.
        session: An elevated session.

    Returns:
        The installation.
    """
    return InstallationOut(**asdict(service.add_installation(data.installation_id)))


@router.post("/github/installations/sync", response_model=InstallationListOut)
def github_sync_installations(
    session: Annotated[dict, Depends(get_current_session)],
) -> InstallationListOut:
    """
    Make the stored installations exactly those GitHub lists for the App.

    Args:
        session: The authenticated session.

    Returns:
        The installations.
    """
    items = [InstallationOut(**asdict(info)) for info in service.sync_installations()]
    return InstallationListOut(items=items, total=len(items))


@router.get("/github/repositories", response_model=RepositoryListOut)
def github_repositories(
    session: Annotated[dict, Depends(get_current_session)],
) -> RepositoryListOut:
    """
    List every repository the App's installations cover, for the new-app wizard.

    Args:
        session: The authenticated session.

    Returns:
        The repositories, by name.
    """
    items = [RepositoryOut(**item) for item in service.list_repositories()]
    return RepositoryListOut(items=items, total=len(items))


@router.get("/github/repositories/{owner}/{repo}/branches", response_model=BranchListOut)
def github_branches(
    owner: str, repo: str, session: Annotated[dict, Depends(get_current_session)]
) -> BranchListOut:
    """
    List a repository's branches.

    Args:
        owner: The repository's owner.
        repo: The repository's name.
        session: The authenticated session.

    Returns:
        The branches, as GitHub orders them.
    """
    items = [BranchOut(**item) for item in service.list_branches(owner, repo)]
    return BranchListOut(items=items, total=len(items))


@router.delete("/github", response_model=RemovalOut)
def github_remove(session: Annotated[dict, Depends(require_elevated)]) -> RemovalOut:
    """
    Forget the App's credentials and installations on this server.

    GitHub offers no API for this, so the App itself stays on GitHub until
    the operator deletes it at ``settings_url``.

    Args:
        session: An elevated session.

    Returns:
        Whether there was one, and where to delete it on GitHub.
    """
    return RemovalOut(**service.remove())
