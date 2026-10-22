# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build a release here, run it elsewhere: the node's side of a central's build server (checklist A-49).

A client of :mod:`noust.deployers.release_bundles`, which ``noust releases bundle`` and ``noust
releases receive`` call too. What these routes add is only the transport a central needs through its
tunnel, in chunks, because a request through a tunnel is cut after a few minutes and a bundle is
hundreds of megabytes:

- ``POST .../releases/bundle`` queues the build (fetch, install and build in the sandbox, no hook,
  no migration, no switch) and packs the release into this server's bundle spool. The job's result
  names the bundle and its SHA-256. ``GET .../releases/bundles/{id}/data`` reads it by ranges, and
  ``DELETE`` lets go of it once it is taken.
- ``POST .../releases/uploads`` starts the arrival of a bundle (the protocol of every upload here:
  chunks at offsets, the digest checked when it completes), and ``POST .../releases/receive``
  queues activating it: the checksum, the extraction checks, the application's own ``pre_deploy``
  hooks and the health gate, which puts back what served when the release does not answer.

Both are deploys: ``apps.deploy``, held by a freeze of deploys. A bundle is a built tree, which
may hold what the build baked in, so it is never offered to a role that only reads.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core import deploy_guard
from noust.core.exceptions import ValidationError
from noust.core.store import DeploymentTrigger
from noust.core.teams import scoped_store
from noust.deployers.bundle_spool import BUNDLE_UPLOADS, MAX_READ_BYTES, BundleSpool, SpooledBundle
from noust.deployers.recorder import CapturingLogger
from noust.deployers.release_bundles import build_and_pack, receive
from noust.deployers.releases import is_release_id
from noust.managers.database.uploads import Upload, UploadOffsetError, UploadStore
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, strict_domain
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)

#: The most a client should put in one chunk, as every upload here.
CHUNK_BYTES = 512 * 1024


class BundleRequest(BaseModel):
    """What to build and pack."""

    commit: str | None = Field(
        default=None, description="Build this commit (full or abbreviated) instead of the branch"
    )
    release_id: str | None = Field(
        default=None, description="Pack this release, already built here, instead of building one"
    )
    keep: bool = Field(default=False, description="Keep the release this builds in releases/")


class BundleOut(BaseModel):
    """A built release waiting to be fetched."""

    id: str
    domain: str
    release_id: str
    commit: str | None
    sha256: str = Field(description="Its digest: the receiving side checks it before reading")
    size: int
    files: int
    built_at: str
    builder: str
    noust_version: str
    app_type: str
    chunk_size: int = Field(description="The most one range request should ask for")


class BundleListOut(BaseModel):
    """The bundles waiting for an application."""

    bundles: list[BundleOut]


class BundleUploadStart(BaseModel):
    """Start the arrival of a bundle."""

    size: int = Field(..., gt=0, description="Its size in bytes")
    sha256: str = Field(
        ..., min_length=64, max_length=64, description="Its digest, as 64 hex digits"
    )
    file_name: str = Field(
        default="bundle.noust-release.tar.gz", max_length=255, description="Its name"
    )


class BundleUploadOut(BaseModel):
    """A bundle on its way in."""

    id: str
    size: int
    received: int = Field(description="Bytes held, which is where to resume")
    sha256: str | None
    complete: bool
    chunk_size: int = Field(description="The most one chunk request may carry")


class ReceiveRequest(BaseModel):
    """What activating a bundle that arrived confirms."""

    upload_id: str = Field(..., description="The finished upload that holds the bundle")
    schema_changed_ok: bool = Field(
        default=False, description="Take a release older than a deployment that changed the schema"
    )
    frozen_ok: bool = Field(
        default=False,
        description="Switch although deploys are frozen for the application: a person's "
        "decision, recorded in the audit trail",
    )


def _spool() -> BundleSpool:
    """
    Returns:
        This server's bundles.
    """
    return BundleSpool()


def _uploads() -> UploadStore:
    """
    Returns:
        The bundles arriving on this server.
    """
    return UploadStore(profile=BUNDLE_UPLOADS)


def _known(domain: str) -> str:
    """
    Args:
        domain: From the path.

    Returns:
        The validated domain of an application the caller sees.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def _bundle(item: SpooledBundle) -> BundleOut:
    """
    Args:
        item: A bundle in the spool.

    Returns:
        The response model.
    """
    return BundleOut(**item.to_dict(), chunk_size=CHUNK_BYTES)


def _upload(upload: Upload) -> BundleUploadOut:
    """
    Args:
        upload: An upload session.

    Returns:
        The response model.
    """
    return BundleUploadOut(
        id=upload.id,
        size=upload.size,
        received=upload.received,
        sha256=upload.sha256,
        complete=upload.complete,
        chunk_size=CHUNK_BYTES,
    )


def bundle_release_job(
    domain: str,
    commit: str | None = None,
    release_id: str | None = None,
    keep: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Build a release without activating it and leave its bundle in the spool, as a job.

    Args:
        domain: The application.
        commit: Build this commit.
        release_id: Pack this release, already built here.
        keep: Keep the release the build made.
        job_context: Injected by the job manager.

    Returns:
        The bundle: its id and digest are what the central fetches by.

    Raises:
        NoustError: The build failed; nothing is left in the spool.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Building to ship", 10)
        logger.attach_sink(job_context.log)
    spool = _spool()
    bundle_id, destination = spool.reserve()
    whole = False
    try:
        packed = build_and_pack(
            domain,
            destination,
            commit=commit,
            release_id=release_id,
            keep=keep,
            logger=logger,
            on_phase=(lambda _i, _n, message: job_context.update(message, 50))
            if job_context is not None
            else None,
        )
        spooled = spool.keep(bundle_id, domain, packed)
        whole = True
    finally:
        # Nothing half-built stays in the spool, however the build ends: a bundle is whole or it
        # is not there.
        if not whole:
            spool.abandon(bundle_id)
    if job_context is not None:
        job_context.update("Packed, waiting to be fetched", 100)
    return {"domain": domain, "status": "packed", "bundle": spooled.to_dict()}


def receive_bundle_job(
    domain: str,
    upload_id: str,
    schema_changed_ok: bool = False,
    frozen_ok: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Activate a bundle that arrived, as a job, and let go of the file whatever happens.

    Args:
        domain: The application.
        upload_id: The finished upload that holds the bundle.
        schema_changed_ok: Take a release older than a deployment that changed the schema.
        frozen_ok: Switch during a freeze.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        NoustError: The checksum did not match, a hook failed, or the release did not pass the
            health gate (what served before is back by then).
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Receiving the release", 10)
        logger.attach_sink(job_context.log)
    store = _uploads()
    try:
        upload = store.get(upload_id)
        reception = receive(
            domain,
            store.path(upload_id),
            sha256=upload.sha256 or "",
            schema_changed_ok=schema_changed_ok,
            frozen_ok=frozen_ok,
            trigger=DeploymentTrigger.PANEL.value,
            job_id=job_context.job_id if job_context is not None else None,
            on_phase=(lambda _i, _n, message: job_context.update(message, 60))
            if job_context is not None
            else None,
            logger=logger,
        )
    finally:
        # The file is the release's whole tree: it does not stay on a disk once it has been tried.
        store.discard(upload_id)
    if job_context is not None:
        job_context.update("Release active", 100)
    return {
        "domain": reception.domain,
        "status": "received",
        "release_id": reception.release,
        "commit": reception.commit,
        "deployment_id": reception.deployment_id,
        "built_on": reception.manifest.builder,
        "rewritten": reception.rewritten,
    }


# -------------------------------------------------------------------------------- building


@router.post("/{domain}/releases/bundle", response_model=JobAcceptedResponse, status_code=202)
def build_bundle(
    domain: str,
    body: BundleRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue building a release without activating it and packing it for another server.

    Nothing that serves is touched: ``current`` stays, no hook runs, nothing restarts, and the
    release is removed once packed unless ``keep`` asks for it.

    Args:
        domain: The application.
        body: What to build.
        session: The authenticated session.

    Returns:
        The queued job; its result names the bundle.

    Raises:
        HTTPException: 400 for a release id that is not one, 404 when the application is unknown.
    """
    validated = _known(domain)
    if body.release_id is not None and not is_release_id(body.release_id):
        raise HTTPException(status_code=400, detail=f"Not a release id: {body.release_id!r}")
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Build {validated} to ship",
        description=f"Building a release of {validated} to run on another server",
        func=bundle_release_job,
        kwargs={
            "domain": validated,
            "commit": body.commit,
            "release_id": body.release_id,
            "keep": body.keep,
        },
        metadata={"domain": validated, "bundle": True},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Build of {validated} queued; its bundle will wait to be fetched",
        job=job.to_dict(),
    )


@router.get("/{domain}/releases/bundles", response_model=BundleListOut)
def list_bundles(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> BundleListOut:
    """
    List the bundles waiting to be fetched for an application.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        Its bundles, newest first.
    """
    validated = _known(domain)
    return BundleListOut(bundles=[_bundle(item) for item in _spool().list(validated)])


@router.get("/{domain}/releases/bundles/{bundle_id}", response_model=BundleOut)
def bundle_info(
    domain: str, bundle_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> BundleOut:
    """
    Describe one bundle.

    Args:
        domain: The application.
        bundle_id: The bundle.
        session: The authenticated session.

    Returns:
        It.
    """
    return _bundle(_spool().get(bundle_id, _known(domain)))


class BundleBytes(Response):
    """A range of a bundle: bytes the caller carries on, never JSON."""

    media_type = "application/octet-stream"


@router.get(
    "/{domain}/releases/bundles/{bundle_id}/data",
    response_class=BundleBytes,
    responses={
        200: {
            "description": "The bytes asked for: fewer at the end of the bundle, none past it.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def bundle_data(
    domain: str,
    bundle_id: str,
    offset: Annotated[int, Query(ge=0, description="Where to start in the bundle")],
    session: Annotated[dict, Depends(get_current_session)],
    length: Annotated[
        int, Query(ge=1, le=MAX_READ_BYTES, description="How many bytes at most")
    ] = CHUNK_BYTES,
) -> BundleBytes:
    """
    Read a range of a bundle.

    Args:
        domain: The application.
        bundle_id: The bundle.
        offset: Where to start.
        session: The authenticated session.
        length: How many bytes at most.

    Returns:
        The raw bytes; fewer at the end of the file, none past it.
    """
    validated = _known(domain)
    data = await run_in_threadpool(_spool().read, bundle_id, validated, offset, length)
    return BundleBytes(content=data)


@router.delete("/{domain}/releases/bundles/{bundle_id}", status_code=204)
def discard_bundle(
    domain: str, bundle_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> None:
    """
    Let go of a bundle: the central has it, or does not want it.

    Args:
        domain: The application.
        bundle_id: The bundle.
        session: The authenticated session.
    """
    _spool().discard(bundle_id, _known(domain))


# ------------------------------------------------------------------------------- receiving


def _offset_refusal(exc: UploadOffsetError, error: str) -> JSONResponse:
    """
    Args:
        exc: What went wrong with the offset.
        error: The error code the client reads.

    Returns:
        The 409 that carries where to resume.
    """
    return JSONResponse(
        status_code=409,
        content={
            "detail": exc.message,
            "hint": exc.details or None,
            "error": error,
            "received": exc.received,
        },
    )


@router.post("/{domain}/releases/uploads", response_model=BundleUploadOut, status_code=201)
def start_bundle_upload(
    domain: str,
    body: BundleUploadStart,
    session: Annotated[dict, Depends(get_current_session)],
) -> BundleUploadOut:
    """
    Start the arrival of a bundle. Nothing is sent yet.

    Args:
        domain: The application that will run it.
        body: The bundle's size and digest.
        session: The authenticated session, which owns the upload.

    Returns:
        The upload, with the offset to start at and the most a chunk may carry.

    Raises:
        ValidationError: 400 for a size past the limit or the disk, or a digest that is not one.
    """
    _known(domain)
    upload = _uploads().create(
        body.file_name, body.size, owner=actor_label(session), sha256=body.sha256
    )
    return _upload(upload)


@router.get("/{domain}/releases/uploads/{upload_id}", response_model=BundleUploadOut)
def bundle_upload_status(
    domain: str,
    upload_id: str,
    session: Annotated[dict, Depends(get_current_session)],
) -> BundleUploadOut:
    """
    Say how much of a bundle the server holds, which is where to resume.

    Args:
        domain: The application.
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The upload.

    Raises:
        ValidationError: 400 when there is no such upload, or it is another's.
    """
    _known(domain)
    return _upload(_uploads().get(upload_id, owner=actor_label(session)))


@router.put("/{domain}/releases/uploads/{upload_id}", response_model=BundleUploadOut)
async def send_bundle_chunk(
    domain: str,
    upload_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0, description="Where this chunk starts in the file")],
    session: Annotated[dict, Depends(get_current_session)],
) -> BundleUploadOut | JSONResponse:
    """
    Add the next chunk of a bundle.

    A chunk at an offset other than the one the server holds is refused with 409 and the offset
    to resume from.

    Args:
        domain: The application.
        upload_id: The upload.
        request: The request, whose body is the chunk.
        offset: Where the chunk starts.
        session: The authenticated session; only the one that started it.

    Returns:
        The upload, with the new offset.

    Raises:
        ValidationError: 400 for an unknown or finished upload, or a chunk past the size.
    """
    _known(domain)
    data = await request.body()
    if len(data) > CHUNK_BYTES:
        raise ValidationError(
            f"A chunk may be at most {CHUNK_BYTES} bytes here",
            details="Split the file in smaller pieces.",
        )
    try:
        upload = await run_in_threadpool(
            _uploads().append, upload_id, offset, data, owner=actor_label(session)
        )
    except UploadOffsetError as exc:
        return _offset_refusal(exc, "upload_offset")
    return _upload(upload)


@router.post("/{domain}/releases/uploads/{upload_id}/complete", response_model=BundleUploadOut)
def complete_bundle_upload(
    domain: str,
    upload_id: str,
    session: Annotated[dict, Depends(get_current_session)],
) -> BundleUploadOut | JSONResponse:
    """
    Close an upload: every byte is in and the digest matches.

    Args:
        domain: The application.
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The finished upload.

    Raises:
        ValidationError: 400 when the digest does not match (the data is discarded) or the upload
            is unknown.
    """
    _known(domain)
    try:
        upload = _uploads().complete(upload_id, owner=actor_label(session))
    except UploadOffsetError as exc:
        return _offset_refusal(exc, "upload_incomplete")
    return _upload(upload)


@router.delete("/{domain}/releases/uploads/{upload_id}", status_code=204)
def discard_bundle_upload(
    domain: str,
    upload_id: str,
    session: Annotated[dict, Depends(get_current_session)],
) -> None:
    """
    Throw an upload away.

    Args:
        domain: The application.
        upload_id: The upload.
        session: The authenticated session; only the one that started it.
    """
    _known(domain)
    store = _uploads()
    store.get(upload_id, owner=actor_label(session))
    store.discard(upload_id)


@router.post("/{domain}/releases/receive", response_model=JobAcceptedResponse, status_code=202)
def receive_bundle(
    domain: str,
    body: ReceiveRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue activating a release that another server built and that has arrived.

    The file is checked against the digest it was declared with, extracted with every check an
    archive gets, given this application's directory, and activated after the application's own
    hooks and behind the health gate: a release that does not answer is put back and removed.
    Held by a freeze of deploys like an update.

    Args:
        domain: The application.
        body: The upload and what the operator confirms.
        session: The authenticated session; only the one that started the upload.

    Returns:
        The queued job.

    Raises:
        DeploysFrozenError: 409 while a freeze holds it and ``frozen_ok`` was not given.
        ValidationError: 400 when the upload is unknown, another's or not finished.
    """
    validated = _known(domain)
    upload = _uploads().get(body.upload_id, owner=actor_label(session))
    if not upload.complete:
        raise ValidationError(
            "The bundle has not finished arriving",
            details=f"{upload.received} of {upload.size} bytes are in. Send the rest, then "
            "complete the upload.",
            field="upload_id",
        )
    deploy_guard.require_open(validated, override=body.frozen_ok, record=False)
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Receive a release of {validated}",
        description=f"Activating a release of {validated} that was built on another server",
        func=receive_bundle_job,
        kwargs={
            "domain": validated,
            "upload_id": body.upload_id,
            "schema_changed_ok": body.schema_changed_ok,
            "frozen_ok": body.frozen_ok,
        },
        metadata={"domain": validated, "bundle": True},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Receiving a release of {validated}",
        job=job.to_dict(),
    )
