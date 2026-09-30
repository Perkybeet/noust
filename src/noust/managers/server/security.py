# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Server security: the one entry point the console and the CLI share.

``noust server security ...`` and ``/api/server/security/...`` are two clients
of :class:`ServerSecurity` (rule 3): the same checks, the same guards, the same
confirm-or-revert, the same words when something is refused. Neither decides
anything; they translate arguments in and results out.

The pieces behind it, each the only code that does its job:

- :mod:`.security_checks` - the hardening checks (also ``noust health`` and ENS).
- :mod:`.security_ssh` - sshd fixes and authorized keys, guarded.
- :mod:`.security_firewall` - ufw and firewalld, guarded.
- :mod:`.security_fail2ban` - status, unban, install.
- :mod:`.security_pending` - confirm or revert, with a systemd timer.
- :mod:`.security_risks` - accepted risks.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from noust.core.exceptions import SecurityError, ValidationError
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner
from noust.managers.server.errors import UnsupportedHostError
from noust.managers.server.host import HostPaths
from noust.managers.server.security_access import AccessGuardError
from noust.managers.server.security_catalog import CATALOG
from noust.managers.server.security_checks import (
    CheckReport,
    cached_report,
    mark_report_stale,
    reapply_risks,
    refresh_in_background,
    refreshing,
    run_checks,
    wait_for_refresh,
)
from noust.managers.server.security_fail2ban import Fail2ban
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_keys import parse_new_key
from noust.managers.server.security_logins import EVIDENCE_DAYS
from noust.managers.server.security_pending import CONFIRM_WINDOW, ChangeLedger, PendingChange
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_risks import AcceptedRisk, AcceptedRisks
from noust.managers.server.security_ssh import SSH_FIXES, FixPlan, SshSecurity
from noust.managers.server.security_sshd import (
    DIRECTIVES,
    SshdDropIn,
    SshdUnavailableError,
    include_present,
    parse_dropin,
    read_unit,
)

#: The sshd keywords the SSH tab shows, in order.
SSH_SHOWN = (
    "port",
    "permitrootlogin",
    "passwordauthentication",
    "kbdinteractiveauthentication",
    "challengeresponseauthentication",
    "usepam",
    "pubkeyauthentication",
    "permitemptypasswords",
    "authenticationmethods",
    "maxauthtries",
    "logingracetime",
    "x11forwarding",
    "allowtcpforwarding",
    "clientaliveinterval",
    "clientalivecountmax",
    "loglevel",
    "strictmodes",
    "allowusers",
    "allowgroups",
    "denyusers",
    "denygroups",
    "authorizedkeysfile",
    "authorizedkeyscommand",
)


#: Every change the Security tab and ``noust server security`` make, with the
#: parameters each takes and whether it changes how the server is reached
#: (those wait for confirmation, one at a time).
OPERATIONS: dict[str, tuple[tuple[str, ...], bool]] = {
    "check.fix": (("check_id", "epel", "ignore"), True),
    "ssh.fix": (("fix",), True),
    "ssh.key.add": (("user", "public_key"), False),
    "ssh.key.remove": (("user", "fingerprint", "force"), False),
    "firewall.add": (("action", "port", "proto", "source", "comment"), True),
    "firewall.delete": (("rule_id",), True),
    "firewall.enable": ((), True),
    "firewall.disable": ((), True),
    "fail2ban.install": (("epel", "ignore"), False),
    "fail2ban.unban": (("address", "jail"), False),
}


class ServerSecurity:
    """
    Everything the Security tab and ``noust server security`` do.

    Args:
        actor: Who is acting, for the record of every change.
        runner: The command runner; the process-wide one by default.
        fs: The filesystem seam; the process-wide one by default.
        host: Where the system files are.
        changes: Where pending changes are recorded.
        on_output: Receives every command a change runs, and its output, verbatim.
        console_port: The console's port, when the caller knows it.
        clock: The current time.
        python: The interpreter a revert timer runs.
        risks: Accepted risks; the store's by default.
    """

    def __init__(
        self,
        *,
        actor: str,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        changes: Path | None = None,
        on_output: Callable[[str], None] | None = None,
        console_port: int | None = None,
        clock: Callable[[], float] = time.time,
        python: str | None = None,
        risks: AcceptedRisks | None = None,
    ) -> None:
        self.actor = actor
        self.on_output = on_output
        self.console_port = console_port
        self.probe = SecurityProbe(runner=runner, fs=fs, host=host, clock=clock)
        # A background run looks at the machine through a probe of its own:
        # a probe caches what it read, and is not shared across threads.
        self._probe_args: dict[str, Any] = {
            "runner": runner,
            "fs": fs,
            "host": host,
            "clock": clock,
        }
        self.ledger = ChangeLedger(
            changes, runner=runner, fs=fs, host=host, clock=clock, python=python
        )
        self.risks = risks or AcceptedRisks()

    # Parts ----------------------------------------------------------------------

    def _ssh(self) -> SshSecurity:
        return SshSecurity(self.probe, self.ledger, actor=self.actor, on_output=self.on_output)

    def _firewall(self) -> Firewall:
        return Firewall(
            self.probe,
            self.ledger,
            actor=self.actor,
            on_output=self.on_output,
            console_port=self.console_port,
        )

    def _fail2ban(self) -> Fail2ban:
        return Fail2ban(self.probe, on_output=self.on_output)

    def _changed(self) -> None:
        """Forget what was read before a change, and mark the shared report stale."""
        self.probe.invalidate()
        mark_report_stale()

    def _run_complete(self) -> CheckReport:
        """
        Run every check with a probe of its own: what a background run does.

        Returns:
            The report, kept for every process.
        """
        return run_checks(
            SecurityProbe(**self._probe_args), risks=self.risks, console_port=self.console_port
        )

    # Checks and risks -------------------------------------------------------------

    def checks(self, *, refresh: bool = False, host_checks: bool = True) -> CheckReport:
        """
        The hardening checks: the last ones computed, or new ones.

        Args:
            refresh: Run them again even when a report is remembered.
            host_checks: Include the package manager and system checks.

        Returns:
            The report.
        """
        report = None if refresh else cached_report()
        if report is not None and report.stale:
            report = None
        if report is None and not refresh and refreshing():
            # The console's first look started a run a moment ago: its result,
            # rather than the same probes twice.
            wait_for_refresh()
            report = cached_report()
            if report is not None and report.stale:
                report = None
        if report is None:
            report = run_checks(
                self.probe,
                risks=self.risks,
                host_checks=host_checks,
                console_port=self.console_port,
            )
        return reapply_risks(report, self.risks)

    def fix(
        self, check_id: str, *, epel: bool = False, ignore: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Apply a check's automatic fix.

        Args:
            check_id: The check.
            epel: Enabling EPEL was confirmed (fail2ban on RHEL rebuilds).
            ignore: Addresses fail2ban must never ban (the console's client).

        Returns:
            What the fix did: a pending change for access changes.

        Raises:
            SecurityError: The check has no automatic fix, or passes.
        """
        action = self._fix_action(check_id)
        return self.run_action(action, epel=epel, ignore=ignore)

    def run_action(
        self, action: str, *, epel: bool = False, ignore: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Run one automatic fix by its action name.

        Args:
            action: ``ssh:<fix>``, ``firewall:enable`` or ``fail2ban:install``.
            epel: Enabling EPEL was confirmed.
            ignore: Addresses fail2ban must never ban.

        Returns:
            What it did.

        Raises:
            SecurityError: No such action.
        """
        kind, _, name = action.partition(":")
        if kind == "ssh" and name in SSH_FIXES:
            return {"change": self.harden_ssh(name).to_dict()}
        if action == "firewall:enable":
            return {"change": self.firewall_enable().to_dict()}
        if action == "fail2ban:install":
            return {"fail2ban": self.fail2ban_install(epel=epel, ignore=ignore)}
        raise SecurityError(f"There is no automatic fix {action!r}")

    def accepted_risks(self) -> list[AcceptedRisk]:
        """
        Every acceptance, newest first.

        Returns:
            The acceptances, revoked and expired included.
        """
        return self.risks.history()

    def accept_risk(self, check_id: str, *, reason: str, until: datetime) -> AcceptedRisk:
        """
        Accept a check's finding until a date.

        Args:
            check_id: The check.
            reason: Why.
            until: Until when.

        Returns:
            The acceptance.
        """
        accepted = self.risks.accept(check_id, reason=reason, by=self.actor, expires_at=until)
        report = cached_report()
        if report is not None:
            reapply_risks(report, self.risks)
        return accepted

    def revoke_risk(self, check_id: str) -> AcceptedRisk | None:
        """
        Withdraw a check's acceptance.

        Args:
            check_id: The check.

        Returns:
            The acceptance withdrawn, or None when none held.
        """
        revoked = self.risks.revoke(check_id, by=self.actor)
        report = cached_report()
        if report is not None:
            reapply_risks(report, self.risks)
        return revoked

    # SSH ----------------------------------------------------------------------------

    def ssh_status(self) -> dict[str, Any]:
        """
        sshd as it runs: effective values, Noust's drop-in, the unit, the logins.

        Returns:
            What the SSH tab shows.
        """
        data: dict[str, Any] = {
            "effective": {},
            "error": None,
            "error_output": None,
            "dropin": None,
            "dropin_settings": {},
            "include_present": include_present(self.probe.host),
            "root_password": self.probe.accounts.password_state("root"),
            "fixes": {},
            "sessions": [],
            "logins": {"source": "", "error": "", "days": EVIDENCE_DAYS, "recent": []},
            "confirm_window": CONFIRM_WINDOW,
        }
        try:
            effective = self.probe.effective("root")
        except SshdUnavailableError as exc:
            data["error"] = exc.message
            data["error_output"] = exc.output
            return data
        data["effective"] = {
            DIRECTIVES.get(key, key): " ".join(effective.all(key))
            for key in SSH_SHOWN
            if effective.all(key)
        }
        data["ports"] = effective.ports
        data["passwords_accepted"] = effective.passwords_accepted
        try:
            text = SshdDropIn(self.probe.host).read()
        except SecurityError as exc:
            data["dropin_error"] = exc.message
            text = None
        data["dropin"] = text
        data["dropin_settings"] = {
            DIRECTIVES.get(key, key): value for key, value in parse_dropin(text).items()
        }
        unit = read_unit(self.probe.runner)
        data["unit"] = {
            "service": unit.service,
            "active": unit.active,
            "socket": unit.socket,
            "socket_active": unit.socket_active,
        }
        data["fixes"] = {name: self.ssh_plan(name).to_dict() for name in SSH_FIXES}
        history = self.probe.logins()
        data["logins"] = {
            "source": history.source,
            "error": history.error,
            "days": EVIDENCE_DAYS,
            "recent": [
                {
                    "at": event.at,
                    "user": event.user,
                    "method": event.method,
                    "source": event.source,
                    "fingerprint": event.fingerprint,
                    "line": event.line,
                }
                for event in list(reversed(history.events))[:20]
            ],
        }
        data["sessions"] = [
            {
                "peer": session.connection.peer_address,
                "port": session.connection.peer_port,
                "user": session.login.user if session.login else None,
                "fingerprint": session.login.fingerprint if session.login else None,
            }
            for session in self.probe.sessions()
        ]
        return data

    def ssh_plan(self, name: str) -> FixPlan:
        """
        What an sshd fix would change, and whether it may.

        Args:
            name: The fix.

        Returns:
            The plan.
        """
        return self._ssh().plan(name)

    def ssh_keys(self) -> list[dict[str, Any]]:
        """
        Every administrator and the keys that open it, with when each was last used.

        Returns:
            One entry per account, root first.
        """
        history = self.probe.logins()
        centrals = self.probe.central_fingerprints()
        in_use = {
            (session.login.user, session.login.fingerprint)
            for session in self.probe.sessions()
            if session.login is not None
        }
        found: list[dict[str, Any]] = []
        for entry in self.probe.account_keys():
            name = entry.account.name
            files = []
            for file in entry.files:
                keys = []
                for key in file.keys:
                    used = history.last_use(name, key.fingerprint)
                    keys.append(
                        {
                            "fingerprint": key.fingerprint,
                            "type": key.key_type,
                            "bits": key.bits,
                            "comment": key.comment,
                            "kind": "central" if key.fingerprint in centrals else key.kind,
                            "options": list(key.options),
                            "weak": key.weak,
                            "last_used": used.at if used else None,
                            "last_used_from": used.source if used else None,
                            "in_use": (name, key.fingerprint) in in_use,
                        }
                    )
                files.append(
                    {
                        "path": file.path,
                        "exists": file.exists,
                        "problems": list(file.problems),
                        "error": file.error,
                        "keys": keys,
                    }
                )
            found.append(
                {
                    "user": name,
                    "uid": entry.account.uid,
                    "sudo": entry.sudo.granted,
                    "sudo_usable": entry.sudo_usable,
                    "password": entry.password,
                    "login_allowed": entry.login_allowed,
                    "login_refusal": entry.login_refusal,
                    "files": files,
                }
            )
        return found

    def harden_ssh(self, name: str) -> PendingChange:
        """
        Apply an sshd fix, pending confirmation.

        Args:
            name: The fix.

        Returns:
            The pending change.
        """
        try:
            return self._ssh().apply(name)
        finally:
            self._changed()

    def add_key(self, user: str, public_key: str) -> dict[str, Any]:
        """
        Let a key log in as an administrator.

        Args:
            user: The account.
            public_key: The ``.pub`` line.

        Returns:
            What was done.
        """
        try:
            return self._ssh().add_key(user, public_key)
        finally:
            self._changed()

    def remove_key(self, user: str, fingerprint: str, *, force: bool = False) -> dict[str, Any]:
        """
        Stop a key from logging in, guarded.

        Args:
            user: The account.
            fingerprint: The key.
            force: Override the guard.

        Returns:
            What was removed.
        """
        try:
            return self._ssh().remove_key(user, fingerprint, force=force)
        finally:
            self._changed()

    # Firewall -------------------------------------------------------------------------

    def firewall_status(self) -> dict[str, Any]:
        """
        The firewall, the ports that answer and what the guard protects.

        Returns:
            What the Firewall and Ports views show.
        """
        firewall = self._firewall()
        state = firewall.state()
        exposures, error = firewall.exposures()
        protected = firewall.protected()
        return {
            "firewall": state.to_dict(),
            "ports": [exposure.to_dict() for exposure in exposures],
            "ports_error": error or None,
            "protected_ports": [
                {"port": port, "reason": reason} for port, reason in sorted(protected.ports.items())
            ],
            "session_sources": sorted(protected.sources),
        }

    def firewall_add(self, request: RuleRequest) -> PendingChange:
        """
        Add a rule, pending confirmation.

        Args:
            request: The rule.

        Returns:
            The pending change.
        """
        try:
            return self._firewall().add_rule(request)
        finally:
            self._changed()

    def firewall_delete(self, rule_id: str) -> PendingChange:
        """
        Delete a rule, pending confirmation.

        Args:
            rule_id: The rule.

        Returns:
            The pending change.
        """
        try:
            return self._firewall().delete_rule(rule_id)
        finally:
            self._changed()

    def firewall_enable(self) -> PendingChange:
        """
        Turn ufw on, SSH allowed first, pending confirmation.

        Returns:
            The pending change.
        """
        try:
            return self._firewall().enable()
        finally:
            self._changed()

    def firewall_disable(self) -> PendingChange:
        """
        Turn the firewall off, pending confirmation.

        Returns:
            The pending change.
        """
        try:
            return self._firewall().disable()
        finally:
            self._changed()

    # fail2ban ---------------------------------------------------------------------------

    def fail2ban_status(self) -> dict[str, Any]:
        """
        fail2ban's state, jails and bans.

        Returns:
            What the Bans view shows.
        """
        return self._fail2ban().state().to_dict()

    def fail2ban_install(
        self, *, epel: bool = False, ignore: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Install fail2ban with an sshd jail.

        Args:
            epel: Enabling EPEL was confirmed.
            ignore: Addresses never to ban, besides the SSH sessions open now.

        Returns:
            What was done.
        """
        try:
            return self._fail2ban().install(epel=epel, ignore=ignore)
        finally:
            self._changed()

    def fail2ban_unban(self, address: str, jail: str | None = None) -> dict[str, Any]:
        """
        Lift a ban.

        Args:
            address: The address.
            jail: The jail; every jail that bans it by default.

        Returns:
            Where it was lifted.
        """
        return self._fail2ban().unban(address, jail)

    # Confirm or revert -------------------------------------------------------------------

    def pending(self) -> list[PendingChange]:
        """
        Every recorded change, newest first, after undoing any whose timer was lost.

        Returns:
            The changes.
        """
        self.ledger.revert_overdue(self.on_output)
        return self.ledger.changes()

    def confirm(self, change_id: str) -> PendingChange:
        """
        Keep a change, once a new SSH login shows nobody was locked out.

        Args:
            change_id: The change.

        Returns:
            The confirmed change.
        """
        try:
            return self._ssh().confirm(change_id)
        finally:
            self._changed()

    def revert(self, change_id: str) -> PendingChange:
        """
        Undo a pending change now.

        Args:
            change_id: The change.

        Returns:
            The reverted change.
        """
        try:
            return self._ssh().revert(change_id)
        finally:
            self._changed()

    # One door for every change ------------------------------------------------------------

    def _rule(self, params: dict[str, Any]) -> RuleRequest:
        return RuleRequest(
            action=str(params.get("action", "")),
            # Anything but a whole number becomes 0, which validation refuses.
            port=int(port) if isinstance(port := params.get("port"), int) else 0,
            proto=str(params.get("proto") or "tcp"),
            source=str(params.get("source") or "any"),
            comment=str(params.get("comment") or ""),
        ).validated()

    def preflight(self, operation: str, params: dict[str, Any]) -> str:
        """
        Check a change before it is queued: everything that would refuse it, now.

        The guards run again when the change is made - the machine may have
        moved in between - but a refusal known now is answered now, with its
        guided steps, instead of as a failed job.

        Args:
            operation: One of :data:`OPERATIONS`.
            params: Its parameters.

        Returns:
            What the change does, in a sentence, for the job's name.

        Raises:
            AccessGuardError: A guard refuses it.
            ConfirmationRequiredError: It needs an explicit yes first (EPEL).
            ValidationError: A parameter is invalid.
            SecurityError: It cannot be done here, or another change is pending.
        """
        if operation not in OPERATIONS:
            raise SecurityError(f"There is no security operation {operation!r}")
        _names, access = OPERATIONS[operation]
        if access:
            waiting = [change for change in self.pending() if change.status == "pending"]
            if waiting:
                raise SecurityError(
                    f"Another change is waiting for confirmation: {waiting[0].title} ({waiting[0].id})",
                    details="Confirm it or revert it first: one change at a time can be undone safely.",
                )
        if operation == "check.fix":
            check_id = str(params.get("check_id", ""))
            if check_id not in CATALOG:
                raise ValidationError(f"There is no check {check_id!r}", field="check_id")
            action = self._fix_action(check_id)
            kind, _, name = action.partition(":")
            if kind == "ssh":
                return self.preflight("ssh.fix", {"fix": name})
            if action == "firewall:enable":
                return self.preflight("firewall.enable", {})
            return self.preflight("fail2ban.install", params)
        if operation == "ssh.fix":
            plan = self.ssh_plan(str(params.get("fix", "")))
            if plan.blockers:
                raise AccessGuardError(
                    f"Not applied: {plan.fix.title.lower()}",
                    details="\n".join([*plan.blockers, *plan.guidance]),
                )
            return plan.fix.title
        if operation == "ssh.key.add":
            key = parse_new_key(str(params.get("public_key", "")))
            self._ssh()._admin(str(params.get("user", "")))
            return f"Add SSH key {key.fingerprint} to {params.get('user')}"
        if operation == "ssh.key.remove":
            user, fingerprint = str(params.get("user", "")), str(params.get("fingerprint", ""))
            self._ssh().check_removal(user, fingerprint, force=bool(params.get("force")))
            return f"Remove SSH key {fingerprint} from {user}"
        firewall = self._firewall()
        if operation == "firewall.add":
            return firewall.plan_add(self._rule(params)).title
        if operation == "firewall.delete":
            return firewall.plan_delete(str(params.get("rule_id", ""))).title
        if operation == "firewall.enable":
            return firewall.plan_enable().title
        if operation == "firewall.disable":
            return firewall.plan_disable().title
        if operation == "fail2ban.install":
            state = self._fail2ban().state()
            if not state.install_supported:
                raise UnsupportedHostError("Noust cannot install fail2ban here", state.install_hint)
            if state.needs_epel and not params.get("epel"):
                self._fail2ban().install(epel=False)  # raises the confirmation, changes nothing
            return "Install fail2ban with an sshd jail"
        address = str(params.get("address", ""))
        try:
            ipaddress.ip_address(address.strip())
        except ValueError as exc:
            raise ValidationError(f"{address!r} is not an IP address", field="address") from exc
        return f"Lift the fail2ban ban of {address}"

    def _fix_action(self, check_id: str) -> str:
        """
        The automatic fix of a check, as it stands now.

        Args:
            check_id: The check.

        Returns:
            Its action name.

        Raises:
            SecurityError: It passes, or its fix is not automatic here.
        """
        report = self.checks(refresh=True, host_checks=False)
        check = report.get(check_id)
        if check is None or check.fix is None or check.status == "pass":
            raise SecurityError(
                f"Check {check_id} has nothing to fix",
                details="Run the checks again; it may have passed since.",
            )
        if check.fix.kind != "automatic" or not check.fix.action:
            raise SecurityError(
                f"Check {check_id} has no automatic fix here",
                details="\n".join(
                    part
                    for part in (check.fix.blocked, *check.fix.steps, check.fix.cli or "")
                    if part
                ),
            )
        return check.fix.action

    def execute(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Make a change: what a console job and a command both run.

        Args:
            operation: One of :data:`OPERATIONS`.
            params: Its parameters.

        Returns:
            What was done; ``change`` holds the pending change of an access change.
        """
        if operation == "check.fix":
            return self.fix(
                str(params.get("check_id", "")),
                epel=bool(params.get("epel")),
                ignore=list(params.get("ignore") or []),
            )
        if operation == "ssh.fix":
            return {"change": self.harden_ssh(str(params.get("fix", ""))).to_dict()}
        if operation == "ssh.key.add":
            return self.add_key(str(params.get("user", "")), str(params.get("public_key", "")))
        if operation == "ssh.key.remove":
            return self.remove_key(
                str(params.get("user", "")),
                str(params.get("fingerprint", "")),
                force=bool(params.get("force")),
            )
        if operation == "firewall.add":
            return {"change": self.firewall_add(self._rule(params)).to_dict()}
        if operation == "firewall.delete":
            return {"change": self.firewall_delete(str(params.get("rule_id", ""))).to_dict()}
        if operation == "firewall.enable":
            return {"change": self.firewall_enable().to_dict()}
        if operation == "firewall.disable":
            return {"change": self.firewall_disable().to_dict()}
        if operation == "fail2ban.install":
            return self.fail2ban_install(
                epel=bool(params.get("epel")), ignore=list(params.get("ignore") or [])
            )
        if operation == "fail2ban.unban":
            jail = params.get("jail")
            return self.fail2ban_unban(str(params.get("address", "")), str(jail) if jail else None)
        raise SecurityError(f"There is no security operation {operation!r}")

    # Overview ------------------------------------------------------------------------------

    def overview(self, *, refresh_if_due: bool = False) -> dict[str, Any]:
        """
        The four cards of the Security tab and the open findings, from the last checks.

        Args:
            refresh_if_due: With no report, a stale one or one older than
                :data:`~noust.managers.server.security_checks.REPORT_PERIOD`,
                run the checks in the background (once: a run in flight is
                joined, not repeated). The console asks for this; nobody then
                sees "not checked" on a server whose data is a click away.

        Returns:
            The counts and summaries; ``checked_at`` is None before the first
            run; ``checking`` is true while a background run is in flight.
        """
        report = cached_report()
        checking = refreshing()
        if refresh_if_due and not checking and (report is None or report.due(self.probe.now())):
            checking = refresh_in_background(self._run_complete)
        if report is not None:
            report = reapply_risks(report, self.risks)
        pending = [change.to_dict() for change in self.pending() if change.status == "pending"]
        if report is None:
            return {
                "checked_at": None,
                "counts": None,
                "attention": [],
                "pending": pending,
                "checking": checking,
            }
        return {
            "checked_at": report.checked_at,
            "counts": report.counts(),
            "attention": [check.to_dict() for check in report.attention()],
            "pending": pending,
            "checking": checking,
        }
