# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/security``: hardening checks, SSH, the firewall, fail2ban, accepted risks.

A client of :class:`~noust.managers.server.security.ServerSecurity`, like
``noust server security``: every guard, every confirm-or-revert and every
audit record is in the managers, so this module only translates.

- **Reads** need ``server.read``.
- **Changes** need sudo mode (``require_elevated``) and run as jobs whose log is
  the tools' own output. Each is checked first (:meth:`ServerSecurity.preflight`),
  so a change a guard refuses answers 400 at once with the guided steps in
  ``hint``, instead of queueing a job that would fail. The job checks again.
- **Changes to how the server is reached** (sshd, keys, the firewall) need
  ``server.host_access``, which a central's fleet token only holds when the
  node allowed it (``noust fleet access --host-access``); the permission map
  (:mod:`noust.web.permissions.routes_server_security`) is enforced where the
  credential is admitted, not here. Their job ends with a pending change
  that undoes itself unless confirmed: ``POST /changes/{id}/confirm`` after
  a new SSH login, or ``/revert``.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from noust.core.exceptions import ValidationError
from noust.managers.server.security import OPERATIONS, ServerSecurity
from noust.managers.server.security_catalog import CATALOG
from noust.managers.server.security_ssh import SSH_FIXES
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, require_elevated
from noust.web.api.server.common import ServerRoute, accepted, require_job_context
from noust.web.auth import actor_label, get_client_ip
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=ServerRoute, prefix="/security")


# Models ---------------------------------------------------------------------------


class CheckFixOut(BaseModel):
    """How a finding is fixed: ``automatic``, ``action``, ``guided`` or ``none``."""

    kind: str
    summary: str = ""
    action: str | None = None
    steps: list[str] = []
    cli: str | None = None
    endpoint: str | None = None
    reverts: bool = False
    blocked: str = ""


class AcceptedRiskOut(BaseModel):
    """A risk accepted instead of fixed, until ``expires_at``."""

    id: int
    check_id: str
    reason: str
    accepted_by: str
    accepted_at: str
    expires_at: str
    revoked_at: str | None = None
    revoked_by: str | None = None


class CheckOut(BaseModel):
    """
    One hardening check.

    ``status`` is ``pass``, ``warn``, ``fail``, ``unknown``, ``n/a`` or
    ``accepted``; ``evidence`` is what the system said, verbatim.
    """

    id: str
    group: str
    title: str
    severity: str
    status: str
    reason: str
    evidence: list[str] = []
    fix: CheckFixOut | None = None
    accepted: AcceptedRiskOut | None = None


class CheckCountsOut(BaseModel):
    """How many checks are in each state."""

    critical: int
    warning: int
    accepted: int
    unknown: int
    passed: int
    not_applicable: int


class ChecksOut(BaseModel):
    """Every check, and when they ran."""

    checked_at: str
    counts: CheckCountsOut
    checks: list[CheckOut]


class ChangeOut(BaseModel):
    """
    A change to sshd or the firewall.

    ``pending`` until confirmed or undone; ``expires_at`` (epoch seconds) is
    when its timer undoes it.
    """

    id: str
    kind: str
    title: str
    actor: str
    applied_at: float
    expires_at: float
    status: str
    unit: str
    files: list[str] = []
    validate_command: list[str] = []
    undo: list[list[str]] = []
    commit: list[list[str]] = []
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    proof: str
    resolved_at: float | None = None
    resolved_by: str | None = None
    resolution: str = ""


class OverviewOut(BaseModel):
    """The Security tab's summary: counts and open findings of the last run, pending changes."""

    checked_at: str | None = None
    counts: CheckCountsOut | None = None
    attention: list[CheckOut] = []
    pending: list[ChangeOut] = []


class DirectiveChangeOut(BaseModel):
    """One sshd directive, before and after."""

    directive: str
    before: str
    after: str


class FixPlanOut(BaseModel):
    """What an sshd fix would change, whether its guard holds, and the steps when not."""

    fix: str
    title: str
    check_id: str
    changes: list[DirectiveChangeOut]
    needed: bool
    allowed: bool
    blockers: list[str] = []
    guidance: list[str] = []
    proof: str | None = None
    evidence: list[str] = []


class SshUnitOut(BaseModel):
    """How systemd runs sshd."""

    service: str | None = None
    active: bool = False
    socket: str | None = None
    socket_active: bool = False


class SshSessionOut(BaseModel):
    """An SSH connection open now."""

    peer: str
    port: int
    user: str | None = None
    fingerprint: str | None = None


class LoginOut(BaseModel):
    """A login sshd recorded."""

    at: float
    user: str
    method: str
    source: str
    fingerprint: str | None = None
    line: str


class LoginsOut(BaseModel):
    """Where the login history came from, and the latest logins."""

    source: str
    error: str
    days: int
    recent: list[LoginOut] = []


class SshStatusOut(BaseModel):
    """sshd as it runs: effective values (``sshd -T -C``), Noust's drop-in, fixes."""

    effective: dict[str, str] = {}
    error: str | None = None
    error_output: str | None = None
    dropin: str | None = None
    dropin_error: str | None = None
    dropin_settings: dict[str, str] = {}
    include_present: bool
    root_password: str
    ports: list[int] = []
    passwords_accepted: bool | None = None
    unit: SshUnitOut | None = None
    fixes: dict[str, FixPlanOut] = {}
    sessions: list[SshSessionOut] = []
    logins: LoginsOut
    confirm_window: int


class KeyOut(BaseModel):
    """One key: its kind (``operator``, ``central``, ``restricted``, ``cloud_disabled``) and use."""

    fingerprint: str
    type: str
    bits: int | None = None
    comment: str
    kind: str
    options: list[str] = []
    weak: str = ""
    last_used: float | None = None
    last_used_from: str | None = None
    in_use: bool = False


class KeyFileOut(BaseModel):
    """One ``authorized_keys`` file, and what would make StrictModes ignore it."""

    path: str
    exists: bool
    problems: list[str] = []
    error: str = ""
    keys: list[KeyOut] = []


class AccountKeysOut(BaseModel):
    """An account that can become root, and the keys that open it."""

    user: str
    uid: int
    sudo: bool
    sudo_usable: bool
    password: str
    login_allowed: bool
    login_refusal: str
    files: list[KeyFileOut] = []


class RuleOut(BaseModel):
    """A firewall rule. ``ports`` are inclusive ranges; empty is every port."""

    id: str
    backend: str
    action: str
    ports: list[list[int]] = []
    proto: str
    source: str
    spec: str
    comment: str = ""
    noust: bool = False
    service: str | None = None
    known: bool = True


class FirewallStateOut(BaseModel):
    """The firewall: ``ufw``, ``firewalld``, ``nftables`` or ``none``."""

    backend: str
    active: bool
    default_incoming: str
    rules: list[RuleOut] = []
    zone: str | None = None
    installed: bool
    status: str
    others: list[str] = []
    warnings: list[str] = []
    error: str


class DockerPortOut(BaseModel):
    """A port Docker publishes on the host."""

    container: str
    project: str | None = None
    host_address: str
    host_port: int
    container_port: int
    proto: str


class PortOut(BaseModel):
    """
    A port that answers, and the firewall's verdict.

    ``verdict`` is ``local``, ``blocked``, ``open``, ``open_to``, ``no_firewall``
    or ``docker_bypass``.
    """

    proto: str
    port: int
    address: str
    process: str | None = None
    verdict: str
    sources: list[str] = []
    risky: str = ""
    baseline: bool = False
    docker: DockerPortOut | None = None
    reachable: bool


class ProtectedPortOut(BaseModel):
    """A port the anti-lockout guard keeps open, and why."""

    port: int
    reason: str


class FirewallOut(BaseModel):
    """The firewall, every port that answers, and what the guard protects."""

    firewall: FirewallStateOut
    ports: list[PortOut]
    ports_error: str | None = None
    protected_ports: list[ProtectedPortOut]
    session_sources: list[str]


class JailOut(BaseModel):
    """A fail2ban jail and its bans."""

    name: str
    currently_failed: int
    total_failed: int
    currently_banned: int
    total_banned: int
    banned: list[str] = []
    reads: str = ""


class Fail2banOut(BaseModel):
    """fail2ban on this server; ``needs_epel`` means installing asks a separate yes."""

    installed: bool
    running: bool
    jails: list[JailOut] = []
    substitutes: list[str] = []
    needs_epel: bool
    install_supported: bool
    install_hint: str
    error: str


class FixRequest(BaseModel):
    """Apply a check's automatic fix; ``epel`` confirms enabling EPEL for fail2ban."""

    epel: bool = False


class AddKeyRequest(BaseModel):
    """A public key, as in a ``.pub`` file, for root or an account that can become root."""

    user: str
    public_key: str


class RemoveKeyRequest(BaseModel):
    """Remove a key; ``force`` overrides the guard (a central's key, a key in use)."""

    user: str
    fingerprint: str
    force: bool = False


class RuleRequestIn(BaseModel):
    """A rule: ``allow`` or ``deny`` a port, from everyone (``any``) or an address or network."""

    action: str
    port: int
    proto: str = "tcp"
    source: str = "any"
    comment: str = ""


class Fail2banInstallRequest(BaseModel):
    """Install fail2ban; ``epel`` confirms enabling EPEL where it is needed."""

    epel: bool = False


class UnbanRequest(BaseModel):
    """Lift a ban; every jail that bans the address when ``jail`` is omitted."""

    address: str
    jail: str | None = None


class AcceptRiskRequest(BaseModel):
    """Accept a finding: why (10 to 500 characters) and until when (ISO date or date-time)."""

    reason: str
    expires_at: str


# Helpers ----------------------------------------------------------------------------


def _security(session: dict[str, Any]) -> ServerSecurity:
    """
    The security manager, acting for whoever made the request.

    Args:
        session: The authenticated session.

    Returns:
        The manager.
    """
    return ServerSecurity(actor=actor_label(session))


def _change(data: dict[str, Any]) -> ChangeOut:
    fields = dict(data)
    fields["validate_command"] = fields.pop("validate", [])
    return ChangeOut(**fields)


def _check(data: dict[str, Any]) -> CheckOut:
    return CheckOut(**data)


def security_job(
    operation: str,
    actor: str,
    params: dict[str, Any],
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Make one security change on the job worker, its tools' output in the job's log.

    Args:
        operation: One of :data:`~noust.managers.server.security.OPERATIONS`.
        actor: Who asked.
        params: The change's parameters.
        job_context: Injected by the job manager.

    Returns:
        What was done; ``change`` holds a pending change to confirm.
    """
    context = require_job_context(job_context)
    context.update(f"Running {operation}", 10)
    security = ServerSecurity(actor=actor, on_output=context.log)
    result = security.execute(operation, params)
    change = result.get("change")
    if isinstance(change, dict) and change.get("status") == "pending":
        context.log(
            f"Applied. It undoes itself in {int(change['expires_at'] - change['applied_at'])} s "
            f"unless confirmed: open a new SSH session, then confirm change {change['id']}.",
            "warning",
        )
    context.update("Done", 100)
    return result


def _queue(
    request: Request, session: dict[str, Any], operation: str, params: dict[str, Any]
) -> JobAcceptedResponse:
    """
    Check a change now, then run it as a job.

    Args:
        request: The request.
        session: The authenticated, elevated session.
        operation: One of :data:`~noust.managers.server.security.OPERATIONS`.
        params: Its parameters.

    Returns:
        The queued job.
    """
    title = _security(session).preflight(operation, params)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_SECURITY,
        name=title,
        description=f"{title} ({operation})",
        func=security_job,
        kwargs={"operation": operation, "actor": actor, "params": params},
        metadata={"operation": operation, "access_change": OPERATIONS[operation][1]},
        actor=actor,
    )
    return accepted(job, title)


def _client_ignore(request: Request) -> list[str]:
    """
    The console client's address, for fail2ban never to ban it.

    Args:
        request: The request.

    Returns:
        The address, unless it is loopback (the SSH tunnel) or unreadable.
    """
    address = get_client_ip(request)
    try:
        return [] if ipaddress.ip_address(address).is_loopback else [address]
    except ValueError:
        return []


def _expiry(text: str) -> datetime:
    """
    Read an acceptance's end.

    Args:
        text: ``2026-12-31`` (the end of that day, UTC) or an ISO date-time.

    Returns:
        The moment, aware.

    Raises:
        ValidationError: It is neither.
    """
    try:
        if len(text) == 10:
            day = datetime.fromisoformat(text)
            return day.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"{text!r} is not a date",
            details="Use 2026-12-31 or 2026-12-31T18:00:00Z.",
            field="expires_at",
        ) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# Reads --------------------------------------------------------------------------------


@router.get("", response_model=OverviewOut)
def get_overview(session: Annotated[dict, Depends(get_current_session)]) -> OverviewOut:
    """
    The Security tab's summary, from the last checks; never probes.

    Args:
        session: The authenticated session.

    Returns:
        Counts, open findings and pending changes; ``checked_at`` is null
        before the first run.
    """
    data = _security(session).overview()
    return OverviewOut(
        checked_at=data["checked_at"],
        counts=CheckCountsOut(**data["counts"]) if data["counts"] else None,
        attention=[_check(item) for item in data["attention"]],
        pending=[_change(item) for item in data["pending"]],
    )


@router.get("/checks", response_model=ChecksOut)
def get_checks(
    session: Annotated[dict, Depends(get_current_session)],
    refresh: Annotated[bool, Query(description="Run the checks again now")] = False,
) -> ChecksOut:
    """
    Every hardening check, from the last run, or now when there is none or ``refresh``.

    Args:
        session: The authenticated session.
        refresh: Run them again.

    Returns:
        The checks.
    """
    report = _security(session).checks(refresh=refresh)
    data = report.to_dict()
    return ChecksOut(
        checked_at=data["checked_at"],
        counts=CheckCountsOut(**data["counts"]),
        checks=[_check(item) for item in data["checks"]],
    )


@router.get("/ssh", response_model=SshStatusOut)
def get_ssh(session: Annotated[dict, Depends(get_current_session)]) -> SshStatusOut:
    """
    sshd's effective configuration, Noust's drop-in, open sessions and every fix's plan.

    Args:
        session: The authenticated session.

    Returns:
        The SSH view.
    """
    return SshStatusOut(**_security(session).ssh_status())


@router.get("/ssh/fixes/{fix}", response_model=FixPlanOut)
def get_ssh_fix(fix: str, session: Annotated[dict, Depends(get_current_session)]) -> FixPlanOut:
    """
    What one sshd fix would change, and whether its guard holds now.

    Args:
        fix: The fix: ``disable-passwords``, ``root-prohibit-password``, ``root-no``,
            ``no-empty-passwords``, ``sensible-defaults`` or ``verbose-logging``.
        session: The authenticated session.

    Returns:
        Before and after, the proof, what blocks it and the guided steps.

    Raises:
        HTTPException: 404 for an unknown fix.
    """
    if fix not in SSH_FIXES:
        raise HTTPException(status_code=404, detail=f"There is no SSH fix named {fix!r}")
    return FixPlanOut(**_security(session).ssh_plan(fix).to_dict())


@router.get("/ssh/keys", response_model=list[AccountKeysOut])
def get_ssh_keys(session: Annotated[dict, Depends(get_current_session)]) -> list[AccountKeysOut]:
    """
    root and every account that can become root, with their keys and when each was last used.

    Args:
        session: The authenticated session.

    Returns:
        The accounts, root first.
    """
    return [AccountKeysOut(**item) for item in _security(session).ssh_keys()]


@router.get("/firewall", response_model=FirewallOut)
def get_firewall(session: Annotated[dict, Depends(get_current_session)]) -> FirewallOut:
    """
    The firewall and its rules, every port that answers with its verdict, and what is protected.

    Args:
        session: The authenticated session.

    Returns:
        The firewall view.
    """
    return FirewallOut(**_security(session).firewall_status())


@router.get("/fail2ban", response_model=Fail2banOut)
def get_fail2ban(session: Annotated[dict, Depends(get_current_session)]) -> Fail2banOut:
    """
    fail2ban: whether it runs, its jails and bans, and how it would be installed.

    Args:
        session: The authenticated session.

    Returns:
        The fail2ban view.
    """
    return Fail2banOut(**_security(session).fail2ban_status())


@router.get("/changes", response_model=list[ChangeOut])
def get_changes(session: Annotated[dict, Depends(get_current_session)]) -> list[ChangeOut]:
    """
    Every change to sshd and the firewall, newest first; the pending ones wait for confirmation.

    Reading undoes any change whose timer was lost (a reboot inside its window).

    Args:
        session: The authenticated session.

    Returns:
        The changes.
    """
    return [_change(change.to_dict()) for change in _security(session).pending()]


@router.get("/risks", response_model=list[AcceptedRiskOut])
def get_risks(session: Annotated[dict, Depends(get_current_session)]) -> list[AcceptedRiskOut]:
    """
    Every accepted risk, newest first, withdrawn and expired ones included.

    Args:
        session: The authenticated session.

    Returns:
        The acceptances.
    """
    return [AcceptedRiskOut(**risk.to_dict()) for risk in _security(session).accepted_risks()]


# Changes --------------------------------------------------------------------------------


@router.post("/checks/refresh", status_code=202, response_model=JobAcceptedResponse)
def refresh_checks(session: Annotated[dict, Depends(get_current_session)]) -> JobAcceptedResponse:
    """
    Run every check again, as a job: the package manager's part takes seconds.

    Args:
        session: The authenticated session.

    Returns:
        The queued job; ``GET /checks`` answers from its result once it ends.
    """
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_SECURITY,
        name="Run the security checks",
        description="Run every hardening check",
        func=refresh_checks_job,
        kwargs={"actor": actor},
        actor=actor,
    )
    return accepted(job, "Running the security checks")


def refresh_checks_job(actor: str, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Run the checks on the job worker.

    Args:
        actor: Who asked.
        job_context: Injected by the job manager.

    Returns:
        The counts.
    """
    context = require_job_context(job_context)
    context.update("Running the checks", 10)
    report = ServerSecurity(actor=actor).checks(refresh=True)
    counts = report.counts()
    context.log(
        f"{counts['critical']} critical, {counts['warning']} warning(s), "
        f"{counts['accepted']} accepted, {counts['passed']} passed"
    )
    context.update("Done", 100)
    return {"checked_at": report.checked_at, "counts": counts}


@router.post("/checks/{check_id}/fix", status_code=202, response_model=JobAcceptedResponse)
def fix_check(
    check_id: str,
    body: FixRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Apply a check's automatic fix; an access change then waits for confirmation.

    Args:
        check_id: The check.
        body: ``epel`` confirms enabling EPEL (fail2ban on RHEL rebuilds).
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 for an unknown check.
    """
    if check_id not in CATALOG:
        raise HTTPException(status_code=404, detail=f"There is no check {check_id!r}")
    params = {"check_id": check_id, "epel": body.epel, "ignore": _client_ignore(request)}
    return _queue(request, session, "check.fix", params)


@router.post("/ssh/fixes/{fix}", status_code=202, response_model=JobAcceptedResponse)
def apply_ssh_fix(
    fix: str, request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Apply an sshd fix; it undoes itself unless confirmed after a new SSH login.

    Args:
        fix: The fix.
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 for an unknown fix.
    """
    if fix not in SSH_FIXES:
        raise HTTPException(status_code=404, detail=f"There is no SSH fix named {fix!r}")
    return _queue(request, session, "ssh.fix", {"fix": fix})


@router.post("/ssh/keys", status_code=202, response_model=JobAcceptedResponse)
def add_ssh_key(
    body: AddKeyRequest, request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Let a key log in as root or an account that can become root.

    Args:
        body: The account and the ``.pub`` line.
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    return _queue(
        request, session, "ssh.key.add", {"user": body.user, "public_key": body.public_key}
    )


@router.post("/ssh/keys/remove", status_code=202, response_model=JobAcceptedResponse)
def remove_ssh_key(
    body: RemoveKeyRequest, request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Remove a key. A POST because a fingerprint holds ``/`` and ``+``.

    Refused unless ``force`` for a central's tunnel key, a key a session open
    now logged in with, or the last key while passwords are off.

    Args:
        body: The account, the fingerprint and ``force``.
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    params = {"user": body.user, "fingerprint": body.fingerprint, "force": body.force}
    return _queue(request, session, "ssh.key.remove", params)


@router.post("/firewall/rules", status_code=202, response_model=JobAcceptedResponse)
def add_firewall_rule(
    body: RuleRequestIn, request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Add a rule; it undoes itself unless confirmed. Never closes SSH or a public console.

    Args:
        body: The rule.
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    params = {
        "action": body.action,
        "port": body.port,
        "proto": body.proto,
        "source": body.source,
        "comment": body.comment,
    }
    return _queue(request, session, "firewall.add", params)


@router.delete("/firewall/rules/{rule_id}", status_code=202, response_model=JobAcceptedResponse)
def delete_firewall_rule(
    rule_id: str, request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Delete a rule; it undoes itself unless confirmed. Never the last one opening SSH.

    Args:
        rule_id: The rule's id, from ``GET /firewall``.
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    return _queue(request, session, "firewall.delete", {"rule_id": rule_id})


@router.post("/firewall/enable", status_code=202, response_model=JobAcceptedResponse)
def enable_firewall(
    request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Turn ufw on, SSH and a public console allowed first; it undoes itself unless confirmed.

    Args:
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    return _queue(request, session, "firewall.enable", {})


@router.post("/firewall/disable", status_code=202, response_model=JobAcceptedResponse)
def disable_firewall(
    request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Turn the firewall off; it comes back on unless confirmed.

    Args:
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    return _queue(request, session, "firewall.disable", {})


@router.post("/fail2ban/install", status_code=202, response_model=JobAcceptedResponse)
def install_fail2ban(
    body: Fail2banInstallRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Install fail2ban with an sshd jail that never bans who is connected now.

    On RHEL rebuilds it comes from EPEL: without ``epel`` the answer is 409
    ``confirmation_required`` with ``required.epel``, and nothing changes.

    Args:
        body: ``epel`` confirms enabling EPEL.
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    params = {"epel": body.epel, "ignore": _client_ignore(request)}
    return _queue(request, session, "fail2ban.install", params)


@router.post("/fail2ban/unban", status_code=202, response_model=JobAcceptedResponse)
def unban_address(
    body: UnbanRequest, request: Request, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Lift a fail2ban ban.

    Args:
        body: The address, and optionally the jail.
        request: The request.
        session: The elevated session.

    Returns:
        The queued job.
    """
    return _queue(request, session, "fail2ban.unban", {"address": body.address, "jail": body.jail})


@router.post("/changes/{change_id}/confirm", response_model=ChangeOut)
def confirm_change(
    change_id: str, session: Annotated[dict, Depends(require_elevated)]
) -> ChangeOut:
    """
    Keep a pending change, once a new SSH login since it was applied is on record.

    Args:
        change_id: The change.
        session: The elevated session.

    Returns:
        The confirmed change; 400 ``accessguarderror`` when no new login was seen yet.
    """
    return _change(_security(session).confirm(change_id).to_dict())


@router.post("/changes/{change_id}/revert", response_model=ChangeOut)
def revert_change(change_id: str, session: Annotated[dict, Depends(require_elevated)]) -> ChangeOut:
    """
    Undo a pending change now.

    Args:
        change_id: The change.
        session: The elevated session.

    Returns:
        The reverted change.
    """
    return _change(_security(session).revert(change_id).to_dict())


@router.put("/risks/{check_id}", response_model=AcceptedRiskOut)
def accept_risk(
    check_id: str, body: AcceptRiskRequest, session: Annotated[dict, Depends(require_elevated)]
) -> AcceptedRiskOut:
    """
    Accept a check's finding until a date: it shows as accepted, not passed, until then.

    Args:
        check_id: The check.
        body: Why, and until when (at most a year).
        session: The elevated session.

    Returns:
        The acceptance.

    Raises:
        HTTPException: 404 for an unknown check.
    """
    if check_id not in CATALOG:
        raise HTTPException(status_code=404, detail=f"There is no check {check_id!r}")
    risk = _security(session).accept_risk(
        check_id, reason=body.reason, until=_expiry(body.expires_at)
    )
    return AcceptedRiskOut(**risk.to_dict())


@router.delete("/risks/{check_id}", response_model=AcceptedRiskOut)
def revoke_risk(
    check_id: str, session: Annotated[dict, Depends(require_elevated)]
) -> AcceptedRiskOut:
    """
    Withdraw a check's acceptance, so its finding shows again.

    Args:
        check_id: The check.
        session: The elevated session.

    Returns:
        The withdrawn acceptance.

    Raises:
        HTTPException: 404 when no acceptance holds for it.
    """
    risk = _security(session).revoke_risk(check_id)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"No accepted risk holds for {check_id!r}")
    return AcceptedRiskOut(**risk.to_dict())
