# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Changing how hardened an application's unit is, behind the health gate.

A new application is hardened from its first deployment
(:func:`noust.managers.unit_hardening.adopt_new_app`). An existing one, and any
change of profile or of the directories a unit may write, goes through
:func:`set_hardening`: the units are rewritten, the application restarted
behind the same gate as a deploy, and, when it does not answer, the previous
units and the previous record are put back and the error carries the probes and
the journal verbatim (checklist B-58). Nothing is hardened that cannot be put
back.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, NoustStore, get_store
from noust.core.unit_hardening_store import (
    PROFILES,
    HardeningRecord,
    forget_record,
    get_record,
    set_record,
)
from noust.deployers.helpers.layout import app_root
from noust.deployers.lifecycle import health_gate_for, restart_blue_green
from noust.managers import unit_hardening
from noust.managers.service_manager import ServiceManager
from noust.validators.domain import validate_domain


@dataclass(frozen=True)
class HardeningStatus:
    """
    How hardened an application's unit is.

    Attributes:
        domain: The application.
        profile: ``off``, ``default`` or ``strict``; ``off`` for an application
            that predates hardening (``configured`` says which).
        configured: Whether the application has a record at all.
        eligible: Whether its unit can be hardened.
        reason: Why it cannot, when it cannot.
        writable_paths: The extra directories its unit may write to.
        directives: The directives its unit has when it has any.
        exposure: systemd's score of the unit, when it could be asked.
        exposure_label: systemd's word for it.
        unit: The unit it was asked about.
    """

    domain: str
    profile: str
    configured: bool
    eligible: bool
    reason: str | None = None
    writable_paths: tuple[str, ...] = ()
    directives: tuple[str, ...] = ()
    exposure: float | None = None
    exposure_label: str | None = None
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The status as JSON-ready data.
        """
        return {
            "domain": self.domain,
            "profile": self.profile,
            "configured": self.configured,
            "eligible": self.eligible,
            "reason": self.reason,
            "writable_paths": list(self.writable_paths),
            "directives": list(self.directives),
            "exposure": self.exposure,
            "exposure_label": self.exposure_label,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class HardeningChange:
    """
    What :func:`set_hardening` did.

    Attributes:
        domain: The application.
        profile: The profile it has now.
        writable_paths: The extra directories its unit may write to.
        units: The units rewritten.
        restarted: Whether they were restarted to run in the new sandbox. When
            not, the running processes keep the sandbox they started in.
        exposure_before: systemd's score of the unit before, when it could be asked.
        exposure_after: And after.
    """

    domain: str
    profile: str
    writable_paths: tuple[str, ...]
    units: tuple[str, ...]
    restarted: bool
    exposure_before: float | None = None
    exposure_after: float | None = None


def _known(store: NoustStore, domain: str) -> App:
    """
    Return an application's row, refusing an unknown one.

    Args:
        store: The store.
        domain: The application.

    Returns:
        The row.

    Raises:
        NoustError: Nothing is deployed there.
    """
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    return app


def status(
    domain: str,
    *,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    services: ServiceManager | None = None,
    analyze: bool = True,
) -> HardeningStatus:
    """
    Say how hardened an application's unit is. Changes nothing.

    Args:
        domain: The application.
        runner: Asked ``systemd-analyze security``.
        store: The store.
        services: The service manager.
        analyze: Ask systemd for its score; False for a listing that must not
            wait for a process per application.

    Returns:
        Its status.

    Raises:
        NoustError: The application is unknown.
    """
    store = store or get_store()
    app = _known(store, validate_domain(domain))
    record = get_record(app.domain, store=store)
    reason = unit_hardening.ineligible(app)
    if reason is not None:
        return HardeningStatus(
            domain=app.domain, profile="off", configured=False, eligible=False, reason=reason
        )
    profile = record.profile if record is not None else "off"
    directives = unit_hardening.directives_for_app(app, record)
    unit: str | None = None
    exposure = None
    if analyze:
        services = services or ServiceManager()
        units = services.serving_units(app)
        unit = units[0] if units else None
        if unit is not None:
            exposure = unit_hardening.exposure_of(unit, runner or get_runner())
    return HardeningStatus(
        domain=app.domain,
        profile=profile,
        configured=record is not None,
        eligible=True,
        writable_paths=record.writable_paths if record is not None else (),
        directives=tuple(directives),
        exposure=exposure.score if exposure is not None else None,
        exposure_label=exposure.label if exposure is not None else None,
        unit=unit,
    )


def set_hardening(
    domain: str,
    profile: str,
    *,
    writable_paths: Sequence[str] | None = None,
    restart: bool = True,
    actor: str = "system",
    logger: Logger | None = None,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    services: ServiceManager | None = None,
) -> HardeningChange:
    """
    Give an application's unit this much of systemd's sandbox.

    Every unit the application runs as is rewritten and the profile is recorded,
    so a redeploy writes the same into the new unit. Restarted, the application
    must pass the same health gate as a deploy: a sandbox it cannot start in
    would otherwise leave it down, so the previous units and the previous record
    are put back, it is restarted as it was, and this raises with the probes'
    and the journal's own output.

    Args:
        domain: The application's domain.
        profile: ``off``, ``default`` or ``strict``.
        writable_paths: The extra directories the unit may write to; None
            keeps what it has.
        restart: Restart now, so the process runs in the new sandbox.
        actor: Who asked, for the audit trail.
        logger: Where the restart and the probes are reported.
        runner: Asked for systemd's score before and after.
        store: The store.
        services: The service manager.

    Returns:
        What was done.

    Raises:
        NoustError: The application is unknown.
        ValidationError: The profile or a path is not one a unit can have, or
            the application cannot be hardened.
        DeploymentError: Restarted, it did not answer in the new sandbox; the
            old one is back.
        ServiceError: A unit could not be rewritten; the ones already
            rewritten are put back.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Applications")
    log = logger or Logger()
    store = store or get_store()
    domain = validate_domain(domain)
    app = _known(store, domain)
    if profile not in PROFILES:
        raise ValidationError(
            f"{profile!r} is not a hardening profile",
            details=f"Use one of: {', '.join(PROFILES)}.",
            field="profile",
        )
    reason = unit_hardening.ineligible(app)
    if reason is not None:
        raise ValidationError(
            f"{domain} cannot be hardened: {reason}", details="Nothing was changed.", field="domain"
        )
    previous = get_record(domain, store=store)
    declared = unit_hardening.check_writable_paths(
        writable_paths
        if writable_paths is not None
        else (previous.writable_paths if previous else ())
    )
    with app_lock(domain, "hardening change"):
        change = _apply(
            _known(store, domain),
            profile,
            declared,
            previous=previous,
            restart=restart,
            actor=actor,
            log=log,
            runner=runner or get_runner(),
            store=store,
            services=services or ServiceManager(),
        )
    return change


def _apply(
    app: App,
    profile: str,
    declared: tuple[str, ...],
    *,
    previous: HardeningRecord | None,
    restart: bool,
    actor: str,
    log: Logger,
    runner: CommandRunner,
    store: NoustStore,
    services: ServiceManager,
) -> HardeningChange:
    """
    Apply a profile to an application whose lock the caller holds.

    Args:
        app: The application.
        profile: The profile.
        declared: The validated extra directories.
        previous: What the record was.
        restart: Restart behind the gate.
        actor: Who asked.
        log: Where progress is reported.
        runner: Asked for systemd's score.
        store: The store.
        services: The service manager.

    Returns:
        What was done.

    Raises:
        DeploymentError: It did not answer in the new sandbox.
        ServiceError: A unit could not be rewritten.
    """
    domain = app.domain
    units = services.app_units(app)
    if not units:
        raise DeploymentError(
            f"{domain} has no unit to harden",
            details=f"Deploy it first: noust update {domain}",
        )
    directives = unit_hardening.directives_for(
        profile,
        family=unit_hardening.family_of(app.app_type),
        app_root=app_root(app),
        extra_writable=declared,
    )
    before = unit_hardening.exposure_of(units[0], runner)
    written: list[tuple[str, str]] = []

    def put_back() -> None:
        for unit, body in reversed(written):
            services.update_config(unit, body)
        if previous is None:
            forget_record(domain, store=store)
        else:
            set_record(
                domain,
                previous.profile,
                previous.writable_paths,
                changed_by=previous.changed_by,
                store=store,
            )

    try:
        if app.zero_downtime:
            # Both instances run from one template: written once, into it.
            written.append((units[0], services.set_unit_hardening(units[0], directives)))
        else:
            for unit in units:
                written.append((unit, services.set_unit_hardening(unit, directives)))
        set_record(domain, profile, declared, changed_by=actor, store=store)
    except NoustError:
        put_back()
        raise

    if restart:
        restart_behind_the_gate(app, units, store, services, log, put_back)

    after = unit_hardening.exposure_of(units[0], runner) if restart else None
    audit.record(
        "apps.hardening",
        target=f"app:{domain}",
        details={
            "profile": profile,
            "writable_paths": list(declared),
            "units": list(units),
            "restarted": restart,
            "by": actor,
        },
    )
    log.success(f"{domain} runs with the {profile} profile")
    return HardeningChange(
        domain=domain,
        profile=profile,
        writable_paths=declared,
        units=tuple(units),
        restarted=restart,
        exposure_before=before.score if before is not None else None,
        exposure_after=after.score if after is not None else None,
    )


def restart_behind_the_gate(
    app: App,
    units: Sequence[str],
    store: NoustStore,
    services: ServiceManager,
    log: Logger,
    put_back: Callable[[], None],
    *,
    what: str = "sandbox",
) -> None:
    """
    Restart an application with a rewritten unit, and undo it when it does not answer.

    The one restart behind the health gate for every change that rewrites an application's unit
    and keeps the previous one to put back: the sandbox (:func:`set_hardening`) and the tuning
    (:mod:`noust.deployers.unit_tuning`). A blue/green application starts the idle instance on the
    new unit and moves the traffic only once it answers.

    Args:
        app: The application.
        units: Its units.
        store: The store.
        services: The service manager.
        log: Where the probes are reported.
        put_back: Puts the previous units and record back.
        what: What changed, for the message (``sandbox``, ``tuning``).

    Raises:
        DeploymentError: It did not answer; the previous unit is back.
    """
    domain = app.domain
    if app.zero_downtime:
        # The idle instance starts on the new unit and takes the traffic
        # only once it answers: the same switch as an activation.
        try:
            restart_blue_green(app, store, log, put_back)
        except NoustError as exc:
            raise DeploymentError(
                f"{domain} did not answer in the new {what}; the previous one is back",
                details=exc.details or exc.message,
            ) from exc
        return

    def restart_all() -> None:
        for unit in units:
            services.restart(unit)

    gate = health_gate_for(app, store, log, restart=restart_all)
    healthy, evidence = gate.restart_and_probe()
    if healthy:
        return
    put_back()
    restored, _ = gate.restart_and_probe()
    state = "running again" if restored else "back, but it is not answering either"
    raise DeploymentError(
        f"{domain} did not answer in the new {what}; the previous one is {state}",
        details=evidence,
    )
