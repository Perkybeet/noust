# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust compliance check``: this server, framework by framework, clause by clause.

For every framework asked about (the ones that are on, or the one named), and for
every control it asks of, one finding that says three things:

1. **Is the control in force at the strictness the framework asks?** The value in
   force (:mod:`noust.core.compliance.resolve`) against the value required. A
   framework that is not enabled is judged the same way: the finding says what
   enabling it would change.
2. **Is the server set up the way the control assumes?** Some controls are limits
   Noust enforces by refusing (a session's idle time); others are expectations only
   the server's state can show (an audit destination off the machine, a backup
   stored elsewhere, a certificate that is not self-signed). For those the facts
   gathered by ``noust ens check`` (:mod:`noust.core.ens.checks`) are read, so the two
   commands never disagree.
3. **Which clause is that?** Every finding carries the clauses of the mapping table.

A finding that passes means the *technical control* is in force. It is never a
statement that the server, or the organisation, complies with the framework: what
stays organisational is listed with each framework and printed after the findings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from noust.core.compliance import controls, frameworks, resolve

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_FAIL = "fail"
STATUS_NA = "n/a"
STATUSES = (STATUS_OK, STATUS_WARNING, STATUS_FAIL, STATUS_NA)

_ORDER = {STATUS_OK: 0, STATUS_NA: 0, STATUS_WARNING: 1, STATUS_FAIL: 2}


@dataclass(frozen=True)
class Finding:
    """
    One control of one framework, judged.

    Attributes:
        framework: The framework's id.
        control: The control's id.
        clauses: The clauses the control serves in that framework, ``id: title``.
        status: ``ok``, ``warning``, ``fail`` or ``n/a``.
        required: What the framework asks.
        actual: The value in force.
        summary: What was found, in a sentence.
        evidence: Values and facts, verbatim; never a secret.
        remediation: What to do; empty when nothing is.
    """

    framework: str
    control: str
    clauses: tuple[str, ...]
    status: str
    required: Any
    actual: Any
    summary: str
    evidence: tuple[str, ...] = ()
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The finding, JSON-ready.
        """
        return {
            "framework": self.framework,
            "control": self.control,
            "clauses": list(self.clauses),
            "status": self.status,
            "required": self.required,
            "actual": self.actual,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "remediation": self.remediation,
        }


@dataclass
class FrameworkResult:
    """
    One framework's findings.

    Attributes:
        framework: The framework.
        enabled: Whether it is on.
        findings: One per control it asks of, in catalog order.
    """

    framework: frameworks.Framework
    enabled: bool
    findings: list[Finding] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """Findings per status."""
        found = dict.fromkeys(STATUSES, 0)
        for finding in self.findings:
            found[finding.status] += 1
        return found

    @property
    def verdict(self) -> str:
        """The worst status among the findings."""
        worst = STATUS_OK
        for finding in self.findings:
            if _ORDER[finding.status] > _ORDER[worst]:
                worst = finding.status
        return worst

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result, JSON-ready, with what stays organisational.
        """
        return {
            "framework": self.framework.id,
            "name": self.framework.name,
            "enabled": self.enabled,
            "verdict": self.verdict,
            "counts": self.counts,
            "findings": [finding.to_dict() for finding in self.findings],
            "organisational": list(self.framework.organisational),
            "not_covered": list(self.framework.not_covered),
        }


@dataclass
class CheckResult:
    """
    The result of ``noust compliance check``.

    Attributes:
        results: One per framework judged.
        errors: Areas of the server that could not be read, verbatim.
    """

    results: list[FrameworkResult]
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        """The worst verdict among the frameworks."""
        worst = STATUS_OK
        for result in self.results:
            if _ORDER[result.verdict] > _ORDER[worst]:
                worst = result.verdict
        return worst

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result, JSON-ready.
        """
        return {
            "verdict": self.verdict,
            "frameworks": [result.to_dict() for result in self.results],
            "errors": self.errors,
            "disclaimer": DISCLAIMER,
        }


#: Said after every check: a pass is a technical control in force, nothing more.
DISCLAIMER = (
    "A passing control means the technical control is in force on this server. It does "
    "not mean the server, or the organisation, complies with the framework: what stays "
    "organisational is listed with each framework."
)

# --------------------------------------------------------------------------- measures

#: What the facts show about a control that only the server's state can show: the
#: control and the facts in, a finding's status, summary, evidence and remediation out
#: (None when the facts say nothing about it).
Measure = Callable[[Any, Any], "tuple[str, str, tuple[str, ...], str] | None"]


def _age(value: Any, facts: Any) -> float | None:
    from noust.core.ens import checks

    return checks.age_days(value, facts.now)


def _day(value: Any) -> str:
    from noust.core.ens import checks

    return checks.day_of(value)


def _measure_audit_sink(facts: Any, required: Any) -> tuple[str, str, tuple[str, ...], str] | None:
    if facts.audit_health is None:
        return None
    evidence = (
        f"journald: {'yes' if facts.audit_journald else 'no'}",
        f"syslog receivers: {facts.audit_syslog}",
    )
    if facts.audit_syslog:
        return STATUS_OK, "The audit trail is shipped as it is written.", evidence, ""
    if facts.audit_journald:
        return (
            STATUS_WARNING,
            "The events reach journald; nothing here shows journald forwards them off the machine.",
            evidence,
            "Forward journald or add a syslog receiver in audit.syslog.",
        )
    return (
        STATUS_FAIL,
        "The audit log exists only on this machine, where root can rewrite it.",
        evidence,
        "Send it to your SIEM: noust config set audit.syslog '[{transport: tls, address: "
        "siem.example:6514}]'.",
    )


def _measure_offsite(facts: Any, required: Any) -> tuple[str, str, tuple[str, ...], str] | None:
    if facts.backups is None:
        return None
    if not facts.backups:
        return STATUS_NA, "No application is deployed here.", (), ""
    away = {s["domain"] for s in facts.schedules if s.get("destinations")}
    missing = [entry["domain"] for entry in facts.backups if entry["domain"] not in away]
    if missing:
        return (
            STATUS_FAIL,
            f"{len(missing)} application(s) have no backup sent to another place.",
            tuple(f"{domain}: no schedule sends it away" for domain in missing),
            "noust backup schedule create <domain> --schedule daily --destination <name>",
        )
    return STATUS_OK, "Every application has a backup sent to another place.", (), ""


def _measure_review(key: str, command: str) -> Measure:
    def measure(facts: Any, required: Any) -> tuple[str, str, tuple[str, ...], str] | None:
        last = facts.audit_events.get(key)
        age = _age(last, facts)
        evidence = (f"last review: {_day(last)}", f"required every {required} days")
        if age is None:
            return STATUS_FAIL, "No review is on record.", evidence, command
        if required is not None and age > required:
            return STATUS_WARNING, f"The last review is {int(age)} days old.", evidence, command
        return STATUS_OK, f"Reviewed {int(age)} day(s) ago.", evidence, ""

    return measure


def _measure_stale(facts: Any, required: Any) -> tuple[str, str, tuple[str, ...], str] | None:
    if facts.accounts is None:
        return None
    stale = []
    for account in facts.accounts:
        if account.get("status") not in ("active", "invited"):
            continue
        age = _age(account.get("last_login_at") or account.get("created_at"), facts)
        if age is not None and required is not None and age > required:
            stale.append(f"{account['username']}: no sign-in for {int(age)} days")
    if stale:
        return (
            STATUS_WARNING,
            f"{len(stale)} account(s) unused for {required} days.",
            tuple(stale),
            "Disable what is no longer needed: noust user disable <name>.",
        )
    return STATUS_OK, f"No account unused for {required} days.", (), ""


def _measure_backup_age(facts: Any, required: Any) -> tuple[str, str, tuple[str, ...], str] | None:
    if facts.backups is None:
        return None
    if not facts.backups:
        return STATUS_NA, "No application is deployed here.", (), ""
    late = []
    for entry in facts.backups:
        age = _age(entry.get("newest"), facts)
        if age is None:
            late.append(f"{entry['domain']}: no backup")
        elif required is not None and age * 24 > required:
            late.append(f"{entry['domain']}: newest backup {_day(entry['newest'])}")
    if late:
        return (
            STATUS_FAIL if any("no backup" in item for item in late) else STATUS_WARNING,
            f"{len(late)} application(s) without a backup in the last {required} hours.",
            tuple(late),
            "noust backup schedule create <domain> --schedule daily",
        )
    return STATUS_OK, f"Every application has a backup under {required} hours old.", (), ""


def _measure_backup_verified(
    facts: Any, required: Any
) -> tuple[str, str, tuple[str, ...], str] | None:
    if facts.backups is None:
        return None
    if not facts.backups:
        return STATUS_NA, "No application is deployed here.", (), ""
    late = []
    for entry in facts.backups:
        age = _age(entry.get("verified"), facts)
        if age is None or (required is not None and age > required):
            late.append(f"{entry['domain']}: last good verification {_day(entry.get('verified'))}")
    if late:
        return (
            STATUS_WARNING,
            f"{len(late)} application(s) not verified in {required} days.",
            tuple(late),
            "noust backup verify <domain>; schedules verify on their own.",
        )
    return STATUS_OK, f"Every application was verified within {required} days.", (), ""


def _measure_certificate(facts: Any, required: Any) -> tuple[str, str, tuple[str, ...], str] | None:
    exposure = facts.exposure
    if exposure is None:
        return None
    if not exposure.certificate:
        if exposure.loopback_only:
            return STATUS_NA, "The console is reached on this machine only.", (), ""
        return (
            STATUS_FAIL,
            "The console answers beyond this machine without a certificate.",
            (),
            "noust web enable --tls-cert <fullchain.pem> --tls-key <privkey.pem>",
        )
    cert = facts.certificate or {}
    if cert.get("self_signed"):
        return (
            STATUS_FAIL,
            "The console serves a self-signed certificate.",
            (f"certificate: {exposure.certificate}",),
            "noust web enable --tls-cert <fullchain.pem> --tls-key <privkey.pem>",
        )
    return STATUS_OK, "The console serves a certificate that is not self-signed.", (), ""


def _measure_certificate_days(
    facts: Any, required: Any
) -> tuple[str, str, tuple[str, ...], str] | None:
    from noust.core.ens import checks

    cert = facts.certificate or {}
    if not cert.get("expires"):
        return None
    expiry = checks.parse_time(cert["expires"])
    if expiry is None:
        return None
    days = (expiry - facts.now).days
    if days < 0:
        return STATUS_FAIL, "The console's certificate has expired.", (), "Renew it."
    if required is not None and days < required:
        return (
            STATUS_WARNING,
            f"The console's certificate expires in {days} days.",
            (),
            "Renew it before it expires.",
        )
    return STATUS_OK, f"The console's certificate expires in {days} days.", (), ""


def _measure_allowlist(facts: Any, required: Any) -> tuple[str, str, tuple[str, ...], str] | None:
    exposure = facts.exposure
    if exposure is None:
        return None
    if exposure.loopback_only:
        return STATUS_NA, "The console is reached on this machine only.", (), ""
    if not exposure.allow_ip:
        return (
            STATUS_FAIL,
            "The console answers beyond this machine and names nobody who may connect.",
            (),
            "noust web enable --allow-ip 10.20.0.0/24",
        )
    return STATUS_OK, f"The console names who may connect: {', '.join(exposure.allow_ip)}.", (), ""


#: The controls that need the server's state to be judged.
MEASURES: dict[str, Measure] = {
    "external_audit_sink": _measure_audit_sink,
    "backup_offsite": _measure_offsite,
    "access_review_days": _measure_review(
        "last_access_review", "noust ens access-review attest --notes '...'"
    ),
    "audit_review_days": _measure_review(
        "last_audit_review", "noust audit review --from <date> --to <date> --notes '...'"
    ),
    "stale_account_days": _measure_stale,
    "backup_max_age_hours": _measure_backup_age,
    "backup_verify_days": _measure_backup_verified,
    "tls_certificate": _measure_certificate,
    "certificate_min_days": _measure_certificate_days,
    "explicit_allowlist": _measure_allowlist,
}


def _show(value: Any) -> str:
    """
    Args:
        value: A control's value.

    Returns:
        How it reads in a finding.
    """
    if value is None:
        return "no limit"
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value)


def judge(
    framework: frameworks.Framework,
    resolutions: dict[str, resolve.Resolution],
    facts: Any | None,
    *,
    enabled: bool,
) -> FrameworkResult:
    """
    Judge one framework against how the server is set up.

    Args:
        framework: The framework.
        resolutions: Every control as it stands.
        facts: What ``noust ens check`` gathers, or None to judge settings only.
        enabled: Whether the framework is on.

    Returns:
        Its findings.
    """
    result = FrameworkResult(framework, enabled)
    asked = framework.all_requirements()
    for control in controls.CONTROLS:
        required = asked.get(control.id)
        if required is None:
            continue
        now = resolutions[control.id].value
        clauses = tuple(f"{clause.id}: {clause.title}" for clause in required.clauses)
        if not control.satisfies(now, required.value):
            where = (
                f"noust compliance enable {framework.id}"
                if not enabled
                else f"noust config set {control.key} ..."
            )
            result.findings.append(
                Finding(
                    framework.id,
                    control.id,
                    clauses,
                    STATUS_FAIL,
                    required.value,
                    now,
                    f"{control.id} is {_show(now)}; {framework.name} asks for {_show(required.value)}.",
                    (f"setting: {control.key or 'set by the framework only'}",),
                    where,
                )
            )
            continue
        measured = None
        measure = MEASURES.get(control.id)
        if measure is not None and facts is not None:
            measured = measure(facts, now if control.kind != controls.KIND_FLAG else required.value)
        if measured is None:
            summary = (
                f"{control.id} is {_show(now)} ({framework.name} asks for {_show(required.value)})."
            )
            if control.check_only:
                summary += (
                    " Nothing refuses it: this is an expectation about how the server is set up."
                )
            result.findings.append(
                Finding(framework.id, control.id, clauses, STATUS_OK, required.value, now, summary)
            )
            continue
        status, summary, evidence, remediation = measured
        result.findings.append(
            Finding(
                framework.id,
                control.id,
                clauses,
                status,
                required.value,
                now,
                summary,
                evidence,
                remediation,
            )
        )
    return result


def run(
    framework_ids: list[str] | None = None,
    *,
    config: Any | None = None,
    facts: Any | None = None,
    gather_facts: bool = True,
) -> CheckResult:
    """
    Judge the server against the frameworks that are on, or the ones named.

    Args:
        framework_ids: Frameworks to judge; the enabled ones when None.
        config: The configuration; the process-wide one by default.
        facts: Facts already gathered (``noust ens check``'s), to avoid gathering twice.
        gather_facts: Read the server's state for the controls that need it. Off,
            only the settings are judged.

    Returns:
        The findings per framework.

    Raises:
        KeyError: When a framework named does not exist; callers validate first.
    """
    enabled = resolve.enabled_ids(config)
    names = enabled if framework_ids is None else list(framework_ids)
    resolutions = {r.control.id: r for r in resolve.resolve_all(config)}
    errors: dict[str, str] = {}
    if facts is None and gather_facts and names:
        from noust.core.ens import checks

        facts = checks.gather(checks.Sources(config=config, refresh_hardening=False))
    if facts is not None:
        errors = dict(facts.errors)
    results = [
        judge(frameworks.get(name), resolutions, facts, enabled=name in enabled) for name in names
    ]
    return CheckResult(results, errors)
