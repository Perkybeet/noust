# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust ens check``: this server against the ``ens-medium`` profile (ENS G09).

Every check has a stable id (``ENS-ACC-02``), the RD 311/2022 measures it
answers (``op.acc.6.r2``), a status (``ok``, ``warning``, ``fail``, ``n/a``),
the evidence verbatim (values and paths, never a secret) and what to do. The
checks judge against the profile's values whatever profile is on: a server on
the standard profile is told how far it is from ``ens-medium``, not told it
passes.

The work is split in two so each half can be tested on its own:

- :func:`gather` reads the facts from the areas that own them (rule 3): the
  accounts, the console's tokens, the audit trail, the hardening checks (the
  same list ``noust health`` and the Security tab read), backups, the
  console's exposure, the fleet, the build sandbox, the inventory. An area
  that cannot answer is recorded in ``Facts.errors`` and its checks say so;
  one failing area never empties the report.
- :func:`evaluate` turns facts into findings, without touching anything.

Nothing here changes the machine. :func:`run_check` is both halves, and what
the CLI and ``GET /api/ens/check`` call.
"""

from __future__ import annotations

import base64
import binascii
import re
import socket
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.core.ens.profile import ENS_MEDIUM, ProfileDefaults, current_profile
from noust.core.exceptions import NoustError

#: Statuses, most serious first.
STATUSES: tuple[str, ...] = ("fail", "warning", "ok", "n/a")

#: What a gathering step may raise and still leave the report whole.
_AREA_ERRORS = (NoustError, OSError, sqlite3.Error, ValueError, KeyError, ImportError)

#: Hardening checks (:mod:`noust.managers.server.security_catalog`) by the
#: ENS check that reports them; the rest are reported by ENS-HRD-01.
HARDENING_GROUPS: dict[str, tuple[str, ...]] = {
    "ENS-NET-02": (
        "ssh.root_password",
        "ssh.password_auth",
        "ssh.root_login",
        "ssh.empty_passwords",
        "ssh.keys",
        "ssh.defaults",
    ),
    "ENS-NET-03": (
        "fw.inactive",
        "fw.public_listener",
        "fw.docker_bypass",
        "fw.console_public",
        "fw.ipv6_mismatch",
    ),
    "ENS-UPD-01": (
        "upd.security_pending",
        "upd.reboot_required",
        "upd.stale_services",
        "upd.auto_disabled",
        "upd.pkg_broken",
        "upd.lists_stale",
        "os.eol",
    ),
    "ENS-MON-01": ("f2b.missing", "f2b.no_sshd_jail", "sys.journal_volatile", "ssh.loglevel"),
    "ENS-LOG-04": ("time.unsynced",),
    "ENS-FLT-01": ("noust.fleet_key_root",),
}


@dataclass(frozen=True)
class Finding:
    """
    One check's result.

    Attributes:
        id: The stable id, such as ``ENS-ACC-02``.
        title: What is checked, in a few words.
        status: ``ok``, ``warning``, ``fail`` or ``n/a``.
        measures: The RD 311/2022 measures it answers.
        summary: What was found, in a sentence.
        evidence: Values and paths, verbatim; never a secret.
        remediation: What to do; empty when nothing is.
    """

    id: str
    title: str
    status: str
    measures: tuple[str, ...]
    summary: str
    evidence: tuple[str, ...] = ()
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        data = asdict(self)
        data["measures"] = list(self.measures)
        data["evidence"] = list(self.evidence)
        return data


@dataclass
class ConsoleExposure:
    """
    How the console is reached.

    Attributes:
        source: Where this was read: ``console`` (the running process),
            ``unit`` (``noust-web.service``), ``central`` or ``none``.
        host: The address it binds to.
        port: Its port.
        certificate: The TLS certificate it serves, if any.
        self_signed: Whether that certificate is self-signed.
        insecure_http: Whether cleartext beyond loopback was accepted.
        allow_ip: Who may connect, as configured.
    """

    source: str
    host: str = "127.0.0.1"
    port: int = 8080
    certificate: str | None = None
    self_signed: bool = False
    insecure_http: bool = False
    allow_ip: tuple[str, ...] = ()

    @property
    def loopback_only(self) -> bool:
        """Whether only this machine can reach it."""
        return self.host in ("127.0.0.1", "::1", "localhost")


@dataclass
class Facts:
    """
    Everything the checks judge, as gathered. A None section could not be read;
    ``errors`` says why.

    Attributes:
        profile: The profile in force.
        now: When the facts were gathered, UTC.
        policy: The sign-in policy in force (``AuthPolicy.to_dict``).
        approvals_enabled: Whether four-eyes approvals are on.
        cli_reason_required: Whether CLI changes need ``--reason``.
        configured: Raw configured values the profile overrides, for drift.
        accounts: Every account (``Account.to_dict``).
        sod_conflicts: People holding incompatible roles.
        sod_exceptions: Separation-of-duties exceptions in force.
        tokens: API token records, no hash.
        master_token_exists: Whether a master token was ever issued.
        audit_verify: The chain's verification.
        audit_health: The trail's health (``AuditHealth.to_dict``).
        audit_retention_days: Retention in force.
        audit_journald: Whether journald receives the events.
        audit_syslog: Configured syslog receivers.
        audit_events: Last reviews, break-glass uses and lockouts.
        hardening: Hardening check results (``Check.to_dict``).
        backups: Per application: newest backup, newest good verification.
        destinations: Remote destinations and whether they are encrypted.
        schedules: Backup schedules and their destinations.
        backup_encryption_required: Whether uploads must be encrypted.
        exposure: How the console is reached.
        certificate: The console certificate's expiry and kind.
        nodes: On a central, the servers it manages.
        fleet_ceiling: On a node, what its centrals may do.
        sandbox_warnings: Applications whose builds are not sandboxed.
        inventory: The inventory (``InventoryEntry.to_dict``).
        allowed_sources: Allowed code origins.
        lockdown: The incident lockdown, if any.
        totp_plaintext: TOTP secrets still stored in clear.
        secrets_sealed: Whether the secrets are sealed.
        role: ``server`` or ``hub``.
        errors: Area to error, verbatim.
    """

    profile: str
    now: datetime
    policy: dict[str, Any] | None = None
    approvals_enabled: bool | None = None
    cli_reason_required: bool | None = None
    configured: dict[str, Any] = field(default_factory=dict)
    accounts: list[dict[str, Any]] | None = None
    sod_conflicts: list[dict[str, Any]] = field(default_factory=list)
    sod_exceptions: list[dict[str, Any]] = field(default_factory=list)
    tokens: list[dict[str, Any]] | None = None
    master_token_exists: bool | None = None
    audit_verify: dict[str, Any] | None = None
    audit_health: dict[str, Any] | None = None
    audit_retention_days: int | None = None
    audit_journald: bool | None = None
    audit_syslog: int = 0
    audit_events: dict[str, Any] = field(default_factory=dict)
    hardening: list[dict[str, Any]] | None = None
    backups: list[dict[str, Any]] | None = None
    destinations: list[dict[str, Any]] = field(default_factory=list)
    schedules: list[dict[str, Any]] = field(default_factory=list)
    backup_encryption_required: bool = False
    exposure: ConsoleExposure | None = None
    certificate: dict[str, Any] | None = None
    nodes: list[dict[str, Any]] = field(default_factory=list)
    fleet_ceiling: dict[str, Any] | None = None
    sandbox_warnings: dict[str, str] = field(default_factory=dict)
    inventory: list[dict[str, Any]] | None = None
    allowed_sources: list[str] = field(default_factory=list)
    lockdown: dict[str, Any] | None = None
    totp_plaintext: int | None = None
    secrets_sealed: bool | None = None
    role: str = "server"
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class ComplianceCheck:
    """
    The result of ``noust ens check``.

    Attributes:
        profile: The profile in force.
        checked_at: When, ISO 8601 UTC.
        host: This server's name.
        version: The Noust it runs.
        findings: One per check, in catalog order.
        indicators: The figures of op.mon.2 (MFA, reviews, patches...).
        errors: Areas that could not be read, verbatim.
    """

    profile: str
    checked_at: str
    host: str
    version: str
    findings: list[Finding]
    indicators: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        """
        Returns:
            Findings per status.
        """
        return {status: sum(1 for f in self.findings if f.status == status) for status in STATUSES}

    @property
    def verdict(self) -> str:
        """``fail`` when any check fails, ``warning`` when any warns, else ``ok``."""
        counts = self.counts()
        if counts["fail"]:
            return "fail"
        return "warning" if counts["warning"] else "ok"

    @property
    def exit_code(self) -> int:
        """0 for ok, 1 for warnings, 2 for failures: what a monitoring check expects."""
        return {"ok": 0, "warning": 1, "fail": 2}[self.verdict]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result, JSON-ready.
        """
        return {
            "profile": self.profile,
            "checked_at": self.checked_at,
            "host": self.host,
            "version": self.version,
            "verdict": self.verdict,
            "counts": self.counts(),
            "findings": [finding.to_dict() for finding in self.findings],
            "indicators": self.indicators,
            "errors": self.errors,
        }


# -- time helpers ---------------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    """
    Read a timestamp as an aware UTC moment.

    Args:
        value: ISO 8601 text (naive is taken as local time, as backups write
            it), UNIX seconds, or None.

    Returns:
        The moment, or None.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc)


def _age_days(value: Any, now: datetime) -> float | None:
    moment = _parse_time(value)
    return None if moment is None else (now - moment).total_seconds() / 86400


def _day(value: Any) -> str:
    moment = _parse_time(value)
    return moment.strftime("%Y-%m-%d") if moment else "never"


# -- gathering --------------------------------------------------------------------------


@dataclass
class Sources:
    """
    Where :func:`gather` reads each area; every field replaceable in a test.

    Attributes:
        config: The configuration; the process-wide one by default.
        store: The store; the process-wide one by default.
        token_manager: The console's token manager (tokens, master token);
            None when the console's dependencies are missing.
        exposure: How the console is reached; read from the installation by
            default, from the running console's own configuration in the API.
        hardening: The hardening checks; the last report, or a fresh run.
        refresh_hardening: Run the hardening checks again rather than reuse
            the last report.
        clock: The time.
    """

    config: Any = None
    store: Any = None
    token_manager: Any = None
    exposure: Callable[[], ConsoleExposure | None] | None = None
    hardening: Callable[[], list[dict[str, Any]]] | None = None
    refresh_hardening: bool = True
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


def _area(facts: Facts, name: str, step: Callable[[], None]) -> None:
    """
    Run one gathering step; a failure is recorded, never raised.

    Args:
        facts: Where the step writes, and where its error goes.
        name: The area, for ``errors``.
        step: The step.
    """
    try:
        step()
    except _AREA_ERRORS as exc:
        facts.errors[name] = f"{type(exc).__name__}: {exc}"


def _certificate_facts(path: Path) -> dict[str, Any]:
    """
    Read a PEM certificate's expiry and whether it is self-signed, without openssl.

    Args:
        path: The certificate.

    Returns:
        ``path``, ``expires`` (ISO or None), ``self_signed`` (issuer equals
        subject), ``fingerprint``.
    """
    from noust.central.setup import _read_tlv, certificate_expiry, certificate_fingerprint

    expiry = certificate_expiry(path)
    self_signed: bool | None = None
    try:
        text = path.read_text(encoding="ascii")
        match = re.search(
            r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", text, flags=re.DOTALL
        )
        if match:
            der = base64.b64decode("".join(match.group(1).split()), validate=True)
            _, certificate, _ = _read_tlv(der, 0)
            _, position, _ = _read_tlv(der, certificate)
            tag, _, end = _read_tlv(der, position)
            if tag == 0xA0:
                position = end
            elements = []
            for _index in range(5):  # serial, signature, issuer, validity, subject
                _, _, element_end = _read_tlv(der, position)
                elements.append(der[position:element_end])
                position = element_end
            self_signed = elements[2] == elements[4]
    except (OSError, UnicodeDecodeError, binascii.Error, ValueError, IndexError):
        self_signed = None
    return {
        "path": str(path),
        "expires": expiry.isoformat() if expiry else None,
        "self_signed": self_signed,
        "fingerprint": certificate_fingerprint(path),
    }


def exposure_from_installation() -> ConsoleExposure | None:
    """
    Read how the console is exposed from what is installed.

    The central's own environment when this is one, else the ``ExecStart``
    of ``noust-web.service``, else the background console's command line.

    Returns:
        The exposure, or None when no console is installed or running.
    """
    import shlex

    from noust.central import setup
    from noust.cli.commands.web import PANEL_TLS_CERT, options_from_argv, running_daemon
    from noust.core.net import ALL_INTERFACES
    from noust.managers.service_manager import ServiceManager

    if paths_data_dir() is not None:
        pair = setup.operator_tls_pair()
        return ConsoleExposure(
            source="central",
            host=ALL_INTERFACES,
            port=8443,
            certificate=pair[0] if pair else str(PANEL_TLS_CERT),
            self_signed=pair is None,
            allow_ip=tuple(setup.allowlist()),
        )
    options = None
    source = "none"
    for unit in ("noust-web", "wasm-web"):
        unit_file = Path(ServiceManager.SYSTEMD_DIR) / f"{unit}.service"
        try:
            text = unit_file.read_text()
        except OSError:
            continue
        line = next((raw for raw in text.splitlines() if raw.startswith("ExecStart=")), None)
        if line:
            options = options_from_argv(shlex.split(line.split("=", 1)[1]))
            source = "unit"
            break
    if options is None:
        daemon = running_daemon()
        if daemon is None:
            return None
        options, source = daemon.options, "daemon"
    certificate = str(PANEL_TLS_CERT) if options.self_signed else options.tls_cert
    return ConsoleExposure(
        source=source,
        host=options.host,
        port=options.port,
        certificate=certificate,
        self_signed=bool(options.self_signed),
        insecure_http=bool(options.insecure_http),
        allow_ip=tuple(options.allow_ip),
    )


def paths_data_dir() -> Path | None:
    """
    Returns:
        The central's data directory, when this is a central in a container.
    """
    from noust.core import paths

    return paths.DATA_DIR


def _default_hardening(refresh: bool) -> list[dict[str, Any]]:
    from noust.managers.server.security_checks import cached_report, reapply_risks, run_checks

    report = None if refresh else cached_report()
    report = reapply_risks(report) if report is not None else run_checks()
    return [check.to_dict() for check in report.checks]


def _scan_audit(log: Any, now: datetime) -> dict[str, Any]:
    """
    Find the events the checks need in one pass over the last year.

    Args:
        log: The audit log.
        now: The moment to judge at.

    Returns:
        The last access review and audit review, break-glass uses and
        lockouts in the last 30 days, verifications in the last 30 days.
    """
    since = (now - timedelta(days=366)).isoformat()
    month = now - timedelta(days=30)
    found: dict[str, Any] = {
        "last_access_review": None,
        "last_audit_review": None,
        "break_glass_30d": 0,
        "last_break_glass": None,
        "lockouts_30d": 0,
        "backup_verifications_30d": 0,
    }
    for entry in log.iter_newest_first():
        timestamp = str(entry.get("ts", ""))
        if timestamp and timestamp < since:
            break
        action = entry.get("action")
        recent = (_parse_time(timestamp) or now) >= month
        if action == "access.review" and found["last_access_review"] is None:
            found["last_access_review"] = timestamp
        elif action == "audit.review" and found["last_audit_review"] is None:
            found["last_audit_review"] = timestamp
        elif action == "auth.break_glass":
            found["last_break_glass"] = found["last_break_glass"] or timestamp
            found["break_glass_30d"] += int(recent)
        elif action == "auth.lockout":
            found["lockouts_30d"] += int(recent)
        elif action == "backups.verify":
            found["backup_verifications_30d"] += int(recent)
    return found


def gather(sources: Sources | None = None) -> Facts:
    """
    Read every fact the checks judge.

    Args:
        sources: Where to read each area; the running system by default.

    Returns:
        The facts; an area that could not be read is in ``errors``.
    """
    sources = sources or Sources()
    from noust.core.config import Config

    config = sources.config if sources.config is not None else Config()
    now = sources.clock()
    facts = Facts(profile=current_profile(config), now=now)

    def store() -> Any:
        if sources.store is not None:
            return sources.store
        from noust.core.store import get_store

        return get_store()

    def policy() -> None:
        from noust.core.accounts.approvals import build_approval_policy
        from noust.core.accounts.policy import load_policy
        from noust.core.ens.profile import defaults_for

        facts.policy = load_policy().to_dict()
        facts.approvals_enabled = build_approval_policy(
            {"profile": facts.profile, "enabled": config.get("approval.enabled", False)}
        ).enabled
        facts.cli_reason_required = defaults_for(facts.profile).cli_reason_required
        facts.configured = {
            key: config.get(key)
            for key in (
                "auth.session.idle_minutes",
                "auth.session.absolute_hours",
                "auth.password.min_length",
                "audit.retention_days",
                "backup.encryption",
            )
            if config.get(key) is not None
        }

    def accounts() -> None:
        from noust.core.accounts import AccountManager

        manager = AccountManager(store())
        facts.accounts = [account.to_dict() for account in manager.list_all()]
        facts.sod_conflicts = manager.separation_conflicts()
        facts.sod_exceptions = [asdict(item) for item in manager.list_exceptions()]
        facts.totp_plaintext = manager.plaintext_totp_secrets()

    def tokens() -> None:
        manager = sources.token_manager
        owned = manager is None
        if manager is None:
            from noust.cli.web_state import token_manager

            manager = token_manager()
        try:
            facts.tokens = list(manager.list_api_tokens())
            facts.master_token_exists = manager.current_master_generation() is not None
        finally:
            if owned:
                manager.sessions.close()

    def audit() -> None:
        from noust.core.audit import get_log, health, load_settings
        from noust.core.audit.sinks import JOURNALD_SOCKET

        log = get_log()
        facts.audit_verify = log.verify().to_dict()
        facts.audit_health = health(log).to_dict()
        settings = load_settings(config)
        facts.audit_retention_days = settings.retention_days
        facts.audit_syslog = len(settings.syslog)
        facts.audit_journald = settings.journald == "on" or (
            settings.journald == "auto" and Path(JOURNALD_SOCKET).exists()
        )
        facts.audit_events = _scan_audit(log, now)

    def hardening() -> None:
        reader = sources.hardening or (lambda: _default_hardening(sources.refresh_hardening))
        facts.hardening = reader()

    def backups() -> None:
        from noust.core.ens.profile import backup_encryption_required
        from noust.managers.backup_manager import BackupManager

        facts.backup_encryption_required = backup_encryption_required(config)
        the_store = store()
        manager = BackupManager(verbose=False)
        by_app: dict[str, dict[str, Any]] = {}
        for app in the_store.list_apps():
            if getattr(app, "preview_parent", None):
                continue
            by_app[app.domain] = {
                "domain": app.domain,
                "newest": None,
                "verified": None,
                "count": 0,
            }
        for backup in manager.list_backups():
            entry = by_app.get(backup.domain)
            if entry is None:
                continue
            entry["count"] += 1
            if entry["newest"] is None or str(backup.created_at) > str(entry["newest"]):
                entry["newest"] = backup.created_at
            if backup.verified_ok and (
                entry["verified"] is None or str(backup.last_verified_at) > str(entry["verified"])
            ):
                entry["verified"] = backup.last_verified_at
        facts.backups = list(by_app.values())
        facts.destinations = [
            {"name": record.name, "backend": record.backend, "encrypted": bool(record.encrypted)}
            for record in the_store.list_backup_destinations()
        ]
        facts.schedules = [
            {
                "domain": record.app_domain,
                "destinations": [d.get("name") for d in record.destinations if d.get("name")],
            }
            for record in the_store.list_backup_schedules()
        ]

    def exposure() -> None:
        reader = sources.exposure or exposure_from_installation
        facts.exposure = reader()
        if facts.exposure is not None and facts.exposure.certificate:
            path = Path(facts.exposure.certificate)
            if path.is_file():
                facts.certificate = _certificate_facts(path)
                if facts.certificate.get("self_signed") is None:
                    facts.certificate["self_signed"] = facts.exposure.self_signed
                else:
                    facts.certificate["self_signed"] = bool(
                        facts.certificate["self_signed"] or facts.exposure.self_signed
                    )

    def fleet() -> None:
        from noust.central import role
        from noust.fleet.policy import current_access

        facts.role = role(config)
        facts.nodes = [
            {
                "name": node.name,
                "ssh_user": node.ssh_user,
                "access_level": node.access_level,
                "host_access": node.host_access,
                "status": node.status,
            }
            for node in store().list_nodes()
        ]
        if store().get_fleet_access() is not None:
            access = current_access(store())
            facts.fleet_ceiling = {"level": access.level, "host_access": access.host_access}

    def sandbox() -> None:
        from noust.deployers.helpers.sandbox import sandbox_warnings

        facts.sandbox_warnings = sandbox_warnings(store())

    def inventory() -> None:
        from noust.core.ens.inventory import Inventory

        facts.inventory = [entry.to_dict() for entry in Inventory(store()).list()]

    def rest() -> None:
        from noust.core import sealing
        from noust.core.ens.incident import lockdown_state
        from noust.core.ens.origins import allowed_sources

        facts.allowed_sources = allowed_sources(config)
        facts.lockdown = lockdown_state(store())
        facts.secrets_sealed = sealing.is_sealed(store().db_path.parent / "secrets")

    for name, step in (
        ("policy", policy),
        ("accounts", accounts),
        ("tokens", tokens),
        ("audit", audit),
        ("hardening", hardening),
        ("backups", backups),
        ("exposure", exposure),
        ("fleet", fleet),
        ("sandbox", sandbox),
        ("inventory", inventory),
        ("other", rest),
    ):
        _area(facts, name, step)
    return facts


# -- the checks -------------------------------------------------------------------------


def _unknown(
    check_id: str, title: str, measures: tuple[str, ...], area: str, facts: Facts
) -> Finding:
    return Finding(
        check_id,
        title,
        "warning",
        measures,
        f"Could not be checked: the {area} could not be read.",
        (facts.errors.get(area, "no data"),),
        "Run the check again as root on the server; the evidence line says what failed.",
    )


def _humans(facts: Facts) -> list[dict[str, Any]]:
    return [a for a in facts.accounts or [] if a.get("status") in ("active", "locked", "invited")]


def check_profile(facts: Facts) -> Finding:
    """ENS-PRF-01: the profile is on, so every area applies its values."""
    title = "Security profile"
    measures = ("op.exp.2", "op.exp.3.r1")
    evidence = [f"security.profile: {facts.profile}"]
    if facts.policy:
        evidence.append(
            "in force: idle {idle_minutes} min, session {absolute_hours} h, lockout "
            "{lockout_threshold}/{lockout_minutes} min, password {password_min_length}, "
            "tokens {token_max_days} days".format(**facts.policy)
        )
    for key, value in sorted(facts.configured.items()):
        evidence.append(f"configured {key}: {value}")
    if facts.profile == ENS_MEDIUM.name:
        return Finding(
            "ENS-PRF-01",
            title,
            "ok",
            measures,
            "The ens-medium profile is on: sign-in, audit, approvals, backups and the CLI apply "
            "its values, which the operator may tighten but not loosen.",
            tuple(evidence),
        )
    return Finding(
        "ENS-PRF-01",
        title,
        "fail",
        measures,
        "The standard profile is on: nothing stops a setting looser than ENS category MEDIUM.",
        tuple(evidence),
        "noust config set security.profile ens-medium",
    )


def check_roles(facts: Facts) -> Finding:
    """ENS-ACC-01: a security officer and an administrator, and no one holding both."""
    title = "Roles and separation of duties"
    measures = ("op.acc.3", "org.1.3")
    if facts.accounts is None:
        return _unknown("ENS-ACC-01", title, measures, "accounts", facts)
    active = [a for a in facts.accounts if a.get("status") == "active"]
    roles = {str(a.get("role")) for a in active}
    evidence = [
        f"{role}: {sum(1 for a in active if a.get('role') == role)}" for role in sorted(roles)
    ]
    missing = [role for role in ("security", "admin") if role not in roles]
    unexcused = [c for c in facts.sod_conflicts if not c.get("exception")]
    for conflict in facts.sod_conflicts:
        names = ", ".join(f"{a['username']} ({a['role']})" for a in conflict.get("accounts", []))
        state = "exception in force" if conflict.get("exception") else "no exception"
        evidence.append(f"person {conflict.get('person_ref')}: {names} - {state}")
    if not active:
        return Finding(
            "ENS-ACC-01",
            title,
            "fail",
            measures,
            "No account exists: everyone shares the master token.",
            tuple(evidence),
            "Create the first accounts: noust user create <name> --role security, then an admin.",
        )
    if missing or unexcused:
        problems = []
        if missing:
            problems.append(f"no active {' or '.join(missing)} account")
        if unexcused:
            problems.append(f"{len(unexcused)} person(s) hold incompatible roles")
        return Finding(
            "ENS-ACC-01",
            title,
            "fail",
            measures,
            "Separation of duties is not in place: " + "; ".join(problems) + ".",
            tuple(evidence),
            "Give each function its own account and person (noust user invite); where one person "
            "must hold both, record the exception: noust user exception add.",
        )
    return Finding(
        "ENS-ACC-01",
        title,
        "ok",
        measures,
        "There is a security officer and an administrator, and nobody holds incompatible "
        "roles without a recorded exception.",
        tuple(evidence),
    )


def check_mfa(facts: Facts) -> Finding:
    """ENS-ACC-02: every account has a second factor."""
    title = "Second factor for every account"
    measures = ("op.acc.6.r2", "op.acc.6.r8")
    if facts.accounts is None:
        return _unknown("ENS-ACC-02", title, measures, "accounts", facts)
    humans = [a for a in facts.accounts if a.get("status") in ("active", "locked")]
    without = [a["username"] for a in humans if not a.get("mfa_enabled")]
    if not humans:
        return Finding("ENS-ACC-02", title, "n/a", measures, "There are no accounts yet.")
    if without:
        return Finding(
            "ENS-ACC-02",
            title,
            "fail",
            measures,
            f"{len(without)} of {len(humans)} account(s) have no second factor.",
            tuple(f"without a second factor: {name}" for name in without),
            "Each of them enrols one at the next sign-in; or reset the account: "
            "noust user reset-mfa <name>.",
        )
    return Finding(
        "ENS-ACC-02",
        title,
        "ok",
        measures,
        f"All {len(humans)} account(s) have a TOTP authenticator or a passkey.",
    )


def check_master_token(facts: Facts) -> Finding:
    """ENS-ACC-03: the master token is for emergencies only, and was not used lately."""
    title = "Master token restricted"
    measures = ("op.acc.6.r8",)
    uses = int(facts.audit_events.get("break_glass_30d") or 0)
    last = facts.audit_events.get("last_break_glass")
    evidence = [f"break-glass uses in 30 days: {uses}", f"last use: {_day(last)}"]
    if facts.accounts is None:
        return _unknown("ENS-ACC-03", title, measures, "accounts", facts)
    if not [a for a in facts.accounts if a.get("status") == "active"]:
        return Finding(
            "ENS-ACC-03",
            title,
            "fail",
            measures,
            "With no account, the master token is the whole console for whoever holds it.",
            tuple(evidence),
            "Create accounts; the master token then becomes the break-glass credential.",
        )
    if facts.profile != ENS_MEDIUM.name:
        return Finding(
            "ENS-ACC-03",
            title,
            "warning",
            measures,
            "The master token is break-glass: it still opens the whole console, audited.",
            tuple(evidence),
            "The ens-medium profile restricts it to getting a person back in.",
        )
    if uses:
        return Finding(
            "ENS-ACC-03",
            title,
            "warning",
            measures,
            f"The master token was used {uses} time(s) in the last 30 days.",
            tuple(evidence),
            "Check each use in the audit log (noust audit list --action auth.break_glass) and "
            "rotate the token afterwards: noust web token --regenerate.",
        )
    return Finding(
        "ENS-ACC-03",
        title,
        "ok",
        measures,
        "The master token only gets a person back in, and was not used in 30 days.",
        tuple(evidence),
    )


def check_tokens(facts: Facts) -> Finding:
    """ENS-ACC-04: every API token has an owner and expires within the profile's limit."""
    title = "API tokens owned and expiring"
    measures = ("op.acc.1.3", "op.acc.4")
    if facts.tokens is None:
        return _unknown("ENS-ACC-04", title, measures, "tokens", facts)
    now = facts.now.timestamp()
    limit = (ENS_MEDIUM.token_max_days or 90) * 86400
    live = [
        t
        for t in facts.tokens
        if not t.get("revoked_at")
        and (t.get("expires_at") is None or float(t["expires_at"]) > now)
        and t.get("scope") != "fleet"
    ]
    problems: list[str] = []
    for token in live:
        name = token.get("name")
        if token.get("expires_at") is None:
            problems.append(f"{name}: never expires")
        elif float(token["expires_at"]) - float(token.get("created_at") or now) > limit + 3600:
            problems.append(f"{name}: lives longer than {ENS_MEDIUM.token_max_days} days")
        if token.get("owner_account_id") is None:
            problems.append(f"{name}: belongs to no account")
    if not live:
        return Finding("ENS-ACC-04", title, "n/a", measures, "No API token is in use.")
    if problems:
        return Finding(
            "ENS-ACC-04",
            title,
            "fail",
            measures,
            f"{len(problems)} problem(s) with {len(live)} live API token(s).",
            tuple(problems),
            "Revoke them and issue tokens with an owner and an expiry: noust token revoke <id>, "
            "then noust token create <name> --owner <account> --expires-hours 2160.",
        )
    return Finding(
        "ENS-ACC-04",
        title,
        "ok",
        measures,
        f"All {len(live)} live API token(s) have an owner and expire within "
        f"{ENS_MEDIUM.token_max_days} days.",
    )


def _review(
    check_id: str,
    title: str,
    measures: tuple[str, ...],
    last: Any,
    days: int,
    facts: Facts,
    command: str,
) -> Finding:
    age = _age_days(last, facts.now)
    evidence = (f"last review: {_day(last)}", f"required every {days} days")
    if age is None or age > days:
        return Finding(
            check_id,
            title,
            "fail" if age is None else "warning",
            measures,
            "No review is on record."
            if age is None
            else f"The last review is {int(age)} days old.",
            evidence,
            f"Review and record it: {command}",
        )
    return Finding(check_id, title, "ok", measures, f"Reviewed {int(age)} day(s) ago.", evidence)


def check_access_review(facts: Facts) -> Finding:
    """ENS-ACC-05: accounts and roles reviewed within 90 days."""
    return _review(
        "ENS-ACC-05",
        "Access review",
        ("op.acc.4.4",),
        facts.audit_events.get("last_access_review"),
        ENS_MEDIUM.access_review_days,
        facts,
        "noust ens access-review list, then noust ens access-review attest --notes '...'.",
    )


def check_stale_accounts(facts: Facts) -> Finding:
    """ENS-ACC-06: no account unused for 90 days, no account locked and forgotten."""
    title = "Stale and locked accounts"
    measures = ("op.acc.1.4", "op.acc.6.6")
    if facts.accounts is None:
        return _unknown("ENS-ACC-06", title, measures, "accounts", facts)
    limit = ENS_MEDIUM.stale_account_days
    stale: list[str] = []
    for account in facts.accounts:
        if account.get("status") not in ("active", "invited"):
            continue
        last = account.get("last_login_at") or account.get("created_at")
        age = _age_days(last, facts.now)
        if age is not None and age > limit:
            stale.append(f"{account['username']}: no sign-in since {_day(last)}")
    locked = [
        f"{a['username']}: locked ({a.get('locked_reason') or 'no reason'})"
        for a in facts.accounts
        if a.get("status") == "locked"
    ]
    if stale or locked:
        return Finding(
            "ENS-ACC-06",
            title,
            "warning",
            measures,
            f"{len(stale)} account(s) unused for {limit} days, {len(locked)} locked.",
            tuple(stale + locked),
            "Disable what is no longer needed (noust user disable <name> --reason ...), and "
            "unlock or disable locked accounts after checking why (noust user unlock).",
        )
    if not facts.accounts:
        return Finding("ENS-ACC-06", title, "n/a", measures, "There are no accounts yet.")
    return Finding(
        "ENS-ACC-06",
        title,
        "ok",
        measures,
        f"Every account was used in the last {limit} days, and none is locked.",
    )


def check_sessions(facts: Facts) -> Finding:
    """ENS-SES-01: sessions lock after 15 minutes and end after 8 hours."""
    title = "Session lock and lifetime"
    measures = ("mp.eq.2",)
    if facts.policy is None:
        return _unknown("ENS-SES-01", title, measures, "policy", facts)
    idle, absolute = int(facts.policy["idle_minutes"]), int(facts.policy["absolute_hours"])
    evidence = (f"idle: {idle} min", f"absolute: {absolute} h")
    if idle > ENS_MEDIUM.idle_minutes or absolute > ENS_MEDIUM.absolute_hours:
        return Finding(
            "ENS-SES-01",
            title,
            "fail",
            measures,
            f"A session stays open {idle} minutes unused and {absolute} hours in all; the profile "
            f"allows {ENS_MEDIUM.idle_minutes} and {ENS_MEDIUM.absolute_hours}.",
            evidence,
            "noust config set security.profile ens-medium (or lower auth.session.*).",
        )
    return Finding(
        "ENS-SES-01", title, "ok", measures, "Sessions end within the profile's limits.", evidence
    )


def check_audit_chain(facts: Facts) -> Finding:
    """ENS-LOG-01: the audit chain verifies."""
    title = "Audit log intact"
    measures = ("op.exp.8", "op.exp.8.r4")
    verify = facts.audit_verify
    if verify is None:
        return _unknown("ENS-LOG-01", title, measures, "audit", facts)
    evidence = [
        f"events checked: {verify.get('checked')}",
        f"head: seq {verify.get('last_seq')}, mac {verify.get('last_mac')}",
    ]
    if not verify.get("ok"):
        broken = verify.get("broken") or {}
        evidence.append(
            f"broken at {broken.get('file')}:{broken.get('line')}: {broken.get('reason')}"
        )
        return Finding(
            "ENS-LOG-01",
            title,
            "fail",
            measures,
            "The audit chain is broken: an event was altered, removed or inserted.",
            tuple(evidence),
            "Treat it as an incident: noust incident freeze --reason ..., then compare with the "
            "copy the audit destination received.",
        )
    health = facts.audit_health or {}
    if health.get("failing"):
        return Finding(
            "ENS-LOG-01",
            title,
            "fail",
            measures,
            "Audit events are not being written.",
            (*evidence, str(health.get("last_failure"))),
            "Fix the cause (disk space, permissions); noust audit status says which.",
        )
    return Finding(
        "ENS-LOG-01",
        title,
        "ok",
        measures,
        "The HMAC chain holds; its head is what an off-machine receiver should hold too.",
        tuple(evidence),
    )


def check_audit_shipping(facts: Facts) -> Finding:
    """ENS-LOG-02: the audit trail leaves the machine, and nothing is stuck."""
    title = "Audit shipped off the machine"
    measures = ("op.mon.3.1", "op.exp.8.r4")
    if facts.audit_health is None:
        return _unknown("ENS-LOG-02", title, measures, "audit", facts)
    sinks = facts.audit_health.get("sinks") or []
    degraded = [s for s in sinks if s.get("degraded")]
    evidence = [
        f"journald: {'yes' if facts.audit_journald else 'no'}",
        f"syslog receivers: {facts.audit_syslog}",
    ]
    evidence += [f"{s.get('sink_id')}: {'degraded' if s.get('degraded') else 'ok'}" for s in sinks]
    if degraded:
        return Finding(
            "ENS-LOG-02",
            title,
            "fail",
            measures,
            f"{len(degraded)} audit destination(s) stopped receiving events.",
            tuple(evidence),
            "noust audit status shows the error; the events are queued and sent once it works.",
        )
    if not facts.audit_syslog and not facts.audit_journald:
        return Finding(
            "ENS-LOG-02",
            title,
            "fail",
            measures,
            "The audit log exists only on this machine, where root can rewrite it.",
            tuple(evidence),
            "Send it to your SIEM: noust config set audit.syslog '[{transport: tls, address: "
            "siem.example:6514, ca: /etc/noust/siem-ca.pem}]'.",
        )
    if not facts.audit_syslog:
        return Finding(
            "ENS-LOG-02",
            title,
            "warning",
            measures,
            "The events reach journald; nothing here shows journald forwards them off the machine.",
            tuple(evidence),
            "Forward journald (systemd-journal-upload, rsyslog imjournal, an agent) or add a "
            "syslog receiver in audit.syslog.",
        )
    return Finding(
        "ENS-LOG-02",
        title,
        "ok",
        measures,
        "The audit trail is shipped as it is written.",
        tuple(evidence),
    )


def check_audit_retention(facts: Facts) -> Finding:
    """ENS-LOG-03: events kept a year, with room to keep them."""
    title = "Audit retention"
    measures = ("op.exp.8.r3",)
    if facts.audit_retention_days is None:
        return _unknown("ENS-LOG-03", title, measures, "audit", facts)
    evidence = [f"audit.retention_days: {facts.audit_retention_days}"]
    problems = [p for p in (facts.audit_health or {}).get("problems", []) if "MiB" in p]
    evidence += problems
    if facts.audit_retention_days < ENS_MEDIUM.audit_retention_days:
        return Finding(
            "ENS-LOG-03",
            title,
            "fail",
            measures,
            f"Events are kept {facts.audit_retention_days} days; the profile keeps them "
            f"{ENS_MEDIUM.audit_retention_days}.",
            tuple(evidence),
            "noust config set security.profile ens-medium (or raise audit.retention_days).",
        )
    if problems:
        return Finding(
            "ENS-LOG-03",
            title,
            "warning",
            measures,
            "The audit log is over its size limit.",
            tuple(evidence),
            "Ship it off the machine and raise audit.max_total_mb.",
        )
    return Finding(
        "ENS-LOG-03",
        title,
        "ok",
        measures,
        f"Events are kept {facts.audit_retention_days} days.",
        tuple(evidence),
    )


def check_audit_review(facts: Facts) -> Finding:
    """ENS-LOG-05: the audit log was reviewed within 7 days."""
    return _review(
        "ENS-LOG-05",
        "Audit review",
        ("op.exp.8.r1",),
        facts.audit_events.get("last_audit_review"),
        ENS_MEDIUM.audit_review_days,
        facts,
        "noust audit review --from <date> --to <date> --notes '...'.",
    )


def check_certificate(facts: Facts) -> Finding:
    """ENS-CRY-01: the console serves the organisation's certificate, far from expiry."""
    title = "Console certificate"
    measures = ("mp.com.2.r1", "mp.com.3.r2")
    exposure = facts.exposure
    if exposure is None:
        if "exposure" in facts.errors:
            return _unknown("ENS-CRY-01", title, measures, "exposure", facts)
        return Finding(
            "ENS-CRY-01", title, "n/a", measures, "No console is installed or running here."
        )
    if not exposure.certificate:
        status = "n/a" if exposure.loopback_only else "fail"
        return Finding(
            "ENS-CRY-01",
            title,
            status,
            measures,
            "The console serves no certificate"
            + (": it listens on loopback only." if exposure.loopback_only else "."),
            (f"listens on {exposure.host}:{exposure.port}",),
            "" if status == "n/a" else "noust web enable --tls-cert <fullchain> --tls-key <key>",
        )
    cert = facts.certificate or {}
    evidence = [
        f"certificate: {exposure.certificate}",
        f"expires: {cert.get('expires') or 'unreadable'}",
        f"self-signed: {'yes' if cert.get('self_signed') else 'no'}",
        f"fingerprint: {cert.get('fingerprint')}",
    ]
    days = None
    if cert.get("expires"):
        expiry = _parse_time(cert["expires"])
        days = (expiry - facts.now).days if expiry else None
    if days is not None and days < 0:
        return Finding(
            "ENS-CRY-01",
            title,
            "fail",
            measures,
            "The console's certificate has expired.",
            tuple(evidence),
            "Renew it and restart the console with the new pair.",
        )
    if cert.get("self_signed"):
        return Finding(
            "ENS-CRY-01",
            title,
            "fail",
            measures,
            "The console serves a self-signed certificate; the profile expects one of the "
            "organisation's PKI.",
            tuple(evidence),
            "noust web enable --tls-cert <fullchain.pem> --tls-key <privkey.pem> (a central: "
            "NOUST_TLS_CERT and NOUST_TLS_KEY).",
        )
    if days is not None and days < ENS_MEDIUM.certificate_min_days:
        return Finding(
            "ENS-CRY-01",
            title,
            "warning",
            measures,
            f"The console's certificate expires in {days} days.",
            tuple(evidence),
            "Renew it before it expires.",
        )
    return Finding(
        "ENS-CRY-01",
        title,
        "ok",
        measures,
        "The console serves an operator certificate that is not close to expiry.",
        tuple(evidence),
    )


def check_cleartext(facts: Facts) -> Finding:
    """ENS-CRY-02: the console never answers in cleartext beyond loopback."""
    title = "No cleartext console"
    measures = ("mp.com.2", "mp.com.3")
    exposure = facts.exposure
    if exposure is None:
        return Finding(
            "ENS-CRY-02", title, "n/a", measures, "No console is installed or running here."
        )
    evidence = (f"listens on {exposure.host}:{exposure.port}", f"read from: {exposure.source}")
    if not exposure.loopback_only and (exposure.insecure_http or not exposure.certificate):
        return Finding(
            "ENS-CRY-02",
            title,
            "fail",
            measures,
            "The console answers on the network without TLS: credentials cross it in clear.",
            evidence,
            "noust web enable --tls-cert <fullchain> --tls-key <key> (no --insecure-http).",
        )
    return Finding(
        "ENS-CRY-02",
        title,
        "ok",
        measures,
        "The console is reached over TLS or on loopback only.",
        evidence,
    )


def check_secrets_at_rest(facts: Facts) -> Finding:
    """ENS-CRY-03: second factors encrypted in the store; a hub's secrets sealed."""
    title = "Secrets at rest"
    measures = ("op.exp.10",)
    evidence = [
        f"TOTP secrets in clear: {facts.totp_plaintext if facts.totp_plaintext is not None else 'unknown'}",
        f"secrets sealed: {'yes' if facts.secrets_sealed else 'no'}",
        f"role: {facts.role}",
    ]
    if facts.totp_plaintext:
        return Finding(
            "ENS-CRY-03",
            title,
            "fail",
            measures,
            f"{facts.totp_plaintext} TOTP secret(s) are stored in clear.",
            tuple(evidence),
            "Restart the console (it encrypts them at start), or have each account sign in once.",
        )
    if facts.role == "hub" and not facts.secrets_sealed:
        return Finding(
            "ENS-CRY-03",
            title,
            "warning",
            measures,
            "This central's secrets (every node's key) are not sealed under a passphrase.",
            tuple(evidence),
            "noust central seal (keep the passphrase with the security officer).",
        )
    return Finding(
        "ENS-CRY-03",
        title,
        "ok",
        measures,
        "Second factors are encrypted in the store.",
        tuple(evidence),
    )


def check_allowlist(facts: Facts) -> Finding:
    """ENS-NET-01: a console beyond loopback names who may connect."""
    title = "Who may reach the console"
    measures = ("mp.com.1", "op.acc.4.5")
    exposure = facts.exposure
    if exposure is None:
        return Finding(
            "ENS-NET-01", title, "n/a", measures, "No console is installed or running here."
        )
    if exposure.loopback_only:
        return Finding(
            "ENS-NET-01",
            title,
            "ok",
            measures,
            "The console listens on loopback only.",
            (f"listens on {exposure.host}:{exposure.port}",),
        )
    from noust.central.setup import DEFAULT_ALLOWLIST

    entries = list(exposure.allow_ip)
    evidence = (
        f"listens on {exposure.host}:{exposure.port}",
        f"allow-list: {', '.join(entries) or 'none'}",
    )
    wide = {"0.0.0.0/0", "::/0"}
    if not entries or wide & set(entries):
        return Finding(
            "ENS-NET-01",
            title,
            "fail",
            measures,
            "The console answers any address on the network.",
            evidence,
            "Name the management network: noust web enable --allow-ip 10.20.0.0/24 (a central: NOUST_ALLOW_IP).",
        )
    if set(entries) == set(DEFAULT_ALLOWLIST):
        return Finding(
            "ENS-NET-01",
            title,
            "warning",
            measures,
            "The console answers every private network, the central's default.",
            evidence,
            "Name the management network only: NOUST_ALLOW_IP=10.20.0.0/24.",
        )
    return Finding(
        "ENS-NET-01",
        title,
        "ok",
        measures,
        "The console answers only the networks named.",
        evidence,
    )


def _hardening_finding(
    check_id: str,
    title: str,
    measures: tuple[str, ...],
    ids: Iterable[str] | None,
    facts: Facts,
    ok: str,
) -> Finding:
    """
    Report a group of hardening checks as one ENS check.

    Args:
        check_id: The ENS check.
        title: Its title.
        measures: Its measures.
        ids: The hardening checks it reports; None for every one no other
            ENS check reports.
        facts: The facts.
        ok: The summary when nothing is open.

    Returns:
        ``fail`` for a failing check, ``warning`` for a warning or one that
        could not run, ``ok`` otherwise; accepted risks are listed with their
        end date.
    """
    if facts.hardening is None:
        return _unknown(check_id, title, measures, "hardening", facts)
    if ids is None:
        grouped = {i for group in HARDENING_GROUPS.values() for i in group}
        selected = [c for c in facts.hardening if c.get("id") not in grouped]
    else:
        wanted = set(ids)
        selected = [c for c in facts.hardening if c.get("id") in wanted]
    relevant = [c for c in selected if c.get("status") != "n/a"]
    if not relevant:
        return Finding(check_id, title, "n/a", measures, "Nothing to check on this server.")
    evidence: list[str] = []
    worst = "ok"
    for check in relevant:
        status = check.get("status")
        if status == "accepted":
            accepted = check.get("accepted") or {}
            evidence.append(
                f"{check['id']}: accepted until {str(accepted.get('expires_at', ''))[:10]} by "
                f"{accepted.get('accepted_by')}: {accepted.get('reason')}"
            )
        elif status in ("fail", "warn", "unknown"):
            evidence.append(f"{check['id']} ({status}): {check.get('reason')}")
            if status == "fail":
                worst = "fail"
            elif worst != "fail":
                worst = "warning"
    if worst == "ok":
        return Finding(check_id, title, "ok", measures, ok, tuple(evidence))
    return Finding(
        check_id,
        title,
        worst,
        measures,
        f"{sum(1 for c in relevant if c.get('status') in ('fail', 'warn', 'unknown'))} hardening "
        "finding(s) open.",
        tuple(evidence),
        "Fix them from Server > Security or with noust server security; accept a risk you keep "
        "with a reason and an end date (noust server security accept <id>).",
    )


def check_backups(facts: Facts) -> Finding:
    """ENS-BAK-01: each application backed up within a day, verified within a week, sent away."""
    title = "Backups taken and verified"
    measures = ("mp.info.6", "mp.info.6.r1")
    if facts.backups is None:
        return _unknown("ENS-BAK-01", title, measures, "backups", facts)
    if not facts.backups:
        return Finding("ENS-BAK-01", title, "n/a", measures, "No application is deployed here.")
    scheduled_away = {s["domain"] for s in facts.schedules if s.get("destinations")}
    problems: list[str] = []
    for entry in facts.backups:
        domain = entry["domain"]
        newest = _age_days(entry.get("newest"), facts.now)
        verified = _age_days(entry.get("verified"), facts.now)
        if newest is None:
            problems.append(f"{domain}: no backup")
            continue
        if newest * 24 > ENS_MEDIUM.backup_max_age_hours:
            problems.append(f"{domain}: newest backup {_day(entry['newest'])}")
        if verified is None or verified > ENS_MEDIUM.backup_verify_days:
            problems.append(f"{domain}: last good verification {_day(entry.get('verified'))}")
        if domain not in scheduled_away:
            problems.append(f"{domain}: no schedule sends it to another place")
    if problems:
        status = "fail" if any("no backup" in p for p in problems) else "warning"
        return Finding(
            "ENS-BAK-01",
            title,
            status,
            measures,
            f"{len(problems)} backup problem(s) across {len(facts.backups)} application(s).",
            tuple(problems),
            "Schedule daily backups with a remote destination (noust backup schedule create "
            "<domain> --schedule daily --destination <name>); schedules verify weekly on "
            "their own.",
        )
    return Finding(
        "ENS-BAK-01",
        title,
        "ok",
        measures,
        f"All {len(facts.backups)} application(s) have a recent, verified backup sent away.",
    )


def check_backup_encryption(facts: Facts) -> Finding:
    """ENS-BAK-02: backups leave the server only encrypted."""
    title = "Backups leave encrypted"
    measures = ("mp.si.2",)
    plain = [d["name"] for d in facts.destinations if not d.get("encrypted")]
    evidence = [f"encryption required: {'yes' if facts.backup_encryption_required else 'no'}"]
    evidence += [
        f"{d['name']} ({d.get('backend')}): {'encrypted' if d.get('encrypted') else 'NOT encrypted'}"
        for d in facts.destinations
    ]
    if not facts.destinations:
        return Finding(
            "ENS-BAK-02",
            title,
            "n/a",
            measures,
            "No remote destination is configured.",
            tuple(evidence),
        )
    if plain:
        return Finding(
            "ENS-BAK-02",
            title,
            "fail",
            measures,
            f"{len(plain)} destination(s) are not encrypted"
            + (" (uploads to them are refused)." if facts.backup_encryption_required else "."),
            tuple(evidence),
            "noust backup destination update <name> --encrypt, then noust backup destination "
            "show-key <name> and keep the key apart.",
        )
    if not facts.backup_encryption_required:
        return Finding(
            "ENS-BAK-02",
            title,
            "warning",
            measures,
            "Every destination is encrypted, but nothing refuses an unencrypted one.",
            tuple(evidence),
            "noust config set security.profile ens-medium (or backup.encryption required).",
        )
    return Finding(
        "ENS-BAK-02",
        title,
        "ok",
        measures,
        "Every destination is encrypted, and it is required.",
        tuple(evidence),
    )


def check_inventory(facts: Facts) -> Finding:
    """ENS-INV-01: every application has an owner and a criticality."""
    title = "Inventory complete"
    measures = ("op.exp.1", "mp.info.2")
    if facts.inventory is None:
        return _unknown("ENS-INV-01", title, measures, "inventory", facts)
    if not facts.inventory:
        return Finding("ENS-INV-01", title, "n/a", measures, "No application is deployed here.")
    incomplete = [e["domain"] for e in facts.inventory if not e.get("complete")]
    if incomplete:
        return Finding(
            "ENS-INV-01",
            title,
            "warning",
            measures,
            f"{len(incomplete)} of {len(facts.inventory)} application(s) have no owner or criticality.",
            tuple(f"incomplete: {domain}" for domain in incomplete),
            "noust ens inventory set <domain> --owner '...' --criticality medium --classification internal",
        )
    return Finding(
        "ENS-INV-01",
        title,
        "ok",
        measures,
        f"All {len(facts.inventory)} application(s) have an owner and a criticality.",
    )


def check_change_control(facts: Facts) -> Finding:
    """ENS-CHG-01: four-eyes approvals and a reason for every change."""
    title = "Change control"
    measures = ("op.exp.5", "op.exp.5.4", "op.acc.3")
    evidence = (
        f"approvals: {'on' if facts.approvals_enabled else 'off'}",
        f"--reason required: {'yes' if facts.cli_reason_required else 'no'}",
    )
    if facts.approvals_enabled and facts.cli_reason_required:
        return Finding(
            "ENS-CHG-01",
            title,
            "ok",
            measures,
            "Root-equivalent actions need a second person, and changes a reason.",
            evidence,
        )
    return Finding(
        "ENS-CHG-01",
        title,
        "fail",
        measures,
        "One person can make a root-equivalent change alone, or without saying why.",
        evidence,
        "noust config set security.profile ens-medium",
    )


def check_origins(facts: Facts) -> Finding:
    """ENS-SUP-01: code is deployed only from named origins."""
    title = "Allowed code origins"
    measures = ("mp.sw.1", "op.exp.5")
    if facts.allowed_sources:
        return Finding(
            "ENS-SUP-01",
            title,
            "ok",
            measures,
            "Code is deployed only from the origins named.",
            tuple(f"allowed: {entry}" for entry in facts.allowed_sources),
        )
    return Finding(
        "ENS-SUP-01",
        title,
        "warning",
        measures,
        "Code may be deployed from anywhere.",
        ("security.allowed_sources: none",),
        "Name your repositories: noust config set security.allowed_sources '[github.com/your-org]'",
    )


def check_builds(facts: Facts) -> Finding:
    """ENS-BLD-01: builds run unprivileged, in the sandbox."""
    title = "Builds sandboxed"
    measures = ("op.exp.6", "op.exp.2.2")
    if "sandbox" in facts.errors:
        return _unknown("ENS-BLD-01", title, measures, "sandbox", facts)
    if facts.sandbox_warnings:
        return Finding(
            "ENS-BLD-01",
            title,
            "warning",
            measures,
            f"{len(facts.sandbox_warnings)} application(s) build as root.",
            tuple(facts.sandbox_warnings.values()),
            "noust app sandbox test <domain>, then noust app sandbox enable <domain>.",
        )
    return Finding(
        "ENS-BLD-01",
        title,
        "ok",
        measures,
        "Every application builds in the sandbox, or has nothing to build.",
    )


def check_fleet(facts: Facts) -> Finding:
    """ENS-FLT-01: centrals reach nodes through the unprivileged account, with a ceiling."""
    title = "Fleet access"
    measures = ("op.acc.4.2", "op.ext.4")
    evidence: list[str] = []
    problems: list[str] = []
    for node in facts.nodes:
        evidence.append(
            f"{node['name']}: tunnel as {node.get('ssh_user')}, ceiling "
            f"{node.get('access_level') or 'unknown'}"
            + (", host access" if node.get("host_access") else "")
        )
        if node.get("ssh_user") == "root":
            problems.append(f"{node['name']}: the tunnel logs in as root")
        if node.get("access_level") == "admin" and node.get("host_access"):
            problems.append(f"{node['name']}: the central may change SSH and the firewall")
    if facts.fleet_ceiling:
        evidence.append(
            f"this node's ceiling for its centrals: {facts.fleet_ceiling['level']}"
            + (", host access" if facts.fleet_ceiling.get("host_access") else "")
        )
        if facts.fleet_ceiling.get("host_access"):
            problems.append("a central may change this node's SSH and firewall")
    root_key = next(
        (c for c in facts.hardening or [] if c.get("id") == "noust.fleet_key_root"), None
    )
    if root_key and root_key.get("status") in ("fail", "warn"):
        problems.append(str(root_key.get("reason")))
    if not evidence and not problems:
        return Finding("ENS-FLT-01", title, "n/a", measures, "This server is not part of a fleet.")
    if problems:
        return Finding(
            "ENS-FLT-01",
            title,
            "warning",
            measures,
            f"{len(problems)} fleet access problem(s).",
            tuple(evidence + problems),
            "noust node migrate-tunnel <node> (the unprivileged noust-tunnel account); "
            "noust fleet access --level deploy on a node that needs no more.",
        )
    return Finding(
        "ENS-FLT-01",
        title,
        "ok",
        measures,
        "Every tunnel uses the unprivileged account.",
        tuple(evidence),
    )


def check_lockdown(facts: Facts) -> Finding:
    """ENS-INC-01: an incident lockdown is not forgotten."""
    title = "Incident lockdown"
    measures = ("op.exp.7",)
    if facts.lockdown is None:
        return Finding("ENS-INC-01", title, "ok", measures, "The console is not locked down.")
    state = facts.lockdown
    return Finding(
        "ENS-INC-01",
        title,
        "warning",
        measures,
        "The console is locked down for an incident: only the master token signs in.",
        (f"since {state.get('since')} by {state.get('by')}: {state.get('reason')}",),
        'Close the incident, then: noust incident unfreeze --reason "..."',
    )


def evaluate(facts: Facts) -> list[Finding]:
    """
    Judge the facts, check by check.

    Args:
        facts: What :func:`gather` read.

    Returns:
        One finding per check, in catalog order.
    """
    return [
        check_profile(facts),
        check_roles(facts),
        check_mfa(facts),
        check_master_token(facts),
        check_tokens(facts),
        check_access_review(facts),
        check_stale_accounts(facts),
        check_sessions(facts),
        check_audit_chain(facts),
        check_audit_shipping(facts),
        check_audit_retention(facts),
        _hardening_finding(
            "ENS-LOG-04",
            "Clock synchronised",
            ("op.exp.8.r2",),
            HARDENING_GROUPS["ENS-LOG-04"],
            facts,
            "The clock is synchronised.",
        ),
        check_audit_review(facts),
        check_certificate(facts),
        check_cleartext(facts),
        check_secrets_at_rest(facts),
        check_allowlist(facts),
        _hardening_finding(
            "ENS-NET-02",
            "SSH hardened",
            ("op.acc.6.r9", "op.exp.2"),
            HARDENING_GROUPS["ENS-NET-02"],
            facts,
            "SSH refuses passwords and root's password, and its limits are tight.",
        ),
        _hardening_finding(
            "ENS-NET-03",
            "Firewall and exposure",
            ("mp.com.1", "op.mon.3.r2"),
            HARDENING_GROUPS["ENS-NET-03"],
            facts,
            "A firewall is on and nothing internal is reachable from outside.",
        ),
        check_backups(facts),
        check_backup_encryption(facts),
        _hardening_finding(
            "ENS-UPD-01",
            "Patches and reboots",
            ("op.exp.4",),
            HARDENING_GROUPS["ENS-UPD-01"],
            facts,
            "No security update is pending and no reboot is needed.",
        ),
        _hardening_finding(
            "ENS-MON-01",
            "Intrusion signals",
            ("op.mon.1",),
            HARDENING_GROUPS["ENS-MON-01"],
            facts,
            "Password guessing on SSH is slowed down and logged.",
        ),
        _hardening_finding(
            "ENS-HRD-01",
            "Other hardening",
            ("op.exp.2", "op.exp.3.r1"),
            None,
            facts,
            "No other hardening finding is open.",
        ),
        check_inventory(facts),
        check_change_control(facts),
        check_origins(facts),
        check_builds(facts),
        check_fleet(facts),
        check_lockdown(facts),
    ]


def indicators(facts: Facts) -> dict[str, Any]:
    """
    The figures of op.mon.2 (and the annual report of art. 32).

    Args:
        facts: The facts.

    Returns:
        Accounts, second factors, reviews, break-glass uses, lockouts,
        backups verified, hardening counts, patches, fleet, sandbox, inventory.
    """
    accounts = [a for a in facts.accounts or [] if a.get("status") in ("active", "locked")]
    with_mfa = sum(1 for a in accounts if a.get("mfa_enabled"))
    hardening = facts.hardening or []
    backups = facts.backups or []
    verified = sum(
        1
        for entry in backups
        if (age := _age_days(entry.get("verified"), facts.now)) is not None
        and age <= ENS_MEDIUM.backup_verify_days
    )
    inventory = facts.inventory or []
    by_role: dict[str, int] = {}
    for account in accounts:
        by_role[str(account.get("role"))] = by_role.get(str(account.get("role")), 0) + 1
    now = facts.now.timestamp()
    tokens = [
        t
        for t in facts.tokens or []
        if not t.get("revoked_at") and (t.get("expires_at") is None or float(t["expires_at"]) > now)
    ]

    def percent(part: int, whole: int) -> float | None:
        return round(100.0 * part / whole, 1) if whole else None

    return {
        "accounts": len(accounts),
        "accounts_by_role": by_role,
        "mfa_percent": percent(with_mfa, len(accounts)),
        "tokens_live": len(tokens),
        "tokens_without_expiry": sum(1 for t in tokens if t.get("expires_at") is None),
        "break_glass_uses_30d": facts.audit_events.get("break_glass_30d", 0),
        "lockouts_30d": facts.audit_events.get("lockouts_30d", 0),
        "last_access_review": facts.audit_events.get("last_access_review"),
        "last_audit_review": facts.audit_events.get("last_audit_review"),
        "audit_events_checked": (facts.audit_verify or {}).get("checked"),
        "backup_verifications_30d": facts.audit_events.get("backup_verifications_30d", 0),
        "applications": len(backups),
        "backups_verified_percent": percent(verified, len(backups)),
        "hardening_failing": sum(1 for c in hardening if c.get("status") == "fail"),
        "hardening_warning": sum(1 for c in hardening if c.get("status") == "warn"),
        "hardening_accepted": sum(1 for c in hardening if c.get("status") == "accepted"),
        "security_updates_pending": any(
            c.get("id") == "upd.security_pending" and c.get("status") in ("fail", "accepted")
            for c in hardening
        ),
        "reboot_required": any(
            c.get("id") == "upd.reboot_required" and c.get("status") in ("warn", "fail", "accepted")
            for c in hardening
        ),
        "nodes": len(facts.nodes),
        "nodes_root_tunnel": sum(1 for n in facts.nodes if n.get("ssh_user") == "root"),
        "applications_building_as_root": len(facts.sandbox_warnings),
        "inventory_complete_percent": percent(
            sum(1 for e in inventory if e.get("complete")), len(inventory)
        ),
    }


def run_check(sources: Sources | None = None) -> tuple[ComplianceCheck, Facts]:
    """
    Gather and judge: ``noust ens check`` and ``GET /api/ens/check``.

    Args:
        sources: Where to read each area; the running system by default.

    Returns:
        The result, and the facts it judged (the report includes them).
    """
    from noust import __version__

    facts = gather(sources)
    result = ComplianceCheck(
        profile=facts.profile,
        checked_at=facts.now.isoformat(timespec="seconds"),
        host=socket.gethostname(),
        version=__version__,
        findings=evaluate(facts),
        indicators=indicators(facts),
        errors=dict(facts.errors),
    )
    return result, facts


def profile_values(defaults: ProfileDefaults) -> dict[str, Any]:
    """
    Args:
        defaults: A profile.

    Returns:
        Its values, JSON-ready.
    """
    return asdict(defaults)


__all__ = [
    "HARDENING_GROUPS",
    "STATUSES",
    "ComplianceCheck",
    "ConsoleExposure",
    "Facts",
    "Finding",
    "Sources",
    "evaluate",
    "exposure_from_installation",
    "gather",
    "indicators",
    "profile_values",
    "run_check",
]
