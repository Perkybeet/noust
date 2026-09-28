# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Blue/green activation: the next release answers before it serves.

Activating a release by swapping ``current`` and restarting the unit leaves
the application without a process for as long as it takes to start. In
zero-downtime mode an application runs as two instances of one template
unit, ``<name>@blue`` on its port and ``<name>@green`` on the next one, and
its site proxies to an nginx ``upstream`` that names only the instance that
serves. An activation is then:

1. point the idle instance at the release (``colors/<color>``) and start it;
2. ask it the health gate's question on its own port;
3. point the upstream at it, ``nginx -t`` and reload: nginx finishes the
   requests in flight on the old workers and sends new ones to the new port;
4. let the old instance drain, then stop it;
5. move ``current`` last, so the CLI, cron and backups see the release that
   serves.

A release that fails the gate is stopped; the old instance never stopped
serving, so there is nothing to undo in nginx. An upstream nginx refuses is
put back before anything reloads.

The mode is opt-in and explicit (:func:`set_zero_downtime`), and only for
what can run twice side by side: a process on the release layout, behind
nginx. Turning it on and off are themselves switches without a cut, and
either is undone whole when a step fails. Every operation here runs under
the application's lock; :class:`BlueGreen` expects its caller to hold it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from wasm.core.applock import app_lock
from wasm.core.exceptions import (
    DeploymentError,
    RolledBackError,
    ValidationError,
    WASMError,
)
from wasm.core.fs import is_rehearsal
from wasm.core.logger import Logger
from wasm.core.store import (
    BLUE_GREEN_COLORS,
    DEFAULT_DRAIN_SECONDS,
    MAX_DRAIN_SECONDS,
    App,
    AppType,
    WASMStore,
    WebServer,
    get_store,
)
from wasm.core.utils import domain_to_app_name
from wasm.deployers.helpers.health import wait_until_healthy
from wasm.deployers.helpers.health_gate import HealthCheck, HealthGate, Probe
from wasm.deployers.helpers.layout import RELEASES, app_root, env_file_for
from wasm.deployers.helpers.nginx_config import NginxConfigBuilder
from wasm.deployers.releases import CURRENT_LINK, ReleaseManager
from wasm.managers.service_manager import ResourceLimits, ServiceManager
from wasm.validators.domain import validate_domain
from wasm.validators.names import MAX_SERVICE_NAME_LENGTH
from wasm.validators.port import is_port_available

if TYPE_CHECKING:
    from wasm.managers.webserver import WebServerManager

BLUE, GREEN = BLUE_GREEN_COLORS

#: Application types that deploy in place and write their own units and
#: sites: nothing about them can be run twice by a template.
_IN_PLACE_TYPES = frozenset({AppType.DOCKER_COMPOSE.value, AppType.MONOREPO.value})

#: How long the instance that kept serving gets to prove it still answers,
#: after the new one failed: a few quick probes, not a whole gate.
_STILL_SERVING_ATTEMPTS = 3
_STILL_SERVING_DELAY = 1.0

#: Longest suffix an instance adds to the application's unit name.
_INSTANCE_SUFFIX = max(len(f"@{color}") for color in BLUE_GREEN_COLORS)

#: What refreshes an application's site from the store (see :func:`_refresh_site`).
SiteRefresher = Callable[[App], None]


# ---------------------------------------------------------------------------
# Names, ports and commands
# ---------------------------------------------------------------------------


def other_color(color: str | None) -> str:
    """
    Name the instance that does not serve.

    Args:
        color: The serving instance, or None when none does.

    Returns:
        ``green`` for ``blue``, ``blue`` otherwise.
    """
    return GREEN if color == BLUE else BLUE


def unit_base(app: App) -> str:
    """
    Name the template an application's instances are made from, without ``@``.

    Args:
        app: The application.

    Returns:
        The application name, such as ``shop-example-com``.
    """
    return domain_to_app_name(app.domain)


def instance_unit(app: App, color: str) -> str:
    """
    Name one instance of an application.

    Args:
        app: The application.
        color: ``blue`` or ``green``.

    Returns:
        ``<name>@<color>``, without ``.service``.
    """
    return f"{unit_base(app)}@{color}"


def color_port(app: App, color: str) -> int:
    """
    Say which port an instance listens on.

    Args:
        app: The application; it must have a port.
        color: ``blue`` or ``green``.

    Returns:
        The application's port for blue, the next one for green.

    Raises:
        DeploymentError: The application has no port.
    """
    if not app.port:
        raise DeploymentError(
            f"{app.domain} has no port recorded",
            details=f"Redeploy it with an explicit port: wasm create -d {app.domain} --port ...",
        )
    return app.port if color == BLUE else app.port + 1


def serving_port(app: App) -> int | None:
    """
    Say which port answers for an application now.

    Args:
        app: The application.

    Returns:
        The serving instance's port in zero-downtime mode, the application's
        port otherwise, or None when it has none.
    """
    if not app.port:
        return None
    if app.zero_downtime and app.active_color in BLUE_GREEN_COLORS:
        return color_port(app, app.active_color)
    return app.port


def drain_of(app: App) -> int:
    """
    Say how long the old instance keeps running after traffic moved.

    Args:
        app: The application.

    Returns:
        Seconds: its own setting, or :data:`DEFAULT_DRAIN_SECONDS`.
    """
    return app.drain_seconds if app.drain_seconds is not None else DEFAULT_DRAIN_SECONDS


def instance_command(command: str, *, app_path: Path, port: int, app_type: str) -> str:
    """
    Turn an application's ExecStart into one that works for either instance.

    The unit of an application on releases runs from ``current`` and, for
    some types, names its port on the command line (gunicorn's ``-b
    0.0.0.0:3000``). Two instances run two releases on two ports, so the
    path to ``current`` becomes the instance's own directory (``%i`` is the
    instance) and the port becomes ``${PORT}``, which systemd expands from
    the instance's environment file. Node and Next.js read ``PORT`` already;
    ``vite preview`` reads only its own flag, which is added.

    Args:
        command: The ExecStart of the application's own unit.
        app_path: The application directory.
        port: The port the command was written for.
        app_type: The application's type.

    Returns:
        The command for the template.
    """
    runtime = re.escape(str(app_path / CURRENT_LINK))
    colors = str(app_path / "colors").replace("%", "%%")
    result = re.sub(rf"{runtime}(?=/|\s|$)", lambda _match: f"{colors}/%i", command)

    literal = str(int(port))
    # host:port in a bind address, and the flags that take a port.
    result = re.sub(rf"(?<=[\w.\]]):{literal}(?!\d)", ":${PORT}", result)
    result = re.sub(rf"(?<=\s)(-p|--port|-b|--bind)(\s+|=){literal}(?!\d)", r"\1\2${PORT}", result)
    result = re.sub(rf"(?<![\w$])PORT={literal}(?!\d)", "PORT=${PORT}", result)

    if app_type == AppType.VITE.value and "--port" not in result:
        # npm needs the separator to hand the flag to the script.
        separator = " --" if re.search(r"(^|/)npm\s+run\s", result) else ""
        result = f"{result}{separator} --port ${{PORT}} --strictPort"
    return result


# ---------------------------------------------------------------------------
# Who may use it
# ---------------------------------------------------------------------------


def check_eligible(
    app: App,
    *,
    store: WASMStore | None = None,
    port_free: Callable[[int], bool] | None = None,
) -> None:
    """
    Refuse an application that cannot run as two instances, and say why.

    Args:
        app: The application.
        store: Where the other applications and the site are read.
        port_free: Tells whether nothing listens on a port. None skips the
            check, for a read that must not open sockets.

    Raises:
        ValidationError: With what to do instead.
    """
    store = store if store is not None else get_store()
    domain = app.domain
    if app.webserver != WebServer.NGINX.value:
        raise ValidationError(
            f"{domain} is served by {app.webserver}; blue/green is nginx-only in 2.2",
            details="Zero-downtime activation switches an nginx upstream. Apache "
            "applications keep activating with a restart.",
            field="enabled",
        )
    if app.is_static or app.app_type == AppType.STATIC.value:
        raise ValidationError(
            f"{domain} is a static site; there is no process to run twice",
            details="Its files are served straight from disk: switching releases is "
            "already instant.",
            field="enabled",
        )
    if app.app_type in _IN_PLACE_TYPES:
        raise ValidationError(
            f"{domain} is a {app.app_type} application, which deploys in place",
            details="Only single-process applications on the release layout can run as "
            "blue and green instances.",
            field="enabled",
        )
    if app.layout != RELEASES:
        raise ValidationError(
            f"{domain} is deployed in place; blue/green runs two releases side by side",
            details=f"Move it onto releases first: wasm app migrate {domain}",
            field="enabled",
        )
    if not app.port:
        raise ValidationError(
            f"{domain} has no port recorded",
            details=f"Redeploy it with an explicit port: wasm create -d {domain} --port ...",
            field="enabled",
        )
    if len(unit_base(app)) + _INSTANCE_SUFFIX > MAX_SERVICE_NAME_LENGTH:
        raise ValidationError(
            f"The unit name of {domain} is too long for its instances",
            details=f"{unit_base(app)}@green would be longer than "
            f"{MAX_SERVICE_NAME_LENGTH} characters.",
            field="enabled",
        )
    current = app_root(app) / CURRENT_LINK
    advanced = NginxConfigBuilder().detect(current) if current.is_dir() else None
    if advanced is not None:
        raise ValidationError(
            f"The site of {domain} has its own routes ({advanced.name})",
            details="Blue/green switches the one upstream of the proxy site. A site "
            "with routes of its own proxies to ports blue and green cannot share.",
            field="enabled",
        )
    green = color_port(app, GREEN)
    if green > 65535:
        raise ValidationError(
            f"{domain} listens on {app.port}; the green instance would need {green}",
            details="Move the application to a lower port first.",
            field="enabled",
        )
    for other in store.list_apps():
        if other.domain == domain or not other.port:
            continue
        taken = {other.port, color_port(other, GREEN)} if other.zero_downtime else {other.port}
        if green in taken:
            raise ValidationError(
                f"Port {green}, which the green instance of {domain} needs, is {other.domain}'s",
                details=f"Move one of the two applications to another port. Blue runs on "
                f"{app.port} and green on the port after it.",
                field="enabled",
            )
    for service in store.list_services():
        if service.port == green and service.app_id != app.id:
            raise ValidationError(
                f"Port {green}, which the green instance of {domain} needs, is the unit "
                f"{service.name}'s",
                details="Move that unit, or this application, to another port.",
                field="enabled",
            )
    if port_free is not None and not port_free(green):
        raise ValidationError(
            f"Port {green}, which the green instance of {domain} needs, is in use",
            details=f"Something already listens on 127.0.0.1:{green}. Find it with "
            f"'ss -ltnp sport = :{green}' and move it, or move {domain} to another port.",
            field="enabled",
        )


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Switch:
    """
    What one blue/green activation did.

    Attributes:
        domain: The application's domain.
        release_id: The release that serves now.
        from_color: The instance that served before, stopped after the drain.
        to_color: The instance that serves now.
        port: Its port.
    """

    domain: str
    release_id: str
    from_color: str
    to_color: str
    port: int


class BlueGreen:
    """
    The two instances of one application, and the switch between them.

    Every method expects the caller to hold the application's lock.
    """

    def __init__(
        self,
        app: App,
        *,
        logger: Logger,
        store: WASMStore | None = None,
        services: ServiceManager | None = None,
        web: WebServerManager | None = None,
        probe: Probe | None = None,
        sleep: Callable[[float], None] | None = None,
        refresh_site: SiteRefresher | None = None,
        port_free: Callable[[int], bool] | None = None,
    ) -> None:
        """
        Initialize the engine.

        Args:
            app: The application's row.
            logger: Where each step is reported.
            store: The store. Defaults to the process-wide one.
            services: The service manager. Defaults to a new one.
            web: The nginx manager. Defaults to a new one.
            probe: The HTTP probe the health gate runs. Defaults to
                :func:`~wasm.deployers.helpers.health.wait_until_healthy`.
            sleep: How the drain waits. Defaults to :func:`time.sleep`.
            refresh_site: Renders the site again from the store and reloads
                nginx, putting the old file back when nginx refuses the new
                one. Defaults to the deployer's own rendering.
            port_free: Tells whether nothing listens on a port.
        """
        self.app = app
        self.log = logger
        self.store = store if store is not None else get_store()
        self.services = services if services is not None else ServiceManager()
        if web is None:
            from wasm.managers.nginx_manager import NginxManager

            web = NginxManager()
        self.web = web
        self._probe = probe if probe is not None else wait_until_healthy
        self._sleep = sleep if sleep is not None else time.sleep
        self._refresh_site = refresh_site if refresh_site is not None else _refresh_site
        self._port_free = port_free if port_free is not None else is_port_available

    # -- Names -------------------------------------------------------------

    @property
    def base(self) -> str:
        """The unit name the template is made from."""
        return unit_base(self.app)

    def unit(self, color: str) -> str:
        """
        Name one instance.

        Args:
            color: ``blue`` or ``green``.

        Returns:
            The instance's unit name.
        """
        return instance_unit(self.app, color)

    def port(self, color: str) -> int:
        """
        Say which port one instance listens on.

        Args:
            color: ``blue`` or ``green``.

        Returns:
            The port.
        """
        return color_port(self.app, color)

    def releases(self) -> ReleaseManager:
        """
        Return the application's releases.

        Returns:
            A manager of its directory, reporting through this engine's logger.
        """
        return ReleaseManager(app_root(self.app), logger=self.log)

    # -- Activation --------------------------------------------------------

    def activate(self, release: Path, releases: ReleaseManager) -> Switch:
        """
        Make a release serve without a moment in which nothing does.

        Args:
            release: The release directory.
            releases: The application's releases.

        Returns:
            What was switched.

        Raises:
            RolledBackError: The release did not answer on the idle instance,
                or nginx refused or failed to load the switch; the instance
                that was serving still serves, and answers.
            DeploymentError: The same, and the instance that was serving does
                not answer either; or no instance serves at all.
        """
        serving = self.app.active_color
        if serving not in BLUE_GREEN_COLORS:
            raise DeploymentError(
                f"{self.app.domain} is in zero-downtime mode but no instance is recorded as serving",
                details=f"Turn the mode off and on again: wasm app zero-downtime "
                f"{self.app.domain} off, then on.",
            )
        target = other_color(serving)
        port = self.port(target)
        unit = self.unit(target)
        release_id = release.name

        if is_rehearsal():
            # Nothing is started, so there is nothing to probe: a rehearsal
            # says what the switch would do and changes nothing.
            self.log.substep(
                f"Would start release {release_id} on the {target} instance (port {port}), "
                f"switch nginx to it and stop {serving} after {drain_of(self.app)}s"
            )
            return Switch(
                domain=self.app.domain,
                release_id=release_id,
                from_color=serving,
                to_color=target,
                port=port,
            )

        self.log.substep(f"Starting release {release_id} on the {target} instance (port {port})")
        releases.point_color(target, release, port=port)
        healthy, evidence = self._gate(target).restart_and_probe()
        if not healthy:
            self._stop(unit)
            raise self._kept_serving(
                f"Release {release_id} did not pass its health check on the {target} instance",
                evidence,
                serving,
            )

        self._switch_upstream(target, unit, serving)
        self._record_color(target)
        self._enable_for_boot(target, serving)

        drain = drain_of(self.app)
        self.log.substep(
            f"nginx sends new requests to {target} (port {port}); "
            f"{serving} finishes what it has for {drain}s"
        )
        if drain:
            self._sleep(drain)
        self._stop(self.unit(serving))

        releases.activate(release)
        self.log.substep(f"Release {release_id} serves from the {target} instance")
        return Switch(
            domain=self.app.domain,
            release_id=release_id,
            from_color=serving,
            to_color=target,
            port=port,
        )

    def _gate(self, color: str) -> HealthGate:
        """
        Build the health gate one instance must pass before it serves.

        Args:
            color: The instance.

        Returns:
            The gate: the application's own check, asked on the instance's port.
        """
        unit = self.unit(color)
        check = HealthCheck.for_app(self.app)
        return HealthGate(
            unit=unit,
            url=check.url(self.port(color)),
            check=check,
            services=self.services,
            logger=self.log,
            probe=self._probe,
            # Restart, not start: an instance left running (a crash loop, a
            # switch interrupted) starts over on the release it was given.
            restart=lambda: self.services.restart(unit),
        )

    def _switch_upstream(self, target: str, unit: str, serving: str) -> None:
        """
        Point nginx at an instance that already answers.

        Args:
            target: The instance to serve.
            unit: Its unit, stopped again if nginx will not switch to it.
            serving: The instance serving until now.

        Raises:
            RolledBackError: nginx refused the configuration or did not
                reload; the old upstream is back and the old instance serves.
            DeploymentError: The same, and the old instance does not answer.
        """
        domain = self.app.domain
        previous = self.web.write_upstream(domain, self.port(target))
        problem = self.web.config_errors()
        if problem is None and self.web.reload():
            return

        self.web.restore_upstream(domain, previous)
        if problem is None:
            # The configuration was valid and the reload still failed: load
            # the old one again so what nginx holds matches the disk.
            self.web.reload()
        self._stop(unit)
        raise self._kept_serving(
            f"nginx did not switch {domain} to the {target} instance",
            problem
            if problem is not None
            else "The configuration is valid but nginx did not reload; see why with: "
            "systemctl status nginx",
            serving,
        )

    def _record_color(self, color: str) -> None:
        """
        Record which instance serves.

        Args:
            color: The instance.
        """
        if not is_rehearsal():
            self.store.set_active_color(self.app.domain, color)
        self.app.active_color = color

    def _enable_for_boot(self, color: str, previous: str | None) -> None:
        """
        Make the serving instance the one that starts at boot.

        A failure is reported, not raised: traffic has already moved, and
        what starts at boot can be fixed without a new activation.

        Args:
            color: The instance that serves now.
            previous: The one that served before, if any.
        """
        try:
            self.services.enable(self.unit(color))
            if previous is not None:
                self.services.disable(self.unit(previous))
        except WASMError as exc:
            self.log.warning(f"The {color} instance may not start at boot: {exc}")

    def _stop(self, unit: str) -> None:
        """
        Stop an instance, reporting rather than raising a failure.

        Args:
            unit: The instance.
        """
        try:
            if not self.services.stop(unit):
                self.log.warning(f"systemd did not confirm that {unit} stopped")
        except WASMError as exc:
            self.log.warning(f"Could not stop {unit}: {exc}")

    def _kept_serving(self, message: str, evidence: str, serving: str) -> DeploymentError:
        """
        Build the error of an activation the serving instance survived.

        Args:
            message: What failed.
            evidence: The gate's or nginx's own output.
            serving: The instance that kept serving.

        Returns:
            A :class:`RolledBackError` when that instance still answers, a
            plain :class:`DeploymentError` when it does not.
        """
        port = self.port(serving)
        check = HealthCheck.for_app(self.app)
        answers = self._probe(
            check.url(port),
            retries=_STILL_SERVING_ATTEMPTS,
            delay=_STILL_SERVING_DELAY,
            on_attempt=self.log.debug,
            accept=check.accepts,
        )
        if answers:
            return RolledBackError(
                f"{message}; the {serving} instance kept serving", details=evidence
            )
        return DeploymentError(
            f"{message}; the {serving} instance kept the traffic but is not answering either",
            details=f"{evidence}\n\nCheck it with: wasm diagnose {self.app.domain}",
        )

    # -- The template ------------------------------------------------------

    def write_template(self, *, command: str, environment: dict[str, str]) -> str | None:
        """
        Write the template both instances run from.

        Args:
            command: The ExecStart of the application's own unit, written for
                ``current`` and the application's port; it is turned into
                one for either instance here.
            environment: The variables the application's unit sets inline.
                PORT is left out: each instance has its own.

        Returns:
            The previous template, or None when there was none.
        """
        root = app_root(self.app)
        return self.services.install_instance_template(
            self.base,
            command=instance_command(
                command,
                app_path=root,
                port=int(self.app.port or 0),
                app_type=self.app.app_type,
            ),
            colors_directory=str(self.releases().colors_dir),
            environment={key: value for key, value in environment.items() if key != "PORT"},
            environment_file=str(env_file_for(self.app)),
            description=f"WASM: {self.app.domain} ({self.app.app_type})",
            limits=ResourceLimits.of(self.app),
        )

    # -- Turning the mode on and off -----------------------------------------

    def enable(self, *, drain_seconds: int | None) -> None:
        """
        Move an application from its own unit to two instances, without a cut.

        The template is written, the active release is started on the green
        instance (blue's port is the application's, which its own unit still
        holds), the upstream and the site are switched to it, and the old
        unit is stopped after the drain. Any failure before the switch
        completes puts everything back: the old unit never stopped serving.

        Args:
            drain_seconds: Seconds the old unit keeps running after traffic
                moved, and every later activation's; None for the default.

        Raises:
            ValidationError: The application cannot run as two instances.
            DeploymentError: A step failed; everything was put back.
        """
        app = self.app
        check_eligible(app, store=self.store, port_free=self._port_free)
        releases = self.releases()
        active = releases.current()
        if active is None:
            raise DeploymentError(
                f"{app.domain} has no active release to start the instances on",
                details=f"Build one first: wasm update {app.domain}",
            )
        service = self.store.get_service_by_app_id(app.id) if app.id is not None else None
        if service is None:
            raise DeploymentError(
                f"{app.domain} has no unit on record to run as two instances",
                details=f"Redeploy it so its unit is recorded: wasm update {app.domain}",
            )

        undo: list[Callable[[], None]] = []
        try:
            self.log.substep(f"Writing the template {self.base}@.service")
            previous_template = self.write_template(
                command=service.command, environment=dict(service.environment)
            )
            undo.append(lambda: self.services.restore_template(self.base, previous_template))

            undo.append(releases.remove_colors)
            for color in BLUE_GREEN_COLORS:
                releases.point_color(color, active.path, port=self.port(color))

            green = self.unit(GREEN)
            self.log.substep(f"Starting release {active.id} on the green instance")
            healthy, evidence = self._gate(GREEN).restart_and_probe()
            undo.append(lambda: self._stop(green))
            if not healthy:
                raise DeploymentError(
                    f"Release {active.id} did not answer on the green instance; "
                    f"{app.domain} keeps running as {service.name}",
                    details=evidence,
                )

            previous_upstream = self.web.write_upstream(app.domain, self.port(GREEN))
            undo.append(lambda: self.web.restore_upstream(app.domain, previous_upstream))

            def mode_off() -> None:
                self.store.set_zero_downtime(app.domain, False)
                app.zero_downtime, app.active_color = False, None

            def site_back() -> None:
                # Rendered again rather than trusted: a reload that failed
                # leaves the new site on disk, and it includes the upstream
                # file about to be removed.
                mode_off()
                self._refresh_site(app)

            self.store.set_zero_downtime(app.domain, True, drain_seconds=drain_seconds)
            self.store.set_active_color(app.domain, GREEN)
            app.zero_downtime, app.active_color = True, GREEN
            app.drain_seconds = drain_seconds
            undo.append(mode_off)
            undo.append(site_back)

            self.log.substep("Switching the site to the instances' upstream")
            self._refresh_site(app)
        except WASMError:
            self._undo(undo)
            raise

        self._enable_for_boot(GREEN, None)
        drain = drain_of(app)
        if drain:
            self.log.substep(f"{service.name} finishes what it has for {drain}s")
            self._sleep(drain)
        self._retire(service.name)

    def disable(self) -> None:
        """
        Move an application from two instances back to its own unit, without a cut.

        The application's unit listens on the application's port, which is
        blue's: when blue serves, green takes over first. Then the unit is
        written and started on the active release, the site goes back to
        proxying to it directly, and after the drain both instances, the
        template, the instance links and the upstream are removed. Any
        failure before the switch completes leaves the instances serving.

        Raises:
            DeploymentError: A step failed; the instances still serve.
        """
        app = self.app
        releases = self.releases()
        active = releases.current()
        if active is None:
            raise DeploymentError(
                f"{app.domain} has no active release to run its own unit on",
                details=f"Build one first: wasm update {app.domain}",
            )
        service = self.store.get_service_by_app_id(app.id) if app.id is not None else None
        if service is None:
            raise DeploymentError(
                f"{app.domain} has no unit on record to go back to",
                details=f"Redeploy it: wasm update {app.domain}",
            )

        if app.active_color != GREEN:
            # Blue holds the application's own port.
            self.log.substep("Moving traffic to the green instance first: blue holds the port")
            self.activate(active.path, releases)

        # The application's own name: a legacy wasm- row is not a name a
        # unit is created under any more.
        name = self.base
        undo: list[Callable[[], None]] = []
        try:
            if self.services.service_exists(name):
                # Left by a switch that did not finish; it runs nothing.
                self.services.delete_service(name, keep_record=True)
            self.log.substep(f"Writing {name}.service to run release {active.id}")
            self.services.create_service(
                name=name,
                command=service.command,
                working_directory=service.working_directory,
                user=service.user,
                group=service.group,
                environment=dict(service.environment),
                environment_file=str(env_file_for(app)),
                description=f"WASM: {app.domain} ({app.app_type})",
                limits=ResourceLimits.of(app),
            )
            undo.append(lambda: self.services.delete_service(name, keep_record=True))

            check = HealthCheck.for_app(app)
            gate = HealthGate(
                unit=name,
                url=check.url(app.port),
                check=check,
                services=self.services,
                logger=self.log,
                probe=self._probe,
            )
            healthy, evidence = gate.restart_and_probe()
            if not healthy:
                raise DeploymentError(
                    f"Release {active.id} did not answer as {name}; "
                    f"the {app.active_color} instance keeps serving",
                    details=evidence,
                )

            serving = app.active_color
            drain = app.drain_seconds

            def mode_on() -> None:
                self.store.set_zero_downtime(app.domain, True, drain_seconds=drain)
                self.store.set_active_color(app.domain, serving)
                app.zero_downtime, app.active_color = True, serving

            def site_back() -> None:
                mode_on()
                self._refresh_site(app)

            self.store.set_zero_downtime(app.domain, False)
            app.zero_downtime, app.active_color = False, None
            undo.append(site_back)
            self.log.substep(f"Switching the site back to {name}")
            self._refresh_site(app)
        except WASMError:
            self._undo(undo)
            raise

        try:
            self.services.enable(name)
        except WASMError as exc:
            self.log.warning(f"{name} may not start at boot: {exc}")
        wait = drain if drain is not None else DEFAULT_DRAIN_SECONDS
        if wait:
            self.log.substep(f"The instances finish what they have for {wait}s")
            self._sleep(wait)
        for warning in self.teardown():
            self.log.warning(warning)

    def teardown(self) -> list[str]:
        """
        Remove both instances, the template, the instance links and the upstream.

        Each step is attempted whatever the one before did, as a deletion
        does; the upstream only goes once the site no longer includes it.

        Returns:
            What could not be removed, and why.
        """
        warnings: list[str] = []
        for color in BLUE_GREEN_COLORS:
            try:
                self.services.delete_service(self.unit(color), keep_record=True)
            except WASMError as exc:
                warnings.append(f"The {color} instance was not removed: {exc}")
        steps: list[tuple[str, Callable[[], object]]] = [
            (
                f"The template {self.base}@.service",
                lambda: self.services.remove_template(self.base),
            ),
            ("The instance links", self.releases().remove_colors),
        ]
        if not self.web.site_exists(self.app.domain) or not self._site_includes_upstream():
            steps.append(("The upstream file", lambda: self.web.remove_upstream(self.app.domain)))
        for what, step in steps:
            try:
                step()
            except (WASMError, OSError) as exc:
                warnings.append(f"{what} was not removed: {exc}")
        return warnings

    def _site_includes_upstream(self) -> bool:
        """
        Tell whether the site on disk still includes the upstream file.

        Returns:
            True when it names it: removing the file would make the next
            ``nginx -t`` fail.
        """
        config = self.web.get_site_config(self.app.domain) or ""
        return str(self.web.upstream_path(self.app.domain)) in config

    def _retire(self, name: str) -> None:
        """
        Stop, disable and remove the application's own unit, keeping its row.

        Args:
            name: The unit.
        """
        try:
            self.services.delete_service(name, keep_record=True)
        except WASMError as exc:
            self.log.warning(f"{name} was left in place: {exc}")

    def _undo(self, steps: list[Callable[[], None]]) -> None:
        """
        Put back what a failed switch changed, newest first.

        Every step is attempted; one that fails is reported, and the error
        that caused the undo is what the caller raises.

        Args:
            steps: The undo of each step that ran, oldest first.
        """
        for step in reversed(steps):
            try:
                step()
            except (WASMError, OSError) as exc:
                self.log.warning(f"Could not put everything back: {exc}")


def _refresh_site(app: App) -> None:
    """
    Render an application's site again from the store, and reload nginx.

    The site is rendered the way a deploy renders it; the web server
    manager decides from the store whether it proxies to the upstream.

    Args:
        app: The application.

    Raises:
        ValidationError: nginx refused the new site; the old file is back.
        DeploymentError: nginx did not reload.
    """
    # Imported here: the domains module builds deployers, which build this.
    from wasm.deployers.domains import _serves_tls, _site_deployer

    deployer = _site_deployer(app, verbose=False)
    deployer.refresh_site(with_ssl=_serves_tls(app, deployer))


# ---------------------------------------------------------------------------
# What the CLI and the API call
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstanceStatus:
    """
    One instance of an application in zero-downtime mode.

    Attributes:
        color: ``blue`` or ``green``.
        unit: Its unit name.
        port: The port it listens on.
        release: The release it runs, or None.
        serving: Whether nginx sends it the traffic.
        state: systemd's ActiveState, or ``unknown``.
    """

    color: str
    unit: str
    port: int
    release: str | None
    serving: bool
    state: str


@dataclass(frozen=True)
class ZeroDowntimeStatus:
    """
    Whether an application activates without a cut, and how.

    Attributes:
        domain: The application's domain.
        enabled: Whether it runs as two instances.
        active_color: The instance that serves, when enabled.
        drain_seconds: Seconds the old instance keeps running after a switch.
        instances: Both instances, when enabled.
        upstream_port: The port nginx's upstream names, when there is one.
        eligible: Whether the mode can be turned on (always True when on).
        reason: Why it cannot, when it cannot.
        hint: What to do about it.
    """

    domain: str
    enabled: bool
    active_color: str | None
    drain_seconds: int
    instances: tuple[InstanceStatus, ...]
    upstream_port: int | None
    eligible: bool
    reason: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class ModeChange:
    """
    What :func:`set_zero_downtime` did.

    Attributes:
        domain: The application's domain.
        enabled: Whether the mode is on now.
        active_color: The instance that serves, when it is.
        drain_seconds: The drain now recorded.
        changed: False when the application already was as asked.
        rehearsed: True under ``--dry-run``: nothing was changed.
    """

    domain: str
    enabled: bool
    active_color: str | None
    drain_seconds: int
    changed: bool
    rehearsed: bool = False


def _known_app(domain: str, store: WASMStore) -> App:
    """
    Read an application's row.

    Args:
        domain: A validated domain.
        store: The store.

    Returns:
        The row.

    Raises:
        WASMError: Nothing is deployed there.
    """
    app = store.get_app(domain)
    if app is None:
        raise WASMError(
            f"Application not found: {domain}", details="Run 'wasm list' to see what is deployed."
        )
    return app


def zero_downtime_status(
    domain: str, *, services: ServiceManager | None = None, web: WebServerManager | None = None
) -> ZeroDowntimeStatus:
    """
    Describe an application's zero-downtime mode. Changes nothing.

    Args:
        domain: The application's domain.
        services: The service manager, for the instances' states.
        web: The nginx manager, for the upstream.

    Returns:
        The mode, the instances and, when off, whether it could be turned on.

    Raises:
        WASMError: The application is unknown.
    """
    store = get_store()
    app = _known_app(validate_domain(domain), store)
    if not app.zero_downtime:
        try:
            check_eligible(app, store=store)
        except ValidationError as exc:
            return ZeroDowntimeStatus(
                domain=app.domain,
                enabled=False,
                active_color=None,
                drain_seconds=drain_of(app),
                instances=(),
                upstream_port=None,
                eligible=False,
                reason=exc.message,
                hint=exc.details or None,
            )
        return ZeroDowntimeStatus(
            domain=app.domain,
            enabled=False,
            active_color=None,
            drain_seconds=drain_of(app),
            instances=(),
            upstream_port=None,
            eligible=True,
        )

    services = services if services is not None else ServiceManager()
    if web is None:
        from wasm.managers.nginx_manager import NginxManager

        web = NginxManager()
    releases = ReleaseManager(app_root(app))
    instances = []
    for color in BLUE_GREEN_COLORS:
        unit = instance_unit(app, color)
        try:
            state = str(services.get_status(unit).get("active_state") or "unknown")
        except WASMError as exc:
            state = "unknown"
            services.logger.debug(f"Could not read the state of {unit}: {exc}")
        release = releases.color_release(color)
        instances.append(
            InstanceStatus(
                color=color,
                unit=unit,
                port=color_port(app, color),
                release=release.id if release is not None else None,
                serving=color == app.active_color,
                state=state,
            )
        )
    return ZeroDowntimeStatus(
        domain=app.domain,
        enabled=True,
        active_color=app.active_color,
        drain_seconds=drain_of(app),
        instances=tuple(instances),
        upstream_port=web.upstream_port(app.domain),
        eligible=True,
    )


def set_zero_downtime(
    domain: str,
    enabled: bool,
    *,
    drain_seconds: int | None = None,
    logger: Logger | None = None,
    engine: Callable[[App, Logger], BlueGreen] | None = None,
) -> ModeChange:
    """
    Turn an application's zero-downtime mode on or off, or change its drain.

    The one way in for the CLI and the API. Turning it on or off is a switch
    without a cut, undone whole when a step fails (see :meth:`BlueGreen.enable`
    and :meth:`BlueGreen.disable`); changing the drain of an application
    already in the mode only records it. Under ``--dry-run`` the request is
    checked as it would be and nothing changes.

    Args:
        domain: The application's domain.
        enabled: The mode wanted.
        drain_seconds: Seconds the old instance keeps running after a switch,
            0 to 300. None keeps what the application has.
        logger: Where the steps are reported.
        engine: Builds the engine for the application; tests replace it.

    Returns:
        What was done.

    Raises:
        WASMError: The application is unknown.
        ValidationError: The application cannot use the mode, or the drain is
            out of range.
        DeploymentError: A step failed; the application serves as it did.
        AppBusyError: Another operation is running on the application.
    """
    log = logger if logger is not None else Logger()
    store = get_store()
    domain = validate_domain(domain)
    if drain_seconds is not None and not 0 <= drain_seconds <= MAX_DRAIN_SECONDS:
        raise ValidationError(
            f"Cannot drain for {drain_seconds} seconds",
            details=f"Drain from 0 to {MAX_DRAIN_SECONDS} seconds.",
            field="drain_seconds",
        )
    build = engine if engine is not None else (lambda row, out: BlueGreen(row, logger=out))

    with app_lock(domain, "zero-downtime switch"):
        app = _known_app(domain, store)
        drain = drain_seconds if drain_seconds is not None else app.drain_seconds

        if app.zero_downtime == enabled:
            changed = enabled and drain != app.drain_seconds
            if changed and not is_rehearsal():
                store.set_zero_downtime(app.domain, True, drain_seconds=drain)
            return ModeChange(
                domain=app.domain,
                enabled=enabled,
                active_color=app.active_color,
                drain_seconds=drain if drain is not None else DEFAULT_DRAIN_SECONDS,
                changed=changed,
                rehearsed=is_rehearsal(),
            )

        if is_rehearsal():
            if enabled:
                check_eligible(app, store=store, port_free=is_port_available)
                log.info(
                    f"Would start {instance_unit(app, GREEN)} on port {color_port(app, GREEN)}, "
                    f"switch the site to it and stop {unit_base(app)}"
                )
            else:
                log.info(
                    f"Would start {unit_base(app)} on port {app.port}, switch the site to it "
                    "and remove both instances"
                )
            return ModeChange(
                domain=app.domain,
                enabled=enabled,
                active_color=GREEN if enabled else None,
                drain_seconds=drain if drain is not None else DEFAULT_DRAIN_SECONDS,
                changed=True,
                rehearsed=True,
            )

        bluegreen = build(app, log)
        if enabled:
            bluegreen.enable(drain_seconds=drain)
            log.success(f"{app.domain} activates without downtime; green serves")
        else:
            bluegreen.disable()
            log.success(f"{app.domain} runs as {unit_base(app)} again")
        return ModeChange(
            domain=app.domain,
            enabled=enabled,
            active_color=bluegreen.app.active_color if enabled else None,
            drain_seconds=drain if drain is not None else DEFAULT_DRAIN_SECONDS,
            changed=True,
        )
