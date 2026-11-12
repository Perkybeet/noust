# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/docker/images``: look for new versions of the images in use, say what updating each would do, and
update.

A client of two managers (rule 3): :class:`~noust.managers.docker_image_versions.ImageVersions` asks the
registries, and :class:`~noust.managers.docker_updates.ImageUpdates` decides what updating an image is,
by whose it is. What an application owns is updated through the application: the job this queues is the
one every door queues for an application's update (:func:`noust.web.api.jobs.queue_update`), with its health
gate and its way back. What nobody of Noust made is refused with the reason as a code (409
``docker_blocked``), and what a database engine owns with its own door (409 ``docker_owned``).

Looking asks the registries with the accounts Noust keeps, so it needs the permission to change Docker and
sudo mode, and it is the operator's act: no page asks them.
"""

from __future__ import annotations

from fastapi import APIRouter

from noust.core import audit
from noust.managers.docker_image_versions import ImageVersions
from noust.managers.docker_updates import ImageUpdates
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute
from noust.web.api.docker import Elevated, Session, _engine, _gate
from noust.web.api.docker_change_models import DockerCheckRequest, DockerUpdateRequest, update_out
from noust.web.api.docker_models import DockerUpdateOut
from noust.web.api.jobs import queue_update
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.enforce import check_permission
from noust.web.permissions.principal import audit_actor

router = APIRouter(route_class=NoustErrorRoute)

__all__ = ["docker_check_job", "router"]


def docker_check_job(pull: bool, job_context: JobContext | None = None) -> dict[str, object]:
    """
    Ask the registries about every image a container runs.

    Args:
        pull: Download what moved to compare.
        job_context: Injected by the job manager.

    Returns:
        What was found and what has no registry to ask.
    """
    if job_context is None:
        raise ValueError("A job runs with its context")
    job_context.update("Asking the registries", 5)

    def told(done: int, total: int, reference: str) -> None:
        job_context.update(f"Looked at {reference}", 5 + int(90 * done / max(total, 1)))
        job_context.log(f"Looked at {reference}")

    found = ImageVersions(_gate()).check(pull=pull, on_progress=told)
    newer = [item for item in found.records if item.state == "newer"]
    audit.record(
        "docker.check",
        target="docker",
        details={
            "stage": "finished",
            "looked": len(found.records),
            "newer": len(newer),
            "unknown": sum(1 for item in found.records if item.state == "unknown"),
            "skipped": len(found.skipped),
        },
    )
    job_context.update("Done", 100)
    return found.to_dict()


@router.post("/images/check", response_model=JobAcceptedResponse, status_code=202)
def check_images(body: DockerCheckRequest, session: Elevated) -> JobAcceptedResponse:
    """
    Ask the registries whether the images in use have a new version, as a job.

    The registry of each image is asked what the tag points at now, which is the question ``docker pull``
    asks, without downloading anything (a Docker that cannot do that says so, and downloads only with
    ``pull``). The accounts Noust keeps for a registry are used for it and for no other. What is found is
    kept, and the pages of the zone say it: nothing else asks a registry. Needs the permission to change
    Docker and sudo mode.

    Args:
        body: Whether to download what moved to compare.
        session: The authenticated session, in sudo mode.

    Returns:
        The queued job, whose result is what was found.

    Raises:
        DockerUnavailableError: 409 when Docker is not there or is below what Noust speaks.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_ACTION,
        name="Look for new versions of the images in use",
        description="Asking the registries what each image in use is now",
        func=docker_check_job,
        kwargs={"pull": body.pull},
        metadata={"action": "docker", "kind": "docker_images_check", "pull": body.pull},
        actor=actor_label(session),
    )
    audit.record(
        "docker.check",
        actor=audit_actor(session),
        target="docker",
        details={"stage": "queued", "job": job.id, "pull": body.pull},
    )
    return JobAcceptedResponse(
        job_id=job.id, status=job.status.value, message="Look queued", job=job.to_dict()
    )


@router.get("/images/{image_id}/update", response_model=DockerUpdateOut)
def get_image_update(image_id: str, session: Session) -> DockerUpdateOut:
    """
    Say what updating an image would do, without doing it.

    Args:
        image_id: The image's id, as the listing gives it.
        session: The authenticated session.

    Returns:
        The plan: through which door, what moves with it, the steps in order, what is missing (a copy that
        was proven to restore, the decision to take a major version) and where it is done when it is not
        here.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerUnavailableError: 409 when Docker is not there or is below what Noust speaks.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    return update_out(ImageUpdates(gate, versions=ImageVersions(gate)).plan_for(image_id))


@router.post("/images/{image_id}/update", response_model=JobAcceptedResponse, status_code=202)
def update_image(
    image_id: str, session: Elevated, body: DockerUpdateRequest | None = None
) -> JobAcceptedResponse:
    """
    Update an image through its application, behind its health gate, with the way back kept.

    What an application owns is updated by its update: every image of it that has a new version moves with
    it, and the plan says which. A major version needs ``accept_major``, and a database's also a copy of
    the application's databases that was proven to restore within the last day (409 ``docker_blocked``,
    ``reason`` ``verified_copy``, with the commands that make one). What nobody of Noust made is not updated
    here (``bring_first``: bring its stack to Noust; ``loose``: a container on its own), and what a database
    engine or Noust owns is updated through its own door (409 ``docker_owned``). Needs the permission to
    change Docker, the one to deploy, and sudo mode.

    Args:
        image_id: The image's id, as the listing gives it.
        session: The authenticated session, in sudo mode.
        body: ``accept_major``.

    Returns:
        The queued update of the application.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerBlockedError: 409 ``docker_blocked`` with the reason.
        DockerOwnedError: 409 when an engine or Noust owns it.
    """
    check_permission(session, Permission.APPS_DEPLOY)
    gate = _gate()
    gate.require_usable(_engine(gate))
    accepted = body.accept_major if body is not None else False
    prepared = ImageUpdates(gate, versions=ImageVersions(gate)).prepare(
        image_id, accept_major=accepted
    )
    job, joined = queue_update(prepared.domain, actor=actor_label(session))
    audit.record(
        "docker.update",
        actor=audit_actor(session),
        target=f"app:{prepared.domain}",
        details={
            "image": prepared.plan.image,
            "images": prepared.plan.images,
            "major": prepared.plan.major,
            "accept_major": accepted,
            "job": job.id,
            "joined": joined,
        },
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Update joined one that was waiting" if joined else "Update queued",
        job=job.to_dict(),
    )
