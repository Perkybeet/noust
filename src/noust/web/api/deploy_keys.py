# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's deploy key: see it, make or import one, rotate it, remove it.

A client of :class:`noust.managers.source_keys.DeployKeyManager`, like ``noust
app deploy-key``: an endpoint here turns HTTP into one call of the manager and
back. The private key is never in a response. Importing one is the only call
that carries a private key in, and it asks for sudo mode like every secret that
is written; removing a key asks for it too.

``/api/apps/{domain}/deploy-key`` is the application's side. ``/api/deploy-keys``
lists every key (to assign one that exists to another repository) and the host
keys Noust pinned the first time it reached each code host.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.core.deploy_keys import DeployKey
from noust.core.teams import scoped_store
from noust.managers.source_keys import MAX_PRIVATE_KEY, DeployKeyManager, DeployKeyView
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label
from noust.web.permissions.principal import actor_of
from noust.web.pydantic_compat import iso_offset_validator

#: ``/api/apps/{domain}/deploy-key``.
router = APIRouter(route_class=NoustErrorRoute)

#: ``/api/deploy-keys``.
keys_router = APIRouter(route_class=NoustErrorRoute)


class PendingKeyResponse(BaseModel):
    """
    The key a rotation waits to put in place.

    Attributes:
        fingerprint: ``SHA256:...``.
        public_key: The OpenSSH public key line to add at the code host.
        since: When the rotation started.
    """

    fingerprint: str
    public_key: str
    since: str

    _iso_timestamps = iso_offset_validator("since")


class DeployKeyResponse(BaseModel):
    """
    A deploy key. Never its private half.

    Attributes:
        name: The key's name.
        origin: ``generated`` here, or ``imported``.
        key_type: ``ssh-ed25519``, ``ssh-rsa``...
        fingerprint: ``SHA256:...``, as ``ssh-keygen -l`` prints it.
        public_key: The OpenSSH public key line to add at the code host.
        created_at: When it was made or imported.
        rotated_at: When a rotation last finished.
        last_used_at: When git last reached a repository with it.
        repositories: The repositories it reaches, as ``host/owner/repo``.
        applications: The applications built from them.
        pending: The new key while a rotation waits; null otherwise.
    """

    name: str
    origin: str
    key_type: str
    fingerprint: str
    public_key: str
    created_at: str
    rotated_at: str | None = None
    last_used_at: str | None = None
    repositories: list[str] = Field(default_factory=list)
    applications: list[str] = Field(default_factory=list)
    pending: PendingKeyResponse | None = None

    _iso_timestamps = iso_offset_validator("created_at", "rotated_at", "last_used_at")


class AppDeployKeyResponse(BaseModel):
    """
    What an application's repository is reached with.

    Attributes:
        domain: The application.
        repository: Its repository as ``host/owner/repo``; null when its source
            is not a git repository.
        source_is_ssh: Whether its source is an ssh URL, the only kind a deploy
            key signs in to.
        ssh_url: The ssh spelling to deploy from when the source is https.
        key: Its deploy key; null when it is reached with the server-wide key.
        shared_with: The other applications built from the same repository,
            which use the same key.
        server_key_fingerprint: The server-wide key ``noust setup ssh`` manages,
            used for a repository with no key of its own.
        warnings: What the operator should know.
    """

    domain: str
    repository: str | None = None
    source_is_ssh: bool
    ssh_url: str | None = None
    key: DeployKeyResponse | None = None
    shared_with: list[str] = Field(default_factory=list)
    server_key_fingerprint: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DeployKeyListResponse(BaseModel):
    """Every deploy key."""

    keys: list[DeployKeyResponse]
    total: int


class DeployKeyCreateRequest(BaseModel):
    """
    Make a key for the application's repository, or assign one that exists.

    Attributes:
        name: The new key's name; the application's name by default.
        key: The name of an existing key to assign instead of making one.
    """

    name: str | None = Field(default=None, max_length=63, description="Name of the new key")
    key: str | None = Field(default=None, max_length=63, description="Existing key to assign")


class DeployKeyImportRequest(BaseModel):
    """
    A private key to use as the application's deploy key.

    Attributes:
        private_key: The key, in the OpenSSH or PEM format, with no passphrase.
        name: The key's name; the application's name by default.
    """

    private_key: str = Field(
        min_length=1, max_length=MAX_PRIVATE_KEY, description="The whole private key file"
    )
    name: str | None = Field(default=None, max_length=63)


class DeployKeyTestResponse(BaseModel):
    """
    What the repository answered.

    Attributes:
        ok: Always true: a refusal is an error response with git's own words.
        message: One sentence; it says when a waiting key became the key.
    """

    ok: bool = True
    message: str


class PinnedHostResponse(BaseModel):
    """
    A host key Noust pinned the first time it reached a code host.

    Attributes:
        host: The host as ``known_hosts`` names it.
        fingerprint: ``SHA256:...``.
        key_type: ``ED25519``, ``RSA``...
    """

    host: str
    fingerprint: str
    key_type: str


class PinnedHostsResponse(BaseModel):
    """The pinned host keys."""

    hosts: list[PinnedHostResponse]
    total: int


class PinHostRequest(BaseModel):
    """
    A code host and the fingerprint of the SSH key it publishes.

    Attributes:
        host: The host, or ``host:port``.
        fingerprint: ``SHA256:`` and 43 characters, from the code host's own documentation.
    """

    host: str = Field(..., min_length=1, max_length=260)
    fingerprint: str = Field(..., min_length=1, max_length=64)


class ForgottenHostResponse(BaseModel):
    """
    The outcome of forgetting a host key.

    Attributes:
        host: The host.
        forgotten: Whether a key was removed.
    """

    host: str
    forgotten: bool


def _actor(session: Mapping[str, Any]) -> Actor:
    """
    Name who a request acts for, as the audit trail records it.

    Args:
        session: The authenticated payload.

    Returns:
        The actor.
    """
    built = actor_of(session)
    return built if isinstance(built, Actor) else Actor.from_label(actor_label(session))


def _manager(session: Mapping[str, Any]) -> DeployKeyManager:
    """
    Build the manager a request uses.

    Args:
        session: The authenticated session.

    Returns:
        A manager over the process-wide store and runner.
    """
    return DeployKeyManager(actor=_actor(session))


def _key(manager: DeployKeyManager, key: DeployKey) -> DeployKeyResponse:
    """
    Turn a key into its response.

    Args:
        manager: The manager, which knows the applications.
        key: The key.

    Returns:
        The response; nothing private.
    """
    return DeployKeyResponse(
        name=key.name,
        origin=key.origin,
        key_type=key.key_type,
        fingerprint=key.fingerprint,
        public_key=key.public_key,
        created_at=key.created_at,
        rotated_at=key.rotated_at,
        last_used_at=key.last_used_at,
        repositories=list(key.repositories),
        applications=manager.apps_of(key),
        pending=(
            PendingKeyResponse(
                fingerprint=key.pending.fingerprint,
                public_key=key.pending.public_key,
                since=key.pending.since,
            )
            if key.pending
            else None
        ),
    )


def _view(manager: DeployKeyManager, view: DeployKeyView) -> AppDeployKeyResponse:
    """
    Turn an application's view into its response.

    Args:
        manager: The manager.
        view: What it found.

    Returns:
        The response.
    """
    return AppDeployKeyResponse(
        domain=view.domain,
        repository=view.repository,
        source_is_ssh=view.source_is_ssh,
        ssh_url=view.ssh_url,
        key=_key(manager, view.key) if view.key else None,
        shared_with=list(view.shared_with),
        server_key_fingerprint=view.server_key_fingerprint,
        warnings=list(view.warnings),
    )


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/deploy-key", response_model=AppDeployKeyResponse)
def get_deploy_key(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppDeployKeyResponse:
    """
    Show what an application's repository is reached with. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Its deploy key, when it has one, and the server-wide key it falls back to.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    manager = _manager(session)
    return _view(manager, manager.view(_known(domain)))


@router.post("/{domain}/deploy-key", response_model=AppDeployKeyResponse, status_code=201)
def create_deploy_key(
    domain: str,
    body: DeployKeyCreateRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> AppDeployKeyResponse:
    """
    Make an ed25519 key for the application's repository, or assign an existing one.

    The public half in the answer is what to add at the code host. The private
    half stays on the server and is never returned.

    Args:
        domain: Domain of the application.
        body: A name for the new key, or the name of one that exists.
        session: The authenticated session.

    Returns:
        The application's deploy key.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The application has no git repository, already has a
            key, or the name is taken (400).
    """
    validated = _known(domain)
    manager = _manager(session)
    if body.key:
        manager.assign(validated, body.key)
    else:
        manager.generate(validated, name=body.name)
    return _view(manager, manager.view(validated))


@router.post("/{domain}/deploy-key/import", response_model=AppDeployKeyResponse, status_code=201)
def import_deploy_key(
    domain: str,
    body: DeployKeyImportRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> AppDeployKeyResponse:
    """
    Use a private key the operator has as the application's deploy key.

    A secret travels in, so this needs sudo mode. The public half is derived
    from the key by ssh-keygen, and a key with a passphrase is refused.

    Args:
        domain: Domain of the application.
        body: The key.
        session: The authenticated, elevated session.

    Returns:
        The application's deploy key.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The key is not valid or has a passphrase (400).
    """
    validated = _known(domain)
    manager = _manager(session)
    manager.import_key(validated, body.private_key, name=body.name)
    return _view(manager, manager.view(validated))


@router.delete("/{domain}/deploy-key", response_model=AppDeployKeyResponse)
def delete_deploy_key(
    domain: str, session: Annotated[dict, Depends(require_elevated)]
) -> AppDeployKeyResponse:
    """
    Take the repository off its deploy key; the key is deleted when nothing else uses it.

    The code host still trusts the public key until it is removed there.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.

    Returns:
        The application's deploy key, which is null now.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    manager = _manager(session)
    manager.remove(validated)
    return _view(manager, manager.view(validated))


@router.post("/{domain}/deploy-key/rotate", response_model=AppDeployKeyResponse)
def start_rotation(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppDeployKeyResponse:
    """
    Make the key that will replace the application's, keeping the old one working.

    The new public key is in ``key.pending``. Add it at the code host, keeping
    the old one there; the first clone or fetch the host answers with it (or
    ``POST .../deploy-key/test``) makes it the key and removes the old one.
    Asking again while one waits returns the same key.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The application's deploy key, with its waiting key.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The application has no deploy key (400).
    """
    validated = _known(domain)
    manager = _manager(session)
    manager.rotate_start(validated)
    return _view(manager, manager.view(validated))


@router.delete("/{domain}/deploy-key/rotate", response_model=AppDeployKeyResponse)
def cancel_rotation(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppDeployKeyResponse:
    """
    Give up a rotation; the old key was never replaced.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The application's deploy key, with nothing waiting.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    manager = _manager(session)
    manager.rotate_cancel(validated)
    return _view(manager, manager.view(validated))


@router.post("/{domain}/deploy-key/test", response_model=DeployKeyTestResponse)
def test_deploy_key(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> DeployKeyTestResponse:
    """
    Ask the repository for its branches with the key, as a deploy would.

    A waiting key that the code host accepts becomes the key here, by the same
    path a deploy takes.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        What the repository answered.

    Raises:
        HTTPException: 404 when the application is unknown.
        SourceError: The repository cannot be read; git's own words are in the
            error (a refused key, a host key that changed).
    """
    return DeployKeyTestResponse(message=_manager(session).verify(_known(domain)))


@keys_router.get("", response_model=DeployKeyListResponse)
def list_deploy_keys(
    session: Annotated[dict, Depends(get_current_session)],
) -> DeployKeyListResponse:
    """
    List every deploy key and the applications it reaches.

    Args:
        session: The authenticated session.

    Returns:
        The keys, by name.
    """
    manager = _manager(session)
    keys = [_key(manager, key) for key in manager.keys()]
    return DeployKeyListResponse(keys=keys, total=len(keys))


@keys_router.get("/hosts", response_model=PinnedHostsResponse)
def list_pinned_hosts(
    session: Annotated[dict, Depends(get_current_session)],
) -> PinnedHostsResponse:
    """
    List the SSH host keys Noust pinned the first time it reached each code host.

    Args:
        session: The authenticated session.

    Returns:
        The pinned keys.
    """
    hosts = [
        PinnedHostResponse(host=h.host, fingerprint=h.fingerprint, key_type=h.key_type)
        for h in _manager(session).pinned_hosts()
    ]
    return PinnedHostsResponse(hosts=hosts, total=len(hosts))


@keys_router.post("/hosts", response_model=PinnedHostResponse, status_code=201)
def pin_host(
    body: PinHostRequest, session: Annotated[dict, Depends(require_elevated)]
) -> PinnedHostResponse:
    """
    Pin a code host's SSH key before the first connection, by the fingerprint it publishes.

    The host is asked for its keys and only the one with that fingerprint is recorded; a host
    that shows no key with it is refused, with the fingerprints it showed. A host that has
    another key pinned is never changed here. Sudo mode.

    Args:
        body: The host and the fingerprint.
        session: The authenticated, elevated session.

    Returns:
        The key now pinned.

    Raises:
        ValidationError: The host or the fingerprint is not valid, or another key is pinned (400).
        SourceError: The host could not be reached or showed no such key (400).
    """
    pinned = _manager(session).pin_host(body.host, body.fingerprint)
    return PinnedHostResponse(
        host=pinned.host, fingerprint=pinned.fingerprint, key_type=pinned.key_type
    )


@keys_router.delete("/hosts/{host}", response_model=ForgottenHostResponse)
def forget_pinned_host(
    host: str, session: Annotated[dict, Depends(require_elevated)]
) -> ForgottenHostResponse:
    """
    Forget a pinned host key, so the next connection pins the one the host shows now.

    The one deliberate way to accept a host key that changed: the operator is
    expected to have compared its fingerprint with the code host's published
    ones. Sudo mode.

    Args:
        host: The host, or ``host:port``.
        session: The authenticated, elevated session.

    Returns:
        Whether a key was removed.
    """
    return ForgottenHostResponse(host=host, forgotten=_manager(session).forget_host(host))
