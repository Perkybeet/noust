# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/software``: what the server is made of, and installing and removing it (item 118).

An endpoint here translates HTTP into a call on
:class:`~noust.managers.server.software.SoftwareManager` and back. The manager is where the guards
are (rule 4), so the endpoints do not decide whether a piece may go: they ask the manager's own
check first, so that a refusal is answered at once (409, with its reason, what depends on the piece
and the sentence for the console to translate) and no job is queued to fail an hour later, and they
queue the work, which the manager then does and records. Installing is a job and so is removing,
because both drive the package manager.

The permission of the route is the floor; the one each component's install has always taken is asked
on top (``routes_server_software.py`` says which and why), and removing takes sudo mode.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from noust.managers.server.software import (
    Impact,
    InstallOptions,
    Inventory,
    SoftwareItem,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import CodedText, JobAcceptedResponse, require_elevated
from noust.web.api.server.common import (
    ServerRoute,
    accepted,
    ensure_no_package_job,
    get_server_context,
    require_job_context,
)
from noust.web.auth import actor_label, get_client_ip
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.permissions.enforce import check_permission
from noust.web.permissions.routes_server_software import (
    DEFAULT_INSTALL_PERMISSION,
    INSTALL_PERMISSIONS,
    REMOVE_PERMISSIONS,
)

router = APIRouter(route_class=ServerRoute)


class DependentOut(BaseModel):
    """
    Something that stops working without a component.

    Attributes:
        kind: ``application``, ``site``, ``database``, ``tunnel``, ``published_port``,
            ``certificate``, ``zone``, ``destination``, ``instance``, ``console`` or
            ``unreadable`` (an engine that runs and cannot be asked what it holds).
        names: Which ones, by the name the operator knows them by.
    """

    kind: str
    names: list[str] = Field(default_factory=list)


class SoftwareItemOut(BaseModel):
    """
    One component, as the page shows it.

    Attributes:
        id: The registry's id.
        purpose: What Noust uses it for, in English.
        purpose_code: The same sentence as a code of the console's catalog.
        severity: ``never``, ``critical_while`` or ``removable``.
        installed: Whether it is on the machine.
        version: The version, when it is installed and the package manager says one.
        origin: ``noust``, ``unknown``, or null when it is not installed. What was installed
            before the ledger existed is ``noust`` only when a file Noust writes is there.
        installable: Whether Noust installs it on this system.
        removable: Whether Noust would remove it now.
        dependents: What depends on it.
        packages: Its packages that are installed.
        installed_at: When Noust installed it, from the ledger.
        installed_by: As whom.
        installed_via: Through which installer.
    """

    id: str
    purpose: str
    purpose_code: str
    severity: str
    installed: bool
    version: str | None = None
    origin: str | None = None
    installable: bool
    removable: bool
    dependents: list[DependentOut] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    installed_at: str | None = None
    installed_by: str | None = None
    installed_via: str | None = None


class SoftwareOut(BaseModel):
    """
    The software of the server.

    Attributes:
        managed: Whether Noust changes this system's packages.
        reason: Why it does not, when it does not.
        reason_coded: ``reason`` as a code and its values, for the console to translate; null
            when Noust does change this system's software.
        family: ``apt``, ``dnf``, ``zypper`` or ``none``.
        items: One per component, in the order the page shows them.
    """

    managed: bool
    reason: str | None = None
    reason_coded: CodedText | None = None
    family: str
    items: list[SoftwareItemOut] = Field(default_factory=list)


class BlockerOut(BaseModel):
    """
    A reason a component is not removed now.

    Attributes:
        reason: ``never``, ``dependents``, ``takes_never``, ``takes_in_use``, ``not_installed``
            or ``cannot_tell``.
        text: The sentence, in English.
        hint: What to do about it, when the sentence has a fix.
        code: The sentence's code in the console's catalog.
        params: The values the sentence is said with.
        dependents: What depends on the component, where that is the reason.
    """

    reason: str
    text: str
    hint: str | None = None
    code: str
    params: dict[str, Any] = Field(default_factory=dict)
    dependents: list[DependentOut] = Field(default_factory=list)


class ImpactOut(BaseModel):
    """
    What removing a component would do, decided before anything is touched.

    Attributes:
        id: The component.
        installed: Whether it is on the machine.
        origin: ``noust``, ``unknown`` or null.
        severity: ``never``, ``critical_while`` or ``removable``.
        dependents: What depends on it.
        removes: Its own packages that are installed.
        also_removes: What else the package manager says would go with them.
        repository_files: The repository source and key files Noust would take back out.
        blockers: Why it cannot be removed now; none means it can.
        removable: Whether nothing stands in the way.
        needs_confirmation: Its origin is unknown, so removing it needs the operator's yes.
    """

    id: str
    installed: bool
    origin: str | None = None
    severity: str
    dependents: list[DependentOut] = Field(default_factory=list)
    removes: list[str] = Field(default_factory=list)
    also_removes: list[str] = Field(default_factory=list)
    repository_files: list[str] = Field(default_factory=list)
    blockers: list[BlockerOut] = Field(default_factory=list)
    removable: bool
    needs_confirmation: bool


class InstallRequest(BaseModel):
    """
    What an install may be told beyond which component.

    Attributes:
        epel: Agree to enable EPEL, where Knot and fail2ban come from it on RHEL and its rebuilds.
    """

    epel: bool = False


class RemoveRequest(BaseModel):
    """
    What a removal needs besides which component.

    Attributes:
        confirm_unknown_origin: The operator knows Noust did not install it and other things on
            the system may use it. Without it, a component of unknown origin is a question
            (409 ``confirmation_required``).
    """

    confirm_unknown_origin: bool = False


def _item(item: SoftwareItem) -> SoftwareItemOut:
    """
    Args:
        item: A component as the manager describes it.

    Returns:
        Its API shape.
    """
    data = item.to_dict()
    return SoftwareItemOut(
        **{**data, "dependents": [DependentOut(**d) for d in data["dependents"]]}
    )


def _inventory(inventory: Inventory) -> SoftwareOut:
    """
    Args:
        inventory: The software as the manager describes it.

    Returns:
        Its API shape.
    """
    return SoftwareOut(
        managed=inventory.managed,
        reason=inventory.reason,
        reason_coded=(
            CodedText(**inventory.reason_coded.to_dict()) if inventory.reason_coded else None
        ),
        family=inventory.family,
        items=[_item(item) for item in inventory.items],
    )


def _impact(impact: Impact) -> ImpactOut:
    """
    Args:
        impact: What a removal would do, as the manager decides it.

    Returns:
        Its API shape.
    """
    data = impact.to_dict()
    blockers = [
        BlockerOut(**{**b, "dependents": [DependentOut(**d) for d in b["dependents"]]})
        for b in data["blockers"]
    ]
    return ImpactOut(
        **{
            **data,
            "dependents": [DependentOut(**d) for d in data["dependents"]],
            "blockers": blockers,
        }
    )


def _client_ignore(request: Request) -> list[str]:
    """
    The console client's address, for fail2ban never to ban it.

    Args:
        request: The request.

    Returns:
        The address, unless it is loopback (the SSH tunnel) or unreadable.
    """
    address = get_client_ip(request)
    try:
        return [] if ipaddress.ip_address(address).is_loopback else [address]
    except ValueError:
        return []


@router.get("", response_model=SoftwareOut)
def list_software(
    session: Annotated[dict, Depends(get_current_session)], refresh: bool = False
) -> SoftwareOut:
    """
    Every piece of software Noust manages: installed or not, its version, who installed it, who
    depends on it.

    Kept for a minute; ``refresh`` asks the machine again. A system whose packages Noust does not
    change answers with ``managed`` false and why, and the list as it is.

    Args:
        session: The authenticated session.
        refresh: Ask again whatever the age of the last answer.

    Returns:
        The software.
    """
    return _inventory(get_server_context().software.inventory(refresh=refresh))


@router.get("/{component}/impact", response_model=ImpactOut)
def software_impact(
    component: str, session: Annotated[dict, Depends(get_current_session)]
) -> ImpactOut:
    """
    What removing a component would do and whether it may, changing nothing.

    Asks the package manager for a simulation, so it takes about a second.

    Args:
        component: The registry's id.
        session: The authenticated session.

    Returns:
        The packages, what else would go, the repository files, and what stands in the way.

    Raises:
        ResourceNotFoundError: The registry has no such component (404).
    """
    return _impact(get_server_context().software.impact(component))


def software_install_job(
    component: str,
    epel: bool = False,
    ignore: list[str] | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Install a component as a job.

    Args:
        component: The registry's id.
        epel: The operator agreed to enable EPEL.
        ignore: The addresses fail2ban must never ban.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What was done (:meth:`~noust.managers.server.software.InstallResult.to_dict`).

    Raises:
        ServerError: The package manager failed; the error carries its own output.
    """
    context = require_job_context(job_context)
    context.update(f"Installing {component}", 10)
    result = get_server_context().software.install(
        component, InstallOptions(epel=epel, ignore=tuple(ignore or ())), on_output=context.log
    )
    context.update("Done", 100)
    return result.to_dict()


def software_remove_job(
    component: str,
    confirm_unknown_origin: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Remove a component as a job. Never a purge.

    Args:
        component: The registry's id.
        confirm_unknown_origin: The operator confirmed a removal of something Noust did not
            install.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What was done (:meth:`~noust.managers.server.software.RemoveResult.to_dict`).

    Raises:
        SoftwareRefusedError: A guard refused now, the situation having changed since the request.
        ServerError: The package manager failed; the error carries its own output.
    """
    context = require_job_context(job_context)
    context.update(f"Removing {component}", 10)
    result = get_server_context().software.remove(
        component, confirm_unknown_origin=confirm_unknown_origin, on_output=context.log
    )
    context.update("Done", 100)
    return result.to_dict()


@router.post("/{component}/install", status_code=202, response_model=JobAcceptedResponse)
def install_software(
    component: str,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
    body: InstallRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue the installation of a component, with sudo mode.

    What can be refused before a job exists is refused here: an id the registry does not have
    (404), a system Noust installs nothing on (409 ``software_refused``), another package manager
    running (409 ``host_busy``). The job installs it through the installer that already knows it.

    Args:
        component: The registry's id.
        request: The request, whose client fail2ban never bans.
        session: The authenticated, elevated session.
        body: ``epel`` to agree to enable EPEL where the component comes from it.

    Returns:
        The queued job.

    Raises:
        ResourceNotFoundError: The registry has no such component (404).
    """
    software = get_server_context().software
    software.component(component)
    check_permission(session, INSTALL_PERMISSIONS.get(component, DEFAULT_INSTALL_PERMISSION))
    software.check_install(component)
    ensure_no_package_job()
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_ACTION,
        name=f"Install {component}",
        description=f"Installing {component} (server.software_install)",
        func=software_install_job,
        kwargs={
            "component": component,
            "epel": bool(body and body.epel),
            "ignore": _client_ignore(request),
            "actor": actor,
        },
        metadata={"component": component, "action": "install"},
        actor=actor,
    )
    return accepted(job, f"Installation of {component} queued")


@router.post("/{component}/remove", status_code=202, response_model=JobAcceptedResponse)
def remove_software(
    component: str,
    session: Annotated[dict, Depends(require_elevated)],
    body: RemoveRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue the removal of a component, with sudo mode. Never a purge.

    The manager's own check runs first and answers at once: 409 ``software_refused`` with its
    ``reason`` and what depends on the component, or 409 ``confirmation_required`` for one whose
    origin is not Noust's. The repository and the key Noust added go with the package.

    Args:
        component: The registry's id.
        session: The authenticated, elevated session.
        body: ``confirm_unknown_origin`` to go ahead with something Noust did not install.

    Returns:
        The queued job.

    Raises:
        ResourceNotFoundError: The registry has no such component (404).
    """
    software = get_server_context().software
    software.component(component)
    extra = REMOVE_PERMISSIONS.get(component)
    if extra is not None:
        check_permission(session, extra)
    confirmed = bool(body and body.confirm_unknown_origin)
    software.check_remove(component, confirm_unknown_origin=confirmed)
    ensure_no_package_job()
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_ACTION,
        name=f"Remove {component}",
        description=f"Removing {component} (server.software_remove)",
        func=software_remove_job,
        kwargs={"component": component, "confirm_unknown_origin": confirmed, "actor": actor},
        metadata={"component": component, "action": "remove"},
        actor=actor,
    )
    return accepted(job, f"Removal of {component} queued")
