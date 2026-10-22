# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tuning an application's unit beyond its hard limits, behind the health gate (checklist B-57, B-60).

What the options are and what they become is :mod:`noust.managers.unit_tuning`. This is the change:
every unit the application runs as is rewritten, the options are recorded so a redeploy writes the
same into the new unit, and the application is restarted behind the gate a deploy passes. One that
does not answer under them (a pinned CPU set it cannot use, a ulimit below what it opens) has its
previous units and its previous record put back and is restarted as it was, and the error carries
the probes and the journal verbatim. Nothing is tuned that cannot be put back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, get_store
from noust.core.unit_tuning_store import TuningRecord, forget_record, get_record, set_record
from noust.deployers.hardening import restart_behind_the_gate
from noust.managers import unit_hardening, unit_tuning
from noust.managers.service_manager import ServiceManager
from noust.validators.domain import validate_domain


@dataclass(frozen=True)
class TuningStatus:
    """
    How an application's unit is tuned.

    Attributes:
        domain: The application.
        configured: Whether anything is set.
        eligible: Whether its unit can be tuned.
        reason: Why it cannot, when it cannot.
        options: What is set, by option name.
        directives: The lines the unit has for it.
        capabilities: Every capability that can be given, with what it is for.
        gpu_presets: Each GPU preset and the device groups it allows.
        cpus: CPUs this machine has, the bound of ``allowed_cpus``.
    """

    domain: str
    configured: bool
    eligible: bool
    reason: str | None = None
    options: Mapping[str, Any] | None = None
    directives: tuple[str, ...] = ()
    capabilities: Mapping[str, str] | None = None
    gpu_presets: Mapping[str, tuple[str, ...]] | None = None
    cpus: int = 1

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The status as JSON-ready data.
        """
        return {
            "domain": self.domain,
            "configured": self.configured,
            "eligible": self.eligible,
            "reason": self.reason,
            "options": dict(self.options or {}),
            "directives": list(self.directives),
            "capabilities": dict(self.capabilities or {}),
            "gpu_presets": {
                name: list(groups) for name, groups in (self.gpu_presets or {}).items()
            },
            "cpus": self.cpus,
        }


@dataclass(frozen=True)
class TuningChange:
    """
    What :func:`set_tuning` did.

    Attributes:
        domain: The application.
        options: The options it has now.
        units: The units rewritten.
        restarted: Whether they were restarted to run under them. When not, the running processes
            keep what they started with.
    """

    domain: str
    options: Mapping[str, Any]
    units: tuple[str, ...]
    restarted: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The change as JSON-ready data.
        """
        return {
            "domain": self.domain,
            "options": dict(self.options),
            "units": list(self.units),
            "restarted": self.restarted,
        }


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


def status(domain: str, *, store: NoustStore | None = None) -> TuningStatus:
    """
    Say how an application's unit is tuned. Changes nothing.

    Args:
        domain: The application.
        store: The store.

    Returns:
        Its status.

    Raises:
        NoustError: The application is unknown.
    """
    import os

    store = store or get_store()
    app = _known(store, validate_domain(domain))
    cpus = os.cpu_count() or 1
    reason = unit_hardening.ineligible(app)
    record = get_record(app.domain, store=store)
    if reason is not None:
        return TuningStatus(
            domain=app.domain, configured=False, eligible=False, reason=reason, cpus=cpus
        )
    tuning = unit_tuning.from_record(record)
    return TuningStatus(
        domain=app.domain,
        configured=record is not None,
        eligible=True,
        options=tuning.to_dict(),
        directives=tuple(unit_tuning.directives(tuning)),
        capabilities=unit_tuning.CAPABILITIES,
        gpu_presets=unit_tuning.GPU_PRESETS,
        cpus=cpus,
    )


def set_tuning(
    domain: str,
    options: Mapping[str, object],
    *,
    restart: bool = True,
    actor: str = "system",
    logger: Logger | None = None,
    store: NoustStore | None = None,
    services: ServiceManager | None = None,
) -> TuningChange:
    """
    Give an application's unit exactly this tuning.

    Every unit the application runs as is rewritten and the options are recorded, so a redeploy
    writes the same into the new unit. Restarted, the application must pass the same health gate as
    a deploy; one that does not has the previous units and record put back, is restarted as it
    was, and this raises with the probes' and the journal's own output.

    Args:
        domain: The application's domain.
        options: The complete set of options; an empty mapping removes the tuning.
        restart: Restart now, so the process runs under the new options.
        actor: Who asked, for the audit trail.
        logger: Where the restart and the probes are reported.
        store: The store.
        services: The service manager.

    Returns:
        What was done.

    Raises:
        NoustError: The application is unknown.
        ValidationError: An option is not one a unit can have, or the application cannot be tuned.
        DeploymentError: Restarted, it did not answer under the new options; the old ones are back.
        ServiceError: A unit could not be rewritten; the ones already rewritten are put back.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Applications")
    log = logger or Logger()
    store = store or get_store()
    domain = validate_domain(domain)
    app = _known(store, domain)
    reason = unit_hardening.ineligible(app)
    if reason is not None:
        raise ValidationError(
            f"{domain} cannot be tuned: {reason}", details="Nothing was changed.", field="domain"
        )
    tuning = unit_tuning.validate(options)
    unit_tuning.check_against_limits(tuning, app)
    with app_lock(domain, "tuning change"):
        return _apply(
            _known(store, domain),
            tuning,
            previous=get_record(domain, store=store),
            restart=restart,
            actor=actor,
            log=log,
            store=store,
            services=services or ServiceManager(),
        )


def _apply(
    app: App,
    tuning: unit_tuning.Tuning,
    *,
    previous: TuningRecord | None,
    restart: bool,
    actor: str,
    log: Logger,
    store: NoustStore,
    services: ServiceManager,
) -> TuningChange:
    """
    Apply a tuning to an application whose lock the caller holds.

    Args:
        app: The application.
        tuning: The validated options.
        previous: What the record was.
        restart: Restart behind the gate.
        actor: Who asked.
        log: Where progress is reported.
        store: The store.
        services: The service manager.

    Returns:
        What was done.

    Raises:
        DeploymentError: It did not answer under the new options.
        ServiceError: A unit could not be rewritten.
    """
    domain = app.domain
    units = services.app_units(app)
    if not units:
        raise DeploymentError(
            f"{domain} has no unit to tune", details=f"Deploy it first: noust update {domain}"
        )
    lines = unit_tuning.directives(tuning)
    written: list[tuple[str, str]] = []

    def put_back() -> None:
        for unit, body in reversed(written):
            services.update_config(unit, body)
        if previous is None:
            forget_record(domain, store=store)
        else:
            set_record(domain, previous.options, changed_by=previous.changed_by, store=store)

    try:
        if app.zero_downtime:
            # Both instances run from one template: written once, into it.
            written.append((units[0], services.set_unit_tuning(units[0], lines)))
        else:
            for unit in units:
                written.append((unit, services.set_unit_tuning(unit, lines)))
        set_record(domain, tuning.to_dict(), changed_by=actor, store=store)
    except NoustError:
        put_back()
        raise

    if restart:
        restart_behind_the_gate(app, units, store, services, log, put_back, what="tuning")

    audit.record(
        "apps.tuning",
        target=f"app:{domain}",
        details={
            "options": tuning.to_dict(),
            "units": list(units),
            "restarted": restart,
            "by": actor,
        },
    )
    log.success(
        f"{domain} runs with its tuning" if lines else f"{domain} runs without extra tuning"
    )
    return TuningChange(
        domain=domain, options=tuning.to_dict(), units=tuple(units), restarted=restart
    )
