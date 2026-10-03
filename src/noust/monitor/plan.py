"""
One plan per application: what is measured, where, and why when it is not.

Every kind of application runs differently, and the collector, the API and the
console used to re-derive that separately (the client even worked out "static"
and "Compose" on its own, and a PHP application had no answer at all). A
:class:`SamplingPlan` is the one answer:

=============================  ========================  ==========================
application                    where CPU and memory are  when there is nothing
=============================  ========================  ==========================
in place / releases            its unit's cgroup         stopped, unit missing,
                               (``wasm-*`` legacy names  accounting off, no cgroup
                               included)
blue/green                     both instances of the     as above
                               template unit, summed
monorepo                       every workspace's unit,   as above
                               summed
Docker Compose                 the cgroup of every       no docker, no containers,
                               container of the project  cgroup not readable
PHP-FPM                        the worker processes of   the FPM service is stopped
                               the application's pool
static                         nothing runs              ``static`` (its traffic is
                                                         read from the access log)
=============================  ========================  ==========================

The path of a unit's cgroup is **asked of systemd** (``ControlGroup``), not
computed: it is authoritative for legacy names, template instances, escapes and
any ``Slice=`` an operator added in a drop-in. The computed path is kept only as
the fallback when systemd cannot be asked.

A plan that cannot measure carries a :class:`Reason`: a machine-readable code,
one sentence saying what is true, the fix, and the system's own output verbatim
as evidence. Nothing here paraphrases a system error.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.exceptions import DeploymentError, NoustError
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import runs_as_instances

log = logging.getLogger(__name__)

#: Where the unified cgroup hierarchy is mounted.
CGROUP_MOUNT = Path("/sys/fs/cgroup")

#: Where systemd parents the cgroups of the units Noust writes.
CGROUP_ROOT = CGROUP_MOUNT / "system.slice"

#: What is asked of systemd about every unit, in one ``systemctl show``.
PLAN_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "ControlGroup",
    "MemoryAccounting",
    "CPUAccounting",
    "ActiveEnterTimestamp",
    "InactiveEnterTimestamp",
)

#: Where access logs are looked for, by web server. Apache's directory depends
#: on the distribution (``APACHE_LOG_DIR``); the site templates always name the
#: file ``<domain>.access.log``.
ACCESS_LOG_DIRS: dict[str, tuple[Path, ...]] = {
    "nginx": (Path("/var/log/nginx"),),
    "apache": (Path("/var/log/apache2"), Path("/var/log/httpd")),
    "apache2": (Path("/var/log/apache2"), Path("/var/log/httpd")),
}

#: Deadline of one docker call. The daemon answers at once or it is broken.
DOCKER_TIMEOUT = 10

#: Kinds of plan, for tests and for anything that groups applications.
KIND_UNIT = "unit"
KIND_LEGACY = "legacy"
KIND_BLUE_GREEN = "blue_green"
KIND_MONOREPO = "monorepo"
KIND_COMPOSE = "compose"
KIND_PHP_FPM = "php_fpm"
KIND_STATIC = "static"

#: Where CPU and memory are read from.
SOURCE_CGROUP = "cgroup"
SOURCE_DOCKER = "docker"
SOURCE_FPM = "fpm"
SOURCE_NONE = "none"


@dataclass(frozen=True)
class Reason:
    """
    Why a series has no data, and what to do about it.

    Attributes:
        code: Machine-readable, stable: the console translates by it.
        message: One sentence saying what is true, in English.
        fix: What the operator can do, a command verbatim when there is one;
            None when there is nothing to fix (a static site has no process).
        evidence: The system's own output that led here, verbatim, or None.
        params: Values the console interpolates into its own sentence (a unit
            name, a time).
    """

    code: str
    message: str
    fix: str | None = None
    evidence: str | None = None
    params: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the reason as the API carries it.

        Returns:
            A JSON-serialisable mapping.
        """
        return {
            "code": self.code,
            "message": self.message,
            "fix": self.fix,
            "evidence": self.evidence,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class Target:
    """
    One cgroup a plan reads.

    Attributes:
        name: The unit's name, or the container's.
        kind: ``unit`` or ``container``.
        cgroup: The cgroup directory, or None when there is none to read.
        active_state: systemd's ``ActiveState``, or None when it was not asked.
        control_group: systemd's ``ControlGroup`` verbatim, or None.
        properties: What systemd said about the unit, for the evidence.
    """

    name: str
    kind: str = "unit"
    cgroup: Path | None = None
    active_state: str | None = None
    control_group: str | None = None
    properties: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SamplingPlan:
    """
    What the collector measures for one application, and why not when it cannot.

    Attributes:
        domain: The application.
        kind: One of the ``KIND_*`` constants.
        source: One of the ``SOURCE_*`` constants: where CPU and memory come
            from.
        targets: The cgroups summed into the application's series.
        reason: Why CPU and memory are not measured, or None when they are.
        pool: The PHP-FPM pool name, for ``fpm`` plans.
        fpm_service: The FPM master's unit, for ``fpm`` plans.
        traffic_log: The access log requests are counted from, or None.
        traffic_reason: Why requests are not counted, or None.
    """

    domain: str
    kind: str = KIND_UNIT
    source: str = SOURCE_NONE
    targets: tuple[Target, ...] = ()
    reason: Reason | None = None
    pool: str | None = None
    fpm_service: str | None = None
    traffic_log: Path | None = None
    traffic_reason: Reason | None = None

    @property
    def measures_resources(self) -> bool:
        """Whether CPU and memory are sampled for this application."""
        return self.reason is None and self.source != SOURCE_NONE


def _escape_unit_name(text: str) -> str:
    """
    Escape a string the way ``systemd-escape`` does for a unit name.

    Args:
        text: The string.

    Returns:
        ASCII letters, digits, ``:``, ``_`` and ``.`` (not leading) as they
        are, ``/`` as ``-``, everything else as ``\\xNN`` per byte.
    """
    out: list[str] = []
    for index, byte in enumerate(text.encode("utf-8")):
        char = chr(byte)
        if char == "/":
            out.append("-")
        elif byte < 128 and (char.isalnum() or char in ":_" or (char == "." and index > 0)):
            out.append(char)
        else:
            out.append(f"\\x{byte:02x}")
    return "".join(out)


def unit_cgroup_path(cgroup_root: Path, unit: str) -> Path:
    """
    Say where systemd would account a unit's processes, without asking it.

    A unit sits directly in ``system.slice``. An instance of a template, such
    as the ``<name>@blue`` and ``<name>@green`` of an application in
    zero-downtime mode, sits in the slice systemd makes for the template:
    ``system-<prefix>.slice``, the prefix escaped as a unit name.

    This is the fallback for when systemd cannot be asked; the plan uses
    ``ControlGroup`` from ``systemctl show``.

    Args:
        cgroup_root: ``system.slice``'s directory.
        unit: The unit, without ``.service``.

    Returns:
        Its cgroup directory.
    """
    prefix, at, _instance = unit.partition("@")
    if not at:
        return cgroup_root / f"{unit}.service"
    return cgroup_root / f"system-{_escape_unit_name(prefix)}.slice" / f"{unit}.service"


def is_cgroup_v2(mount: Path = CGROUP_MOUNT) -> bool:
    """
    Say whether the unified cgroup hierarchy is mounted.

    Args:
        mount: Where cgroups are mounted.

    Returns:
        True on a cgroup v2 host: its root carries ``cgroup.controllers``.
    """
    return (mount / "cgroup.controllers").is_file()


def log_directories(webserver: str) -> tuple[Path, ...]:
    """
    Name the directories a web server writes its access logs to.

    Args:
        webserver: ``nginx`` or ``apache``; anything else reads as nginx.

    Returns:
        The directories to look in, in order.
    """
    return ACCESS_LOG_DIRS.get(webserver) or ACCESS_LOG_DIRS.get("nginx", ())


def find_access_log(domain: str, webserver: str) -> Path | None:
    """
    Find the access log of a site.

    Args:
        domain: The site's domain: the templates name the log after it.
        webserver: ``nginx`` or ``apache``.

    Returns:
        The first existing ``<domain>.access.log`` in the directories that web
        server writes to, or None.
    """
    for directory in log_directories(webserver):
        candidate = directory / f"{domain}.access.log"
        if candidate.is_file():
            return candidate
    return None


class PlanBuilder:
    """
    Builds :class:`SamplingPlan` for applications, asking systemd once for all.

    The collector rebuilds its plans every thirty seconds and the API builds
    one on demand: both go through here, so a unit type has one definition.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        cgroup_mount: Path = CGROUP_MOUNT,
        service_manager: Any | None = None,
    ) -> None:
        """
        Args:
            runner: Command runner for systemctl and docker. Defaults to the
                process-wide one.
            cgroup_mount: Where cgroups are mounted. Injected so tests can
                point it at a fake tree.
            service_manager: Names the units an application runs as. Built on
                ``runner`` when None; injected so tests need no systemd.
        """
        self.runner = runner or get_runner()
        self.cgroup_mount = Path(cgroup_mount)
        self._manager = service_manager

    @property
    def manager(self) -> Any:
        """The service manager, built on first use."""
        if self._manager is None:
            from noust.managers.service_manager import ServiceManager

            self._manager = ServiceManager(verbose=False, runner=self.runner)
        return self._manager

    # ------------------------------------------------------------------ build

    def build(self, apps: Sequence[Any]) -> dict[str, SamplingPlan]:
        """
        Plan every application.

        Args:
            apps: Application rows.

        Returns:
            Domain to its plan. systemd is asked once, for every unit of every
            application.
        """
        services = self._stored_services()
        unit_names: dict[str, list[str]] = {}
        for app in apps:
            if self._process_kind(app) is not None:
                continue
            try:
                unit_names[app.domain] = self.manager.app_units(
                    app, services=services, apps=list(apps)
                )
            except NoustError as exc:
                log.debug("could not name the units of %s: %s", app.domain, exc)
                unit_names[app.domain] = []

        described = self._describe([name for names in unit_names.values() for name in names])
        fpm_control_group = self._fpm_control_group(apps)

        plans: dict[str, SamplingPlan] = {}
        for app in apps:
            if not getattr(app, "domain", ""):
                continue
            plans[app.domain] = self._plan_for(
                app, unit_names.get(app.domain, []), described, fpm_control_group
            )
        return plans

    def build_one(self, app: Any) -> SamplingPlan:
        """
        Plan one application, asking systemd afresh.

        Args:
            app: The application row.

        Returns:
            Its plan.
        """
        return self.build([app]).get(app.domain, SamplingPlan(domain=app.domain))

    def _stored_services(self) -> list[Any]:
        """
        Read the services table once for every application's unit lookup.

        Returns:
            The rows; empty when the store cannot be read.
        """
        try:
            return list(self.manager.store.list_services())
        except (NoustError, sqlite3.Error) as exc:
            log.debug("could not read the services table: %s", exc)
            return []

    def _describe(self, names: list[str]) -> dict[str, dict[str, str]]:
        """
        Ask systemd about units, once.

        Args:
            names: Unit names, without ``.service``.

        Returns:
            Unit name to its properties; empty when systemd cannot be asked,
            in which case the plan falls back to the computed cgroup path.
        """
        unique = list(dict.fromkeys(names))
        if not unique:
            return {}
        try:
            return dict(self.manager.describe_units(unique, PLAN_PROPERTIES))
        except NoustError as exc:
            log.debug("systemctl show failed for the metrics plan: %s", exc)
            return {}

    @staticmethod
    def _process_kind(app: Any) -> str | None:
        """
        Classify applications that are not one or more systemd units.

        Args:
            app: The application row.

        Returns:
            ``php_fpm``, ``compose`` or ``static``; None for the rest.
        """
        from noust.deployers.helpers.php_fpm import is_php_fpm

        if is_php_fpm(app):
            return KIND_PHP_FPM
        if getattr(app, "app_type", None) == "docker-compose":
            return KIND_COMPOSE
        if getattr(app, "is_static", False) or getattr(app, "app_type", None) == "static":
            return KIND_STATIC
        return None

    # --------------------------------------------------------------- one plan

    def _plan_for(
        self,
        app: Any,
        units: list[str],
        described: dict[str, dict[str, str]],
        fpm_control_group: dict[str, Any],
    ) -> SamplingPlan:
        """
        Build the plan of one application.

        Args:
            app: The application row.
            units: The units it runs as, primary first.
            described: What systemd said about every unit.
            fpm_control_group: The FPM installation, or why there is none.

        Returns:
            The plan.
        """
        kind = self._process_kind(app)
        traffic_log, traffic_reason = self._traffic(app, kind)

        if kind == KIND_STATIC:
            return SamplingPlan(
                domain=app.domain,
                kind=KIND_STATIC,
                reason=Reason(
                    code="static",
                    message="A static site has no process, so there is no CPU or memory to measure.",
                    fix=None,
                ),
                traffic_log=traffic_log,
                traffic_reason=traffic_reason,
            )
        if kind == KIND_PHP_FPM:
            return self._fpm_plan(app, fpm_control_group, traffic_log, traffic_reason)
        if kind == KIND_COMPOSE:
            return self._compose_plan(app)
        return self._unit_plan(app, units, described)

    def _unit_plan(
        self, app: Any, units: list[str], described: dict[str, dict[str, str]]
    ) -> SamplingPlan:
        """
        Plan an application that runs as one or more systemd units.

        Args:
            app: The application row.
            units: Its unit names, primary first.
            described: What systemd said about every unit.

        Returns:
            The plan, with a reason when no unit has a readable cgroup.
        """
        if runs_as_instances(app):
            kind = KIND_BLUE_GREEN
        elif getattr(app, "app_type", None) == "monorepo":
            kind = KIND_MONOREPO
        elif units and units[0].startswith("wasm-"):
            kind = KIND_LEGACY
        else:
            kind = KIND_UNIT

        if not units:
            return SamplingPlan(
                domain=app.domain,
                kind=kind,
                reason=Reason(
                    code="unit_missing",
                    message=f"No systemd unit is recorded for {app.domain}.",
                    fix=f"Redeploy it: noust app update {app.domain}",
                ),
            )

        v2 = is_cgroup_v2(self.cgroup_mount)
        targets = tuple(self._unit_target(unit, described.get(unit)) for unit in units)
        readable = [
            target
            for target in targets
            if target.cgroup is not None and (target.cgroup / "memory.current").is_file()
        ]
        if readable and v2:
            return SamplingPlan(domain=app.domain, kind=kind, source=SOURCE_CGROUP, targets=targets)
        return SamplingPlan(
            domain=app.domain,
            kind=kind,
            source=SOURCE_CGROUP,
            targets=targets,
            reason=self._unit_reason(app, targets, described, v2),
        )

    def _unit_target(self, unit: str, properties: dict[str, str] | None) -> Target:
        """
        Locate one unit's cgroup.

        Args:
            unit: The unit, without ``.service``.
            properties: What systemd said, or None when it was not asked or
                did not answer.

        Returns:
            The target. With systemd's ``ControlGroup`` its directory is that
            path under the mount; without it the computed path is the guess.
        """
        if properties is None:
            return Target(
                name=unit, cgroup=unit_cgroup_path(self.cgroup_mount / "system.slice", unit)
            )
        control_group = properties.get("ControlGroup", "").strip()
        directory = self.cgroup_mount / control_group.lstrip("/") if control_group else None
        return Target(
            name=unit,
            cgroup=directory,
            active_state=properties.get("ActiveState", "").strip() or None,
            control_group=control_group or None,
            properties=dict(properties),
        )

    def _unit_reason(
        self,
        app: Any,
        targets: tuple[Target, ...],
        described: dict[str, dict[str, str]],
        v2: bool,
    ) -> Reason:
        """
        Say why none of an application's units can be measured.

        The primary unit (the one serving) decides: an idle blue/green
        instance being stopped is by design and says nothing.

        Args:
            app: The application row.
            targets: Its units.
            described: What systemd said about every unit.
            v2: Whether the unified cgroup hierarchy is mounted.

        Returns:
            The reason.
        """
        primary = targets[0]
        properties = primary.properties
        unit = primary.name
        evidence = _evidence(properties)

        if not v2:
            return Reason(
                code="cgroup_v1",
                message="This server does not use the unified cgroup hierarchy (cgroup v2), "
                "which is where Noust reads CPU and memory.",
                fix="Boot with systemd.unified_cgroup_hierarchy=1 (Ubuntu 22.04+ and Debian 11+ "
                "already do).",
                evidence=evidence,
            )
        if properties.get("LoadState") == "not-found":
            return Reason(
                code="unit_missing",
                message=f"systemd does not know the unit {unit}.service.",
                fix=f"Redeploy the application to write it again: noust app update {app.domain}",
                evidence=evidence,
                params={"unit": unit},
            )
        state = properties.get("ActiveState", "")
        if state in ("inactive", "failed"):
            since = properties.get("InactiveEnterTimestamp", "").strip()
            return Reason(
                code="failed" if state == "failed" else "stopped",
                message=(
                    f"{unit}.service has failed, so there is nothing running to measure."
                    if state == "failed"
                    else f"{unit}.service is stopped, so there is nothing running to measure."
                ),
                fix=(
                    f"journalctl -u {unit} -n 50"
                    if state == "failed"
                    else f"noust service start {unit}"
                ),
                evidence=evidence,
                params={"unit": unit, "since": since},
            )
        if state in ("activating", "deactivating", "reloading"):
            return Reason(
                code="starting",
                message=f"{unit}.service is {state}; it will be measured once it is running.",
                evidence=evidence,
                params={"unit": unit},
            )
        if primary.cgroup is not None and primary.cgroup.is_dir():
            # The cgroup is there but memory.current is not: the memory
            # controller is not enabled for it.
            return Reason(
                code="accounting_off",
                message=f"systemd is not counting memory for {unit}.service.",
                fix=(
                    "Turn accounting on and restart the application: add MemoryAccounting=yes "
                    "and CPUAccounting=yes to a drop-in of the unit "
                    f"(systemctl edit {unit}), or set DefaultMemoryAccounting=yes in "
                    "/etc/systemd/system.conf."
                ),
                evidence=evidence,
                params={"unit": unit},
            )
        if not properties:
            return Reason(
                code="cgroup_missing",
                message=f"No cgroup was found for {unit}.service, and systemd could not be asked.",
                fix=f"systemctl status {unit}",
                params={"unit": unit},
            )
        return Reason(
            code="cgroup_missing",
            message=f"{unit}.service is running but its cgroup could not be found.",
            fix=f"systemctl status {unit}",
            evidence=evidence,
            params={"unit": unit},
        )

    # ------------------------------------------------------------------- PHP

    def _fpm_control_group(self, apps: Sequence[Any]) -> dict[str, Any]:
        """
        Locate the PHP-FPM master's cgroup, if any application needs it.

        Args:
            apps: Every application.

        Returns:
            ``{"installation": FpmInstallation, "properties": {...}}`` or
            ``{"error": "..."}`` when there is no PHP-FPM; empty when no
            application is a PHP one.
        """
        from noust.deployers.helpers.php_fpm import find_fpm, is_php_fpm

        if not any(is_php_fpm(app) for app in apps):
            return {}
        try:
            installation = find_fpm()
        except DeploymentError as exc:
            return {"error": str(exc)}
        described = self._describe([installation.service])
        return {
            "installation": installation,
            "properties": described.get(installation.service, {}),
        }

    def _fpm_plan(
        self,
        app: Any,
        fpm: dict[str, Any],
        traffic_log: Path | None,
        traffic_reason: Reason | None,
    ) -> SamplingPlan:
        """
        Plan a PHP application: the worker processes of its pool.

        Args:
            app: The application row.
            fpm: :meth:`_fpm_control_group`'s answer.
            traffic_log: The access log, if found.
            traffic_reason: Why there is none.

        Returns:
            A plan whose target is the FPM master's cgroup, whose processes
            are then told apart by pool; or one with a reason.
        """
        from noust.deployers.helpers.layout import app_root

        installation = fpm.get("installation")
        if installation is None:
            return SamplingPlan(
                domain=app.domain,
                kind=KIND_PHP_FPM,
                reason=Reason(
                    code="php_fpm_missing",
                    message="PHP-FPM is not installed, so the application's pool cannot be measured.",
                    fix="Install PHP-FPM, then redeploy the application.",
                    evidence=fpm.get("error"),
                ),
                traffic_log=traffic_log,
                traffic_reason=traffic_reason,
            )
        properties = fpm.get("properties", {})
        control_group = properties.get("ControlGroup", "").strip()
        pool = f"{installation.prefix(app_root(app).name)}{app_root(app).name}"
        target = Target(
            name=installation.service,
            cgroup=self.cgroup_mount / control_group.lstrip("/") if control_group else None,
            active_state=properties.get("ActiveState", "").strip() or None,
            control_group=control_group or None,
            properties=dict(properties),
        )
        state = properties.get("ActiveState", "")
        if not control_group or state in ("inactive", "failed"):
            return SamplingPlan(
                domain=app.domain,
                kind=KIND_PHP_FPM,
                source=SOURCE_FPM,
                targets=(target,),
                reason=Reason(
                    code="stopped",
                    message=f"{installation.service}.service is not running, so the pool "
                    f"{pool} has no workers to measure.",
                    fix=f"systemctl start {installation.service}",
                    evidence=_evidence(properties),
                    params={"unit": installation.service},
                ),
                pool=pool,
                fpm_service=installation.service,
                traffic_log=traffic_log,
                traffic_reason=traffic_reason,
            )
        return SamplingPlan(
            domain=app.domain,
            kind=KIND_PHP_FPM,
            source=SOURCE_FPM,
            targets=(target,),
            pool=pool,
            fpm_service=installation.service,
            traffic_log=traffic_log,
            traffic_reason=traffic_reason,
        )

    # --------------------------------------------------------------- Compose

    def _compose_plan(self, app: Any) -> SamplingPlan:
        """
        Plan a Docker Compose stack: the cgroup of every container.

        Args:
            app: The application row.

        Returns:
            One target per running container, or a reason (no docker, no
            containers, no readable cgroup). The stack's own unit is a
            oneshot that exits, so its numbers would be a chart that lies.
        """
        if not self.runner.exists("docker"):
            return SamplingPlan(
                domain=app.domain,
                kind=KIND_COMPOSE,
                reason=Reason(
                    code="compose_docker_unavailable",
                    message="The docker command is not installed, so the stack's containers "
                    "cannot be found.",
                    fix="Install Docker.",
                ),
            )
        containers = self._compose_containers(app)
        if isinstance(containers, Reason):
            return SamplingPlan(domain=app.domain, kind=KIND_COMPOSE, reason=containers)
        if not containers:
            return SamplingPlan(
                domain=app.domain,
                kind=KIND_COMPOSE,
                source=SOURCE_DOCKER,
                reason=Reason(
                    code="stopped",
                    message=f"No container of {app.domain} is running, so there is nothing "
                    "to measure.",
                    fix=f"noust service start {app.domain}",
                ),
            )

        targets: list[Target] = []
        for container_id, name in containers:
            directory = self._container_cgroup(container_id)
            targets.append(Target(name=name, kind="container", cgroup=directory))
        if not any(target.cgroup is not None for target in targets):
            tried = "\n".join(
                str(self.cgroup_mount / relative)
                for relative in (
                    f"system.slice/docker-{containers[0][0]}.scope",
                    f"docker/{containers[0][0]}",
                )
            )
            return SamplingPlan(
                domain=app.domain,
                kind=KIND_COMPOSE,
                source=SOURCE_DOCKER,
                targets=tuple(targets),
                reason=Reason(
                    code="compose_cgroup_unreadable",
                    message="The containers are running, but their cgroups were not found "
                    "where Docker puts them.",
                    fix="Use 'docker stats' for their usage.",
                    evidence=f"looked for:\n{tried}",
                ),
            )
        return SamplingPlan(
            domain=app.domain, kind=KIND_COMPOSE, source=SOURCE_DOCKER, targets=tuple(targets)
        )

    def _compose_containers(self, app: Any) -> list[tuple[str, str]] | Reason:
        """
        List the running containers of a Compose project.

        Args:
            app: The application row.

        Returns:
            ``(full id, name)`` per container, or a Reason when docker could
            not be asked.
        """
        from noust.deployers.docker_compose import compose_project_name

        app_path = Path(getattr(app, "app_path", "") or "")
        filters: list[str] = []
        # An adopted stack runs under the project it already had, which need
        # not be its directory's name: that project is the one that answers.
        pinned = getattr(app, "compose_project", None)
        if pinned:
            filters.append(f"label=com.docker.compose.project={pinned}")
        filters.append(f"label=com.docker.compose.project.working_dir={app_path}")
        project = compose_project_name(app_path, None) if app_path.name else None
        if project and project != pinned:
            filters.append(f"label=com.docker.compose.project={project}")

        for docker_filter in filters:
            result = self.runner.run(
                [
                    "docker",
                    "ps",
                    "--no-trunc",
                    "--filter",
                    docker_filter,
                    "--format",
                    "{{.ID}}\t{{.Names}}",
                ],
                timeout=DOCKER_TIMEOUT,
            )
            if not result.success:
                return Reason(
                    code="compose_docker_unavailable",
                    message="The docker daemon could not be asked which containers run.",
                    fix="systemctl status docker",
                    evidence=(result.stderr or result.stdout).strip() or None,
                )
            found = []
            for line in result.stdout.splitlines():
                container_id, _, name = line.partition("\t")
                if container_id.strip():
                    found.append((container_id.strip(), name.strip() or container_id[:12]))
            if found:
                return found
        return []

    def _container_cgroup(self, container_id: str) -> Path | None:
        """
        Find a container's cgroup.

        Args:
            container_id: The full container id.

        Returns:
            The directory under the systemd cgroup driver
            (``system.slice/docker-<id>.scope``) or the cgroupfs driver
            (``docker/<id>``), whichever exists.
        """
        for relative in (f"system.slice/docker-{container_id}.scope", f"docker/{container_id}"):
            candidate = self.cgroup_mount / relative
            if candidate.is_dir():
                return candidate
        return None

    # --------------------------------------------------------------- traffic

    @staticmethod
    def _traffic(app: Any, kind: str | None) -> tuple[Path | None, Reason | None]:
        """
        Find the access log requests are counted from.

        Only sites nothing else measures (static ones, and PHP applications,
        which the web server serves through FastCGI): an application behind a
        proxy is measured by its process.

        Args:
            app: The application row.
            kind: What :meth:`_process_kind` said.

        Returns:
            ``(log, None)`` when found; ``(None, reason)`` when it should exist
            and does not; ``(None, None)`` for an application that is not
            counted this way.
        """
        if kind not in (KIND_STATIC, KIND_PHP_FPM):
            return None, None
        webserver = str(getattr(app, "webserver", "nginx") or "nginx")
        found = find_access_log(app.domain, webserver)
        if found is not None:
            return found, None
        directories = log_directories(webserver)
        return None, Reason(
            code="access_log_missing",
            message=f"No access log exists for {app.domain}, so requests cannot be counted.",
            fix=(
                "The web server creates it when it first serves the site after a reload; "
                f"check that the site's configuration has an access_log for {app.domain}."
            ),
            evidence="looked for: "
            + ", ".join(str(directory / f"{app.domain}.access.log") for directory in directories),
        )


def _evidence(properties: dict[str, str]) -> str | None:
    """
    Quote what systemd said, verbatim, as ``key=value`` lines.

    Args:
        properties: The properties of one unit.

    Returns:
        The lines the reason rests on, or None when systemd said nothing.
    """
    keys = (
        "Id",
        "LoadState",
        "ActiveState",
        "SubState",
        "ControlGroup",
        "MemoryAccounting",
        "CPUAccounting",
    )
    lines = [f"{key}={properties[key]}" for key in keys if key in properties]
    return "\n".join(lines) or None


@dataclass(frozen=True)
class TargetStatus:
    """
    What is true about one target right now, for the API.

    Attributes:
        name: The unit or container.
        kind: ``unit`` or ``container``.
        active_state: systemd's ``ActiveState``, or None.
        control_group: systemd's ``ControlGroup``, verbatim, or None.
        cgroup_exists: Whether the directory exists.
        memory_current: ``memory.current`` in bytes, or None when unreadable.
        cpu_stat: Whether ``cpu.stat`` is readable.
    """

    name: str
    kind: str
    active_state: str | None
    control_group: str | None
    cgroup_exists: bool
    memory_current: int | None
    cpu_stat: bool


def target_status(target: Target) -> TargetStatus:
    """
    Read a target's cgroup now.

    Args:
        target: One of a plan's targets.

    Returns:
        Its state; nothing raised for a cgroup that vanished.
    """
    directory = target.cgroup
    memory: int | None = None
    cpu = False
    exists = directory is not None and directory.is_dir()
    if directory is not None and exists:
        try:
            memory = int((directory / "memory.current").read_text())
        except (OSError, ValueError):
            memory = None
        cpu = (directory / "cpu.stat").is_file()
    return TargetStatus(
        name=target.name,
        kind=target.kind,
        active_state=target.active_state,
        control_group=target.control_group,
        cgroup_exists=exists,
        memory_current=memory,
        cpu_stat=cpu,
    )
