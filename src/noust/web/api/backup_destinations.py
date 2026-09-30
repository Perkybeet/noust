# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Remote backup destinations through rclone (2.2).

A thin client of :class:`~noust.managers.backup_destinations.BackupDestinationManager`,
which owns every rule about what rclone is told and how a destination's
secrets are kept. Three things live here rather than in the manager:

- **Secrets never leave this process as values.** A destination's secret
  fields (a password, a token, the crypt passphrases) are reported as which
  ones are configured, never what they hold - :func:`_to_info` calls
  :meth:`~noust.managers.backup_destinations.BackupDestinationManager.configured_secret_fields`
  instead of reading them. ``show-key`` is the one deliberate exception, and
  it requires sudo mode.
- **Every mutation requires sudo mode.** Creating, changing or removing a
  destination changes where an operator's backups end up, and revealing an
  encryption key makes a set of backups recoverable by whoever reads it - the
  same D5 confirmation every other destructive or secret-revealing endpoint
  in this package requires.
- **Every mutation is audited**, like every other change the panel can make.

An encrypted destination is recoverable through here too: ``POST`` accepts
the key ``show-key`` returned (``encryption_key``) for a replacement server,
and ``DELETE`` of an encrypted destination is refused until ``key_saved`` says
the key was kept - the console shows it first - because removing the
destination deletes the only copy Noust has of what reads its backups.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import BackupError
from noust.core.store import BackupDestinationRecord
from noust.managers.backup_destinations import (
    BACKEND_FIELDS,
    BackendField,
    BackupDestinationManager,
    backend_fields,
    validate_destination_name,
)
from noust.managers.backup_manager import app_name_of_backup_id
from noust.validators.names import validate_app_name, validate_filename
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label
from noust.web.jobs import JobType, get_job_manager, restore_from_destination_job

router = APIRouter(route_class=NoustErrorRoute)

audit_log = logging.getLogger("noust.audit")


class BackendFieldInfo(BaseModel):
    """One field a backend's destination form asks for."""

    key: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""
    choices: list[str] = Field(default_factory=list)


class BackendInfo(BaseModel):
    """One backend Noust can build a destination for."""

    backend: str
    fields: list[BackendFieldInfo]


class BackendsResponse(BaseModel):
    """Response for the destination form catalogue."""

    backends: list[BackendInfo]


class DestinationInfo(BaseModel):
    """
    One backup destination, with its secrets redacted.

    Attributes:
        configured_secret_fields: Which of the backend's secret fields have a
            stored value. Never the value itself.
        encryption_configured: Whether the crypt passphrases exist, when the
            destination is encrypted.
    """

    name: str
    backend: str
    encrypted: bool
    settings: dict[str, str]
    configured_secret_fields: list[str] = Field(default_factory=list)
    encryption_configured: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class DestinationListResponse(BaseModel):
    """Response for listing backup destinations."""

    destinations: list[DestinationInfo]
    total: int


class EncryptionKey(BaseModel):
    """An encrypted destination's two passphrases, as show-key returns them."""

    password: str
    password2: str


class CreateDestinationRequest(BaseModel):
    """Request to create a backup destination."""

    name: str = Field(..., description="Destination name; also its rclone remote name")
    backend: str = Field(..., description="One of the backends from GET /backends")
    fields: dict[str, str] = Field(default_factory=dict, description="Field values by key")
    encrypted: bool = Field(default=False, description="Wrap the remote in an rclone crypt backend")
    encryption_key: EncryptionKey | None = Field(
        default=None,
        description="An existing key, as show-key returned it, to use instead of generating "
        "one: how backups already on the destination are read from a new server. Requires "
        "encrypted.",
    )


class UpdateDestinationRequest(BaseModel):
    """Request to change a backup destination. A blank secret field keeps its stored value."""

    fields: dict[str, str] = Field(default_factory=dict)
    encrypted: bool | None = None


class DestinationActionResponse(BaseModel):
    """Response for a destination action that completed immediately."""

    success: bool
    message: str
    destination: DestinationInfo | None = None


class TestDestinationResponse(BaseModel):
    """Response for testing a destination."""

    ok: bool
    entries: list[str] = Field(default_factory=list)


class ShowKeyResponse(BaseModel):
    """A destination's encryption passphrases, for safekeeping."""

    password: str
    password2: str


class RemoteBackupInfo(BaseModel):
    """One backup found on a destination."""

    backup_id: str
    app_name: str
    size: int | None = None
    modified: str | None = None
    has_metadata: bool = False


class RemoteBackupsResponse(BaseModel):
    """
    Response for browsing a destination.

    ``apps`` is populated when the request did not name one (the top-level
    directories found on the destination); ``backups`` when it did.
    """

    apps: list[str] = Field(default_factory=list)
    backups: list[RemoteBackupInfo] = Field(default_factory=list)


def _field_info(spec: BackendField) -> BackendFieldInfo:
    """
    Convert a backend field spec into the API model.

    Args:
        spec: The field, from :func:`~noust.managers.backup_destinations.backend_fields`.

    Returns:
        The API representation.
    """
    return BackendFieldInfo(
        key=spec.key,
        label=spec.label,
        secret=spec.secret,
        required=spec.required,
        placeholder=spec.placeholder,
        help=spec.help,
        choices=list(spec.choices),
    )


def _to_info(
    destination: BackupDestinationRecord, manager: BackupDestinationManager
) -> DestinationInfo:
    """
    Convert a destination record into the API model, with secrets redacted.

    Args:
        destination: Record as the store holds it.
        manager: Manager used to look up which secret fields are configured.

    Returns:
        The API representation.
    """
    configured = manager.configured_secret_fields(destination.name)
    encryption_configured = destination.encrypted and manager.has_encryption_key(destination.name)
    return DestinationInfo(
        name=destination.name,
        backend=destination.backend,
        encrypted=destination.encrypted,
        settings=destination.settings,
        configured_secret_fields=configured,
        encryption_configured=encryption_configured,
        created_at=destination.created_at,
        updated_at=destination.updated_at,
    )


@router.get("/backends", response_model=BackendsResponse)
def list_backends(session: Annotated[dict, Depends(get_current_session)]) -> BackendsResponse:
    """
    Describe every backend's destination form.

    Declared before ``/{name}`` so the literal path wins.

    Args:
        session: The authenticated session.

    Returns:
        Every backend Noust can build a destination for, with its fields.
    """
    return BackendsResponse(
        backends=[
            BackendInfo(backend=name, fields=[_field_info(spec) for spec in backend_fields(name)])
            for name in sorted(BACKEND_FIELDS)
        ]
    )


@router.get("", response_model=DestinationListResponse)
def list_destinations(
    session: Annotated[dict, Depends(get_current_session)],
) -> DestinationListResponse:
    """
    List every configured backup destination.

    Args:
        session: The authenticated session.

    Returns:
        The destinations, with every secret redacted.
    """
    manager = BackupDestinationManager()
    destinations = manager.list_destinations()
    return DestinationListResponse(
        destinations=[_to_info(destination, manager) for destination in destinations],
        total=len(destinations),
    )


@router.post("", response_model=DestinationActionResponse, status_code=201)
def create_destination(
    data: CreateDestinationRequest, session: Annotated[dict, Depends(require_elevated)]
) -> DestinationActionResponse:
    """
    Create a backup destination.

    Args:
        data: The destination to create.
        session: The authenticated, elevated session.

    Returns:
        The action outcome, carrying the destination as created.
    """
    name = validate_destination_name(data.name)
    audit_log.info(
        "create_backup_destination name=%s backend=%s session=%s",
        name,
        data.backend,
        actor_label(session),
    )
    manager = BackupDestinationManager()
    crypt_key = (
        {"password": data.encryption_key.password, "password2": data.encryption_key.password2}
        if data.encryption_key is not None
        else None
    )
    destination = manager.add(
        name, data.backend, data.fields, encrypted=data.encrypted, crypt_key=crypt_key
    )
    return DestinationActionResponse(
        success=True,
        message=f"Backup destination created: {name}",
        destination=_to_info(destination, manager),
    )


@router.put("/{name}", response_model=DestinationActionResponse)
def update_destination(
    name: str, data: UpdateDestinationRequest, session: Annotated[dict, Depends(require_elevated)]
) -> DestinationActionResponse:
    """
    Change a backup destination's fields.

    Args:
        name: Destination name.
        data: Fields to change; a blank secret field keeps its stored value.
        session: The authenticated, elevated session.

    Returns:
        The action outcome, carrying the destination as it now stands.
    """
    audit_log.info("update_backup_destination name=%s session=%s", name, actor_label(session))
    manager = BackupDestinationManager()
    destination = manager.update(name, data.fields, encrypted=data.encrypted)
    return DestinationActionResponse(
        success=True,
        message=f"Backup destination updated: {name}",
        destination=_to_info(destination, manager),
    )


@router.delete("/{name}", response_model=DestinationActionResponse)
def delete_destination(
    name: str,
    session: Annotated[dict, Depends(require_elevated)],
    force: Annotated[bool, Query(description="Remove it even if a schedule references it")] = False,
    key_saved: Annotated[
        bool,
        Query(
            description="The encryption key was saved (show-key): required to remove an "
            "encrypted destination, whose backups are unreadable without it"
        ),
    ] = False,
) -> DestinationActionResponse:
    """
    Remove a backup destination and its secrets.

    Args:
        name: Destination name.
        force: Remove it even when a schedule references it, dropping the
            reference from those schedules.
        key_saved: The operator kept a copy of the encryption key.
        session: The authenticated, elevated session.

    Returns:
        The action outcome.

    Raises:
        BackupError: When a schedule references it and ``force`` was not
            given, or it is encrypted and ``key_saved`` was not given.
    """
    audit_log.info(
        "delete_backup_destination name=%s force=%s key_saved=%s session=%s",
        name,
        force,
        key_saved,
        actor_label(session),
    )
    BackupDestinationManager().remove(name, force=force, key_saved=key_saved)
    return DestinationActionResponse(success=True, message=f"Backup destination removed: {name}")


def _unreachable(exc: BackupError) -> HTTPException:
    """
    Answer a destination that could not be reached as what it is: the operator's to fix.

    A destination that does not answer is a wrong address, a wrong credential
    or a missing permission, not a fault of this server, so it is a 4xx and
    not the 500 a bare :class:`~noust.core.exceptions.BackupError` becomes.
    What rclone said travels verbatim in ``output`` (already scrubbed of the
    destination's secrets by the manager), under a fixed hint.

    Args:
        exc: What the manager raised.

    Returns:
        The exception to raise, in the API's error contract.
    """
    return HTTPException(
        status_code=400,
        detail={
            "error": "destination_unreachable",
            "detail": exc.message,
            "hint": (
                "Check the destination's address, credentials and permissions; "
                "the output below is rclone's own."
            ),
            "output": exc.details or None,
        },
    )


@router.post("/{name}/test", response_model=TestDestinationResponse)
def test_destination(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> TestDestinationResponse:
    """
    Check that a destination can be reached.

    Args:
        name: Destination name.
        session: The authenticated session.

    Returns:
        The top-level entries found there.

    Raises:
        HTTPException: 400 ``destination_unreachable`` when it cannot be
            reached, with rclone's own words in ``output``.
    """
    try:
        result = BackupDestinationManager().test(name)
    except BackupError as exc:
        raise _unreachable(exc) from exc
    return TestDestinationResponse(ok=bool(result["ok"]), entries=list(result["entries"]))


@router.post("/{name}/show-key", response_model=ShowKeyResponse)
def show_key(name: str, session: Annotated[dict, Depends(require_elevated)]) -> ShowKeyResponse:
    """
    Reveal a destination's encryption passphrases, for safekeeping.

    Sudo mode: printing these makes every backup encrypted with them
    recoverable by whoever reads the response, same as revealing any other
    stored credential.

    Args:
        name: Destination name.
        session: The authenticated, elevated session.

    Returns:
        The passphrases.

    Raises:
        BackupError: When the destination is not encrypted, or its keys are
            missing.
    """
    audit_log.info("show_backup_destination_key name=%s session=%s", name, actor_label(session))
    keys = BackupDestinationManager().show_key(name)
    return ShowKeyResponse(**keys)


@router.get("/{name}/backups", response_model=RemoteBackupsResponse)
def list_remote_backups(
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    app: Annotated[str | None, Query(description="Application name to list backups for")] = None,
) -> RemoteBackupsResponse:
    """
    Browse what a destination holds.

    Args:
        name: Destination name.
        app: Application to list backups for; without it, the application
            directories found at the destination's own path.
        session: The authenticated session.

    Returns:
        The applications found, or that application's backups, newest first.

    Raises:
        HTTPException: 400 ``destination_unreachable`` when it cannot be
            reached, with rclone's own words in ``output``.
    """
    try:
        result = BackupDestinationManager().remote_list(name, app)
    except BackupError as exc:
        raise _unreachable(exc) from exc
    return RemoteBackupsResponse(
        apps=list(result.get("apps", [])),
        backups=[RemoteBackupInfo(**entry) for entry in result.get("backups", [])],
    )


class RestoreFromDestinationRequest(BaseModel):
    """Request to restore a backup found on a remote destination."""

    app_name: str | None = Field(
        default=None,
        description="Application the backup belongs to; derived from the backup id when omitted",
    )
    target_domain: str | None = Field(default=None, description="Domain to restore into")
    restore_env: bool = Field(default=True, description="Restore the .env files from the archive")


@router.post(
    "/{name}/backups/{backup_id}/restore", response_model=JobAcceptedResponse, status_code=202
)
def restore_from_destination(
    name: str,
    backup_id: str,
    session: Annotated[dict, Depends(require_elevated)],
    data: RestoreFromDestinationRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue a restore of a backup downloaded from a remote destination.

    Args:
        name: Destination to download from.
        backup_id: Backup identifier.
        data: Restore options.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 400 when the application cannot be determined from
            the backup id and ``app_name`` was not given.
    """
    validated_id = validate_filename(backup_id)
    app_name = (data.app_name if data else None) or app_name_of_backup_id(validated_id)
    if not app_name:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot determine the application {validated_id!r} belongs to; pass app_name.",
        )

    # Refused here so the request fails, not the job; the manager checks it again
    # where it becomes a remote path.
    app_name = validate_app_name(app_name)

    requested = data.target_domain if data else None
    target_domain = strict_domain(requested) if requested else None
    restore_env = data.restore_env if data else True

    # Without a target the backup goes back to the application whose folder it
    # is read from; the download refuses a backup whose metadata says otherwise.
    into = target_domain or f"the application {app_name}"
    job = get_job_manager().create_job(
        job_type=JobType.RESTORE,
        name=f"Restore {validated_id} from {name} into {into}",
        description=f"Downloading {validated_id} from {name} and restoring it into {into}",
        func=restore_from_destination_job,
        kwargs={
            "destination_name": name,
            "backup_id": validated_id,
            "app_name": app_name,
            "target_domain": target_domain,
            "restore_env": restore_env,
        },
        metadata={"backup_id": validated_id, "destination": name},
        actor=actor_label(session),
    )

    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Restore from {name} queued for {validated_id}",
        job=job.to_dict(),
    )
