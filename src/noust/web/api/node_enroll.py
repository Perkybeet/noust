# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Guided enrollment of a server, over HTTP: ``/api/fleet/enroll``.

A thin layer over :class:`noust.fleet.enroll.NodeEnroller` (rule 3), in two
steps because the host key is never taken on trust:

1. ``POST /api/fleet/enroll/host-key`` asks an address what host key answers,
   and shows its fingerprint for the operator to compare with the server's own;
2. ``POST /api/fleet/enroll`` carries that fingerprint back, with the credential
   to use once, and starts a job that installs Noust on the server, authorizes
   this central and joins it. The job's log streams the installer's output.

The credential is in the request body and nowhere afterwards. It is the argument
of one job function, handed to ssh through its environment and removed with the
private directory it may have been written to; the job's own record keeps its
log and its result, neither of which holds it, and the approval a four-eyes
policy asks for is made over everything but it
(:func:`noust.core.accounts.approvals.one_time_fields`). The checks that need no
connection run before the job is queued, so a refusal is the response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core import audit
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    require_elevated,
    require_scope,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

if TYPE_CHECKING:
    from noust.fleet.enroll import EnrollRequest, NodeEnroller

router = APIRouter(route_class=NoustErrorRoute)


class HostKeyRequest(BaseModel):
    """
    Body of ``POST /api/fleet/enroll/host-key``.

    Attributes:
        host: The server's address or name.
        port: Where its sshd listens.
    """

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)


class HostKeyResponse(BaseModel):
    """
    What answered at the address.

    Attributes:
        host: The address that was asked.
        port: Its port.
        key_type: ``ssh-ed25519``.
        fingerprint: ``SHA256:...``, as ``ssh-keygen -lf`` prints it.
    """

    host: str
    port: int
    key_type: str
    fingerprint: str


class EnrollBody(BaseModel):
    """
    Body of ``POST /api/fleet/enroll``.

    Attributes:
        name: The name the server gets on this central.
        host: Its address.
        port: Where its sshd listens.
        ssh_user: The account the credential is for.
        host_key_fingerprint: The fingerprint the operator confirmed.
        access: The ceiling the server sets for this central.
        allow_host_access: Whether the central may also reach the host there.
        install: Install Noust when it is missing.
        replace_token: Revoke a token this central already holds there.
        password: The account's password, used once. Never stored.
        private_key: A private key instead, as text, used once. Never stored.
        passphrase: The key's passphrase, when it has one.
    """

    name: str = Field(min_length=1, max_length=32)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", min_length=1, max_length=32)
    host_key_fingerprint: str = Field(min_length=1, max_length=64)
    access: Literal["read", "deploy", "admin"] = "admin"
    allow_host_access: bool = False
    install: bool = True
    replace_token: bool = False
    password: str | None = Field(default=None, max_length=1024)
    private_key: str | None = Field(default=None, max_length=16384)
    passphrase: str | None = Field(default=None, max_length=1024)

    def __repr__(self) -> str:
        """Describe it without the credential."""
        return f"EnrollBody(name={self.name!r}, host={self.host!r}, port={self.port})"

    __str__ = __repr__


def _enroller() -> NodeEnroller:
    """
    Build the enroller.

    Returns:
        A :class:`~noust.fleet.enroll.NodeEnroller`.
    """
    from noust.fleet.enroll import NodeEnroller

    return NodeEnroller()


def _request(body: EnrollBody) -> EnrollRequest:
    """
    Turn a body into the enrollment's request.

    Args:
        body: The validated body.

    Returns:
        An :class:`~noust.fleet.enroll.EnrollRequest`.
    """
    from noust.fleet.enroll import Credential, EnrollRequest

    return EnrollRequest(
        name=body.name,
        host=body.host,
        port=body.port,
        ssh_user=body.ssh_user,
        host_key_fingerprint=body.host_key_fingerprint,
        credential=Credential(
            password=body.password or None,
            private_key=body.private_key or None,
            passphrase=body.passphrase or None,
        ),
        access=body.access,
        host_access=body.allow_host_access,
        install=body.install,
        replace_token=body.replace_token,
    )


def enroll_job(
    name: str,
    host: str,
    port: int,
    ssh_user: str,
    host_key_fingerprint: str,
    access: str,
    allow_host_access: bool,
    install: bool,
    replace_token: bool,
    password: str | None,
    private_key: str | None,
    passphrase: str | None,
    actor: str,
    job_context: JobContext,
) -> dict[str, Any]:
    """
    Enroll a server, as a job whose log streams what happens on it.

    Args:
        name: The node's name.
        host: Its address.
        port: Its sshd port.
        ssh_user: The account the credential is for.
        host_key_fingerprint: The confirmed fingerprint.
        access: The ceiling.
        allow_host_access: Whether the central may reach the host there.
        install: Install Noust when missing.
        replace_token: Revoke an existing token.
        password: The credential, one of three (see :class:`EnrollBody`).
        private_key: As above.
        passphrase: As above.
        actor: Who asked.
        job_context: The job's handle.

    Returns:
        The node, as :meth:`NodeRecord.to_dict`: no credential is in it.
    """
    # Whatever any line of the log or the error might quote, it is scrubbed: the
    # job's own classifier knows these names are secrets.
    job_context.add_secret_env(
        {
            key: value
            for key, value in (
                ("ENROLL_PASSWORD", password),
                ("ENROLL_PRIVATE_KEY", private_key),
                ("ENROLL_PASSPHRASE", passphrase),
            )
            if value
        }
    )
    request = _request(
        EnrollBody(
            name=name,
            host=host,
            port=port,
            ssh_user=ssh_user,
            host_key_fingerprint=host_key_fingerprint,
            access=access,  # type: ignore[arg-type]
            allow_host_access=allow_host_access,
            install=install,
            replace_token=replace_token,
            password=password,
            private_key=private_key,
            passphrase=passphrase,
        )
    )
    job_context.update("Enrolling", 5)
    node = _enroller().enroll(request, actor=actor, log=job_context.log)
    return dict(node.to_dict())


@router.post("/host-key", response_model=HostKeyResponse)
def scan_host_key(
    body: HostKeyRequest,
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
) -> HostKeyResponse:
    """
    Ask an address for its ed25519 host key, to be confirmed before anything is sent.

    Args:
        body: The address.
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The key's fingerprint. Nothing is trusted by this; the enrollment
        connects to the fingerprint that comes back with it.
    """
    scan = _enroller().scan_host_key(body.host, body.port)
    audit.record(
        "fleet.enroll.hostkey",
        target=f"host:{scan.host}:{scan.port}",
        details={"fingerprint": scan.fingerprint, "by": actor_label(session)},
    )
    return HostKeyResponse(**scan.to_dict())


@router.post("", response_model=JobAcceptedResponse, status_code=202)
def enroll_server(
    body: EnrollBody, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Enroll a server: install Noust if needed, authorize this central, join it.

    Args:
        body: The server, the confirmed host key, and the credential to use once.
        session: The authenticated session in sudo mode.

    Returns:
        The queued job; its log streams the installer.
    """
    enroller = _enroller()
    request = _request(body)
    enroller.validate(request)
    job = get_job_manager().create_job(
        job_type=JobType.NODE_ENROLL,
        name=f"Enroll {body.name}",
        description=f"Installing Noust on {body.host} if needed, and joining it as {body.name}",
        func=enroll_job,
        kwargs={
            "name": body.name,
            "host": body.host,
            "port": body.port,
            "ssh_user": body.ssh_user,
            "host_key_fingerprint": body.host_key_fingerprint,
            "access": body.access,
            "allow_host_access": body.allow_host_access,
            "install": body.install,
            "replace_token": body.replace_token,
            "password": body.password,
            "private_key": body.private_key,
            "passphrase": body.passphrase,
            "actor": actor_label(session),
        },
        metadata={"kind": "node_enroll", "node": body.name, "host": body.host},
        total_steps=100,
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Enrolling {body.name} queued",
        job=job.to_dict(),
    )
