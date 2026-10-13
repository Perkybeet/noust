# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The processes an application runs beside its web process (checklist B-67, B-68).

A queue worker, a scheduler, a stream consumer: processes that belong to an
application and must run the code it is serving, not a copy that was current once.
Each is a unit of its own from the same release, with the account, the environment
and the sandbox of the web unit, its own command and limits, and as many copies as
the operator wants on this server. ``noust service create`` makes a generic unit that
nothing ties to a release; this ties them (Forge's daemons, Heroku's Procfile).

**One template per process, one instance per copy.** ``<app>-<process>@.service`` is
rendered from the definition; its instances ``@1`` to ``@N`` are the copies, so scaling
a process is starting and stopping instances, nothing more. They run from
``<app>/workers``, a link that follows the release that serves
(:meth:`~noust.deployers.releases.ReleaseManager.point_workers`), moved by an
activation with ``current`` or, in zero-downtime mode, before the traffic is.

**Atomic with the web process.** Every activation restarts the processes together with
the web unit and judges them with it: the health gate restarts them
(:meth:`~noust.deployers.helpers.health_gate.HealthGate.restart_and_probe`), waits for
each copy to stay up (:func:`settle`), and a copy that does not fails the gate, so the
release is put back for the web process and the processes alike.

**Not in the services table.** A row there carrying the application's id would make the
process the application's unit (:meth:`ServiceManager.app_units`). Their units are
Noust's by the marker they carry, and this module is the only thing that names them.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import app_process_store as store_module
from noust.core import audit
from noust.core.app_process_store import (
    MAX_INSTANCES,
    ProcessRecord,
    check_instances,
    check_name,
)
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ServiceError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.validators.domain import validate_domain
from noust.validators.environment import validate_environment, validate_unit_value
from noust.validators.names import MAX_SERVICE_NAME_LENGTH

logger = logging.getLogger(__name__)

#: Deadline of one ``systemctl show``.
SHOW_TIMEOUT = 30

#: Seconds a restarted copy must stay up before the release is judged by it.
SETTLE_SECONDS = 6

#: Journal lines attached to the evidence of a copy that did not stay up.
JOURNAL_LINES = 40

#: A copy as ``systemctl list-units`` names it: ``<base>@<number>.service``.
_LISTED = re.compile(r"^\s*[●*]?\s*(\S+)@(\d+)\.service\b", re.MULTILINE)


@dataclass(frozen=True)
class InstanceState:
    """
    One copy of a process, as systemd sees it.

    Attributes:
        unit: Its unit, such as ``shop-example-com-worker@1``.
        number: Which copy it is.
        state: systemd's active state (``active``, ``failed``, ``inactive``...).
        sub_state: Its sub state (``running``, ``auto-restart``...).
        restarts: How many times systemd restarted it since it started.
    """

    unit: str
    number: int
    state: str
    sub_state: str = ""
    restarts: int = 0

    @property
    def running(self) -> bool:
        """Whether it is running now."""
        return self.state == "active" and self.sub_state in ("running", "")


@dataclass(frozen=True)
class ProcessStatus:
    """
    A process and its copies.

    Attributes:
        record: What the operator (or the repository) defined.
        base: The template's name without ``@``.
        instances: The copies that should run, as they are.
        installed: Whether the template is on disk.
    """

    record: ProcessRecord
    base: str
    instances: tuple[InstanceState, ...] = ()
    installed: bool = False

    @property
    def running(self) -> int:
        """How many copies run."""
        return sum(1 for instance in self.instances if instance.running)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The process as JSON-ready data.
        """
        record = self.record
        return {
            "name": record.name,
            "command": record.command,
            "instances": record.instances,
            "memory_max_mb": record.memory_max_mb,
            "cpu_quota_percent": record.cpu_quota_percent,
            "tasks_max": record.tasks_max,
            "source": record.source,
            "unit": f"{self.base}@",
            "installed": self.installed,
            "running": self.running,
            "copies": [
                {
                    "unit": copy.unit,
                    "number": copy.number,
                    "state": copy.state,
                    "sub_state": copy.sub_state,
                    "restarts": copy.restarts,
                }
                for copy in self.instances
            ],
        }


@dataclass(frozen=True)
class ProcessChange:
    """
    What changing a process did.

    Attributes:
        domain: The application.
        name: The process.
        instances: How many copies it has now.
        started: The copies started.
        stopped: The copies stopped.
        removed: The process was removed.
    """

    domain: str
    name: str
    instances: int = 0
    started: tuple[str, ...] = ()
    stopped: tuple[str, ...] = ()
    removed: bool = False


@dataclass
class Settled:
    """
    Whether the copies stayed up.

    Attributes:
        ok: Every copy is running.
        failed: The copies that are not, each with how systemd sees it.
    """

    ok: bool = True
    failed: list[str] = field(default_factory=list)


def base_name(app: App, name: str) -> str:
    """
    Name the template a process is made from, without ``@``.

    Args:
        app: The application.
        name: The process.

    Returns:
        ``<app>-<process>``, such as ``shop-example-com-worker``.
    """
    return f"{domain_to_app_name(app.domain)}-{name}"


def instance_name(app: App, name: str, number: int) -> str:
    """
    Name one copy of a process.

    Args:
        app: The application.
        name: The process.
        number: Which copy, from 1.

    Returns:
        ``<app>-<process>@<number>``, without ``.service``.
    """
    return f"{base_name(app, name)}@{number}"


def check_unit_fits(app: App, name: str) -> None:
    """
    Refuse a process whose unit name would be too long.

    Args:
        app: The application.
        name: The process.

    Raises:
        ValidationError: The longest copy's unit name exceeds what Noust writes.
    """
    longest = instance_name(app, name, MAX_INSTANCES)
    if len(longest) > MAX_SERVICE_NAME_LENGTH:
        raise ValidationError(
            f"The unit of {name!r} would be {len(longest)} characters: {longest}",
            details=f"Noust names units up to {MAX_SERVICE_NAME_LENGTH} characters. Give the "
            "process a shorter name.",
            field="name",
        )


def _definition(record: ProcessRecord) -> tuple[object, ...]:
    """
    Name what a copy runs from: everything about a process but how many copies.

    Args:
        record: The process.

    Returns:
        Its command and limits.
    """
    return (record.command, record.memory_max_mb, record.cpu_quota_percent, record.tasks_max)


def validate_record(app: App, record: ProcessRecord) -> ProcessRecord:
    """
    Check a process definition as a whole.

    Args:
        app: The application it belongs to.
        record: The definition.

    Returns:
        The same definition, command stripped.

    Raises:
        ValidationError: The name, the count, the command or a limit is not one a
            unit can have, or the application has no process of its own to
            run it beside (a stack, a container, a site).
    """
    from noust.managers.app_identity import ineligible as identity_ineligible
    from noust.managers.service_manager import ResourceLimits

    check_name(record.name)
    check_instances(record.instances)
    check_unit_fits(app, record.name)
    reason = identity_ineligible(app)
    if reason is not None and (app.app_type or "") != "monorepo":
        raise ValidationError(
            f"{app.domain} cannot have processes of its own: {reason}",
            details="Run the process as a service of the stack, or as a scheduled task "
            "(noust cron create --service).",
            field="domain",
        )
    if (app.app_type or "") == "monorepo":
        raise ValidationError(
            f"{app.domain} is a monorepo: its workspaces are its processes",
            details="Each workspace is a unit of its own already.",
            field="domain",
        )
    command = record.command.strip()
    validate_unit_value(command, field="ExecStart")
    try:
        import shlex

        argv = shlex.split(command)
    except ValueError as exc:
        raise ValidationError(
            f"Cannot read the command: {exc}",
            details="Check the quoting. No shell runs it: pipes and && are text.",
            field="command",
        ) from exc
    if not argv:
        raise ValidationError("A process needs a command", field="command")
    ResourceLimits(
        memory_max_mb=record.memory_max_mb,
        cpu_quota_percent=record.cpu_quota_percent,
        tasks_max=record.tasks_max,
    ).validated()
    return replace(record, command=command)


class ProcessManager:
    """
    Defines, runs and judges the processes of applications.

    Every public method that changes anything takes the application's lock, so a
    deployment, an update and a scale never rewrite the same units at once.
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        services: Any | None = None,
        runner: CommandRunner | None = None,
        log: Logger | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """
        Initialize the manager.

        Args:
            store: The store; the process-wide one by default.
            services: The service manager; built on first use by default.
            runner: Asked ``systemctl show``; the process-wide one by default.
            log: Where progress is reported.
            clock: Monotonic time, for the settle wait.
            sleep: How to pause, for the settle wait.
        """
        self._store = store
        self._services = services
        self._runner = runner
        self.log = log or Logger()
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def services(self) -> Any:
        """The service manager."""
        if self._services is None:
            from noust.managers.service_manager import ServiceManager

            self._services = ServiceManager()
        return self._services

    @property
    def runner(self) -> CommandRunner:
        """The command runner."""
        return self._runner or get_runner()

    # -- Reading -----------------------------------------------------------

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

    def unit_state(self, unit: str) -> InstanceState:
        """
        Ask systemd how one copy is.

        Args:
            unit: The copy's unit, such as ``shop-example-com-worker@1``.

        Returns:
            Its state; ``inactive`` when systemd does not know it.
        """
        number = unit.rsplit("@", 1)[-1]
        result = self.runner.run(
            [
                "systemctl",
                "show",
                f"{unit}.service",
                "--property=ActiveState,SubState,NRestarts",
            ],
            timeout=SHOW_TIMEOUT,
        )
        values: dict[str, str] = {}
        if result.success:
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    values[key] = value.strip()
        try:
            restarts = int(values.get("NRestarts", "0"))
        except ValueError:
            restarts = 0
        return InstanceState(
            unit=unit,
            number=int(number) if number.isdigit() else 0,
            state=values.get("ActiveState", "inactive"),
            sub_state=values.get("SubState", ""),
            restarts=restarts,
        )

    def status(self, domain: str) -> list[ProcessStatus]:
        """
        List an application's processes with their copies.

        Args:
            domain: The application.

        Returns:
            One entry per process.

        Raises:
            NoustError: The application is unknown.
        """
        app = self._app(domain)
        found = []
        for record in store_module.list_processes(app.domain, store=self.store):
            base = base_name(app, record.name)
            copies = tuple(
                self.unit_state(instance_name(app, record.name, number))
                for number in range(1, record.instances + 1)
            )
            found.append(
                ProcessStatus(
                    record=record,
                    base=base,
                    instances=copies,
                    installed=(self.services.SYSTEMD_DIR / f"{base}@.service").is_file(),
                )
            )
        return found

    def units_for(self, domain: str) -> list[str]:
        """
        Name the copies that should run for an application.

        What the health gate restarts together with the web unit.

        Args:
            domain: The application.

        Returns:
            The units of every copy of every process, without ``.service``; empty
            for an application with none or an unknown one.
        """
        app = self.store.get_app(domain)
        if app is None:
            return []
        return [
            instance_name(app, record.name, number)
            for record in store_module.list_processes(domain, store=self.store)
            for number in range(1, record.instances + 1)
        ]

    # -- Changing ----------------------------------------------------------

    def set_process(
        self,
        domain: str,
        record: ProcessRecord,
        *,
        actor: str = "system",
        apply: bool = True,
    ) -> ProcessChange:
        """
        Define a process, or change one, and make the units match.

        Args:
            domain: The application.
            record: The definition.
            actor: Who asked, for the audit trail.
            apply: Write the unit and start or stop the copies now. False only
                records the definition (a repository's, synchronised during a
                deployment that restarts them itself).

        Returns:
            What was done.

        Raises:
            ValidationError: The definition is not one a unit can have.
            ServiceError: A unit could not be written.
            AppBusyError: Another operation is running on the application.
        """
        require_server_role("Applications")
        app = self._app(domain)
        record = validate_record(app, record)
        with app_lock(app.domain, "process change"):
            before = store_module.get_process(app.domain, record.name, store=self.store)
            store_module.put_process(app.domain, record, updated_by=actor, store=self.store)
            # A copy that runs what the definition says is left running: scaling
            # up starts the new copies and restarts none of the others.
            same = before is not None and _definition(before) == _definition(record)
            try:
                change = (
                    self._apply(app, record.name, restart_running=not same)
                    if apply
                    else ProcessChange(
                        domain=app.domain, name=record.name, instances=record.instances
                    )
                )
                if apply and change.started:
                    self._require_stay_up(app, record.name, change.started)
            except NoustError:
                # Nothing is left half done: the process is as it was, running
                # what it ran, or not there at all.
                self._restore(app, record.name, before)
                raise
        audit.record(
            "apps.process",
            target=f"app:{app.domain}",
            details={
                "action": "set",
                "process": record.name,
                "instances": record.instances,
                "by": actor,
            },
        )
        return change

    def _require_stay_up(self, app: App, name: str, units: Sequence[str]) -> None:
        """
        Judge the copies a change started, as an activation judges them.

        Args:
            app: The application.
            name: The process.
            units: The copies that were started.

        Raises:
            DeploymentError: A copy did not stay up; the message carries the
                state of each and the end of the journal.
        """
        settled = self.settle(units)
        if not settled.ok:
            raise DeploymentError(
                f"The {name} process of {app.domain} did not stay up; it is as it was",
                details=self.evidence(settled, units),
            )

    def _restore(self, app: App, name: str, before: ProcessRecord | None) -> None:
        """
        Put a process back as it was after a change that did not hold.

        Args:
            app: The application.
            name: The process.
            before: What it was, or None when it did not exist.
        """
        try:
            if before is None:
                self._retire(app, ProcessRecord(name=name, command="x"))
                store_module.delete_process(app.domain, name, store=self.store)
                self._after_last(app)
            else:
                store_module.put_process(
                    app.domain, before, updated_by=before.updated_by, store=self.store
                )
                self._apply(app, name, restart_running=True)
        except NoustError as exc:
            self.log.warning(f"The {name} process could not be put back as it was: {exc}")

    def scale(
        self, domain: str, name: str, instances: int, *, actor: str = "system"
    ) -> ProcessChange:
        """
        Run another number of copies of a process.

        Args:
            domain: The application.
            name: The process.
            instances: How many copies; 0 stops them and keeps the definition.
            actor: Who asked, for the audit trail.

        Returns:
            What was done.

        Raises:
            ValidationError: The count is out of range, or there is no such process.
            ServiceError: A copy could not be started or stopped.
        """
        check_instances(instances)
        app = self._app(domain)
        record = store_module.get_process(app.domain, name, store=self.store)
        if record is None:
            raise ValidationError(
                f"{app.domain} has no process called {name!r}",
                details="See its processes with: noust app process list " + app.domain,
                field="name",
            )
        return self.set_process(
            app.domain, replace(record, instances=instances), actor=actor, apply=True
        )

    def remove(self, domain: str, name: str, *, actor: str = "system") -> ProcessChange:
        """
        Stop a process and forget it.

        Args:
            domain: The application.
            name: The process.
            actor: Who asked, for the audit trail.

        Returns:
            What was done.

        Raises:
            ValidationError: There is no such process.
            AppBusyError: Another operation is running on the application.
        """
        require_server_role("Applications")
        app = self._app(domain)
        record = store_module.get_process(app.domain, name, store=self.store)
        if record is None:
            raise ValidationError(f"{app.domain} has no process called {name!r}", field="name")
        with app_lock(app.domain, "process change"):
            stopped = self._retire(app, record)
            store_module.delete_process(app.domain, name, store=self.store)
            self._after_last(app)
        audit.record(
            "apps.process",
            target=f"app:{app.domain}",
            details={"action": "remove", "process": name, "by": actor},
        )
        return ProcessChange(domain=app.domain, name=name, stopped=tuple(stopped), removed=True)

    def remove_all(self, domain: str) -> list[str]:
        """
        Stop and forget every process of an application that is being deleted.

        The caller holds the application's lock.

        Args:
            domain: The application.

        Returns:
            The units stopped.
        """
        app = self.store.get_app(domain)
        if app is None:
            return []
        stopped: list[str] = []
        for record in store_module.list_processes(domain, store=self.store):
            stopped.extend(self._retire(app, record))
            store_module.delete_process(domain, record.name, store=self.store)
        self._after_last(app)
        return stopped

    def apply_all(self, domain: str) -> list[ProcessChange]:
        """
        Make the units of every process match its definition.

        What a deployment calls after it synchronised the repository's
        definitions, with the application's lock held by the caller.

        Args:
            domain: The application.

        Returns:
            What was done for each process.
        """
        app = self._app(domain)
        return [
            self._apply(app, record.name)
            for record in store_module.list_processes(domain, store=self.store)
        ]

    # -- The units -----------------------------------------------------------

    def _runtime_directory(self, app: App) -> Path:
        """
        Name the directory the processes run in.

        Args:
            app: The application.

        Returns:
            The ``workers`` link on the release layout, pointed at the release that
            serves; the application's own tree in place.
        """
        from noust.deployers.helpers.layout import RELEASES, app_root, code_path_for
        from noust.deployers.releases import ReleaseManager

        if app.layout != RELEASES:
            return code_path_for(app)
        releases = ReleaseManager(app_root(app))
        if not releases.workers_link.is_symlink():
            current = releases.current()
            if current is None:
                raise DeploymentError(
                    f"{app.domain} has no active release to run its processes from",
                    details=f"Build one first: noust update {app.domain}",
                )
            releases.point_workers(current.path)
        return releases.workers_link

    def _context(self, app: App, record: ProcessRecord) -> dict[str, Any]:
        """
        Gather what the template needs for a process.

        Args:
            app: The application.
            record: The process.

        Returns:
            The template's variables, validated.
        """
        from noust.deployers.helpers.layout import app_root, env_file_for
        from noust.deployers.helpers.path_resolver import PathResolver
        from noust.deployers.unit_environment import inline_variables
        from noust.managers import unit_hardening
        from noust.managers.app_identity import service_account
        from noust.managers.service_manager import ResourceLimits

        user, group = service_account(app)
        main_unit = app_root(app).name
        text = self.services.get_service_config(main_unit) or ""
        inline = {key: value for key, value in inline_variables(text).items() if key != "PORT"}
        prefer = [Path(part) for part in inline.get("PATH", "").split(":") if part.startswith("/")]
        command = PathResolver(logger=self.log).resolve_command(record.command, prefer=prefer)
        return {
            "base": base_name(app, record.name),
            "process": record.name,
            "application": app.domain,
            "description": validate_unit_value(
                f"Noust: {app.domain} ({record.name})", field="Description"
            ),
            "user": validate_unit_value(user, field="User"),
            "group": validate_unit_value(group or user, field="Group"),
            "working_directory": validate_unit_value(
                str(self._runtime_directory(app)), field="WorkingDirectory"
            ),
            "environment": validate_environment(inline),
            "environment_file": str(env_file_for(app)),
            "command": validate_unit_value(command, field="ExecStart"),
            "resource_limits": ResourceLimits(
                memory_max_mb=record.memory_max_mb,
                cpu_quota_percent=record.cpu_quota_percent,
                tasks_max=record.tasks_max,
            )
            .validated()
            .directives(),
            "hardening": unit_hardening.directives_for_app(
                app, unit_hardening.get_record(app.domain, store=self.store)
            ),
        }

    def _apply(self, app: App, name: str, *, restart_running: bool = True) -> ProcessChange:
        """
        Write a process's template and start or stop its copies to match.

        The application's lock is held by the caller.

        Args:
            app: The application.
            name: The process.
            restart_running: Restart the copies that already run; False starts
                only the ones that do not (a scale-up).

        Returns:
            What was done.

        Raises:
            ServiceError: A unit could not be written, started or stopped.
        """
        record = store_module.get_process(app.domain, name, store=self.store)
        if record is None:
            return ProcessChange(domain=app.domain, name=name)
        base = base_name(app, name)
        self.services.install_unit(f"{base}@", "process@", self._context(app, record))
        loaded = self._loaded(base)
        started: list[str] = []
        stopped: list[str] = []
        # Copies beyond the count stop first, so a scale-down never overlaps a start.
        for number in sorted(loaded):
            if number > record.instances:
                unit = instance_name(app, name, number)
                self.services.delete_service(unit, keep_record=True)
                stopped.append(unit)
        for number in range(1, record.instances + 1):
            unit = instance_name(app, name, number)
            if number in loaded and not restart_running:
                continue
            self.services.enable(unit)
            self.services.restart(unit)
            started.append(unit)
        return ProcessChange(
            domain=app.domain,
            name=name,
            instances=record.instances,
            started=tuple(started),
            stopped=tuple(stopped),
        )

    def _loaded(self, base: str) -> set[int]:
        """
        Name the copies of a process systemd has loaded, running or not.

        Args:
            base: The template's name without ``@``.

        Returns:
            Their numbers.
        """
        result = self.runner.run(
            [
                "systemctl",
                "list-units",
                "--all",
                "--plain",
                "--no-legend",
                "--no-pager",
                f"{base}@*.service",
            ],
            timeout=SHOW_TIMEOUT,
        )
        if not result.success:
            return set()
        return {int(number) for name, number in _LISTED.findall(result.stdout) if name == base}

    def _retire(self, app: App, record: ProcessRecord) -> list[str]:
        """
        Stop every copy of a process and remove its template.

        Args:
            app: The application.
            record: The process.

        Returns:
            The copies stopped.
        """
        stopped: list[str] = []
        base = base_name(app, record.name)
        for number in sorted(self._loaded(base)):
            unit = instance_name(app, record.name, number)
            self.services.delete_service(unit, keep_record=True)
            stopped.append(unit)
        self.services.remove_template(base)
        return stopped

    def _after_last(self, app: App) -> None:
        """
        Drop the link the processes ran from once the last one is gone.

        Args:
            app: The application.
        """
        if store_module.list_processes(app.domain, store=self.store):
            return
        from noust.deployers.helpers.layout import RELEASES, app_root
        from noust.deployers.releases import ReleaseManager

        if app.layout == RELEASES:
            ReleaseManager(app_root(app)).remove_workers()

    # -- Together with the web process ----------------------------------------

    def control(self, domain: str, action: str) -> list[str]:
        """
        Start, stop or restart every copy of an application's processes.

        What starting, stopping or restarting an application means for the
        processes that belong to it.

        Args:
            domain: The application.
            action: ``start``, ``stop`` or ``restart``.

        Returns:
            The copies acted on; empty when it has no processes.

        Raises:
            ServiceError: systemd refused one of them.
        """
        units = self.units_for(domain)
        for unit in units:
            getattr(self.services, action)(unit)
        return units

    def restart(self, units: Sequence[str]) -> None:
        """
        Restart some copies.

        Args:
            units: Their units.

        Raises:
            ServiceError: A copy could not be restarted.
        """
        for unit in units:
            self.services.restart(unit)

    def settle(self, units: Sequence[str], *, seconds: float = SETTLE_SECONDS) -> Settled:
        """
        Wait for restarted copies to stay up.

        A copy is judged when it has run for the whole wait without systemd
        restarting it: one that starts and dies is in ``auto-restart`` within a
        second, and one that fails for good is ``failed``.

        Args:
            units: The copies' units.
            seconds: How long they must keep running.

        Returns:
            Whether every copy is running, and which are not.
        """
        if not units:
            return Settled()
        deadline = self._clock() + seconds
        baseline = {unit: self.unit_state(unit) for unit in units}
        while True:
            self._sleep(min(1.0, max(0.0, deadline - self._clock())))
            now = {unit: self.unit_state(unit) for unit in units}
            broken = [
                unit
                for unit, state in now.items()
                if not state.running or state.restarts > baseline[unit].restarts
            ]
            if broken or self._clock() >= deadline:
                return Settled(
                    ok=not broken,
                    failed=[
                        f"{unit}: {now[unit].state} ({now[unit].sub_state or 'no sub state'})"
                        + (
                            f", restarted {now[unit].restarts - baseline[unit].restarts} time(s)"
                            if now[unit].restarts > baseline[unit].restarts
                            else ""
                        )
                        for unit in broken
                    ],
                )

    def evidence(self, settled: Settled, units: Sequence[str]) -> str:
        """
        Put together what a copy that did not stay up shows the operator.

        Args:
            settled: What :meth:`settle` returned.
            units: The copies' units.

        Returns:
            The copies that failed and the end of the journal of the first.
        """
        parts = ["These processes did not stay up:\n" + "\n".join(settled.failed)]
        first = next(
            (unit for unit in units if any(f.startswith(unit) for f in settled.failed)), None
        )
        if first is not None:
            try:
                journal = self.services.logs(first, lines=JOURNAL_LINES).strip()
            except NoustError as exc:
                journal = f"(the journal could not be read: {exc})"
            if journal:
                parts.append(f"Last lines of the journal of {first}:\n{journal}")
        return "\n\n".join(parts)

    # -- Logs ------------------------------------------------------------------

    def logs(self, domain: str, name: str, *, lines: int = 100) -> str:
        """
        Read the journal of a process: all its copies, interleaved.

        Args:
            domain: The application.
            name: The process.
            lines: How many lines.

        Returns:
            The journal text.

        Raises:
            ValidationError: There is no such process.
        """
        app = self._app(domain)
        record = store_module.get_process(app.domain, name, store=self.store)
        if record is None:
            raise ValidationError(f"{app.domain} has no process called {name!r}", field="name")
        result = self.runner.run(
            [
                "journalctl",
                "--no-pager",
                "-o",
                "short-iso",
                "-n",
                str(max(1, min(lines, 5000))),
                "-u",
                f"{base_name(app, name)}@*.service",
            ],
            timeout=60,
        )
        if not result.success:
            raise ServiceError(
                f"Could not read the journal of {name}", details=(result.stderr or "").strip()
            )
        return result.stdout
