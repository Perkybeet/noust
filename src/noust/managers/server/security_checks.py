# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The hardening checks: one list, shared by the console, ``noust health`` and the ENS check.

:func:`run_checks` is the one implementation (rule 3). Each check has a stable
id from :mod:`~noust.managers.server.security_catalog`, a status, the reason
in a sentence, the evidence verbatim (a line of ``sshd -T``, a socket, a
package count), and a fix of one of four kinds:

- ``automatic``: Noust applies it, through the same manager method the
  Security tab uses (an sshd fix, turning ufw on, installing fail2ban), with
  its guard and, for access changes, confirm-or-revert. A fix whose guard
  cannot be proven right now is reported as ``guided`` instead, with the
  reason and the steps.
- ``action``: another part of Noust does it (installing updates, creating
  swap); the fix names the command and the route.
- ``guided``: the exact commands, for what should not be automated (moving
  a Docker port to loopback, removing a second uid 0).
- ``none``: nothing to do.

A probe that fails makes its check ``unknown`` with the error verbatim; it
never becomes a pass, and never an exception that empties the list.

The expensive probes (a package simulation, ``sshd -T`` per account, the
journal) run when the list is computed; ``GET /api/system/health`` and the
console's summary read the last list from :func:`cached_report`.

Nobody has to ask for that list. A complete one is kept beside the store
(:func:`report_path`), so every process reads the same report whoever ran it;
a change to sshd or the firewall marks it stale, and it is old after
:data:`REPORT_PERIOD`. ``noust-monitor`` has systemd run ``python -m`` this
module (:func:`refresh_command`) when it is missing or due - outside the
monitor's own sandbox, which is read-only for the tools the probes run - and
the console runs the checks once in the background
(:func:`refresh_in_background`) when it finds none, instead of saying they
never ran.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.exceptions import NoustError
from noust.core.fs import get_fs
from noust.fleet.authorize import key_removal_command
from noust.managers.server.host import PROBE_TIMEOUT, read_os_release
from noust.managers.server.security_catalog import CATALOG, SEVERITIES
from noust.managers.server.security_fail2ban import Fail2ban
from noust.managers.server.security_firewall import Firewall, FirewallState, PortExposure
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_risks import AcceptedRisk, AcceptedRisks
from noust.managers.server.security_ssh import SSH_FIXES, plan_fix
from noust.managers.server.security_sshd import SshdEffective, SshdUnavailableError

#: Statuses, in the order the console sorts them.
STATUSES = ("fail", "warn", "unknown", "accepted", "pass", "n/a")

#: What a probe may raise and still leave the list whole: Noust's own errors
#: (a tool refused, a host unsupported), the files it read, bad data.
_PROBE_ERRORS = (NoustError, OSError, ValueError, KeyError)

#: A package list or reboot flag older than this is overdue.
STALE_DAYS = 7

#: sshd's limits a hardened server tightens (CIS, Mozilla).
MAX_AUTH_TRIES = 6
MAX_LOGIN_GRACE = 120

#: Seconds a report stays current: the monitor and the console run the checks
#: again once it is older.
REPORT_PERIOD = 3600

#: Seconds a reader waits for a run in flight before running its own.
REFRESH_WAIT = 300

#: The module systemd runs for the monitor (:func:`refresh_command`).
MODULE = "noust.managers.server.security_checks"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckFix:
    """
    How a finding is fixed.

    Attributes:
        kind: ``automatic``, ``action``, ``guided`` or ``none``.
        summary: What the fix does, in a sentence.
        action: For ``automatic``: what ``POST .../checks/{id}/fix`` applies
            (``ssh:disable-passwords``, ``firewall:enable``, ``fail2ban:install``).
        steps: For ``guided``: the steps, commands verbatim; for an automatic
            fix that is not available now, why and how to make it available.
        cli: The Noust command that does the same.
        endpoint: For ``action``: the route that does it.
        reverts: The change undoes itself unless it is confirmed.
        blocked: Why an automatic fix cannot run now (then ``kind`` is ``guided``).
    """

    kind: str
    summary: str = ""
    action: str | None = None
    steps: tuple[str, ...] = ()
    cli: str | None = None
    endpoint: str | None = None
    reverts: bool = False
    blocked: str = ""


@dataclass(frozen=True)
class Check:
    """
    One hardening check's result.

    Attributes:
        id: The stable id.
        group: Its group.
        title: What a failure means.
        severity: How serious this finding is.
        status: ``pass``, ``warn``, ``fail``, ``unknown``, ``n/a`` or
            ``accepted`` (a risk accepted until a date).
        reason: What was found, in a sentence.
        evidence: What the system said, verbatim, one item per line.
        fix: How to fix it; None when it passes.
        accepted: The acceptance, for ``accepted``.
    """

    id: str
    group: str
    title: str
    severity: str
    status: str
    reason: str
    evidence: tuple[str, ...] = ()
    fix: CheckFix | None = None
    accepted: AcceptedRisk | None = None

    @property
    def failing(self) -> bool:
        """Whether the check found something to act on (accepted risks included)."""
        return self.status in ("fail", "warn", "accepted")

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the check for the API and ``--json``.

        Returns:
            Every field.
        """
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        if self.fix is not None:
            data["fix"]["steps"] = list(self.fix.steps)
        return data


@dataclass
class CheckReport:
    """
    Every check, and when they ran.

    Attributes:
        checks: The results, in catalog order.
        checked_at: When, ISO 8601 UTC.
        complete: Every check ran; False for the quick ones, which skip the
            package manager and the system managers.
        stale: Something changed since (sshd, the firewall): run them again.
    """

    checks: list[Check]
    checked_at: str
    complete: bool = True
    stale: bool = False

    def age(self, now: float) -> float:
        """
        How old the report is.

        Args:
            now: The current time, epoch seconds.

        Returns:
            Seconds since the checks ran; infinite when the date is unreadable.
        """
        try:
            moment = datetime.fromisoformat(self.checked_at)
        except ValueError:
            return float("inf")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return now - moment.timestamp()

    def due(self, now: float, period: float = REPORT_PERIOD) -> bool:
        """
        Report whether the checks should run again.

        Args:
            now: The current time, epoch seconds.
            period: How long a report stays current.

        Returns:
            True when stale, quick or older than the period.
        """
        return self.stale or not self.complete or self.age(now) >= period

    def counts(self) -> dict[str, int]:
        """
        How many checks are in each state.

        Returns:
            ``critical`` and ``warning`` (open findings by severity class),
            ``accepted``, ``unknown``, ``passed`` and ``not_applicable``.
        """
        return {
            "critical": sum(1 for c in self.checks if c.status == "fail"),
            "warning": sum(1 for c in self.checks if c.status == "warn"),
            "accepted": sum(1 for c in self.checks if c.status == "accepted"),
            "unknown": sum(1 for c in self.checks if c.status == "unknown"),
            "passed": sum(1 for c in self.checks if c.status == "pass"),
            "not_applicable": sum(1 for c in self.checks if c.status == "n/a"),
        }

    def attention(self) -> list[Check]:
        """
        The open findings, most serious first.

        Returns:
            Failing and warning checks, by severity then catalog order.
        """
        order = {name: index for index, name in enumerate(SEVERITIES)}
        return sorted(
            (check for check in self.checks if check.status in ("fail", "warn")),
            key=lambda check: (check.status != "fail", order.get(check.severity, 9)),
        )

    def get(self, check_id: str) -> Check | None:
        """
        One check's result.

        Args:
            check_id: Its id.

        Returns:
            The result, or None when it did not run.
        """
        return next((check for check in self.checks if check.id == check_id), None)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the report for the API and ``--json``.

        Returns:
            The checks, the counts and when they ran.
        """
        return {
            "checked_at": self.checked_at,
            "counts": self.counts(),
            "checks": [check.to_dict() for check in self.checks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckReport:
        """
        Read back what :meth:`to_dict` wrote, with the kept report's flags.

        Args:
            data: The report.

        Returns:
            The report.

        Raises:
            KeyError: A field is missing.
            TypeError: A field has the wrong shape.
        """
        checks: list[Check] = []
        for item in data["checks"]:
            fix = item.get("fix")
            accepted = item.get("accepted")
            checks.append(
                Check(
                    id=item["id"],
                    group=item["group"],
                    title=item["title"],
                    severity=item["severity"],
                    status=item["status"],
                    reason=item["reason"],
                    evidence=tuple(item.get("evidence") or ()),
                    fix=CheckFix(**{**fix, "steps": tuple(fix.get("steps") or ())})
                    if fix
                    else None,
                    accepted=AcceptedRisk(**accepted) if accepted else None,
                )
            )
        return cls(
            checks,
            str(data["checked_at"]),
            complete=bool(data.get("complete", True)),
            stale=bool(data.get("stale", False)),
        )


def _result(
    check_id: str,
    ok: bool,
    reason: str,
    evidence: list[str] | tuple[str, ...] = (),
    fix: CheckFix | None = None,
    *,
    severity: str | None = None,
) -> Check:
    """
    Build a check's result.

    Args:
        check_id: The check.
        ok: It passed.
        reason: What was found.
        evidence: What the system said.
        fix: How to fix it, when it did not pass.
        severity: This finding's severity, when not the catalog's.

    Returns:
        The result: ``pass``, or ``fail`` for a critical finding and ``warn``
        for any other.
    """
    spec = CATALOG[check_id]
    level = severity or spec.severity
    status = "pass" if ok else ("fail" if level == "critical" else "warn")
    return Check(
        id=check_id,
        group=spec.group,
        title=spec.title,
        severity=level,
        status=status,
        reason=reason,
        evidence=tuple(evidence),
        fix=None if ok else fix,
    )


def _other(check_id: str, status: str, reason: str, evidence: tuple[str, ...] = ()) -> Check:
    """
    Build an ``unknown`` or ``n/a`` result.

    Args:
        check_id: The check.
        status: ``unknown`` or ``n/a``.
        reason: Why.
        evidence: What the failing probe said.

    Returns:
        The result.
    """
    spec = CATALOG[check_id]
    return Check(check_id, spec.group, spec.title, spec.severity, status, reason, evidence)


def _action(summary: str, cli: str, endpoint: str) -> CheckFix:
    return CheckFix(kind="action", summary=summary, cli=cli, endpoint=endpoint)


def _guided(summary: str, *steps: str) -> CheckFix:
    return CheckFix(kind="guided", summary=summary, steps=steps)


class HardeningChecks:
    """
    Run every check against one look at the machine.

    Args:
        probe: This pass's look at the machine.
        risks: Accepted risks; the store's by default.
        console_port: The console's port, when known.
    """

    def __init__(
        self,
        probe: SecurityProbe,
        *,
        risks: AcceptedRisks | None = None,
        console_port: int | None = None,
    ) -> None:
        self.probe = probe
        self.risks = risks
        self.firewall = Firewall(probe, console_port=console_port)
        self.fail2ban = Fail2ban(probe)

    # Fixes that Noust applies ----------------------------------------------------

    def _ssh_fix(self, name: str) -> CheckFix:
        """
        The fix of an SSH check: automatic when its guard holds now, guided otherwise.

        Args:
            name: One of :data:`~noust.managers.server.security_ssh.SSH_FIXES`.

        Returns:
            The fix.
        """
        fix = SSH_FIXES[name]
        plan = plan_fix(self.probe, name)
        if plan.blockers:
            return CheckFix(
                kind="guided",
                summary=fix.title,
                steps=tuple(plan.guidance),
                cli=f"noust server security ssh harden {name}",
                blocked=" ".join(plan.blockers),
            )
        summary = fix.title
        if plan.proof and plan.proof.evidence:
            summary += f". {plan.proof.summary()}"
        return CheckFix(
            kind="automatic",
            summary=summary,
            action=f"ssh:{name}",
            cli=f"noust server security ssh harden {name}",
            reverts=True,
        )

    # SSH ------------------------------------------------------------------------

    def _ssh_checks(self) -> list[Check]:
        ids = [spec for spec in CATALOG if spec.startswith("ssh.")]
        try:
            effective = self.probe.effective("root")
        except SshdUnavailableError as exc:
            evidence = (exc.output,) if exc.output else ()
            return [_other(check_id, "unknown", exc.message, evidence) for check_id in ids]
        return [
            self._root_password(effective),
            self._password_auth(effective),
            self._root_login(effective),
            self._empty_passwords(effective),
            self._keys(),
            self._defaults(effective),
            self._loglevel(effective),
        ]

    def _password_lines(self, effective: SshdEffective) -> list[str]:
        return [
            effective.line("passwordauthentication"),
            effective.line(effective.kbd_keyword),
            effective.line("usepam"),
        ]

    def _root_password(self, effective: SshdEffective) -> Check:
        permit = effective.first("permitrootlogin")
        state = self.probe.accounts.password_state("root")
        evidence = [effective.line("permitrootlogin"), *self._password_lines(effective)]
        evidence.append(f"root password: {state}")
        exposed = permit == "yes" and effective.passwords_accepted and state != "locked"
        if not exposed:
            return _result(
                "ssh.root_password", True, "Root cannot log in over SSH with a password.", evidence
            )
        return _result(
            "ssh.root_password",
            False,
            "sshd lets root in with a password, and root has one.",
            evidence,
            self._ssh_fix("root-prohibit-password"),
        )

    def _password_auth(self, effective: SshdEffective) -> Check:
        evidence = self._password_lines(effective)
        if not effective.passwords_accepted:
            return _result("ssh.password_auth", True, "SSH accepts keys only.", evidence)
        return _result(
            "ssh.password_auth",
            False,
            "SSH accepts passwords, which can be guessed; keys cannot.",
            evidence,
            self._ssh_fix("disable-passwords"),
        )

    def _root_login(self, effective: SshdEffective) -> Check:
        permit = effective.first("permitrootlogin", "prohibit-password")
        evidence = [effective.line("permitrootlogin")]
        if permit == "no":
            return _result("ssh.root_login", True, "Root cannot log in over SSH.", evidence)
        return _result(
            "ssh.root_login",
            False,
            f"Root can log in over SSH ({permit}); logging in as a named account and using "
            "sudo leaves a record of who did what.",
            evidence,
            self._ssh_fix("root-no"),
        )

    def _empty_passwords(self, effective: SshdEffective) -> Check:
        empty = self.probe.accounts.empty_passwords()
        permit = effective.first("permitemptypasswords", "no")
        evidence = [effective.line("permitemptypasswords")]
        evidence += [f"{name}: empty password field in /etc/shadow" for name in empty]
        if permit != "yes" and not empty:
            return _result(
                "ssh.empty_passwords", True, "No account has an empty password.", evidence
            )
        if permit == "yes":
            return _result(
                "ssh.empty_passwords",
                False,
                "sshd accepts logins with an empty password.",
                evidence,
                self._ssh_fix("no-empty-passwords"),
            )
        return _result(
            "ssh.empty_passwords",
            False,
            f"{len(empty)} account(s) have no password at all, so anything that accepts "
            "passwords lets anyone in as them.",
            evidence,
            _guided(
                "Lock those passwords (keys keep working)",
                *[f"passwd -l {name}" for name in empty],
            ),
        )

    def _keys(self) -> Check:
        problems: list[str] = []
        operator_keys = 0
        for entry in self.probe.account_keys():
            for file in entry.files:
                if file.error:
                    problems.append(f"{file.path}: {file.error}")
                if file.keys and file.problems:
                    problems.extend(f"{file.path}: {problem}" for problem in file.problems)
                for key in file.keys:
                    if key.weak:
                        problems.append(
                            f"{entry.account.name}: {key.fingerprint} ({key.comment or key.key_type}): {key.weak}"
                        )
            operator_keys += len(entry.usable_operator_keys())
        if operator_keys == 0:
            problems.append("No administrator has an operator key sshd would accept.")
        if not problems:
            return _result(
                "ssh.keys", True, f"{operator_keys} operator key(s), none weak or ignored."
            )
        return _result(
            "ssh.keys",
            False,
            "Some SSH keys are weak, ignored by StrictModes, or missing.",
            problems,
            _guided(
                "Replace weak keys and fix the permissions sshd refuses",
                "Create a key on your computer: ssh-keygen -t ed25519",
                "Add it: noust server security ssh add-key --user <you> --file ~/.ssh/id_ed25519.pub",
                "Remove a weak one: noust server security ssh remove-key <fingerprint> --user <you>",
                "Fix the permissions named above with the chmod or chown shown next to each.",
            ),
        )

    def _defaults(self, effective: SshdEffective) -> Check:
        loose: list[str] = []
        tries = effective.first("maxauthtries", "6")
        grace = effective.first("logingracetime", "120")
        if tries.isdigit() and int(tries) > MAX_AUTH_TRIES:
            loose.append(effective.line("maxauthtries"))
        if grace.isdigit() and int(grace) > MAX_LOGIN_GRACE:
            loose.append(effective.line("logingracetime"))
        if effective.first("x11forwarding", "no") == "yes":
            loose.append(effective.line("x11forwarding"))
        if effective.first("clientaliveinterval", "0") in ("0", ""):
            loose.append(effective.line("clientaliveinterval"))
        if not loose:
            return _result("ssh.defaults", True, "sshd's limits are tight.")
        return _result(
            "ssh.defaults",
            False,
            "Some of sshd's limits are looser than CIS and Mozilla recommend.",
            loose,
            self._ssh_fix("sensible-defaults"),
        )

    def _loglevel(self, effective: SshdEffective) -> Check:
        level = effective.first("loglevel", "INFO").upper()
        evidence = [effective.line("loglevel")]
        if level == "VERBOSE" or level.startswith("DEBUG"):
            return _result("ssh.loglevel", True, "sshd logs the key of every login.", evidence)
        return _result(
            "ssh.loglevel",
            False,
            "sshd does not log which key opened each session, only that one did.",
            evidence,
            self._ssh_fix("verbose-logging"),
        )

    # fail2ban -------------------------------------------------------------------

    def _fail2ban_checks(self, exposures: list[PortExposure]) -> list[Check]:
        state = self.fail2ban.state()
        install = CheckFix(
            kind="automatic" if state.install_supported else "guided",
            summary="Install fail2ban with an sshd jail that never bans who is connected now"
            + (" (enables EPEL, after asking)" if state.needs_epel else ""),
            action="fail2ban:install" if state.install_supported else None,
            steps=(state.install_hint,) if state.install_hint else (),
            cli="noust server security fail2ban install",
            blocked=state.install_hint,
        )
        evidence = [f"{name}: active" for name in state.substitutes]
        if state.error:
            evidence.append(state.error)
        protected = state.running or bool(state.substitutes)
        public_ssh = any(
            exposure.baseline and exposure.reachable and exposure.port in self.probe.ssh_ports()
            for exposure in exposures
        )
        passwords = self._passwords_accepted()
        missing = _result(
            "f2b.missing",
            protected,
            "fail2ban runs."
            if state.running
            else (
                f"{', '.join(state.substitutes)} protects SSH."
                if state.substitutes
                else "Nothing bans an address that keeps guessing SSH passwords."
            ),
            evidence,
            install,
            severity="critical" if public_ssh and passwords and not protected else None,
        )
        if not state.running:
            jail = _other("f2b.no_sshd_jail", "n/a", "fail2ban is not running.")
        else:
            sshd = state.jail("sshd")
            jails = ", ".join(jail.name for jail in state.jails) or "none"
            if sshd is not None and sshd.reads:
                jail = _result(
                    "f2b.no_sshd_jail",
                    True,
                    f"The sshd jail watches {sshd.reads}.",
                    [f"Jail list: {jails}"],
                )
            else:
                jail = _result(
                    "f2b.no_sshd_jail",
                    False,
                    "fail2ban has no sshd jail, or the one it has reads nothing.",
                    [f"Jail list: {jails}"],
                    install,
                )
        return [missing, jail]

    def _passwords_accepted(self) -> bool:
        try:
            return self.probe.effective().passwords_accepted
        except SshdUnavailableError:
            return True

    # Firewall -------------------------------------------------------------------

    def _firewall_checks(self) -> tuple[list[Check], list[PortExposure]]:
        state = self.firewall.state()
        exposures, error = self.firewall.exposures()
        results: list[Check] = []
        if error:
            listeners_unknown = _other(
                "fw.public_listener",
                "unknown",
                "The listening sockets could not be read.",
                (error,),
            )
        else:
            listeners_unknown = None

        public_extra = [
            exposure for exposure in exposures if exposure.reachable and not exposure.baseline
        ]
        status_line = f"{state.backend}: {'active' if state.active else 'inactive'}" + (
            f", default {state.default_incoming} (incoming)" if state.default_incoming else ""
        )
        if state.error:
            results.append(
                _other("fw.inactive", "unknown", "The firewall could not be read.", (state.error,))
            )
        elif state.active:
            results.append(
                _result("fw.inactive", True, f"{state.backend} is active.", [status_line])
            )
        else:
            if state.backend == "ufw" and state.installed:
                fix = CheckFix(
                    kind="automatic",
                    summary="Turn on ufw, allowing SSH and a public console first",
                    action="firewall:enable",
                    cli="noust server security firewall enable",
                    reverts=True,
                )
            else:
                fix = _guided(
                    "Install a firewall and turn it on, allowing SSH first",
                    "Debian and Ubuntu: apt-get install ufw, then noust server security firewall enable",
                    "RHEL, Fedora and SUSE: firewall-offline-cmd --add-service=ssh; "
                    "systemctl enable --now firewalld",
                )
            results.append(
                _result(
                    "fw.inactive",
                    False,
                    "No firewall filters incoming traffic"
                    + (
                        f", and {len(public_extra)} port(s) beyond SSH and the web answer everyone."
                        if public_extra
                        else "."
                    ),
                    [status_line, *[_describe(exposure) for exposure in public_extra]],
                    fix,
                    severity="critical" if public_extra else "warning",
                )
            )

        # A database Docker publishes with no firewall at all is reachable the
        # same way a native one is; around an active firewall it is the bypass
        # check's finding instead.
        risky = [
            exposure
            for exposure in exposures
            if exposure.risky and exposure.verdict in ("open", "no_firewall", "open_to")
        ]
        if listeners_unknown is not None:
            results.append(listeners_unknown)
        elif not risky:
            results.append(
                _result(
                    "fw.public_listener",
                    True,
                    "No database or internal service answers the internet.",
                )
            )
        else:
            only_restricted = all(exposure.verdict == "open_to" for exposure in risky)
            results.append(
                _result(
                    "fw.public_listener",
                    False,
                    "These services listen on every interface and the firewall lets "
                    + ("some addresses" if only_restricted else "anyone")
                    + " reach them.",
                    [_describe(exposure) for exposure in risky],
                    _guided(
                        "Bind each one to 127.0.0.1, or allow only the addresses that need it",
                        "PostgreSQL: listen_addresses = 'localhost' in postgresql.conf, then "
                        "systemctl restart postgresql",
                        "MySQL/MariaDB: bind-address = 127.0.0.1 in its server .cnf, then restart it",
                        "Redis: bind 127.0.0.1 ::1 in redis.conf, then restart it",
                        "Or allow one address only: noust server security firewall allow <port> "
                        "--from <address>",
                    ),
                    severity="warning" if only_restricted else None,
                )
            )

        bypass = [exposure for exposure in exposures if exposure.verdict == "docker_bypass"]
        if not bypass:
            results.append(
                _result(
                    "fw.docker_bypass",
                    True,
                    "Docker publishes no port around the firewall."
                    if state.active
                    else "No firewall is active for Docker to get around.",
                )
            )
        else:
            results.append(
                _result(
                    "fw.docker_bypass",
                    False,
                    f"Docker publishes {len(bypass)} port(s) on every interface; {state.backend} "
                    "is active but does not filter them, because Docker's rules come first.",
                    [_describe(exposure) for exposure in bypass],
                    _guided(
                        "Publish on 127.0.0.1 only",
                        'In the Compose file, write the port as "127.0.0.1:<host port>:<container '
                        'port>", then docker compose up -d',
                        'For every container at once: "ip": "127.0.0.1" in '
                        "/etc/docker/daemon.json, then systemctl restart docker (restarts every "
                        "container)",
                        "Reading: https://docs.docker.com/engine/network/packet-filtering-firewalls/",
                    ),
                    severity="critical"
                    if any(exposure.risky for exposure in bypass)
                    else "warning",
                )
            )

        results.append(self._ipv6(state, exposures))
        results.append(self._console_public(exposures))
        return results, exposures

    def _ipv6(self, state: FirewallState, exposures: list[PortExposure]) -> Check:
        if state.backend != "ufw":
            return _other("fw.ipv6_mismatch", "n/a", "Only ufw can be set to leave IPv6 alone.")
        off = any("IPV6=no" in warning for warning in state.warnings)
        on_v6 = [
            exposure
            for exposure in exposures
            if exposure.address in ("::", "*") and not exposure.docker
        ]
        if not off or not on_v6:
            return _result("fw.ipv6_mismatch", True, "ufw filters IPv6 too.")
        return _result(
            "fw.ipv6_mismatch",
            False,
            "ufw is set to IPV6=no, and services listen on IPv6.",
            ["/etc/default/ufw: IPV6=no", *[_describe(exposure) for exposure in on_v6]],
            _guided(
                "Let ufw filter IPv6",
                "Set IPV6=yes in /etc/default/ufw",
                "ufw reload",
            ),
        )

    def _console_public(self, exposures: list[PortExposure]) -> Check:
        console = self.firewall.console_sockets()
        mine = [
            exposure
            for exposure in exposures
            if exposure.docker is None
            and console.holds(exposure.proto, exposure.port, exposure.address)
        ]
        public = [exposure for exposure in mine if exposure.verdict != "local"]
        # How the console was found is part of the evidence whenever the
        # configured port, and not its processes, decided.
        note = [console.note] if console.note else []
        if not public:
            return _result(
                "fw.console_public",
                True,
                "The console listens on loopback only."
                if mine
                else "No console socket answers on the network.",
                [*(_describe(exposure) for exposure in mine), *note],
            )
        unit = self.probe.runner.run(
            ["systemctl", "show", f"{paths.WEB_UNIT}.service", "--property=ExecStart"],
            timeout=PROBE_TIMEOUT,
        )
        # A pair of its own or one Noust minted: both are TLS.
        tls = "--tls-cert" in unit.stdout or "--self-signed" in unit.stdout
        if tls:
            return _result(
                "fw.console_public",
                True,
                "The console answers on the network, over TLS.",
                [*(_describe(exposure) for exposure in public), *note],
            )
        return _result(
            "fw.console_public",
            False,
            "The console answers on the network without TLS: sign-in tokens cross it in clear.",
            [*(_describe(exposure) for exposure in public), *note],
            _guided(
                "Serve the console on loopback behind a TLS site, or give it a certificate",
                "noust web enable --host 127.0.0.1 (then reach it through an SSH tunnel or a site)",
                "Or: noust web enable --tls-cert <fullchain.pem> --tls-key <privkey.pem>",
            ),
        )

    # Updates and the system (B3a's managers) -------------------------------------

    def _update_checks(self) -> list[Check]:
        from noust.managers.server.updates import UpdatesManager

        ids = ("upd.security_pending", "upd.pkg_broken", "upd.lists_stale")
        manager = UpdatesManager(runner=self.probe.runner, host=self.probe.host)
        results: list[Check] = []
        try:
            if not manager.platform.updates_supported:
                reason = manager.platform.why_updates_unsupported()
                return [
                    _other(check_id, "n/a", reason)
                    for check_id in (
                        *ids,
                        "upd.reboot_required",
                        "upd.stale_services",
                        "upd.auto_disabled",
                    )
                ]
            pending = manager.pending()
        except _PROBE_ERRORS as exc:
            results += [_other(check_id, "unknown", _message(exc)) for check_id in ids]
        else:
            security = [package for package in pending.packages if package.security]
            serious = [
                package
                for package in security
                if package.kernel
                or (package.severity or "").lower() in ("critical", "important", "high")
            ]
            results.append(
                _result(
                    "upd.security_pending",
                    not security,
                    f"{len(security)} security update(s) are waiting."
                    if security
                    else "No security update is waiting.",
                    [f"{p.name} {p.installed or ''} -> {p.candidate}" for p in security[:10]],
                    _action(
                        "Install the security updates",
                        "noust server updates apply --security",
                        "POST /api/server/updates/apply",
                    ),
                    severity=None if serious or not security else "warning",
                )
            )
            results.append(
                _result(
                    "upd.pkg_broken",
                    not pending.broken,
                    "The package database is half configured."
                    if pending.broken
                    else "Packages are consistent.",
                    list(pending.notes),
                    _action(
                        "Repair it",
                        "noust server updates repair",
                        "POST /api/server/updates/repair",
                    ),
                )
            )
            age = pending.lists_age_seconds
            stale = age is not None and age > STALE_DAYS * 86400
            results.append(
                _result(
                    "upd.lists_stale",
                    not stale,
                    f"The package lists are {age // 86400 if age else 0} days old."
                    if stale
                    else "The package lists are recent.",
                    fix=_action(
                        "Refresh them",
                        "noust server updates refresh",
                        "POST /api/server/updates/refresh",
                    ),
                )
            )
        try:
            probe = manager.restart_probe()
        except _PROBE_ERRORS as exc:
            results += [
                _other("upd.reboot_required", "unknown", _message(exc)),
                _other("upd.stale_services", "unknown", _message(exc)),
            ]
        else:
            reboot = probe.reboot
            overdue = False
            if reboot.required and reboot.since:
                try:
                    since = datetime.fromisoformat(reboot.since.replace("Z", "+00:00"))
                    overdue = (self._now() - since).days >= STALE_DAYS
                except ValueError:
                    overdue = False
            results.append(
                _result(
                    "upd.reboot_required",
                    not reboot.required,
                    "A reboot is needed for installed updates to take effect."
                    if reboot.required
                    else "No reboot is pending.",
                    [*reboot.reasons, *reboot.packages],
                    _action(
                        "Schedule a reboot",
                        "noust server reboot --in 5m",
                        "POST /api/server/power/reboot",
                    ),
                    severity="critical" if overdue else None,
                )
            )
            results.append(
                _result(
                    "upd.stale_services",
                    not probe.services,
                    f"{len(probe.services)} service(s) still run replaced libraries."
                    if probe.services
                    else "No service runs replaced libraries.",
                    list(probe.services),
                    _action(
                        "Restart them",
                        "noust server restart-services",
                        "POST /api/server/updates/restart-services",
                    ),
                )
            )
        try:
            auto = manager.backend.auto_status()
        except _PROBE_ERRORS as exc:
            results.append(_other("upd.auto_disabled", "unknown", _message(exc)))
        else:
            if not auto.supported and not auto.installed:
                results.append(
                    _other("upd.auto_disabled", "n/a", auto.detail or "Not available here.")
                )
            else:
                results.append(
                    _result(
                        "upd.auto_disabled",
                        auto.enabled,
                        f"{auto.mechanism} is on."
                        if auto.enabled
                        else "Security updates are not installed automatically.",
                        [auto.detail] if auto.detail else [],
                        _action(
                            "Turn on automatic security updates",
                            "noust server updates auto enable --security-only",
                            "PUT /api/server/updates/auto",
                        ),
                    )
                )
        return results

    def _system_checks(self) -> list[Check]:
        from noust.managers.server.clock import ClockManager
        from noust.managers.server.eol import status_of
        from noust.managers.server.storage import StorageManager
        from noust.managers.server.swap import SwapManager

        runner = self.probe.runner
        host = self.probe.host
        results: list[Check] = []
        try:
            eol = status_of(read_os_release(host), today=self._now().date())
            if eol.status in ("rolling", "unknown"):
                results.append(_other("os.eol", "n/a", f"End of support: {eol.status}."))
            else:
                results.append(
                    _result(
                        "os.eol",
                        eol.status == "ok",
                        f"Security updates for this release end on {eol.end_date}"
                        + (" - they already ended." if eol.status == "expired" else "."),
                        [f"{read_os_release(host).pretty_name}: {eol.end_date} ({eol.source})"],
                        _guided(
                            "Move to a supported release",
                            "Build a new server on the current release, move the applications "
                            "with noust app export and noust import, then retire this one.",
                        ),
                        severity="warning" if eol.status == "warn" else None,
                    )
                )
        except _PROBE_ERRORS as exc:
            results.append(_other("os.eol", "unknown", _message(exc)))
        try:
            clock = ClockManager(runner=runner, host=host).status()
            results.append(
                _result(
                    "time.unsynced",
                    clock.synchronized,
                    "The clock is synchronised."
                    if clock.synchronized
                    else "The clock is not synchronised with any time source.",
                    [f"NTP enabled: {'yes' if clock.ntp_enabled else 'no'}"],
                    _action(
                        "Turn on time synchronisation",
                        "noust server time ntp on",
                        "PUT /api/server/time",
                    ),
                )
            )
        except _PROBE_ERRORS as exc:
            results.append(_other("time.unsynced", "unknown", _message(exc)))
        try:
            swap = SwapManager(runner=runner, host=host).status()
            results.append(
                _result(
                    "mem.no_swap",
                    not swap.recommended,
                    "Little memory and no swap: a build can be killed for memory."
                    if swap.recommended
                    else "Swap is not needed or is in place.",
                    [
                        f"RAM: {swap.memory_bytes // 2**20} MiB, swap: {swap.total_bytes // 2**20} MiB"
                    ],
                    _action(
                        "Create a swap file", "noust server swap create", "POST /api/server/swap"
                    ),
                )
            )
        except _PROBE_ERRORS as exc:
            results.append(_other("mem.no_swap", "unknown", _message(exc)))
        try:
            mounts = StorageManager(runner=runner, host=host).mounts()
            full = [mount for mount in mounts if mount.status in ("warn", "critical")]
            results.append(
                _result(
                    "disk.full",
                    not full,
                    f"{len(full)} filesystem(s) are nearly full."
                    if full
                    else "Every filesystem has room.",
                    [
                        f"{m.mount_point}: {m.percent_used:.0f}% used, inodes {m.inodes_percent:.0f}%"
                        for m in full
                    ],
                    _action(
                        "Free space", "noust server disk clean", "POST /api/server/storage/cleanup"
                    ),
                    severity="critical" if any(m.status == "critical" for m in full) else None,
                )
            )
        except _PROBE_ERRORS as exc:
            results.append(_other("disk.full", "unknown", _message(exc)))
        results.append(self._degraded())
        results.append(self._uid0())
        results.append(self._selinux())
        results.append(self._journal())
        return results

    def _degraded(self) -> Check:
        state = self.probe.runner.run(["systemctl", "is-system-running"], timeout=PROBE_TIMEOUT)
        answer = state.stdout.strip()
        if answer != "degraded":
            return _result("sys.degraded", True, f"systemd reports {answer or 'no state'}.")
        failed = self.probe.runner.run(
            ["systemctl", "list-units", "--failed", "--plain", "--no-legend"], timeout=PROBE_TIMEOUT
        )
        units = [line.split()[0] for line in failed.stdout.splitlines() if line.strip()]
        return _result(
            "sys.degraded",
            False,
            f"{len(units)} unit(s) failed.",
            units,
            _action(
                "See the failed services",
                "noust server units --failed",
                "GET /api/server/units?state=failed",
            ),
        )

    def _uid0(self) -> Check:
        others = self.probe.accounts.other_uid0()
        return _result(
            "sys.uid0",
            not others,
            f"{', '.join(others)} also have user id 0: each is root under another name."
            if others
            else "Only root has user id 0.",
            [f"{name}: uid 0 in /etc/passwd" for name in others],
            _guided(
                "Remove the extra uid 0 accounts or give them their own id",
                *[f"userdel {name} (or usermod -u <new id> {name})" for name in others],
            ),
        )

    def _selinux(self) -> Check:
        if not self.probe.runner.exists("getenforce"):
            return _other("sys.selinux", "n/a", "SELinux is not part of this system.")
        mode = self.probe.runner.run(["getenforce"], timeout=PROBE_TIMEOUT).stdout.strip()
        return _result(
            "sys.selinux",
            mode == "Enforcing",
            f"SELinux is {mode or 'unknown'}.",
            [f"getenforce: {mode}"],
            _guided(
                "Turn SELinux to enforcing",
                "Check what it would deny first: ausearch -m avc -ts recent",
                "setenforce 1, and SELINUX=enforcing in /etc/selinux/config",
            ),
        )

    def _journal(self) -> Check:
        persistent = self.probe.host.journal_dir.is_dir()
        return _result(
            "sys.journal_volatile",
            persistent,
            "The journal is kept across reboots."
            if persistent
            else "The journal lives in memory and is lost at every reboot.",
            fix=_guided(
                "Make the journal persistent",
                "mkdir -p /var/log/journal",
                "systemd-tmpfiles --create --prefix /var/log/journal",
                "systemctl restart systemd-journald",
            ),
        )

    # Noust ------------------------------------------------------------------------

    def _noust_checks(self) -> list[Check]:
        results: list[Check] = []
        enabled = self.probe.runner.run(
            ["systemctl", "is-enabled", f"{paths.WEB_UNIT}.service"], timeout=PROBE_TIMEOUT
        )
        answer = enabled.stdout.strip()
        if answer == "enabled":
            results.append(
                _result("noust.web_not_unit", True, "The console starts with the server.")
            )
        elif answer in ("", "not-found") and not _console_configured():
            results.append(
                _other("noust.web_not_unit", "n/a", "The console is not set up on this server.")
            )
        else:
            results.append(
                _result(
                    "noust.web_not_unit",
                    False,
                    "The console does not start when the server boots.",
                    [f"systemctl is-enabled {paths.WEB_UNIT}: {answer or enabled.stderr.strip()}"],
                    _guided("Install the console as a service", "noust web enable"),
                )
            )
        try:
            from noust.managers.server.power import unenabled_app_units

            missing = unenabled_app_units(self.probe.runner)
            results.append(
                _result(
                    "noust.units_not_enabled",
                    not missing,
                    f"{len(missing)} application service(s) would not start after a reboot."
                    if missing
                    else "Every application service starts with the server.",
                    missing,
                    _guided(
                        "Enable them",
                        *[f"systemctl enable {unit}.service" for unit in missing],
                    ),
                )
            )
        except _PROBE_ERRORS as exc:
            results.append(_other("noust.units_not_enabled", "unknown", _message(exc)))
        root = self.probe.keys_of("root")
        centrals = root.keys("central") if root else []
        removals = [
            command
            for file in (root.files if root else ())
            for key in file.keys
            if key.kind == "central"
            and (command := key_removal_command(file.path, key.blob)) is not None
        ]
        results.append(
            _result(
                "noust.fleet_key_root",
                not centrals,
                f"{len(centrals)} central(s) tunnel in as root: such a key can make sshd "
                "create a Unix socket as root anywhere."
                if centrals
                else "No central tunnels in as root.",
                [f"{key.comment}: {key.fingerprint}" for key in centrals],
                _guided(
                    "Move the tunnel to the unprivileged account, then take the key out of "
                    "root's file",
                    "On the central: noust node migrate-tunnel <this node>, run what it prints "
                    "here (its --replace-root-key takes the old key out of root's file), and "
                    "paste the new join code there.",
                    "A key no central uses as root any more (its node already reached as "
                    "noust-tunnel) is removed here with:",
                    *removals,
                ),
            )
        )
        return results

    # The run ---------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.probe.now(), tz=timezone.utc)

    def run(self, *, host_checks: bool = True) -> CheckReport:
        """
        Run every check.

        Args:
            host_checks: Also run the updates and system checks, which ask the
                package manager (seconds). False runs only what this module
                probes itself.

        Returns:
            The report, in catalog order, accepted risks applied.
        """
        found: dict[str, Check] = {}
        for check in self._ssh_checks():
            found[check.id] = check
        firewall, exposures = self._firewall_checks()
        for check in firewall:
            found[check.id] = check
        for check in self._fail2ban_checks(exposures):
            found[check.id] = check
        if host_checks:
            for check in self._update_checks():
                found[check.id] = check
            for check in self._system_checks():
                found[check.id] = check
        else:
            for check in (self._uid0(), self._journal()):
                found[check.id] = check
        for check in self._noust_checks():
            found[check.id] = check
        accepted = self._accepted()
        ordered = [
            _apply_risk(found[check_id], accepted.get(check_id))
            for check_id in CATALOG
            if check_id in found
        ]
        return CheckReport(ordered, self._now().isoformat(timespec="seconds"), complete=host_checks)

    def _accepted(self) -> dict[str, AcceptedRisk]:
        risks = self.risks or AcceptedRisks()
        try:
            return risks.active()
        except _PROBE_ERRORS:
            return {}


def _apply_risk(check: Check, risk: AcceptedRisk | None) -> Check:
    """
    Show a failing check as accepted while its acceptance holds.

    Args:
        check: The result.
        risk: Its acceptance, if any.

    Returns:
        The result, ``accepted`` when it failed and an acceptance holds.
    """
    if risk is None or check.status not in ("fail", "warn"):
        return check
    return Check(
        id=check.id,
        group=check.group,
        title=check.title,
        severity=check.severity,
        status="accepted",
        reason=check.reason,
        evidence=check.evidence,
        fix=check.fix,
        accepted=risk,
    )


def _describe(exposure: PortExposure) -> str:
    """
    One line about an open port.

    Args:
        exposure: The port.

    Returns:
        Such as ``0.0.0.0:5432/tcp postgres (PostgreSQL): open to everyone``.
    """
    address = f"[{exposure.address}]" if ":" in exposure.address else exposure.address
    label = f" ({exposure.risky})" if exposure.risky else ""
    verdicts = {
        "open": "open to everyone",
        "open_to": "open to " + ", ".join(exposure.sources),
        "no_firewall": "no firewall",
        "docker_bypass": "published by Docker around the firewall",
        "blocked": "blocked by the firewall",
        "local": "local only",
    }
    return (
        f"{address}:{exposure.port}/{exposure.proto} {exposure.process or 'unknown'}{label}: "
        f"{verdicts.get(exposure.verdict, exposure.verdict)}"
    )


def _message(exc: BaseException) -> str:
    """
    The sentence of a probe's error.

    Args:
        exc: The error.

    Returns:
        Its message, with a tool's output after it when there is one.
    """
    if isinstance(exc, NoustError):
        return f"{exc.message} {exc.output or ''}".strip()
    return str(exc)


def _console_configured() -> bool:
    """
    Report whether the console is meant to run on this server.

    Returns:
        ``web.enabled`` in the configuration.
    """
    try:
        from noust.core.config import Config

        return bool(Config().get("web.enabled", False))
    except _PROBE_ERRORS:
        return False


# The shared list ------------------------------------------------------------------

_cache: CheckReport | None = None
_cache_lock = threading.Lock()
_refresh: threading.Thread | None = None

#: What a kept report may fail to read with: a file half written by a crash or
#: by hand, one from another release.
_READ_ERRORS = (OSError, ValueError, KeyError, TypeError)


def report_path() -> Path:
    """
    Where the complete report is kept: beside the store, like the security ledger.

    Returns:
        ``<store directory>/security/checks.json``.
    """
    from noust.core.store import get_store

    return get_store().db_path.parent / "security" / "checks.json"


def _read_kept() -> CheckReport | None:
    """
    Read the kept report.

    Returns:
        It, or None when there is none or it cannot be read.
    """
    try:
        path = report_path()
        if not path.is_file():
            return None
        return CheckReport.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (*_READ_ERRORS, NoustError, sqlite3.Error) as exc:
        log.warning("The kept security report could not be read: %s", exc)
        return None


def _keep(report: CheckReport) -> None:
    """
    Keep a complete report for every process: root only, it lists the server's weaknesses.

    Args:
        report: The report.
    """
    data = {**report.to_dict(), "complete": report.complete, "stale": report.stale}
    fs = get_fs()
    try:
        fs.write_text(report_path(), json.dumps(data), mode=0o600)
    except (OSError, NoustError, sqlite3.Error) as exc:
        # This process still has it; the others run the checks themselves.
        log.warning("The security report could not be kept: %s", exc)


def run_checks(
    probe: SecurityProbe | None = None,
    *,
    risks: AcceptedRisks | None = None,
    host_checks: bool = True,
    console_port: int | None = None,
) -> CheckReport:
    """
    Run every hardening check and remember the result.

    This is what the console, ``noust server security checks``, ``noust
    health``, the ENS compliance check and the monitor's run call. A complete
    report is kept for every process; a quick one only in this one, or it
    would hide the package manager's findings from everyone else.

    Args:
        probe: The look at the machine; a fresh one by default.
        risks: Accepted risks; the store's by default.
        host_checks: Also ask the package manager and the system managers.
        console_port: The console's configured port, when known.

    Returns:
        The report.
    """
    global _cache
    report = HardeningChecks(probe or SecurityProbe(), risks=risks, console_port=console_port).run(
        host_checks=host_checks
    )
    with _cache_lock:
        _cache = report
    if report.complete:
        _keep(report)
    return report


def hardening_checks(**kwargs: Any) -> list[Check]:
    """
    The list of checks, for a consumer that only wants the list (the ENS check).

    Args:
        **kwargs: Passed to :func:`run_checks`.

    Returns:
        The checks, in catalog order.
    """
    return run_checks(**kwargs).checks


def cached_report() -> CheckReport | None:
    """
    The last report, without probing anything: this process's or the kept one, the newer.

    Returns:
        The report, or None when no check has run yet.
    """
    with _cache_lock:
        memory = _cache
    kept = _read_kept()
    if memory is None or kept is None:
        return memory or kept
    if kept.checked_at > memory.checked_at or (kept.stale and kept.checked_at == memory.checked_at):
        return kept
    return memory


def forget_memory() -> None:
    """Drop this process's report; the kept one stays."""
    global _cache
    with _cache_lock:
        _cache = None


def forget_report() -> None:
    """Drop the remembered report, here and the kept one, so the next reader computes it again."""
    forget_memory()
    fs = get_fs()
    try:
        fs.remove(report_path())
    except (OSError, NoustError, sqlite3.Error) as exc:
        log.warning("The kept security report could not be removed: %s", exc)


def mark_report_stale() -> None:
    """
    Say the report no longer describes the server: something was just changed.

    The report stays readable (the console shows it while the checks run
    again), marked stale, so the monitor and the console run them again and
    :meth:`CheckReport.due` is true everywhere.
    """
    forget_memory()
    kept = _read_kept()
    if kept is not None and not kept.stale:
        kept.stale = True
        _keep(kept)


def refreshing() -> bool:
    """
    Report whether a background run started by this process is in flight.

    Returns:
        True while it runs.
    """
    with _cache_lock:
        return _refresh is not None and _refresh.is_alive()


def refresh_in_background(run: Callable[[], object]) -> bool:
    """
    Run the checks on a thread of this process, unless a run is already in flight.

    Args:
        run: Runs them (and keeps the report, through :func:`run_checks`).

    Returns:
        True: a run is in flight, this one or the one before.
    """
    global _refresh

    def target() -> None:
        # A thread's error boundary: nothing above it would see the error.
        try:
            run()
        except (NoustError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
            log.warning("The security checks could not run in the background: %s", exc)

    with _cache_lock:
        if _refresh is not None and _refresh.is_alive():
            return True
        _refresh = threading.Thread(target=target, name="noust-security-checks", daemon=True)
        _refresh.start()
    return True


def wait_for_refresh(timeout: float | None = REFRESH_WAIT) -> bool:
    """
    Wait for this process's background run, if one is in flight.

    Args:
        timeout: Seconds to wait at most; None for as long as it takes.

    Returns:
        True when no run is in flight any more.
    """
    with _cache_lock:
        thread = _refresh
    if thread is not None:
        thread.join(timeout)
        return not thread.is_alive()
    return True


def refresh_command(python: str | None = None) -> list[str]:
    """
    The ``systemd-run`` command that runs the complete checks in a unit of their own.

    The monitor runs sandboxed, read-only for most of the system; the tools
    the probes run (ufw, the package managers) take locks and write caches.
    A transient unit runs them as root outside that sandbox, once at a time
    (the unit name is the lock), and is removed when it ends.

    Args:
        python: The interpreter; the one running this code by default.

    Returns:
        The argv.
    """
    argv = [
        "systemd-run",
        f"--unit={paths.SECURITY_CHECKS_UNIT}",
        "--description=Noust security checks",
        "--collect",
        "--quiet",
        "--no-block",
        "--property=Nice=10",
    ]
    data_dir = paths.getenv(paths.DATA_DIR_ENV)
    if data_dir:
        argv.append(f"--setenv={paths.DATA_DIR_ENV}={data_dir}")
    return [*argv, "--", python or sys.executable, "-m", MODULE, "refresh"]


def reapply_risks(report: CheckReport, risks: AcceptedRisks | None = None) -> CheckReport:
    """
    Recompute a report's accepted states without probing again.

    Args:
        report: A report.
        risks: Accepted risks; the store's by default.

    Returns:
        The report with each check's acceptance as it is now.
    """
    try:
        accepted = (risks or AcceptedRisks()).active()
    except _PROBE_ERRORS:
        accepted = {}
    checks: list[Check] = []
    for check in report.checks:
        base = check
        if check.status == "accepted":
            base = Check(
                id=check.id,
                group=check.group,
                title=check.title,
                severity=check.severity,
                status="fail" if check.severity == "critical" else "warn",
                reason=check.reason,
                evidence=check.evidence,
                fix=check.fix,
            )
        checks.append(_apply_risk(base, accepted.get(check.id)))
    updated = CheckReport(checks, report.checked_at, report.complete, report.stale)
    global _cache
    with _cache_lock:
        if _cache is report:
            _cache = updated
    return updated


@dataclass(frozen=True)
class HardeningSummary:
    """
    What ``noust health`` and ``GET /api/system/health`` say about hardening.

    Attributes:
        checked_at: When the checks ran, or None when they have not.
        critical: The titles of the critical findings.
        warnings: The titles of the other findings.
        accepted: How many findings are accepted risks.
    """

    checked_at: str | None
    critical: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    accepted: int = 0


def summarize(report: CheckReport | None) -> HardeningSummary:
    """
    Reduce a report to what the health check shows.

    Args:
        report: The report, or None.

    Returns:
        The summary.
    """
    if report is None:
        return HardeningSummary(checked_at=None)
    return HardeningSummary(
        checked_at=report.checked_at,
        critical=[check.title for check in report.checks if check.status == "fail"],
        warnings=[check.title for check in report.checks if check.status == "warn"],
        accepted=sum(1 for check in report.checks if check.status == "accepted"),
    )


def main(argv: list[str] | None = None) -> int:
    """
    Run the complete checks and keep the report: what the monitor's transient unit runs.

    Args:
        argv: ``refresh``.

    Returns:
        0 once the report is kept, whatever it found (a finding is not a
        failure of the unit).
    """
    parser = argparse.ArgumentParser(prog=f"python -m {MODULE}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("refresh", help="Run every hardening check and keep the report.")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = run_checks()
    counts = report.counts()
    log.info(
        "%s critical, %s warning(s), %s accepted, %s unknown, %s passed",
        counts["critical"],
        counts["warning"],
        counts["accepted"],
        counts["unknown"],
        counts["passed"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
