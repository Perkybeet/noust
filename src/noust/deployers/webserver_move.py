# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving sites from one web server to the other, and back if any of them does not answer (item 105).

:func:`~noust.deployers.webserver_move_plan.plan_move` says what would happen; :func:`move` does it,
and the rules are the in-place migration's (:mod:`noust.deployers.migrate`) applied to a web server:

- **Nothing is touched until the new server has said yes.** The modules the sites need are
  enabled, then the new server tests each translated site inside its own live configuration
  (:meth:`~noust.managers.webserver.WebServerManager.test_config_text`). A site it refuses ends the
  move with the server's own message, before a file is written.
- **The old site stays until the new one has answered.** The new files are written and enabled
  beside the old ones; in a handover the old server is stopped, the new one started, and every
  site is asked for through the new server, with the application's own health check, over HTTP and
  over TLS with the name in the handshake. Only then is the store updated and the old files
  retired.
- **A move that does not come up is undone.** Every step has its undo, run in reverse even when an
  earlier one fails; what could not be put back is said, with where it is.
- **A site the operator wrote is never rewritten or deleted.** One whose equivalent the operator
  already wrote for the new server is moved without either file being touched (the old one is only
  disabled).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.exceptions import (
    ConfirmationRequired,
    NoustError,
    ValidationError,
    WebserverMoveError,
)
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.core.server_text import ServerText, say
from noust.core.store import App, NoustStore, Site, get_store
from noust.deployers.helpers.health import wait_until_command_succeeds, wait_until_healthy
from noust.deployers.helpers.health_gate import HealthCheck, HealthGate, Probe
from noust.deployers.helpers.served_probe import https_check
from noust.deployers.webserver_move_plan import (
    Managers,
    MovePlan,
    SitePlan,
    display,
    plan_move,
)
from noust.managers.service_manager import ServiceManager
from noust.managers.webserver import WebServerManager, config_serves_tls

_log = logging.getLogger(__name__)

#: A site file is world-readable, like every file a web server ships with.
_SITE_MODE = 0o644

#: Seconds systemctl gets to stop or start a web server.
_UNIT_TIMEOUT = 60

#: The web server's plain HTTP port on this machine.
HTTP_PORT = 80

#: How a site is asked over TLS: given the name, the path and which statuses mean up, the check the
#: health gate waits on (:func:`~noust.deployers.helpers.served_probe.https_check`).
TlsCheck = Callable[[str, str, Callable[[int], bool]], Callable[[float], tuple[bool, str]]]


@dataclass(frozen=True)
class MovedSite:
    """
    One site that moved.

    Attributes:
        domain: Its domain.
        kind: ``app`` or ``site``.
        written: Whether a file was written for it (not when the operator's own was kept).
        target_path: Its file now.
        retired: Whether the old file was removed (an operator's is only disabled).
    """

    domain: str
    kind: str
    written: bool
    target_path: str
    retired: bool


@dataclass(frozen=True)
class MoveResult:
    """
    What :func:`move` did.

    Attributes:
        source: The web server the sites were behind.
        target: The one they are behind now.
        mode: ``live`` or ``handover``.
        sites: Each site that moved.
        modules: The Apache modules that were enabled for them.
        losses: How many things the new sites do not do that the old ones did, accepted.
        warnings: What did not go as it should, without undoing the move (a file that could not be
            removed).
        rehearsed: True under ``--dry-run``: nothing changed.
    """

    source: str
    target: str
    mode: str
    sites: tuple[MovedSite, ...]
    modules: tuple[str, ...] = ()
    losses: int = 0
    warnings: tuple[str, ...] = ()
    rehearsed: bool = False


def require_ready(plan: MovePlan, *, accept_losses: bool, confirm: bool) -> None:
    """
    Refuse a plan that cannot go ahead as it stands.

    Args:
        plan: What :func:`~noust.deployers.webserver_move_plan.plan_move` said.
        accept_losses: Whether the operator accepted what the new sites will not do.
        confirm: Whether the operator agreed to the move itself.

    Raises:
        ValidationError: There is nothing to move.
        WebserverMoveError: Something stops the move; the details name each thing.
        ConfirmationRequired: The operator has not accepted the losses or agreed to the move yet.
    """
    if plan.empty:
        raise ValidationError(
            "There is nothing to move",
            details=f"Nothing is behind {display(plan.source)}"
            + (
                f"; already behind {display(plan.target)}: {', '.join(plan.skipped)}"
                if plan.skipped
                else ""
            )
            + ".",
            field="domains",
        )
    if plan.blocked:
        raise blocked_error(plan)
    losses = plan.losses
    if losses and not accept_losses:
        warnings = [f"{domain}: {finding.sentence.text}" for domain, finding in losses]
        raise ConfirmationRequired(
            warnings,
            field="accept_losses",
            coded=say("webserver_move.losses_to_accept", count=len(losses)),
        )
    if not confirm:
        confirmation = plan.confirmation()
        raise ConfirmationRequired([confirmation.text], field="confirm", coded=confirmation)


def blocked_error(plan: MovePlan) -> WebserverMoveError:
    """
    Say everything that stops a plan.

    Args:
        plan: A plan that is blocked.

    Returns:
        The error: the first reason as its message and every reason in its details.
    """
    reasons: list[ServerText] = list(plan.blockers)
    for site in plan.sites:
        reasons.extend(site.blockers)
        if site.translation is not None:
            reasons.extend(
                finding.sentence
                for finding in site.translation.findings
                if finding.severity == "blocks"
            )
    first = reasons[0]
    lines = [f"- {item.text}" + (f" {item.hint}" if item.hint else "") for item in reasons]
    return WebserverMoveError(first.text, details="\n".join(lines), coded=first)


class _Undo:
    """The steps of a move that can be taken back, and the ones that could not be."""

    def __init__(self, log: Logger) -> None:
        self._log = log
        self._steps: list[tuple[str, Callable[[], object]]] = []
        self.failures: list[str] = []

    def record(self, description: str, undo: Callable[[], object]) -> None:
        """
        Remember how to take a step back.

        Args:
            description: What was done.
            undo: What puts it back.
        """
        self._steps.append((description, undo))

    def run(self) -> None:
        """Take every step back, newest first, whatever an earlier one does."""
        while self._steps:
            description, undo = self._steps.pop()
            try:
                undo()
            except (NoustError, OSError, sqlite3.Error) as exc:
                self.failures.append(f"{description}: {exc}")
                self._log.warning(f"Could not put back: {description}: {exc}")

    def report(self) -> str:
        """
        Returns:
            What could not be put back, one per line; empty when everything was.
        """
        return "\n".join(f"- {item}" for item in self.failures)


def move(
    plan: MovePlan,
    *,
    accept_losses: bool = False,
    confirm: bool = False,
    managers: Managers | None = None,
    store: NoustStore | None = None,
    logger: Logger | None = None,
    probe: Probe | None = None,
    tls: TlsCheck | None = None,
) -> MoveResult:
    """
    Move the sites of a plan to the other web server.

    Args:
        plan: What :func:`~noust.deployers.webserver_move_plan.plan_move` said.
        accept_losses: Go ahead although the new sites will not do everything the old ones did.
        confirm: Go ahead with the move itself.
        managers: The two web servers (tests); the real ones by default.
        store: The store; the process-wide one by default.
        logger: Where progress is reported.
        probe: How a site is asked over HTTP (tests); the health gate's by default.
        tls: How a site is asked over TLS (tests); a real connection by default.

    Returns:
        What was done.

    Raises:
        ValidationError: There is nothing to move.
        ConfirmationRequired: The losses or the move are not agreed to yet.
        WebserverMoveError: Something stops the move, the new server refused a site, did not start,
            or a site did not answer through it. Everything was put back; the details say what, if
            anything, could not be.
        AppBusyError: Another operation is running on one of the applications.
    """
    require_server_role("Sites")
    log = logger or Logger(verbose=False)
    require_ready(plan, accept_losses=accept_losses, confirm=confirm)
    if is_rehearsal():
        return MoveResult(
            plan.source,
            plan.target,
            plan.mode,
            tuple(
                MovedSite(site.domain, site.kind, site.writes, site.target_path, False)
                for site in plan.sites
            ),
            plan.modules,
            len(plan.losses),
            rehearsed=True,
        )
    managers = managers or Managers.between(plan.source, plan.target)
    store = store or get_store()
    apps = sorted(site.domain for site in plan.sites if site.kind == "app")
    with ExitStack() as stack:
        # Nothing else may deploy, update or restore an application while its site changes hands.
        for domain in apps:
            stack.enter_context(app_lock(domain, "webserver"))
        fresh = plan_move(
            plan.target, [site.domain for site in plan.sites], store=store, managers=managers
        )
        _require_same(plan, fresh)
        return _Move(
            plan, managers, store, log, probe or wait_until_healthy, tls or https_check
        ).run()


def _require_same(asked: MovePlan, fresh: MovePlan) -> None:
    """
    Refuse to go ahead if the sites are not what the operator was shown.

    Args:
        asked: The plan the operator confirmed.
        fresh: The plan worked out now, under the applications' locks.

    Raises:
        WebserverMoveError: The plan changed, or something stops it now.
    """
    if fresh.blocked:
        raise blocked_error(fresh)
    shown = {(site.domain, site.text) for site in asked.sites}
    now = {(site.domain, site.text) for site in fresh.sites}
    if shown != now or asked.mode != fresh.mode:
        raise WebserverMoveError(
            "The sites changed while the move was being asked for",
            details="Plan the move again and confirm the new plan: what is written is what you "
            "were shown, never something else.",
        )


class _Move:
    """One move, from the first file written to the last one retired."""

    def __init__(
        self,
        plan: MovePlan,
        managers: Managers,
        store: NoustStore,
        log: Logger,
        probe: Probe,
        tls: TlsCheck,
    ) -> None:
        self.probe = probe
        self.tls = tls
        self.plan = plan
        self.source = managers.source
        self.target = managers.target
        self.store = store
        self.log = log
        self.undo = _Undo(log)
        self.warnings: list[str] = []

    # -- The move ------------------------------------------------------------------------

    def run(self) -> MoveResult:
        """
        Returns:
            What was done.

        Raises:
            WebserverMoveError: See :func:`move`.
        """
        plan = self.plan
        try:
            self._prepare()
            for site in plan.sites:
                self._stage(site)
            self._switch()
            self._gate()
            self._commit()
        except (NoustError, OSError, sqlite3.Error) as error:
            self._fail(error)
            raise self._failure(error) from error
        self._after_commit()
        self._audit("ok")
        moved = tuple(
            MovedSite(
                site.domain,
                site.kind,
                site.writes,
                site.target_path,
                not site.operator_site,
            )
            for site in plan.sites
        )
        return MoveResult(
            plan.source,
            plan.target,
            plan.mode,
            moved,
            plan.modules,
            len(plan.losses),
            tuple(self.warnings),
        )

    def _fail(self, error: BaseException) -> None:
        self.log.warning(f"The move failed, putting everything back: {error}")
        self.undo.run()
        self._audit("failure", reason=str(error))

    def _failure(self, error: BaseException) -> WebserverMoveError:
        """
        Make the error a failed move ends in.

        Args:
            error: What stopped it.

        Returns:
            The error as it is raised: the stopping one when it is already a move's, else one that
            carries its words; what could not be put back is added to its details.
        """
        details = ""
        if isinstance(error, NoustError):
            result = WebserverMoveError(
                error.message, details=error.details, output=error.output, coded=error.coded
            )
            details = error.details
        else:
            result = WebserverMoveError(f"The move failed and was undone: {error}")
        report = self.undo.report()
        if report:
            result.details = "\n\n".join(
                part
                for part in (
                    details,
                    say("webserver_move.not_restored").text,
                    report,
                )
                if part
            )
        return result

    def _audit(self, outcome: str, **extra: str) -> None:
        for site in self.plan.sites:
            audit.record(
                "sites.webserver",
                target=f"{'app' if site.kind == 'app' else 'site'}:{site.domain}",
                outcome=outcome,
                details={
                    "from": self.plan.source,
                    "to": self.plan.target,
                    "mode": self.plan.mode,
                    "written": site.writes,
                    **extra,
                },
            )
        if self.plan.mode == "handover":
            audit.record(
                "sites.webserver_handover",
                outcome=outcome,
                details={"from": self.plan.source, "to": self.plan.target, **extra},
            )

    # -- 1. What the new server needs, and whether it accepts the sites --------------------

    def _prepare(self) -> None:
        for module in self.plan.modules:
            if not self.target.enable_module(module):
                self.log.warning(f"Could not enable the {module} module of {self.plan.target}")
        for site in self.plan.sites:
            if not site.writes:
                continue
            ok, output = self.target.test_config_text(site.text, domain=site.domain)
            if not ok:
                raise WebserverMoveError.from_text(
                    say(
                        "webserver_move.rejected",
                        target=display(self.plan.target),
                        source=display(self.plan.source),
                        domain=site.domain,
                    ),
                    output=output,
                )
            self.log.substep(f"{display(self.plan.target)} accepts the site of {site.domain}")

    # -- 2. Write the new files beside the old ones ----------------------------------------

    def _stage(self, site: SitePlan) -> None:
        target = self.target
        domain = site.domain
        path = Path(site.target_path)
        if site.writes:
            existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
            if existing is not None:
                target.history.about_to_overwrite(domain, existing)
            target.fs.write_text(path, site.text, mode=_SITE_MODE)
            self.undo.record(
                f"wrote {path}",
                partial(target.fs.remove, path, missing_ok=True)
                if existing is None
                else partial(target.fs.write_text, path, existing, mode=_SITE_MODE),
            )
            target.history.written(domain, site.text, source="generated")
            self._accounts(site)
        row = self.store.get_site(domain)
        if not target.site_enabled(domain):
            target.enable_site(domain)
            self.undo.record(
                f"enabled {path.name} in {self.plan.target}",
                partial(self._disable_target, domain, row),
            )

    def _accounts(self, site: SitePlan) -> None:
        """
        Write the account file of a password-protected site for the new server.

        Args:
            site: The site.
        """
        target = self.target
        root = target.backend.sites_available.parent
        target.policies.sync_auth_file(site.domain, root, target.backend.name)
        self.undo.record(
            f"wrote the accounts of {site.domain} for {self.plan.target}",
            partial(target.policies.remove_auth_file, site.domain, root),
        )

    def _disable_target(self, domain: str, row: Site | None) -> None:
        self.target.disable_site(domain)
        # disable_site records the new state on the site's row, which still belongs to the old
        # server; the row is put back as it was.
        if row is not None:
            self.store.update_site(row)

    # -- 3. Hand the traffic to the new server ----------------------------------------------

    def _switch(self) -> None:
        if self.plan.mode == "live":
            self._reload(self.target, "the new sites")
            self.undo.record(
                f"reloaded {self.plan.target}", partial(self._quiet_reload, self.target)
            )
            return
        self._handover()

    def _handover(self) -> None:
        source, target = self.source, self.target
        errors = target.config_errors()
        if errors is not None:
            raise WebserverMoveError.from_text(
                say(
                    "webserver_move.rejected",
                    target=display(self.plan.target),
                    source=display(self.plan.source),
                    domain="the sites together",
                ),
                output=errors,
            )
        if source.is_running():
            self._unit(source, "stop")
            self.undo.record(
                f"stopped {source.backend.service}",
                partial(self._unit, source, "start"),
            )
        self.log.substep(f"Starting {self.plan.target} on ports 80 and 443")
        output = target.load_configuration()
        self.undo.record(f"started {target.backend.service}", partial(self._unit, target, "stop"))
        if output is not None or not target.is_running():
            raise WebserverMoveError.from_text(
                say(
                    "webserver_move.not_serving",
                    target=display(self.plan.target),
                    source=display(self.plan.source),
                ),
                output=output or "",
            )

    def _unit(self, manager: WebServerManager, action: str) -> None:
        result = manager.runner.run(
            ["systemctl", action, manager.backend.service], timeout=_UNIT_TIMEOUT
        )
        if not result.success:
            raise WebserverMoveError(
                f"systemctl {action} {manager.backend.service} failed",
                details=(result.stderr or result.stdout).strip(),
            )

    def _reload(self, manager: WebServerManager, what: str) -> None:
        output = manager.load_configuration()
        if output is not None:
            raise WebserverMoveError.from_text(
                say(
                    "webserver_move.not_serving",
                    target=display(manager.backend.name),
                    source=display(self.plan.source),
                ),
                output=output,
            )
        self.log.substep(f"{display(manager.backend.name)} loaded {what}")

    def _quiet_reload(self, manager: WebServerManager) -> None:
        if not manager.reload():
            raise WebserverMoveError(f"{manager.backend.service} did not reload")

    # -- 4. Ask every site, through the new server ------------------------------------------

    def _gate(self) -> None:
        for site in self.plan.sites:
            self._ask(site)

    def _ask(self, site: SitePlan) -> None:
        """
        Ask a site, through the new server, what its health check asks of the application.

        Args:
            site: The site.

        Raises:
            WebserverMoveError: It did not answer.
        """
        app = self.store.get_app(site.domain) if site.kind == "app" else None
        base = HealthCheck.for_app(app) if app is not None else HealthCheck()
        serves_tls = config_serves_tls(site.text) if site.text else False
        # What the application's check asks of its own process (a command, a port, another scheme)
        # is not what a visitor's request asks of the web server: only the path and the answer are.
        check = replace(
            base,
            kind="http",
            scheme="http",
            port=None,
            command=None,
            service=None,
            host=site.domain,
            domain=None,
            # The plain port of a site with a certificate redirects: the redirect is the answer.
            expect=None if serves_tls else base.expect,
        )
        gate = HealthGate(
            unit=None,
            url=f"http://127.0.0.1:{HTTP_PORT}{check.path}",
            services=ServiceManager(),
            logger=self.log,
            probe=self.probe,
            check=check,
        )
        healthy, evidence = gate.restart_and_probe()
        if healthy and serves_tls:
            healthy, evidence = self._ask_tls(site, base, gate)
        if healthy:
            self.log.substep(f"{site.domain} answers through {self.plan.target}")
            return
        raise WebserverMoveError.from_text(
            say(
                "webserver_move.unanswered",
                domain=site.domain,
                target=display(self.plan.target),
                source=display(self.plan.source),
            ),
            output=evidence,
        )

    def _ask_tls(self, site: SitePlan, base: HealthCheck, gate: HealthGate) -> tuple[bool, str]:
        failures: list[str] = []
        run = self.tls(site.domain, base.path, base.accepts)
        delay = base.delay
        healthy = wait_until_command_succeeds(
            run,
            retries=base.attempts(delay),
            delay=delay,
            on_attempt=failures.append,
            within=base.wall_clock,
        )
        return healthy, "" if healthy else gate.evidence("\n".join(failures[-3:]))

    # -- 5. Record it, then retire the old files ---------------------------------------------

    def _commit(self) -> None:
        for site in self.plan.sites:
            if site.kind == "app":
                app = self.store.get_app(site.domain)
                if app is not None:
                    self._set_app_server(app)
            row = self.store.get_site(site.domain)
            if row is not None:
                self._set_site_row(row, site)

    def _set_app_server(self, app: App) -> None:
        before = replace(app)
        app.webserver = self.plan.target
        self.store.update_app(app)
        self.undo.record(
            f"recorded {app.domain} behind {self.plan.target}",
            partial(self.store.update_app, before),
        )

    def _set_site_row(self, row: Site, site: SitePlan) -> None:
        before = replace(row)
        row.webserver = self.plan.target
        row.config_path = site.target_path
        row.enabled = True
        self.store.update_site(row)
        self.undo.record(
            f"recorded the site of {row.domain} behind {self.plan.target}",
            partial(self.store.update_site, before),
        )

    def _after_commit(self) -> None:
        """Retire what the move replaced. A failure here leaves a stale file, not a broken site."""
        for site in self.plan.sites:
            try:
                self._retire(site)
            except (NoustError, OSError) as exc:
                self.warnings.append(f"{site.source_path} was not removed: {exc}")
        if self.plan.mode == "live":
            if self.source.is_running() and not self.source.reload():
                self.warnings.append(f"{self.source.backend.service} did not reload")
            return
        self._boot_at_switch()

    def _retire(self, site: SitePlan) -> None:
        source = self.source
        path = Path(site.source_path)
        link = source.sites_enabled / path.name
        source.fs.remove(link, missing_ok=True)
        if site.operator_site:
            return
        source.fs.remove(path, missing_ok=True)
        source.policies.remove_auth_file(site.domain, source.backend.sites_available.parent)

    def _boot_at_switch(self) -> None:
        """Make the next boot start the server that holds the ports now."""
        for manager, action in ((self.source, "disable"), (self.target, "enable")):
            result = manager.runner.run(
                ["systemctl", action, manager.backend.service], timeout=_UNIT_TIMEOUT
            )
            if not result.success:
                self.warnings.append(
                    f"systemctl {action} {manager.backend.service} failed: "
                    f"{(result.stderr or result.stdout).strip()}"
                )


def domains_of(plan: MovePlan) -> Sequence[str]:
    """
    Args:
        plan: A plan.

    Returns:
        The domains it moves.
    """
    return [site.domain for site in plan.sites]
