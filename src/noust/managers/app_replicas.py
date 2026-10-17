# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Running several copies of an application's web process on one server (checklist B-68).

An application is one unit on one port. Scaled out it is that unit and some more: copies
of the same process, from the same release, as the same account and under the same
sandbox, each on a port of its own, with nginx sharing the traffic among all of them
through the same upstream file blue/green writes (``least_conn``, and a copy that fails
twice is left out for a few seconds). One server, as many CPUs as it has: more copies are
more cores for a runtime that uses one, and a restart that never takes the last one down.

- **A copy is a unit**: ``<app>-replica@<port>`` from one template, ``replica@.service``,
  rendered from the unit the application already has (its command, its environment, its
  file of secrets), so a change to that unit reaches the copies at the next restart. The
  command takes the port from ``%i``; ``env`` sets ``PORT`` after the unit's files, so a
  ``PORT`` in the application's ``.env`` cannot put two copies on one port.
- **A copy is judged like the application**: a new one starts, and answers the
  application's own health check on its own port, before nginx is told about it. One that
  does not leaves things as they were.
- **A release reaches the copies like the unit**: they are restarted one after another
  behind the health gate that restarts the unit (:class:`~noust.deployers.helpers.health_gate.HealthGate`),
  and judged with it, so a release the copies cannot run is gone back from like any other.
- **The ports are Noust's to pick and keep**: reserved when the copy is first asked for and
  recorded (:mod:`noust.core.app_replica_store`), so another application is never handed
  one and scaling down and up brings the same ports back.

Scaled out is not zero-downtime mode: that one runs two releases side by side and switches
between them, this one runs one release in several copies. They exclude each other, and
what cannot run twice (static sites, stacks, monorepos, sites with routes of their own,
Apache) is refused with the reason.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import app_replica_store as store_module
from noust.core import audit
from noust.core.app_replica_store import MAX_COPIES, ReplicaRecord
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, AppType, NoustStore, WebServer, get_store
from noust.managers.app_processes import (
    InstanceState,
    ProcessManager,
    base_name,
    check_unit_fits,
    instance_name,
)
from noust.validators.domain import validate_domain
from noust.validators.environment import validate_environment, validate_unit_value
from noust.validators.port import find_available_port

logger = logging.getLogger(__name__)

#: The name a copy's template is made under: ``<app>-replica@.service``. A process of the
#: application cannot have it (see ``app_process_store.RESERVED_NAMES``).
NAME = "replica"

#: How far above the application's own port a copy's is looked for.
PORT_SPAN = 2000

#: Types that cannot run as copies of one process: they deploy in place and write their own
#: units and sites, or have no process of their own.
_NOT_SCALABLE = frozenset(
    {
        AppType.DOCKER_COMPOSE.value,
        AppType.MONOREPO.value,
        AppType.CONTAINER.value,
        AppType.STATIC.value,
    }
)

_PORT_VARIABLE = re.compile(r"\$\{PORT\}")


@dataclass(frozen=True)
class ReplicaCopy:
    """
    One copy of an application's web process.

    Attributes:
        unit: Its unit, without ``.service``; the application's own for the first.
        port: The port it listens on.
        state: systemd's ActiveState, or ``inactive``.
        own: Whether it is the application's own unit, not an extra copy.
        serving: Whether nginx sends it traffic.
    """

    unit: str
    port: int
    state: str
    own: bool = False
    serving: bool = False


@dataclass(frozen=True)
class ReplicaStatus:
    """
    How an application is scaled.

    Attributes:
        domain: The application.
        copies: How many copies run (the unit and the extra ones), as recorded.
        maximum: The most there can be.
        ports: The ports reserved for the extra copies, running or not.
        units: Every copy, the application's own first.
        eligible: Whether it can be scaled out.
        reason: Why not, when it cannot.
    """

    domain: str
    copies: int
    maximum: int
    ports: tuple[int, ...] = ()
    units: tuple[ReplicaCopy, ...] = ()
    eligible: bool = True
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The status as JSON-ready data.
        """
        return {
            "domain": self.domain,
            "copies": self.copies,
            "maximum": self.maximum,
            "ports": list(self.ports),
            "eligible": self.eligible,
            "reason": self.reason,
            "units": [
                {
                    "unit": copy.unit,
                    "port": copy.port,
                    "state": copy.state,
                    "own": copy.own,
                    "serving": copy.serving,
                }
                for copy in self.units
            ],
        }


@dataclass(frozen=True)
class ReplicaChange:
    """
    What scaling did.

    Attributes:
        domain: The application.
        copies: How many copies run now.
        before: How many ran before.
        ports: The ports of the extra copies that run.
        started: The units started.
        stopped: The units stopped.
    """

    domain: str
    copies: int
    before: int
    ports: tuple[int, ...] = ()
    started: tuple[str, ...] = ()
    stopped: tuple[str, ...] = ()


@dataclass
class _Undo:
    """What a failed change puts back, newest first."""

    steps: list[Callable[[], None]] = field(default_factory=list)

    def add(self, step: Callable[[], None]) -> None:
        self.steps.append(step)

    def run(self, log: Logger) -> None:
        for step in reversed(self.steps):
            try:
                step()
            except (NoustError, OSError) as exc:
                log.warning(f"Could not put everything back: {exc}")


def eligibility_problem(app: App, *, store: NoustStore | None = None) -> str | None:
    """
    Say why an application cannot run as several copies, if it cannot.

    Args:
        app: The application.
        store: The store, to read its unit from.

    Returns:
        The reason in one sentence, or None when it can.
    """
    store = store if store is not None else get_store()
    if app.webserver != WebServer.NGINX.value:
        return f"{app.domain} is served by {app.webserver}; copies are shared by an nginx upstream"
    if app.is_static or app.app_type in _NOT_SCALABLE:
        return (
            f"{app.domain} is a {app.app_type} application: there is no single web process "
            "of its own to run more than once"
        )
    if getattr(app, "zero_downtime", False):
        return (
            f"{app.domain} runs in zero-downtime mode, which switches between two instances; "
            f"turn it off first: noust app zero-downtime {app.domain} off"
        )
    if not app.port:
        return f"{app.domain} has no port recorded; redeploy it with an explicit port"
    if app.id is None or store.get_service_by_app_id(app.id) is None:
        return f"{app.domain} has no unit on record to copy; redeploy it so one is recorded"
    return None


def copy_command(command: str, *, port: int, app_type: str) -> str:
    """
    Turn the application's ExecStart into the one every copy runs.

    The port the command was written for becomes the instance (``%i``): the flags that name
    it (``-b 0.0.0.0:3000``, ``--port 3000``) are rewritten, and a command that reads
    ``PORT`` is given the copy's by ``env`` in the template.

    Args:
        command: The ExecStart of the application's own unit.
        port: The port the command was written for.
        app_type: The application's type.

    Returns:
        The command for the template.
    """
    from noust.deployers.bluegreen import port_placeholder

    rewritten = port_placeholder(command, port=port, app_type=app_type)
    return _PORT_VARIABLE.sub("%i", rewritten)


class ReplicaManager:
    """
    Scales an application's web process across copies on this server.

    Every method that changes anything takes the application's lock, so a deployment, an
    update and a scale never rewrite the same units at once.
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        services: Any | None = None,
        web: Any | None = None,
        runner: Any | None = None,
        log: Logger | None = None,
        probe: Callable[..., bool] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """
        Initialize the manager.

        Args:
            store: The store; the process-wide one by default.
            services: The service manager; built on first use by default.
            web: The web server manager that owns the upstream; built on first use.
            runner: Asked ``systemctl show``; the process-wide one by default.
            log: Where progress is reported.
            probe: The HTTP probe of a copy; the deploy's own by default.
            sleep: How to pause while a copy drains.
        """
        self._store = store
        self._services = services
        self._web = web
        self._runner = runner
        self.log = log or Logger()
        self._probe = probe
        self._sleep = sleep or time.sleep
        self._processes: ProcessManager | None = None

    # -- Collaborators ---------------------------------------------------------------

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def processes(self) -> ProcessManager:
        """The manager whose unit helpers the copies share."""
        if self._processes is None:
            self._processes = ProcessManager(
                store=self._store,
                services=self._services,
                runner=self._runner,
                log=self.log,
                sleep=self._sleep,
            )
        return self._processes

    @property
    def services(self) -> Any:
        """The service manager."""
        return self.processes.services

    @property
    def web(self) -> Any:
        """The web server manager."""
        if self._web is None:
            from noust.managers.nginx_manager import NginxManager

            self._web = NginxManager()
        return self._web

    def _app(self, domain: str) -> App:
        """
        Return an application's row.

        Args:
            domain: The application.

        Returns:
            The row.

        Raises:
            NoustError: Nothing is deployed there.
        """
        app = self.store.get_app(validate_domain(domain))
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        return app

    # -- Reading ------------------------------------------------------------------------

    def units_for(self, domain: str) -> list[str]:
        """
        Name the extra copies that should run.

        What the health gate restarts together with the application's unit.

        Args:
            domain: The application.

        Returns:
            Their units, without ``.service``; empty for an application that runs alone.
        """
        app = self.store.get_app(domain)
        if app is None:
            return []
        record = store_module.get_record(domain, store=self.store)
        if record is None:
            return []
        return [instance_name(app, NAME, port) for port in record.active_ports]

    def status(self, domain: str) -> ReplicaStatus:
        """
        Say how an application is scaled and how each copy is.

        Args:
            domain: The application.

        Returns:
            Its copies, the first being its own unit.

        Raises:
            NoustError: Nothing is deployed there.
        """
        from noust.deployers.helpers.layout import app_root

        app = self._app(domain)
        problem = eligibility_problem(app, store=self.store)
        record = store_module.get_record(app.domain, store=self.store)
        units: list[ReplicaCopy] = []
        if app.port:
            own = app_root(app).name
            own_state = self.processes.unit_state(own)
            units.append(
                ReplicaCopy(unit=own, port=app.port, state=own_state.state, own=True, serving=True)
            )
            if record is not None:
                for port in record.ports:
                    unit = instance_name(app, NAME, port)
                    running = port in record.active_ports
                    units.append(
                        ReplicaCopy(
                            unit=unit,
                            port=port,
                            state=self.processes.unit_state(unit).state,
                            serving=running,
                        )
                    )
        return ReplicaStatus(
            domain=app.domain,
            copies=record.copies if record is not None else 1,
            maximum=MAX_COPIES,
            ports=record.ports if record is not None else (),
            units=tuple(units),
            eligible=problem is None,
            reason=problem,
        )

    def check_eligible(self, app: App) -> None:
        """
        Refuse an application that cannot run as several copies.

        Args:
            app: The application.

        Raises:
            ValidationError: With what to do instead.
        """
        problem = eligibility_problem(app, store=self.store)
        if problem is not None:
            raise ValidationError(problem, field="copies")
        check_unit_fits(app, NAME)

    # -- Changing -----------------------------------------------------------------------

    def set_copies(self, domain: str, copies: int, *, actor: str = "system") -> ReplicaChange:
        """
        Run an application as this many copies of its web process.

        More copies are started and judged one by one before nginx is told about
        them; fewer are taken out of the upstream first and stopped after the traffic
        drained. Any failure puts back what ran before.

        Args:
            domain: The application.
            copies: The copies in all, the application's own unit included, from 1.
            actor: Who asked, for the audit trail.

        Returns:
            What was done.

        Raises:
            ValidationError: The count is out of range or the application cannot be scaled.
            DeploymentError: A new copy did not pass the health check; things are as they were.
            ServiceError: A unit could not be written, started or stopped.
            AppBusyError: Another operation is running on the application.
        """
        require_server_role("Applications")
        app = self._app(domain)
        store_module.check_copies(copies)
        with app_lock(app.domain, "scale"):
            before = store_module.get_record(app.domain, store=self.store)
            already = before.copies if before is not None else 1
            wanted = copies - 1
            if wanted == 0 and (before is None or before.running == 0):
                return ReplicaChange(domain=app.domain, copies=1, before=1)
            if wanted > 0:
                self.check_eligible(app)
            change = self._scale(app, before, wanted, actor)
        audit.record(
            "apps.replicas",
            target=f"app:{app.domain}",
            details={"action": "scale", "copies": copies, "before": already, "by": actor},
        )
        return change

    def _scale(
        self, app: App, before: ReplicaRecord | None, wanted: int, actor: str
    ) -> ReplicaChange:
        """
        Make the units, the record and the upstream say ``wanted`` extra copies.

        The application's lock is held by the caller.

        Args:
            app: The application.
            before: What was recorded.
            wanted: The extra copies that should run.
            actor: Who asked.

        Returns:
            What was done.

        Raises:
            DeploymentError: A new copy did not answer, or nginx refused the change.
        """
        previous_running = before.running if before is not None else 0
        ports = list(before.ports) if before is not None else []
        while len(ports) < wanted:
            ports.append(self._new_port(app, ports))
        active = tuple(ports[:wanted])
        running = before.active_ports if before is not None else ()
        added = [port for port in active if port not in running]
        leaving = ports[wanted:previous_running]
        undo = _Undo()
        started: list[str] = []
        try:
            if wanted > 0:
                previous_template = self._write_template(app)
                undo.add(lambda: self._restore_template(app, previous_template))
                for port in added:
                    unit = instance_name(app, NAME, port)
                    undo.add(lambda unit=unit: self._stop(unit))  # type: ignore[misc]
                    self._start_and_judge(app, port)
                    started.append(unit)
            self._write_routing(app, before, ports, active, undo)
        except NoustError:
            undo.run(self.log)
            raise

        # The copies leaving are out of the upstream already: what they have in flight
        # finishes before they stop.
        stopped = [instance_name(app, NAME, port) for port in leaving]
        if stopped:
            from noust.deployers.bluegreen import drain_of

            drain = drain_of(app)
            if drain:
                self.log.substep(f"The copies leaving finish what they have for {drain}s")
                self._sleep(drain)
            for unit in stopped:
                self.services.delete_service(unit, keep_record=True)
        if wanted == 0:
            self._forget_template(app)
        return ReplicaChange(
            domain=app.domain,
            copies=1 + wanted,
            before=1 + previous_running,
            ports=active,
            started=tuple(started),
            stopped=tuple(stopped),
        )

    def _write_routing(
        self,
        app: App,
        before: ReplicaRecord | None,
        ports: list[int],
        active: tuple[int, ...],
        undo: _Undo,
    ) -> None:
        """
        Record the copies and point nginx at them, or back at the unit alone.

        Args:
            app: The application.
            before: What was recorded.
            ports: Every port reserved.
            active: The ports of the extra copies that run.
            undo: Where to register how to put it back.

        Raises:
            DeploymentError: nginx refused the change; it is back as it was.
            ValidationError: The same, for the site's own configuration.
        """
        from noust.deployers.domains import refresh_site

        domain = app.domain

        def record_back() -> None:
            if before is None:
                store_module.delete_record(domain, store=self.store)
            else:
                store_module.set_record(
                    domain,
                    before.ports,
                    before.running,
                    changed_by=before.changed_by,
                    store=self.store,
                )
            refresh_site(app)

        # Registered first, so it runs last: the old upstream file is back by the time
        # the site is rendered and nginx reloaded against the old record.
        undo.add(record_back)
        if active:
            previous_upstream = self.web.write_upstream(domain, [app.port, *active])
            undo.add(lambda: self.web.restore_upstream(domain, previous_upstream))
        store_module.set_record(
            domain, tuple(ports), len(active), changed_by="scale", store=self.store
        )
        self.log.substep(
            "Switching the site to the copies' upstream"
            if active
            else "Switching the site back to the unit"
        )
        refresh_site(app)
        if not active:
            self.web.remove_upstream(domain)

    def _new_port(self, app: App, taken: Sequence[int]) -> int:
        """
        Pick a port for a copy: free now, and nobody's.

        Args:
            app: The application.
            taken: The ports it already reserved.

        Returns:
            A port above the application's own.

        Raises:
            ValidationError: There is none free near it.
        """
        base = int(app.port or 0)
        owned = self.store.ports_owned_by_apps() | set(taken) | {base}
        start = max([base, *taken]) + 1
        port = find_available_port(start=start, end=min(65536, start + PORT_SPAN), exclude=owned)
        if port is None:
            raise ValidationError(
                f"No free port was found above {base} for another copy of {app.domain}",
                details="Free some ports, or move the application to a lower one.",
                field="copies",
            )
        return port

    def _context(self, app: App) -> dict[str, Any]:
        """
        Gather what the template needs, from the application's own unit.

        Args:
            app: The application.

        Returns:
            The template's variables, validated.

        Raises:
            DeploymentError: The application has no unit on record.
        """
        from noust.core import unit_tuning_store
        from noust.deployers.helpers.layout import app_root, env_file_for
        from noust.deployers.unit_environment import inline_variables
        from noust.managers import unit_hardening, unit_tuning
        from noust.managers.app_identity import service_account
        from noust.managers.service_manager import ResourceLimits

        service = self.store.get_service_by_app_id(app.id) if app.id is not None else None
        if service is None:
            raise DeploymentError(
                f"{app.domain} has no unit on record to copy",
                details=f"Redeploy it so its unit is recorded: noust update {app.domain}",
            )
        user, group = service_account(app)
        text = self.services.get_service_config(app_root(app).name) or ""
        inline = {key: value for key, value in inline_variables(text).items() if key != "PORT"}
        return {
            "base": base_name(app, NAME),
            "application": app.domain,
            "description": validate_unit_value(
                f"Noust: {app.domain} ({app.app_type}), a copy", field="Description"
            ),
            "user": validate_unit_value(user, field="User"),
            "group": validate_unit_value(group or user, field="Group"),
            "working_directory": validate_unit_value(
                str(self.processes._runtime_directory(app)), field="WorkingDirectory"
            ),
            "environment": validate_environment(inline),
            "environment_file": str(env_file_for(app)),
            "command": validate_unit_value(
                copy_command(service.command, port=int(app.port or 0), app_type=app.app_type),
                field="ExecStart",
            ),
            "resource_limits": ResourceLimits.of(app).validated().directives(),
            "hardening": unit_hardening.directives_for_app(
                app, unit_hardening.get_record(app.domain, store=self.store)
            ),
            "tuning": unit_tuning.directives_for_app(
                app, unit_tuning_store.get_record(app.domain, store=self.store)
            ),
        }

    def _template_path(self, app: App) -> Path:
        """
        Name where the copies' template lives.

        Args:
            app: The application.

        Returns:
            The unit file, which may not exist.
        """
        return Path(self.services.SYSTEMD_DIR) / f"{base_name(app, NAME)}@.service"

    def _write_template(self, app: App) -> str | None:
        """
        Write the template every copy runs from.

        Args:
            app: The application.

        Returns:
            The previous body, to put back, or None when there was none.
        """
        path = self._template_path(app)
        previous = path.read_text() if path.is_file() and not path.is_symlink() else None
        self.services.install_unit(f"{base_name(app, NAME)}@", "replica@", self._context(app))
        return previous

    def _restore_template(self, app: App, previous: str | None) -> None:
        """
        Put the template back as it was.

        Args:
            app: The application.
            previous: What :meth:`_write_template` returned.
        """
        if previous is None:
            self._forget_template(app)
        else:
            self.services.restore_template(base_name(app, NAME), previous)

    def _forget_template(self, app: App) -> None:
        """
        Remove the template once no copy needs it.

        Args:
            app: The application.
        """
        try:
            self.services.remove_template(base_name(app, NAME))
        except NoustError as exc:
            self.log.warning(f"The template of the copies was left in place: {exc}")

    def _stop(self, unit: str) -> None:
        """
        Stop and forget a copy.

        Args:
            unit: Its unit.
        """
        self.services.delete_service(unit, keep_record=True)

    def _start_and_judge(self, app: App, port: int) -> None:
        """
        Start a copy and ask it the application's own health check.

        Args:
            app: The application.
            port: The copy's port.

        Raises:
            DeploymentError: It did not answer; its probes and journal are in the details.
        """
        from noust.deployers.helpers.health import wait_until_healthy
        from noust.deployers.helpers.health_gate import HealthCheck, HealthGate

        unit = instance_name(app, NAME, port)
        self.log.substep(f"Starting {unit}")
        self.services.enable(unit)
        check = HealthCheck.for_app(app)
        gate = HealthGate(
            unit=unit,
            url=check.url(port),
            check=check,
            services=self.services,
            logger=self.log,
            probe=self._probe or wait_until_healthy,
            restart=lambda: self.services.restart(unit),
        )
        healthy, evidence = gate.restart_and_probe()
        if not healthy:
            raise DeploymentError(
                f"A copy of {app.domain} on port {port} did not answer its health check; "
                "nothing changed",
                details=evidence,
            )

    # -- Together with the unit ------------------------------------------------------------

    def prepare(self, domain: str) -> None:
        """
        Write the template again from the application's unit, when it changed.

        What the health gate does before it restarts the copies, so a start command that
        an update changed reaches them.

        Args:
            domain: The application.
        """
        record = store_module.get_record(domain, store=self.store)
        app = self.store.get_app(domain)
        if app is None or record is None or not record.running:
            return
        try:
            self._write_template(app)
        except NoustError as exc:
            self.log.warning(f"The copies of {domain} keep the command they had: {exc}")

    def unanswered(self, domain: str, check: Any) -> str | None:
        """
        Ask every running copy the health check once, after the gate restarted them.

        Args:
            domain: The application.
            check: The application's :class:`~noust.deployers.helpers.health_gate.HealthCheck`.

        Returns:
            What did not answer, in a sentence, or None when every copy does.
        """
        from noust.deployers.helpers.health import wait_until_healthy

        record = store_module.get_record(domain, store=self.store)
        if (
            record is None
            or not record.running
            or check is None
            or getattr(check, "is_command", False)
        ):
            return None
        probe = self._probe or wait_until_healthy
        silent = [
            port
            for port in record.active_ports
            if not probe(
                check.url(port),
                retries=check.attempts(check.delay),
                delay=check.delay,
                accept=check.accepts,
                within=check.wall_clock,
                **check.probe_options(),
            )
        ]
        if not silent:
            return None
        return "These copies did not answer the health check: " + ", ".join(
            f"port {port}" for port in silent
        )

    def control(self, domain: str, action: str) -> list[str]:
        """
        Start, stop or restart the extra copies of an application.

        Args:
            domain: The application.
            action: ``start``, ``stop`` or ``restart``.

        Returns:
            The copies acted on.

        Raises:
            ServiceError: systemd refused one of them.
        """
        units = self.units_for(domain)
        for unit in units:
            getattr(self.services, action)(unit)
        return units

    def remove_all(self, domain: str) -> list[str]:
        """
        Stop and forget the copies of an application that is being deleted.

        The caller holds the application's lock.

        Args:
            domain: The application.

        Returns:
            The units stopped.
        """
        app = self.store.get_app(domain)
        record = store_module.get_record(domain, store=self.store)
        if app is None or record is None:
            return []
        stopped = []
        for port in record.ports:
            unit = instance_name(app, NAME, port)
            self.services.delete_service(unit, keep_record=True)
            stopped.append(unit)
        self._forget_template(app)
        store_module.delete_record(domain, store=self.store)
        return stopped

    def states(self, domain: str) -> list[InstanceState]:
        """
        Ask systemd how each extra copy is.

        Args:
            domain: The application.

        Returns:
            Their states.
        """
        return [self.processes.unit_state(unit) for unit in self.units_for(domain)]


__all__ = [
    "MAX_COPIES",
    "NAME",
    "ReplicaChange",
    "ReplicaCopy",
    "ReplicaManager",
    "ReplicaStatus",
    "copy_command",
    "eligibility_problem",
]
