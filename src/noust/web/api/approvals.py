# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Four-eyes approvals over HTTP: the guard on every route, and ``/api/approvals``.

:func:`require_approval` is installed on the API router beside
``require_auth`` (rule 4: the guard at the chokepoint, like sudo mode), so no
root-equivalent route can forget it and a new one is covered the moment its
map says ``root_equivalent``. When approvals apply
(:func:`noust.core.accounts.approvals.load_approval_policy`) and the call is
one of :func:`~noust.core.accounts.approvals.rule_for`'s:

1. Without ``X-Noust-Approval``, the call does not run. It becomes a request,
   answered **202** ``approval_required`` naming it (``Location``,
   ``X-Noust-Approval-Request`` and ``fields.approval``). ``X-Noust-Reason``
   (percent-encoded UTF-8) says why; the ENS profile requires it. Sudo mode is
   asked first when the route asks for it, so nobody files a request they
   could not make.
2. A decider approves or rejects it here, in sudo mode.
3. The requester sends the same call again with ``X-Noust-Approval: <id>``;
   it runs once, and ``approval.use`` records both people.

A central's fleet token acting on a node carries the central's decision
instead: the node lets the call through only with ``X-Noust-Approved-By``
and ``X-Noust-Approval`` from the central, and refuses it otherwise - an
outdated or compromised central cannot skip the node's guard.

Everything about a request - who may decide, what an approval allows - is
:class:`~noust.core.accounts.approvals.ApprovalManager`'s, the same manager
``noust approval`` drives.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noust.core.accounts.approvals import (
    STATES,
    ApprovalActor,
    ApprovalDenied,
    ApprovalError,
    ApprovalManager,
    ApprovalRequest,
    describe_rules,
    may_need_approval,
    rule_for,
    snapshot,
)
from noust.web.api.deps import NoustErrorRoute, ensure_elevated, require_elevated
from noust.web.api.openapi import api_routes, route_requires_elevation
from noust.web.auth import FLEET_ACTOR_PATTERN, get_client_ip, is_fleet, require_auth
from noust.web.permissions import Permission
from noust.web.permissions.enforce import has_permission, required_permissions, route_template
from noust.web.permissions.principal import actor_of, principal_of
from noust.web.permissions.registry import NODE_PROXY_TEMPLATE, match_path, proxied_path

router = APIRouter(route_class=NoustErrorRoute)

#: Names the approved request a call is made under.
APPROVAL_HEADER = "X-Noust-Approval"
#: Names the request a 202 created (also in ``Location`` and ``fields.approval``).
APPROVAL_REQUEST_HEADER = "X-Noust-Approval-Request"
#: Why the call is made: percent-encoded UTF-8, at most 500 characters.
REASON_HEADER = "X-Noust-Reason"
#: Who approved, on a central's call to a node; honoured on a fleet token only.
APPROVED_BY_HEADER = "X-Noust-Approved-By"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_APPROVAL_ID = re.compile(r"[0-9]{1,12}")
#: A reason as it travels: percent-encoded UTF-8 of at most 500 characters.
_ENCODED_REASON = re.compile(r"[\x21-\x7e ]{1,1500}")
_ELEVATION_INDEX = "noust_elevation_index"


class ApprovalActorInfo(BaseModel):
    """
    Who asked, or who decided.

    Attributes:
        kind: ``account``, ``master``, ``token``, ``fleet``, ``cli`` or ``system``.
        id: The account id, the token's name, the login uid.
        name: The username, ``master``, ``token:<name>``, the login.
        role: The account's role.
    """

    kind: str
    id: str | None = None
    name: str
    role: str | None = None


class ApprovalInfo(BaseModel):
    """
    One request for a second person's approval.

    Attributes:
        id: Its id; the value of ``X-Noust-Approval``.
        action: ``root_equivalent``, ``fleet.node.add``, ``fleet.node.remove``,
            ``db.query.write``, ``db.query.analyze``, ``db.rows.write``,
            ``apps.local_source``, ``user.role_change``, ``user.create`` or
            ``user.invite``.
        kind: ``infrastructure`` or ``role_change``.
        description: What the action is, in one sentence.
        method: The call's method.
        path: The call's path.
        parameters: What the call carries, secrets redacted: ``method``,
            ``path``, ``query`` and ``body``.
        fingerprint: SHA-256 of the exact call the approval allows.
        reason: Why the requester asked.
        state: ``requested``, ``approved``, ``rejected``, ``expired`` or
            ``executed``.
        requester: Who asked.
        created_at: When, UNIX seconds.
        expires_at: When it expires undecided.
        decided_at: When it was decided.
        decider: Who decided.
        decision_comment: What they said.
        execute_by: Until when an approval allows the call.
        executed_at: When the call ran.
        mine: Whether the caller asked for it.
        can_decide: Whether the caller may approve or reject it now.
    """

    id: int
    action: str
    kind: str
    description: str
    method: str
    path: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str
    reason: str | None = None
    state: str
    requester: ApprovalActorInfo
    created_at: float
    expires_at: float
    decided_at: float | None = None
    decider: ApprovalActorInfo | None = None
    decision_comment: str | None = None
    execute_by: float | None = None
    executed_at: float | None = None
    mine: bool = False
    can_decide: bool = False


class ApprovalListResponse(BaseModel):
    """
    The requests the caller may see.

    Attributes:
        approvals: Newest first.
        pending: How many wait for a decision, of those listed.
    """

    approvals: list[ApprovalInfo]
    pending: int


class ApprovalRuleInfo(BaseModel):
    """
    One kind of call that needs approval.

    Attributes:
        method: Its method, or ``*``.
        template: Its route, or ``*`` for the permission rule.
        action: The action name requests carry.
        kind: ``infrastructure`` or ``role_change``.
        description: What it is.
        when: When it applies.
    """

    method: str
    template: str
    action: str
    kind: str
    description: str
    when: str


class ApprovalPolicyInfo(BaseModel):
    """
    Whether approvals apply here, who decides, and to what.

    Attributes:
        enabled: Whether the calls of ``rules`` need an approval now.
        approvers: The roles that decide (``admin`` for infrastructure only).
        request_hours: How long a request waits for a decision.
        execute_minutes: How long an approval allows its call.
        reason_required: Whether ``X-Noust-Reason`` is required.
        rules: The calls that need one.
    """

    enabled: bool
    approvers: list[str]
    request_hours: float
    execute_minutes: float
    reason_required: bool
    rules: list[ApprovalRuleInfo]


class DecisionBody(BaseModel):
    """
    A decision's comment.

    Attributes:
        comment: What the decider says; shown to the requester.
    """

    comment: str | None = Field(default=None, max_length=500)


def approvals() -> ApprovalManager:
    """
    Returns:
        The approval manager over the console's store and configuration.
    """
    return ApprovalManager()


def approval_actor(session: dict[str, Any], client_ip: str | None = None) -> ApprovalActor:
    """
    Name the principal behind a request as an approval sees it.

    Args:
        session: The authenticated payload.
        client_ip: Where it came from.

    Returns:
        The actor: an account with its role and person, the master token, a
        token or a central. A token carries its owner's account and person,
        so its owner never decides what it asked for.
    """
    principal = principal_of(session)
    behind: str | None = None
    if principal.kind == "account":
        behind = principal.id
    elif principal.kind == "token" and session.get("owner_account_id") is not None:
        behind = str(session["owner_account_id"])
    person_ref = None
    if behind is not None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.get(int(behind))
        person_ref = account.person_ref if account is not None else None
    return ApprovalActor(
        kind=principal.kind,
        id=principal.id,
        name=principal.name,
        role=principal.role or principal.grant,
        person_ref=person_ref,
        via=principal.via,
        source=client_ip or principal.source,
        account=behind,
    )


def _http(exc: ApprovalError | ApprovalDenied) -> HTTPException:
    """
    Args:
        exc: Why a request cannot be decided or used.

    Returns:
        The answer, with the code the console branches on.
    """
    return HTTPException(
        status_code=exc.status,
        detail={
            "error": exc.code,
            "detail": exc.message,
            "hint": exc.details or None,
            "fields": None,
        },
    )


def _reason(request: Request) -> str | None:
    """
    Args:
        request: The call.

    Returns:
        ``X-Noust-Reason``, decoded, or None.
    """
    raw = request.headers.get(REASON_HEADER)
    if not raw:
        return None
    text = unquote(raw, errors="replace")
    cleaned = "".join(char for char in text if char.isprintable()).strip()
    return cleaned[:500] or None


def _json_body(raw: bytes) -> Any:
    """
    Args:
        raw: A request body.

    Returns:
        It parsed as JSON, or None when it is not JSON.
    """
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return None


def _route_needs_elevation(request: Request, method: str, template: str) -> bool:
    """
    Report whether a route asks for sudo mode, from its dependency graph.

    Args:
        request: The call, for its application.
        method: The method.
        template: The route's template.

    Returns:
        True when the route depends on ``require_elevated``.
    """
    app = request.app
    cached = getattr(app.state, _ELEVATION_INDEX, None)
    if cached is None or cached[0] != len(app.routes):
        index: dict[tuple[str, str], bool] = {}
        for route in api_routes(app.routes):
            needs = route_requires_elevation(route)
            for verb in route.methods or ():
                index[(verb, str(route.path_format))] = needs
        cached = (len(app.routes), index)
        setattr(app.state, _ELEVATION_INDEX, cached)
    return bool(cached[1].get((method, template)))


def central_approval_headers(approval: ApprovalRequest) -> dict[str, str]:
    """
    Say to a node that this central approved the call it forwards.

    The central's half of :func:`_vouched_by_central`: only built from an
    approval this central consumed for the very call (``request.state``),
    never from what the browser sent, exactly as the proxy vouches for sudo
    mode.

    Args:
        approval: The request the central consumed.

    Returns:
        ``X-Noust-Approval`` and ``X-Noust-Approved-By``, the approver as a
        label the node accepts.
    """
    from noust.fleet.client import actor_label as fleet_actor

    decider = approval.decider.name if approval.decider is not None else "unknown"
    return {APPROVAL_HEADER: str(approval.id), APPROVED_BY_HEADER: fleet_actor(decider)}


def forwardable_reason(value: str | None) -> str | None:
    """
    Keep a call's reason for forwarding when it is what the header should hold.

    Args:
        value: ``X-Noust-Reason`` as received.

    Returns:
        It, when it is percent-encoded text of a reasonable length; None otherwise.
    """
    if value is None or not _ENCODED_REASON.fullmatch(value):
        return None
    return value


def _vouched_by_central(request: Request, session: dict[str, Any], action: str) -> None:
    """
    Let a central's call through when the central says it was approved there.

    The accounts, and so the decisions, live on the central; the node only
    refuses a call its central did not vouch for. That is the trust model: a
    node cannot check the central's decision, only that the central took one.
    Called only while this node's own policy asks for approvals, so a node
    that requires them refuses a call whose headers are missing or malformed,
    or that names its own requester as the approver; the record names both
    people, the operator behind the central and the approver, and the
    central's token.

    Args:
        request: The call.
        session: The fleet token's payload.
        action: What the call is.

    Raises:
        HTTPException: 403 ``approval_required`` without the central's word.
    """
    from noust.core.audit import record

    approved_by = request.headers.get(APPROVED_BY_HEADER, "")
    reference = request.headers.get(APPROVAL_HEADER, "")
    actor = actor_of(session)
    principal = principal_of(session)
    requested_by = {"name": principal.name, "central": principal.id, "role": principal.role}
    reason = "the central did not vouch for an approval"
    if FLEET_ACTOR_PATTERN.fullmatch(approved_by) and _APPROVAL_ID.fullmatch(reference):
        if approved_by.lower() != principal.name.lower():
            record(
                "approval.use",
                actor=actor,
                target=f"central-approval:{reference}",
                details={
                    "action": action,
                    "method": request.method,
                    "path": request.url.path,
                    "requested_by": requested_by,
                    "approved_by": {"name": approved_by, "on": "central"},
                    "central_approval": reference,
                },
            )
            return
        reason = "the central named the requester as the approver"
    record(
        "http.denied.approval",
        actor=actor,
        target=request.url.path,
        outcome="denied",
        details={
            "action": action,
            "reason": reason,
            "requested_by": requested_by,
            "approved_by": approved_by[:64] or None,
            "central_approval": reference[:16] or None,
        },
    )
    raise HTTPException(
        status_code=403,
        detail={
            "error": "approval_required",
            "detail": "This needs a second person's approval on the central",
            "hint": "Ask for it on the central and retry through it once it is approved.",
            "fields": None,
        },
    )


async def require_approval(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> None:
    """
    Hold every call that needs a second person to that person's decision.

    Installed on the API router after ``require_auth`` (FastAPI resolves that
    one once per request, so this adds no second authentication).

    Args:
        request: The call.
        session: The authenticated payload; empty for a public route.

    Raises:
        HTTPException: 202 ``approval_required`` naming the request it
            created; 400 ``approval_reason_required`` under the ENS profile
            without ``X-Noust-Reason``; 403 ``elevation_required`` first when
            the route asks for sudo mode; ``approval_*`` (403, 404, 409) for
            an ``X-Noust-Approval`` that does not allow this call.
    """
    method = request.method.upper()
    if not session or method in _SAFE_METHODS:
        return
    template = route_template(request)
    if template is None:
        return
    needed = await required_permissions(request) or []
    rule_template = template
    if template == NODE_PROXY_TEMPLATE:
        inner = proxied_path(request.url.path)
        matched = match_path(method, inner) if inner else None
        rule_template = matched[0] if matched else template
    if not may_need_approval(method, rule_template, needed):
        return
    manager = approvals()
    policy = manager.policy()
    if not policy.enabled:
        return
    # Read only now, for a call a rule may cover: every such route takes JSON,
    # which FastAPI has read already and Starlette keeps on the request.
    raw = await request.body()
    rule = rule_for(method, rule_template, needed, _json_body(raw), request.path_params)
    if rule is None:
        return
    if is_fleet(session):
        _vouched_by_central(request, session, rule.action)
        return

    client_ip = get_client_ip(request)
    digest, parameters = snapshot(method, request.url.path, request.query_params.multi_items(), raw)
    requester = approval_actor(session, client_ip)
    presented = request.headers.get(APPROVAL_HEADER)
    if presented:
        try:
            if not _APPROVAL_ID.fullmatch(presented.strip()):
                raise ApprovalError(
                    "approval_not_found",
                    f"{APPROVAL_HEADER} must be the number of an approval request",
                    status=400,
                )
            approval = manager.consume(int(presented.strip()), requester, digest)
        except (ApprovalError, ApprovalDenied) as exc:
            from noust.core.audit import record

            record(
                "http.denied.approval",
                actor=requester.audit_actor(),
                target=request.url.path,
                outcome="denied",
                details={"action": rule.action, "reason": exc.code, "approval": presented[:16]},
            )
            raise _http(exc) from exc
        request.state.noust_approval = approval
        return

    if _route_needs_elevation(request, method, template):
        ensure_elevated(request, session)
    reason = _reason(request)
    if policy.reason_required and not reason:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "approval_reason_required",
                "detail": "Say why: this call needs a second person's approval, and a reason",
                "hint": f"Send the reason in the {REASON_HEADER} header (percent-encoded).",
                "fields": None,
            },
        )
    try:
        approval, _created = manager.request(
            rule,
            method=method,
            path=request.url.path,
            parameters=parameters,
            fingerprint=digest,
            reason=reason,
            requester=requester,
        )
    except ApprovalError as exc:
        raise _http(exc) from exc
    raise HTTPException(
        status_code=202,
        detail={
            "error": "approval_required",
            "detail": (
                f"This needs a second person's approval. Request {approval.id} is "
                + (
                    "approved: send the call again with it now."
                    if approval.state == "approved"
                    else f"waiting for a {' or '.join(policy.approvers)} account to decide it."
                )
            ),
            "hint": (
                f"Once approved, send exactly the same call again with the header "
                f"{APPROVAL_HEADER}: {approval.id}, within {int(policy.execute_minutes)} minutes."
            ),
            "fields": {"approval": str(approval.id), "state": approval.state},
        },
        headers={
            "Location": f"/api/approvals/{approval.id}",
            APPROVAL_REQUEST_HEADER: str(approval.id),
        },
    )


# ------------------------------------------------------------------- the API


def _sees_all(session: dict[str, Any], manager: ApprovalManager) -> bool:
    """
    Args:
        session: The caller.
        manager: The approval manager, for the deciding roles.

    Returns:
        True for whoever reads the audit trail (``security``, ``auditor``,
        the master token) and whoever decides; everyone else sees their own.
    """
    if has_permission(session, Permission.AUDIT_READ):
        return True
    return (
        session.get("account_id") is not None and session.get("role") in manager.policy().approvers
    )


def _info(
    request: ApprovalRequest, viewer: ApprovalActor, manager: ApprovalManager
) -> ApprovalInfo:
    """
    Args:
        request: A request.
        viewer: Who is looking.
        manager: The approval manager, to judge whether the viewer may decide.

    Returns:
        Its description for the viewer.
    """
    data = request.to_dict()
    data["mine"] = request.requester.same_principal(viewer)
    data["can_decide"] = (
        request.state in ("requested",) and manager.eligibility(request, viewer) is None
    )
    return ApprovalInfo(**data)


def _visible(
    approval_id: int, session: dict[str, Any], manager: ApprovalManager
) -> tuple[ApprovalRequest, ApprovalActor]:
    """
    Args:
        approval_id: A request.
        session: The caller.
        manager: The approval manager.

    Returns:
        The request and the caller, when the caller may see it.

    Raises:
        HTTPException: 404, the same for a request that does not exist and
            one the caller may not see.
    """
    viewer = approval_actor(session)
    try:
        request = manager.get(approval_id)
    except ApprovalError as exc:
        raise _http(exc) from exc
    if not _sees_all(session, manager) and not request.requester.same_principal(viewer):
        raise HTTPException(status_code=404, detail=f"No approval request with id {approval_id}")
    return request, viewer


@router.get("", response_model=ApprovalListResponse)
def list_approvals(
    session: dict[str, Any] = Depends(require_auth),
    state: str | None = Query(default=None, description="Only requests in this state"),
    mine: bool = Query(default=False, description="Only the caller's own requests"),
) -> ApprovalListResponse:
    """
    List the requests the caller may see, newest first.

    Deciders and auditors see every request; everybody else sees their own.

    Args:
        session: The caller.
        state: Only this state: ``requested``, ``approved``, ``rejected``,
            ``expired`` or ``executed``.
        mine: Only the caller's own.

    Returns:
        The requests.

    Raises:
        HTTPException: 400 for an unknown state.
    """
    if state is not None and state not in STATES:
        raise HTTPException(
            status_code=400, detail=f"Unknown state {state!r}; use one of {', '.join(STATES)}"
        )
    manager = approvals()
    viewer = approval_actor(session)
    only = viewer if mine or not _sees_all(session, manager) else None
    found = manager.list(state=state, requester=only)
    return ApprovalListResponse(
        approvals=[_info(item, viewer, manager) for item in found],
        pending=sum(1 for item in found if item.state == "requested"),
    )


@router.get("/policy", response_model=ApprovalPolicyInfo)
def approval_policy(session: dict[str, Any] = Depends(require_auth)) -> ApprovalPolicyInfo:
    """
    Say whether approvals apply here, who decides, and which calls need one.

    Args:
        session: The caller.

    Returns:
        The policy and the rules.
    """
    policy = approvals().policy()
    return ApprovalPolicyInfo(
        **policy.to_dict(), rules=[ApprovalRuleInfo(**rule) for rule in describe_rules()]
    )


@router.get("/{approval_id}", response_model=ApprovalInfo)
def get_approval(approval_id: int, session: dict[str, Any] = Depends(require_auth)) -> ApprovalInfo:
    """
    Describe one request.

    Args:
        approval_id: Its id.
        session: The caller.

    Returns:
        The request.

    Raises:
        HTTPException: 404 when it does not exist or is not the caller's to see.
    """
    manager = approvals()
    request, viewer = _visible(approval_id, session, manager)
    return _info(request, viewer, manager)


def _decide(
    approval_id: int, body: DecisionBody, session: dict[str, Any], *, approve: bool
) -> ApprovalInfo:
    """
    Approve or reject a request.

    Args:
        approval_id: The request.
        body: The comment.
        session: The decider, in sudo mode.
        approve: Approve rather than reject.

    Returns:
        The request after the decision.

    Raises:
        HTTPException: 404, 403 ``approval_denied`` or 409 ``approval_*``.
    """
    manager = approvals()
    _request, decider = _visible(approval_id, session, manager)
    try:
        decided = (
            manager.approve(approval_id, decider, body.comment)
            if approve
            else manager.reject(approval_id, decider, body.comment)
        )
    except (ApprovalError, ApprovalDenied) as exc:
        raise _http(exc) from exc
    return _info(decided, decider, manager)


@router.post("/{approval_id}/approve", response_model=ApprovalInfo)
def approve_request(
    approval_id: int, body: DecisionBody, session: dict[str, Any] = Depends(require_elevated)
) -> ApprovalInfo:
    """
    Approve a request, in sudo mode: its requester may make the call once.

    Args:
        approval_id: The request.
        body: An optional comment.
        session: The decider.

    Returns:
        The request, approved.

    Raises:
        HTTPException: 403 ``approval_denied`` for the requester, another
            account of the same person, a role that does not decide it, or a
            credential that is not a person; 409 when it is not waiting.
    """
    return _decide(approval_id, body, session, approve=True)


@router.post("/{approval_id}/reject", response_model=ApprovalInfo)
def reject_request(
    approval_id: int, body: DecisionBody, session: dict[str, Any] = Depends(require_elevated)
) -> ApprovalInfo:
    """
    Reject a request, or withdraw an approval not used yet, in sudo mode.

    Args:
        approval_id: The request.
        body: Why, shown to the requester.
        session: The decider.

    Returns:
        The request, rejected.

    Raises:
        HTTPException: As for approving.
    """
    return _decide(approval_id, body, session, approve=False)
