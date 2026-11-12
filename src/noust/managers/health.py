# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The machine-wide health check ``noust health`` and the panel both report.

This used to live entirely inside the ``noust health`` command handler, which
is where ``GET /api/system/health`` would have had to duplicate it rather
than call it. The checking logic - disk space, the web servers, every
deployed application, certificates close to expiry, memory pressure - moved
here unchanged; :mod:`noust.cli.commands.health` now only formats and prints
:class:`HealthReport`, and the endpoint serialises the same object to JSON.

Since 3.1 the report also counts the server's hardening findings, from the one
list of checks (:mod:`noust.managers.server.security_checks`). They arrive as
warnings, never as issues: a server without a firewall is exposed, not broken,
and ``noust health``'s exit code - which monitoring scripts read - still means
"something is down". The console never probes for them on this path; it reads
the checks it last ran (``hardening="cached"``), while ``noust health`` runs
the quick ones itself (``hardening="live"``).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from noust.core import cert_life, descriptors, paths
from noust.core.app_state import RUNNING, RUNNING_UNMANAGED, STATIC, resolve_states
from noust.core.config import Config
from noust.core.domain_registrations import Registration, RegistrationStore
from noust.core.exceptions import NoustError
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, WebServer, get_store
from noust.managers.apache_manager import ApacheManager
from noust.managers.cert_manager import CertificateInfo, CertManager
from noust.managers.cert_renewal import FAILED, TIMER_STOPPED, read_renewal
from noust.managers.internal_ca import InternalCA
from noust.managers.nginx_manager import NginxManager
from noust.managers.server.security_checks import (
    CheckReport,
    cached_report,
    run_checks,
    summarize,
)
from noust.managers.service_manager import ServiceManager

#: The server's own authority is an issue this close to expiry: a new one has to be trusted
#: again on every device, which is not done in a week.
AUTHORITY_CRITICAL_DAYS = 30

#: Where the hardening count comes from: run the quick checks now (the CLI),
#: the last checks this process ran (the console), or not at all.
HardeningMode = Literal["live", "cached", "off"]


@dataclass(frozen=True)
class HealthCheck:
    """
    One item of the health report.

    Attributes:
        name: What was checked, such as ``"Disk Space"`` or ``"Nginx"``.
        value: Human readable result.
        status: One of ``"ok"``, ``"warning"``, ``"error"`` or ``"info"``.
    """

    name: str
    value: str
    status: str


@dataclass
class HealthReport:
    """
    The result of a full health check.

    Attributes:
        disk: Disk space check, or ``None`` when free space could not be
            read at all - the one check that can be skipped outright rather
            than answer "could not check", because it needs a working
            filesystem call to have anything to report.
        nginx: Nginx's own status.
        apache: Apache's own status.
        applications: Summary across every deployed application.
        certificates: Summary across every certificate.
        memory: Memory pressure check.
        issues: Problems serious enough to fail the check.
        warnings: Problems worth an operator's attention that do not.
        hardening: The count of hardening findings, or None when they were
            not asked for.
        builds: Whether every application builds in the sandbox (backlog 44).
        audit: Whether the audit trail records (ENS G05).
        updates: How many applications update without a cut, and which could
            and do not yet. Always informational: a suggestion, not a problem.
        security: What the server's own security posture lacks: backups that
            leave unencrypted, a central's keys not sealed, a security release
            that is out. Warnings only, each with its fix.
        accounts: How many applications still share one system account, so a
            compromised one could read the others' trees (4.0, B-59).
        recipes: How many applications were installed from a recipe the catalogue has a newer
            version of (D-15); informational, an old recipe is a choice.
        names: What still has a name from before 4.0 and waits for the operator: a token a
            script still signs in with, a repository's overrides file, a site that names an
            upstream. Warnings only, each with what to change.
        descriptors: The files the console and the monitor hold open against their limit
            (issue 19); a warning from 80 %, an issue where requests are failing.
        domains: When the registrations of the domains served expire, as their registries last
            said (item 173); None while none was ever asked, which is how a server that never
            turned ``monitor.domain_expiry`` on looks. Reading it asks nobody.
    """

    disk: HealthCheck | None
    nginx: HealthCheck
    apache: HealthCheck
    applications: HealthCheck
    certificates: HealthCheck
    memory: HealthCheck
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hardening: HealthCheck | None = None
    builds: HealthCheck | None = None
    audit: HealthCheck | None = None
    updates: HealthCheck | None = None
    security: HealthCheck | None = None
    accounts: HealthCheck | None = None
    recipes: HealthCheck | None = None
    names: HealthCheck | None = None
    descriptors: HealthCheck | None = None
    domains: HealthCheck | None = None

    @property
    def checks(self) -> list[HealthCheck]:
        """Every check that ran, in the order the command prints them."""
        return [
            check
            for check in (
                self.disk,
                self.nginx,
                self.apache,
                self.applications,
                self.certificates,
                self.domains,
                self.memory,
                self.descriptors,
                self.builds,
                self.accounts,
                self.names,
                self.updates,
                self.recipes,
                self.audit,
                self.security,
                self.hardening,
            )
            if check is not None
        ]

    @property
    def verdict(self) -> str:
        """``"error"`` if anything failed, ``"warning"`` if not but something needs attention, else ``"healthy"``."""
        if self.issues:
            return "error"
        if self.warnings:
            return "warning"
        return "healthy"


def _certificate_label(cert: CertificateInfo) -> str:
    """
    Name a certificate for an operator-facing message.

    Args:
        cert: Certificate entry from the certificate manager.

    Returns:
        The lineage name, falling back to the first covered domain.
    """
    name = cert.get("name")
    if name:
        return name
    domains = cert.get("domains") or []
    return domains[0] if domains else "unknown"


def _read_meminfo() -> dict[str, int]:
    """
    Read ``/proc/meminfo`` into its numeric fields.

    Returns:
        Field name to value in kilobytes, for every line that carries a number.

    Raises:
        OSError: When /proc/meminfo cannot be read.
        ValueError: When a value is not a number.
    """
    meminfo: dict[str, int] = {}
    with open("/proc/meminfo") as handle:
        for line in handle:
            key, separator, value = line.partition(":")
            if separator and value.strip():
                meminfo[key.strip()] = int(value.split()[0])
    return meminfo


def _check_disk_space(config: Config, warnings: list[str], issues: list[str]) -> HealthCheck | None:
    """
    Report free space under the applications directory.

    Args:
        config: Configuration the applications directory is read from.
        warnings: Warning list to append to.
        issues: Issue list to append to.

    Returns:
        The check, or ``None`` when disk usage could not be read at all.
    """
    try:
        apps_dir = config.apps_directory
        if not apps_dir.exists():
            return HealthCheck("Disk Space", "Apps directory not found", "warning")

        stat = shutil.disk_usage(str(apps_dir))
        free_gb = stat.free / (1024**3)
        total_gb = stat.total / (1024**3)
        used_percent = ((stat.total - stat.free) / stat.total) * 100
        value = f"{free_gb:.1f}GB free / {total_gb:.1f}GB total ({used_percent:.0f}% used)"

        if free_gb < 1.0:
            issues.append(f"Low disk space: {free_gb:.1f}GB free")
            return HealthCheck("Disk Space", value, "error")
        if free_gb < 5.0:
            warnings.append(f"Disk space is getting low: {free_gb:.1f}GB free")
            return HealthCheck("Disk Space", value, "warning")
        return HealthCheck("Disk Space", value, "ok")
    except OSError as exc:
        warnings.append(f"Could not check disk space: {exc}")
        return None


def _check_web_servers(
    verbose: bool, issues: list[str], warnings: list[str]
) -> tuple[HealthCheck, HealthCheck]:
    """
    Report whether nginx and Apache are installed and running.

    Args:
        verbose: Passed through to the web server managers.
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        The nginx check and the Apache check.
    """
    nginx = NginxManager(verbose=verbose)
    apache = ApacheManager(verbose=verbose)

    nginx_installed = nginx.is_installed()
    apache_installed = apache.is_installed()

    if nginx_installed:
        if nginx.get_status().get("active"):
            nginx_check = HealthCheck("Nginx", "Running", "ok")
        else:
            issues.append("Nginx is installed but not running")
            nginx_check = HealthCheck("Nginx", "Stopped", "error")
    else:
        nginx_check = HealthCheck("Nginx", "Not installed", "info")

    if apache_installed:
        if apache.get_status().get("active"):
            apache_check = HealthCheck("Apache", "Running", "ok")
        elif apache_in_use(apache):
            warnings.append("Apache is installed but not running")
            apache_check = HealthCheck("Apache", "Stopped", "warning")
        else:
            # The package is present - systemd loads its unit - but nothing on
            # this server is configured to run on it. That is not an
            # operator's problem the way a stopped, in-use server is: it is
            # what "apache2 is not installed" looked like before is_installed
            # asked the binary on PATH instead of the unit, and a stray
            # apache2ctl or a package pulled in as a dependency must not read
            # as an outage.
            apache_check = HealthCheck("Apache", "Stopped (not in use)", "info")
    else:
        apache_check = HealthCheck("Apache", "Not installed", "info")

    if not nginx_installed and not apache_installed:
        issues.append("No web server installed")

    return nginx_check, apache_check


def apache_in_use(apache: ApacheManager) -> bool:
    """
    Report whether anything is actually configured to run on Apache.

    Apache being installed is a fact about the package; this is the fact that
    decides whether a stopped Apache is an operator's problem. A distribution
    that installs it as a dependency of something else, never enabled and
    serving nothing, must not be reported the same way as a real site that
    stopped answering.

    Args:
        apache: The Apache manager to read sites from.

    Returns:
        True when a site is enabled in Apache's ``sites-enabled``, or a
        deployed application records Apache as its web server.
    """
    if any(site.enabled for site in apache.list_sites()):
        return True
    try:
        apps = get_store().list_apps()
    except (NoustError, sqlite3.Error):
        return False
    return any(app.webserver == WebServer.APACHE.value for app in apps)


def _check_applications(verbose: bool, warnings: list[str]) -> HealthCheck:
    """
    Summarise every deployed application's state.

    Args:
        verbose: Passed through to :class:`ServiceManager`.
        warnings: Warning list to append to, one entry per unhealthy app.

    Returns:
        The applications check.
    """
    store = get_store()
    service_manager = ServiceManager(verbose=verbose)

    apps = store.list_apps()

    # The same resolver `noust list` uses. When these two commands each decided
    # for themselves what "running" meant, list reported fifteen applications
    # running while health reported seven stopped, and five of the seven were
    # static sites that have no service to run in the first place.
    states = resolve_states(apps, service_manager)

    # A stack running outside its unit serves too; it is listed below as
    # needing attention, not counted as down.
    apps_running = sum(1 for s in states.values() if s.label in (RUNNING, RUNNING_UNMANAGED))
    apps_static = sum(1 for s in states.values() if s.label == STATIC)
    unhealthy = [(domain, s) for domain, s in states.items() if not s.healthy]

    for domain, current in unhealthy:
        warnings.append(f"App '{domain}' - {current.detail or current.label.lower()}")

    total_apps = len(apps)
    if total_apps == 0:
        return HealthCheck("Applications", "No applications deployed", "info")

    served = apps_running + apps_static
    summary = f"{served}/{total_apps} serving"
    if apps_static:
        summary += f" ({apps_static} static)"
    if unhealthy:
        return HealthCheck("Applications", f"{summary}, {len(unhealthy)} need attention", "warning")
    return HealthCheck("Applications", summary, "ok")


def _check_certificates(verbose: bool, issues: list[str], warnings: list[str]) -> HealthCheck:
    """
    Summarise the certificates that need a renewal, and whether renewal works.

    A certificate is judged on its own life (:mod:`noust.core.cert_life`): a warning once a
    third of it is left and it has not been renewed, an issue under a sixth. Counting days
    instead would put a certificate of 45 days in warning for half its life and one of 6 days
    in alarm for all of it.

    Args:
        verbose: Passed through to :class:`CertManager`.
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        The certificates check.
    """
    cert_manager = CertManager(verbose=verbose)

    try:
        certs = cert_manager.list_certificates()
        expired: list[str] = []
        expiring_soon: list[str] = []
        # Tracked apart from `expiring_soon` (which also holds a cert that is
        # merely past its renewal point, not gone) so the check's own status can
        # be "error" exactly when the verdict this feeds is: a certificate in
        # its last sixth is as urgent as one already expired, even though
        # neither is "expired" in the strict sense.
        critical = False

        for cert in certs:
            label = _certificate_label(cert)
            life = cert_life.life_of(cert)
            days_left = life.days_left
            if life.state == cert_life.UNKNOWN or days_left is None:
                if cert.get("expiry"):
                    warnings.append(f"Certificate for {label} has an unreadable expiry date")
                continue

            if life.state == cert_life.EXPIRED:
                issues.append(f"Certificate for {label} expired {abs(days_left)} days ago")
                expired.append(label)
                critical = True
            elif life.state == cert_life.CRITICAL:
                issues.append(
                    f"Certificate for {label} expires in {days_left} days and its renewal is "
                    "overdue"
                )
                expiring_soon.append(label)
                critical = True
            elif life.state == cert_life.RENEWAL_PENDING:
                warnings.append(
                    f"Certificate for {label} expires in {days_left} days: its renewal is pending"
                )
                expiring_soon.append(label)

        renewal = _check_renewal(certs, critical, issues, warnings) if certs else "ok"
        authority = _check_internal_authority(issues, warnings)
        if expired or expiring_soon or renewal != "ok" or authority != "ok":
            value = (
                f"{len(certs)} total, {len(expired)} expired, {len(expiring_soon)} expiring soon"
            )
            if renewal != "ok":
                value += ", renewal failing" if renewal == FAILED else ", renewal stopped"
            if authority != "ok":
                value += ", the internal authority is about to expire"
            return HealthCheck(
                "SSL Certificates",
                value,
                "error" if critical or authority == "error" else "warning",
            )
        if certs:
            return HealthCheck("SSL Certificates", f"{len(certs)} total, all valid", "ok")
        return HealthCheck("SSL Certificates", "None configured", "info")
    except NoustError as exc:
        return HealthCheck("SSL Certificates", f"Could not check: {exc}", "warning")


def _check_renewal(
    certs: list[CertificateInfo], critical: bool, issues: list[str], warnings: list[str]
) -> str:
    """
    Say whether certbot's renewal is working, which the days left do not tell.

    Args:
        certs: The certificates, which a renewal that does not run would leave to expire.
        critical: Whether one of them is already in its last sixth: then a renewal that is not
            working is an issue, because there is little time left to fix it.
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        ``failed`` or ``timer_stopped`` when that is the problem, ``ok`` otherwise (a server that
        renews some other way, or that cannot be asked, is not a problem).
    """
    if not any(cert.get("source", "letsencrypt") != "custom" for cert in certs):
        return "ok"
    status = read_renewal(get_runner())
    where = issues if critical else warnings
    if status.problem == FAILED:
        when = f" ({status.last_run})" if status.last_run else ""
        where.append(f"The last certbot renewal failed{when}: {status.output or 'no output'}")
        return FAILED
    if status.problem == TIMER_STOPPED:
        where.append(
            f"{status.unit} is not running, so certbot renews nothing by itself. "
            f"Start it with: systemctl enable --now {status.unit}"
        )
        return TIMER_STOPPED
    return "ok"


#: A registration this close to its end is an issue: renewing a domain takes a payment and, at some
#: registrars, days. A month is a warning.
DOMAIN_CRITICAL_DAYS = 7
DOMAIN_WARNING_DAYS = 30


def _check_domain_registrations(issues: list[str], warnings: list[str]) -> HealthCheck | None:
    """
    Show when the registrations of the domains served expire, as their registries last said.

    A registration that expires takes down every site under it and the certificates with it. This
    reads what the monitor's daily look kept (``monitor.domain_expiry``, off by default because the
    domain name goes to the registry) and asks nobody: with nothing kept there is no line.

    Args:
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        The check; None when no registry was ever asked or the record cannot be read.
    """
    try:
        kept = RegistrationStore().all()
    except (NoustError, sqlite3.Error) as exc:
        logging.getLogger(__name__).debug("The domain registrations were not read: %s", exc)
        return None
    if not kept:
        return None
    dated: list[tuple[Registration, int]] = []
    for item in kept:
        left = item.days_left() if item.status == "ok" else None
        if left is not None:
            dated.append((item, left))
    status = "ok"
    for item, days in dated:
        if days < 0:
            issues.append(f"The registration of {item.domain} expired {-days} days ago")
            status = "error"
        elif days < DOMAIN_CRITICAL_DAYS:
            issues.append(f"The registration of {item.domain} expires in {days} days")
            status = "error"
        elif days <= DOMAIN_WARNING_DAYS:
            warnings.append(f"The registration of {item.domain} expires in {days} days")
            status = status if status == "error" else "warning"
    if not dated:
        return HealthCheck(
            "Domain registrations",
            f"{len(kept)} domain(s), no expiry published by their registries",
            "info",
        )
    soonest = min(dated, key=lambda pair: pair[1])
    unknown = len(kept) - len(dated)
    value = f"{len(kept)} domain(s), next to expire: {soonest[0].domain} in {soonest[1]} days"
    if unknown:
        value += f", {unknown} with no expiry"
    return HealthCheck("Domain registrations", value, status)


def _check_internal_authority(issues: list[str], warnings: list[str]) -> str:
    """
    Say when the server's own certificate authority is about to expire.

    A certificate that expires is renewed by itself, an authority is not: a new root has to be
    trusted again on every device, so the warning comes early (six months) and it is an issue in
    the last month.

    Args:
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        ``ok`` (there is none, or it has time), ``warning`` or ``error``.
    """
    authority = InternalCA()
    if not authority.exists():
        return "ok"
    try:
        status = authority.status()
    except NoustError as exc:
        warnings.append(f"The internal certificate authority could not be read: {exc.message}")
        return "warning"
    if status is None or not status.warnings:
        return "ok"
    if status.days_left < AUTHORITY_CRITICAL_DAYS:
        issues.extend(status.warnings)
        return "error"
    warnings.extend(status.warnings)
    return "warning"


def _check_memory(issues: list[str], warnings: list[str]) -> HealthCheck:
    """
    Report memory pressure from ``/proc/meminfo``.

    Args:
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        The memory check.
    """
    try:
        meminfo = _read_meminfo()

        total_mem = meminfo.get("MemTotal", 0) / 1024 / 1024  # GB
        free_mem = (meminfo.get("MemAvailable", 0) or meminfo.get("MemFree", 0)) / 1024 / 1024  # GB
        used_percent = ((total_mem - free_mem) / total_mem) * 100 if total_mem > 0 else 0
        value = f"{free_mem:.1f}GB free / {total_mem:.1f}GB total ({used_percent:.0f}% used)"

        if used_percent > 90:
            issues.append(f"High memory usage: {used_percent:.0f}%")
            return HealthCheck("Memory", value, "error")
        if used_percent > 75:
            warnings.append(f"Memory usage is high: {used_percent:.0f}%")
            return HealthCheck("Memory", value, "warning")
        return HealthCheck("Memory", value, "ok")
    except (OSError, ValueError, ZeroDivisionError) as exc:
        return HealthCheck("Memory", f"Could not check: {exc}", "warning")


#: The units Noust runs for itself, and so the processes whose open files this measures.
DESCRIPTOR_UNITS: tuple[str, ...] = (paths.WEB_UNIT, paths.MONITOR_UNIT)


def _unit_pid(runner: CommandRunner, unit: str) -> int | None:
    """
    Find the main process of a unit.

    Args:
        runner: Runs ``systemctl``.
        unit: The unit's name, without ``.service``.

    Returns:
        The pid; None when the unit does not run or systemd does not say.
    """
    result = runner.run(
        ["systemctl", "show", "-p", "MainPID", "--value", f"{unit}.service"], timeout=10
    )
    value = result.stdout.strip() if result.success else ""
    return int(value) if value.isdigit() and int(value) > 0 else None


def _check_descriptors(issues: list[str], warnings: list[str]) -> HealthCheck:
    """
    Show the files the console and the monitor hold open against the limit each was given.

    A console that reached its limit of 1024 answered every request with "Too many open files"
    (issue 19). This says it from 80 %, while a restart is still a choice, and is an issue where
    requests are already failing.

    Args:
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        The file descriptors check.
    """
    runner = get_runner()
    shown: list[str] = []
    unreadable: list[str] = []
    status = "ok"
    for unit in DESCRIPTOR_UNITS:
        pid = _unit_pid(runner, unit)
        if pid is None:
            continue
        usage = descriptors.read_usage(pid)
        if usage is None:
            unreadable.append(unit)
            continue
        shown.append(
            f"{unit} {usage.open} of {usage.limit}"
            if usage.limit is not None
            else f"{unit} {usage.open}, no limit"
        )
        if not usage.near or usage.fraction is None:
            continue
        sentence = (
            f"{unit} holds {usage.open} of its {usage.limit} file descriptors "
            f"({usage.fraction:.0%}). Restart it to free them with: noust web restart "
            "(the monitor: systemctl restart noust-monitor), and run 'noust setup doctor': it "
            "says whether the unit still has the limit from before 4.3."
        )
        if usage.critical:
            issues.append(sentence)
            status = "error"
        else:
            warnings.append(sentence)
            status = status if status == "error" else "warning"
    if not shown:
        if unreadable:
            return HealthCheck(
                "File descriptors", f"{', '.join(unreadable)} could not be read", "info"
            )
        return HealthCheck("File descriptors", "No Noust service is running as a unit", "info")
    return HealthCheck("File descriptors", ", ".join(shown), status)


def _check_builds(warnings: list[str]) -> HealthCheck:
    """
    Report the applications whose builds or containers have root's access.

    Args:
        warnings: Warning list to append each application's sentence to.

    Returns:
        The build sandbox check.
    """
    from noust.deployers.helpers import sandbox as build_sandbox

    try:
        found = build_sandbox.sandbox_warnings()
    except (NoustError, OSError, sqlite3.Error) as exc:
        return HealthCheck("Build sandbox", f"Could not check: {exc}", "warning")
    if not found:
        return HealthCheck("Build sandbox", "Every build runs in the sandbox", "ok")
    warnings.extend(found.values())
    return HealthCheck(
        "Build sandbox", f"{len(found)} application(s) build or run with root's access", "warning"
    )


def _check_accounts(warnings: list[str]) -> HealthCheck | None:
    """
    Report the applications that still run as the one account they share.

    Since 3.2 an application Noust creates runs as its own account; one from
    before keeps the shared one until an operator moves it. That is the file
    permissions between applications on this server, not a multi-tenant
    boundary, so it is a warning with its remedy and nothing louder.

    Args:
        warnings: Warning list to append the sentence to.

    Returns:
        The check, or None when every application that can have its own
        account has one (or there is nothing to say).
    """
    from noust.managers import app_identity

    try:
        shared = app_identity.shared_account_apps(get_store())
    except (NoustError, sqlite3.Error) as exc:
        return HealthCheck("Application accounts", f"Could not check: {exc}", "warning")
    if not shared:
        return None
    names = ", ".join(app.domain for app in shared[:3]) + (
        f" and {len(shared) - 3} more" if len(shared) > 3 else ""
    )
    warnings.append(
        f"{len(shared)} application(s) run as the account they share ({names}): each could read "
        "the others' files. Give each its own with: noust app identity migrate --all"
    )
    return HealthCheck(
        "Application accounts",
        f"{len(shared)} application(s) share one system account",
        "warning",
    )


def _check_names(warnings: list[str]) -> HealthCheck | None:
    """
    Name what only the operator can finish of the renaming of 4.0.

    What Noust renames by itself (a unit at the next update, an account at the next read) is not
    a warning: it is on its way. A token a script still presents with its old prefix, a
    repository file, a site somebody else wrote that names an upstream of Noust's: those are.

    Args:
        warnings: Warning list to append one sentence per finding to.

    Returns:
        The check, or None when nothing waits for the operator.
    """
    from noust.managers import name_migration

    try:
        found = [item for item in name_migration.findings() if item.waits_for_operator]
    except (NoustError, OSError, sqlite3.Error) as exc:
        return HealthCheck("Names before 4.0", f"Could not check: {exc}", "warning")
    if not found:
        return None
    warnings.extend(f"{item.text}" + (f". {item.fix}" if item.fix else "") for item in found)
    return HealthCheck(
        "Names before 4.0",
        f"{len(found)} thing(s) still use a name from before 4.0",
        "warning",
    )


def _check_updates() -> HealthCheck | None:
    """
    Say which applications could update without a cut and do not.

    A new application that can run twice starts that way (4.0); one deployed
    before keeps restarting in place on every update until the operator turns
    it on, and this is where the server says which and how. Never a warning:
    restarting in place is a working way to update.

    Returns:
        The check, or None when no application could use the mode (static
        sites and the like) so there is nothing to say.
    """
    from noust.deployers.bluegreen import could_update_without_downtime

    try:
        apps = [app for app in get_store().list_apps() if isinstance(app, App)]
    except (NoustError, sqlite3.Error) as exc:
        return HealthCheck("Updates", f"Could not check: {exc}", "info")
    running = [app for app in apps if app.zero_downtime and app.app_type != "docker-compose"]
    could = [app.domain for app in apps if could_update_without_downtime(app)]
    if not running and not could:
        return None
    if not could:
        return HealthCheck(
            "Updates", f"{len(running)} application(s) update without downtime", "ok"
        )
    shown = ", ".join(could[:3]) + (f" and {len(could) - 3} more" if len(could) > 3 else "")
    return HealthCheck(
        "Updates",
        f"{len(running)} update without downtime; {shown} restart on every update and could "
        f"avoid it: noust app zero-downtime {could[0]} on",
        "info",
    )


def _check_recipes() -> HealthCheck | None:
    """
    Say which installed recipes have a newer version in the catalogue (checklist D-15).

    Never a warning: an old version of a recipe is a choice and a working one. The monitor
    announces a change of the list (``recipe_outdated``); this is the line anyone reads when
    they run the report.

    Returns:
        The check; None when no application was installed from a recipe, so there is nothing
        to say.
    """
    from noust.recipes.updates import installed_statuses

    try:
        statuses = installed_statuses()
    except (NoustError, sqlite3.Error, OSError) as exc:
        return HealthCheck("Recipes", f"Could not check: {exc}", "info")
    if not statuses:
        return None
    behind = [status for status in statuses if status.update_available]
    if not behind:
        count = len(statuses)
        noun = "recipe is" if count == 1 else "recipes are"
        return HealthCheck("Recipes", f"{count} installed {noun} up to date", "ok")
    shown = ", ".join(
        f"{status.domain} ({status.installed_version} -> {status.latest_version})"
        for status in behind[:3]
    )
    if len(behind) > 3:
        shown += f" and {len(behind) - 3} more"
    noun = "recipe has" if len(behind) == 1 else "recipes have"
    return HealthCheck(
        "Recipes",
        f"{len(behind)} installed {noun} a newer version: {shown}; see: noust recipe outdated",
        "info",
    )


def _check_audit(issues: list[str], warnings: list[str]) -> HealthCheck:
    """
    Report whether the audit trail records, from :func:`noust.core.audit.health`.

    A trail that cannot write is an issue: under the ENS profile an action
    that leaves no record is a failure of its own.

    Args:
        issues: Issue list to append to.
        warnings: Warning list to append to.

    Returns:
        The audit trail check.
    """
    from noust.core import audit

    try:
        report = audit.health()
    except (NoustError, OSError, sqlite3.Error) as exc:
        return HealthCheck("Audit trail", f"Could not check: {exc}", "warning")
    problems = [f"Audit: {problem}" for problem in report.problems]
    if report.status == "error":
        issues.extend(problems)
        return HealthCheck("Audit trail", f"{len(problems)} problem(s)", "error")
    if report.status == "warning":
        warnings.extend(problems)
        return HealthCheck("Audit trail", f"{len(problems)} warning(s)", "warning")
    return HealthCheck("Audit trail", "Recording", "ok")


def _check_security_posture(warnings: list[str]) -> HealthCheck:
    """
    Report what the server's own security posture lacks.

    Args:
        warnings: Warning list to append each finding to.

    Returns:
        The posture check: ``ok`` when nothing is lacking.
    """
    from noust.managers import security_posture

    try:
        found = security_posture.collect()
    except (NoustError, OSError, sqlite3.Error) as exc:
        return HealthCheck("Security posture", f"Could not check: {exc}", "warning")
    if not found:
        return HealthCheck("Security posture", "Nothing lacking", "ok")
    warnings.extend(f"Security posture: {finding}" for finding in found)
    return HealthCheck("Security posture", f"{len(found)} finding(s)", "warning")


def _check_hardening(mode: HardeningMode, warnings: list[str]) -> HealthCheck | None:
    """
    Count the server's hardening findings.

    Args:
        mode: ``live`` runs the quick checks now (sshd, the firewall,
            fail2ban, accounts; not the package manager); ``cached`` reads the
            last checks this process ran and probes nothing; ``off`` skips it.
        warnings: Warning list to append each finding to.

    Returns:
        The check, or None for ``off``.
    """
    if mode == "off":
        return None
    report: CheckReport | None
    if mode == "live":
        try:
            report = run_checks(host_checks=False)
        except (NoustError, OSError, sqlite3.Error) as exc:
            return HealthCheck("Security hardening", f"Could not check: {exc}", "warning")
    else:
        report = cached_report()
    summary = summarize(report)
    if summary.checked_at is None:
        return HealthCheck(
            "Security hardening",
            "Not checked yet: run 'noust server security checks'",
            "info",
        )
    warnings.extend(f"Security (critical): {title}" for title in summary.critical)
    warnings.extend(f"Security: {title}" for title in summary.warnings)
    value = f"{len(summary.critical)} critical, {len(summary.warnings)} warning(s)"
    if summary.accepted:
        value += f", {summary.accepted} accepted"
    status = "warning" if summary.critical or summary.warnings else "ok"
    return HealthCheck("Security hardening", value, status)


def collect_health_report(
    *, verbose: bool = False, hardening: HardeningMode = "cached"
) -> HealthReport:
    """
    Run every check and return the structured report.

    The one implementation ``noust health`` and ``GET /api/system/health``
    both call: disk space, the web servers, every deployed application,
    certificates close to expiry, memory pressure, the build sandbox, the
    audit trail and the hardening count.

    Args:
        verbose: Passed through to the managers this instantiates.
        hardening: Where the hardening count comes from; see
            :func:`_check_hardening`. The console's endpoint keeps the
            default, which never probes on a request.

    Returns:
        Every check, plus the issues and warnings that decide the verdict.
    """
    config = Config()
    issues: list[str] = []
    warnings: list[str] = []

    disk = _check_disk_space(config, warnings, issues)
    nginx, apache = _check_web_servers(verbose, issues, warnings)
    applications = _check_applications(verbose, warnings)
    certificates = _check_certificates(verbose, issues, warnings)
    memory = _check_memory(issues, warnings)
    descriptors_check = _check_descriptors(issues, warnings)
    domains_check = _check_domain_registrations(issues, warnings)
    builds = _check_builds(warnings)
    accounts = _check_accounts(warnings)
    names = _check_names(warnings)
    updates = _check_updates()
    recipes = _check_recipes()
    audit_check = _check_audit(issues, warnings)
    security_check = _check_security_posture(warnings)
    hardening_check = _check_hardening(hardening, warnings)

    return HealthReport(
        disk=disk,
        nginx=nginx,
        apache=apache,
        applications=applications,
        certificates=certificates,
        memory=memory,
        issues=issues,
        warnings=warnings,
        hardening=hardening_check,
        builds=builds,
        accounts=accounts,
        names=names,
        updates=updates,
        recipes=recipes,
        audit=audit_check,
        security=security_check,
        descriptors=descriptors_check,
        domains=domains_check,
    )
