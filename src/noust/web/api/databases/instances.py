# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances``: engines Noust runs itself, in containers, beside the host's.

The host's PostgreSQL, MySQL, Redis and MongoDB stay the server's primary
engines. An instance is another one: another version of the same engine, or an
engine the host does not provide, run from the engine's official image pinned by
digest, published on 127.0.0.1 only and held to a memory limit
(:class:`~noust.managers.database.managed.ManagedInstances`, the one
implementation, which ``noust db instance`` calls too).

Once an instance exists it is an engine like the others: it is listed by ``GET
/api/databases/engines`` under its key (``postgresql@pg16``), and every route
that takes an ``{engine}`` takes that key. These routes are only the instance's
own life: making it, starting it, stopping it, moving it to a newer image of its
version and removing it. Making, upgrading and removing ask for sudo mode and
run as jobs, because they pull images and wait for engines; removing needs the
operator's ``confirm``, since its data goes with it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import ConfirmationRequired
from noust.managers.database.instance_catalog import offered
from noust.managers.database.managed import ManagedInstances
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import ActionResponse, queue, service
from noust.web.api.databases.jobs import instance_job
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType

router = APIRouter(route_class=NoustErrorRoute)


class InitScript(BaseModel):
    """
    A SQL script the engine runs the first time it starts.

    Attributes:
        name: A file name ending in ``.sql``; scripts run in name order.
        content: The SQL, up to 1 MiB. The file is readable by the container, so it must not
            hold a secret.
    """

    name: str = Field(..., max_length=64)
    content: str = Field(..., min_length=1, max_length=1024 * 1024)


class InstanceRequest(BaseModel):
    """
    An instance to make.

    Attributes:
        name: What to call it: lower-case letters, digits and single dashes,
            starting with a letter, up to 24 characters. It is the part of its
            key after the ``@`` (``postgresql@pg16``).
        engine: What to run: ``postgresql``, ``mysql``, ``mariadb``, ``redis``,
            ``valkey`` or ``mongodb`` (``GET /api/databases/instances/catalog``).
        version: Its version, such as ``16`` or ``8.4``; the catalog's default
            when omitted. The image is pinned by tag and digest.
        memory_mb: Its memory limit in megabytes: mandatory, enforced on the
            container and used to size the engine's own caches.
        port: The port on 127.0.0.1 it is published on; a free one just above
            the engine's own when omitted.
        variant: An image of the engine with something added, such as ``pgvector`` or
            ``postgis`` for PostgreSQL (``GET /api/databases/instances/catalog`` lists
            them); the engine's own image when omitted.
        init_scripts: SQL scripts run, in name order, the first time the engine starts
            (PostgreSQL, MySQL and MariaDB; at most ten).
        image: An image of your own instead of the catalog's, ``repository[:tag]@sha256:digest``.
            The digest is mandatory (a tag can be moved after you looked) and ``version`` says
            which version it is. Noust has not tested it; not with ``variant``.
        command: The container's whole command, instead of the engine's: a list of words, never
            a shell line. Noust adds nothing to it. Not with ``args``.
        args: Arguments added after the ones Noust passes the engine, a list of words. A
            password is never one: a command line is visible to every local user.
        accept_untested: The operator accepts that Noust has not tested an image, a command or
            arguments they chose. Without it, queueing one is a 400 ``confirmation_required``
            carrying what is accepted, and nothing is queued.
    """

    name: str = Field(..., max_length=24)
    engine: str = Field(..., max_length=32)
    version: str | None = Field(default=None, max_length=16)
    memory_mb: int | None = Field(default=None, ge=1, le=4194304)
    port: int | None = Field(default=None, ge=1, le=65535)
    variant: str | None = Field(default=None, max_length=24)
    init_scripts: list[InitScript] = Field(default_factory=list, max_length=10)
    image: str | None = Field(default=None, max_length=255)
    command: list[str] | None = Field(default=None, max_length=64)
    args: list[str] | None = Field(default=None, max_length=64)
    accept_untested: bool = False

    def scripts(self) -> list[tuple[str, bytes]]:
        """
        Returns:
            The scripts as the manager takes them: a name and the content's bytes.
        """
        return [(item.name, item.content.encode("utf-8")) for item in self.init_scripts]


class UpgradeRequest(BaseModel):
    """
    What an upgrade is asked to move an instance to.

    Attributes:
        image: For an instance on an image of the operator's own: the next one,
            ``repository[:tag]@sha256:digest``. Noust never looks for a newer image of one the
            operator pinned. Refused for an instance on the catalog's.
        accept_untested: The operator accepts that Noust has not tested that image.
    """

    image: str | None = Field(default=None, max_length=255)
    accept_untested: bool = False


class InstanceResponse(BaseModel):
    """
    An instance Noust runs.

    Attributes:
        name: Its name.
        key: What it is addressed by wherever an engine is named.
        engine: The manager that speaks it.
        flavour: What it runs.
        display_name: The flavour for a person.
        version: The version it was made with.
        image: The image's repository.
        digest: The digest it runs; ``previous_digest`` is what ran before the
            last upgrade.
        reference: The image reference its container is started from.
        host_port: The port on 127.0.0.1.
        address: Always ``127.0.0.1``: it is reached through an SSH tunnel, or
            made public one database at a time (``/api/databases/public``).
        memory_limit_mb: Its memory limit.
        state: ``creating``, ``ready``, ``upgrading`` or ``removing``: how far
            the last change got.
        running: Its container runs.
        status: ``running``, ``stopped`` (it has no container) or Docker's own
            word.
        unit: The systemd unit that runs it.
        container: Its container's name.
        volume: The Docker volume that holds its data.
        directory: The directory that holds its Compose file and its
            environment file, which only root can read.
        end_of_life: When its version's upstream support ends, where the project
            publishes it; ``support`` is ``supported``, ``ending_soon``,
            ``ended`` or ``unknown``.
        tls_mode: ``off``, ``optional`` or ``required``: whether it serves TLS
            (``GET .../{name}/tls`` says with which certificate).
        variant: The image variant it runs (``pgvector``); null for the engine's own.
        image_tag: The tag its digest was read from when that is not the version.
        init_scripts: The names of the scripts it ran the first time it started.
        custom_image: The image is the operator's own, pinned by their digest.
        command: The whole command the operator made the container run; empty for the engine's.
        args: What the operator added after the arguments Noust passes; empty for none.
        customised: Anything about the container is the operator's.
    """

    name: str
    key: str
    engine: str
    flavour: str
    display_name: str
    version: str
    image: str
    digest: str
    previous_digest: str | None = None
    reference: str
    host_port: int
    address: str
    memory_limit_mb: int
    state: str
    running: bool
    status: str
    unit: str
    container: str
    volume: str
    directory: str
    created_at: str
    created_by: str | None = None
    updated_at: str
    upgraded_at: str | None = None
    end_of_life: str | None = None
    support: str | None = None
    tls_mode: str = "off"
    variant: str | None = None
    image_tag: str | None = None
    init_scripts: list[str] = Field(default_factory=list)
    custom_image: bool = False
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    customised: bool = False


class BudgetResponse(BaseModel):
    """
    How the server's memory is divided among the instances.

    Attributes:
        server_mb: The server's memory; 0 when the system does not say.
        allocatable_mb: The share the limits of all instances may add up to.
        allocated_mb: What they add up to.
        available_mb: What is left to give another.
    """

    server_mb: int
    allocatable_mb: int
    allocated_mb: int
    available_mb: int


class InstanceListResponse(BaseModel):
    """Every instance Noust runs, and the memory they hold."""

    instances: list[InstanceResponse]
    budget: BudgetResponse


class CatalogVersionResponse(BaseModel):
    """One version Noust offers: its tag, the digest pinned and when support ends."""

    version: str
    digest: str
    default: bool
    end_of_life: str | None = None


class CatalogVariantVersionResponse(BaseModel):
    """One version of a variant: the PostgreSQL major, the tag it is served under and its digest."""

    version: str
    tag: str
    digest: str
    default: bool
    end_of_life: str | None = None


class CatalogVariantResponse(BaseModel):
    """
    An image of an engine with something added.

    Attributes:
        name: What to give as ``variant``.
        display_name: The engine with it, for a person.
        description: What it adds.
        image: The project's image repository.
        platforms: The processors the image is published for.
        versions: What is offered, oldest first.
    """

    name: str
    display_name: str
    description: str
    image: str
    platforms: list[str]
    versions: list[CatalogVariantVersionResponse]


class CatalogEntryResponse(BaseModel):
    """
    One engine Noust can run as an instance.

    Attributes:
        flavour: What to give as ``engine``.
        engine: The manager that speaks it.
        display_name: Its name for a person.
        image: The official image's repository.
        container_port: Where it listens inside its container.
        min_memory_mb: The least memory it starts and answers in.
        default_memory_mb: What an interface may suggest.
        versions: What is offered, oldest first.
        variants: Other images of the engine, with an extension or a feature added.
    """

    flavour: str
    engine: str
    display_name: str
    image: str
    container_port: int
    min_memory_mb: int
    default_memory_mb: int
    versions: list[CatalogVersionResponse]
    variants: list[CatalogVariantResponse] = Field(default_factory=list)


class CatalogResponse(BaseModel):
    """What Noust can run as an instance."""

    engines: list[CatalogEntryResponse]


class PlanResponse(BaseModel):
    """
    What making an instance would do. Nothing was changed.

    Attributes:
        warnings: What the operator should know before going ahead.
        custom: The image is the operator's own.
        command: The whole command the operator wrote, or empty.
        args: The arguments the operator adds, or empty.
        requires_confirmation: Making it needs ``accept_untested``: Noust has not tested what
            the operator chose.
    """

    name: str
    key: str
    flavour: str
    engine: str
    display_name: str
    version: str
    image: str
    digest: str
    reference: str
    host_port: int
    memory_limit_mb: int
    warnings: list[str]
    variant: str | None = None
    tag: str | None = None
    init_scripts: list[str] = Field(default_factory=list)
    custom: bool = False
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class RemovePlanResponse(BaseModel):
    """
    What removing an instance would delete, and what stops it.

    Attributes:
        databases: What it holds now.
        blockers: What stops the removal: applications that use it, databases
            made public on it. Nothing is removed while any is listed.
        warnings: What the operator accepts by confirming.
    """

    name: str
    databases: list[str]
    blockers: list[str]
    warnings: list[str]


def _manager(session: dict[str, Any]) -> ManagedInstances:
    """
    Build the manager a request uses.

    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The manager over the request's database service.
    """
    return ManagedInstances(service(session))


def _job(
    session: dict[str, Any], action: str, name: str, message: str, **kwargs: Any
) -> JobAcceptedResponse:
    """
    Hand an instance action to the job manager.

    Args:
        session: The authenticated session.
        action: ``create``, ``start``, ``upgrade`` or ``remove``.
        name: The instance's name.
        message: The accepted response's summary.
        **kwargs: What :func:`~noust.web.api.databases.jobs.instance_job` takes.

    Returns:
        The accepted job.
    """
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"{action.capitalize()} the database instance {name}",
        description=message,
        func=instance_job,
        kwargs={"action": action, "name": name, **kwargs},
        metadata={"instance": name, "action": action},
        message=message,
    )


@router.get("/instances", response_model=InstanceListResponse)
def list_instances(
    session: Annotated[dict, Depends(get_current_session)],
) -> InstanceListResponse:
    """
    List the instances Noust runs, and the memory they hold.

    Args:
        session: The authenticated session.

    Returns:
        Every instance, by name, and the memory budget.
    """
    managed = _manager(session)
    return InstanceListResponse(
        instances=[InstanceResponse(**entry) for entry in managed.all()],
        budget=BudgetResponse(**managed.budget()),
    )


@router.get("/instances/catalog", response_model=CatalogResponse)
def instance_catalog(session: Annotated[dict, Depends(get_current_session)]) -> CatalogResponse:
    """
    List what Noust can run as an instance: each engine, and its versions with their digests.

    Args:
        session: The authenticated session.

    Returns:
        The engines, in the order the console offers them.
    """
    return CatalogResponse(engines=[CatalogEntryResponse(**entry) for entry in offered()])


@router.post("/instances/plan", response_model=PlanResponse)
def plan_instance(
    request: InstanceRequest, session: Annotated[dict, Depends(get_current_session)]
) -> PlanResponse:
    """
    Say what making an instance would do. Changes nothing.

    A request that could not succeed (Docker missing, no memory limit, a port or
    a name that is taken, a version the catalog lacks, more memory than the
    server has left) is refused here, with the reason.

    Args:
        request: What to make.
        session: The authenticated session.

    Returns:
        The plan.
    """
    plan = _manager(session).plan(
        request.name,
        request.engine,
        version=request.version,
        memory=request.memory_mb,
        port=request.port,
        variant=request.variant,
        init_scripts=request.scripts(),
        image=request.image,
        command=request.command,
        args=request.args,
    )
    return PlanResponse(**plan.to_dict())


@router.post("/instances", response_model=JobAcceptedResponse, status_code=202)
def create_instance(
    request: InstanceRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue making an instance, with sudo mode.

    It is planned first, so a request that could not succeed is refused here
    rather than failing in the job. The job pulls the image, writes the
    instance's files and unit, starts it, and returns once its engine accepts
    the superuser Noust generated; whatever fails is undone.

    Args:
        request: What to make.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ConfirmationRequired: An image, a command or arguments of the operator's own, without
            ``accept_untested``.
    """
    plan = _manager(session).plan(
        request.name,
        request.engine,
        version=request.version,
        memory=request.memory_mb,
        port=request.port,
        variant=request.variant,
        init_scripts=request.scripts(),
        image=request.image,
        command=request.command,
        args=request.args,
    )
    plan.require_accepted(request.accept_untested)
    return _job(
        session,
        "create",
        plan.name,
        f"Making {plan.display_name} {plan.version} as {plan.name}",
        engine=plan.flavour,
        version=plan.version,
        memory_mb=plan.memory_limit_mb,
        port=plan.host_port,
        # What was asked, not what the plan decided: an image of the operator's own is planned as
        # variant 'custom', which asking for again alongside the image would be two answers.
        variant=request.variant,
        init_scripts=[
            {"name": item.name, "content": item.content} for item in request.init_scripts
        ],
        image=request.image,
        command=request.command,
        args=request.args,
        accept_untested=request.accept_untested,
    )


@router.get("/instances/{name}", response_model=InstanceResponse)
def get_instance(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> InstanceResponse:
    """
    Describe one instance.

    Args:
        name: Its name.
        session: The authenticated session.

    Returns:
        The instance.
    """
    managed = _manager(session)
    return InstanceResponse(**managed.describe(managed.get(name)))


@router.post("/instances/{name}/start", response_model=JobAcceptedResponse, status_code=202)
def start_instance(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue starting an instance; the job returns once its engine answers.

    Args:
        name: Its name.
        session: The authenticated session.

    Returns:
        The queued job.
    """
    managed = _manager(session)
    managed.get(name)
    return _job(session, "start", name, f"Starting {name}")


@router.post("/instances/{name}/stop", response_model=ActionResponse)
def stop_instance(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Stop an instance. Its data stays; the applications that use it lose their database.

    Args:
        name: Its name.
        session: The authenticated session.

    Returns:
        The action's outcome.
    """
    _manager(session).stop(name)
    return ActionResponse(success=True, message=f"{name} stopped")


@router.post("/instances/{name}/upgrade", response_model=JobAcceptedResponse, status_code=202)
def upgrade_instance(
    name: str,
    session: Annotated[dict, Depends(require_elevated)],
    backup: Annotated[bool, Query(description="Dump every database first")] = True,
    request: UpgradeRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue moving an instance to the newest image of its version, with sudo mode.

    The tag does not change (PostgreSQL 17 stays 17); what moves is the build the
    registry serves for it. A dump of every database is taken first (a failed
    dump stops it), the instance must become healthy and accept its superuser,
    and when it does not the previous image is put back.

    An instance on an image of the operator's own is never moved to what a registry serves:
    the body names the next image (pinned by digest) and accepts again that Noust did not test
    it. The catalog's takes no body.

    Args:
        name: Its name.
        session: The authenticated, elevated session.
        backup: Dump every database first; on by default.
        request: The image to move to, for an instance on one of the operator's own.

    Returns:
        The queued job.

    Raises:
        ValidationError: An image for an instance on the catalog's, none for one on the
            operator's, or one that is not pinned by a digest.
        ConfirmationRequired: The operator's image, without ``accept_untested``.
    """
    asked = request or UpgradeRequest()
    _manager(session).check_upgrade(name, asked.image, asked.accept_untested)
    # The job of an ordinary upgrade is what it always was; the image travels only when named.
    named = {"image": asked.image, "accept_untested": asked.accept_untested} if asked.image else {}
    return _job(session, "upgrade", name, f"Upgrading {name}", backup=backup, **named)


@router.get("/instances/{name}/remove-plan", response_model=RemovePlanResponse)
def plan_remove_instance(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> RemovePlanResponse:
    """
    Say what removing an instance would delete, and what stops it. Changes nothing.

    Args:
        name: Its name.
        session: The authenticated session.

    Returns:
        The plan.
    """
    return RemovePlanResponse(**_manager(session).plan_remove(name))


@router.delete("/instances/{name}", response_model=JobAcceptedResponse, status_code=202)
def remove_instance(
    name: str,
    session: Annotated[dict, Depends(require_elevated)],
    confirm: Annotated[bool, Query(description="Accept that its data is deleted")] = False,
) -> JobAcceptedResponse:
    """
    Queue removing an instance, its container and its data, with sudo mode.

    Without ``confirm`` nothing is changed and the answer is a 400
    ``confirmation_required`` carrying what is deleted. Applications that use it
    and databases made public on it stop the removal, whatever is confirmed.

    Args:
        name: Its name.
        session: The authenticated, elevated session.
        confirm: The operator accepted that its data goes.

    Returns:
        The queued job.

    Raises:
        ConfirmationRequired: ``confirm`` is false.
        ValidationError: Something uses it.
    """
    managed = _manager(session)
    plan = managed.plan_remove(name)
    if plan["blockers"]:
        # The same refusal the job would give, now rather than in a failed job.
        managed.remove(name, confirm=confirm)
    if not confirm:
        raise ConfirmationRequired(plan["warnings"])
    return _job(session, "remove", name, f"Removing {name}", confirm=True)
