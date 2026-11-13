# Copyright (c) 2024-2026 Yago López Prado
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
    ORIGIN_BOTH,
    ORIGIN_POLICY,
    ORIGIN_RULE,
    POLICY_RULE,
    STATES,
    ApprovalActor,
    ApprovalDenied,
    ApprovalError,
    ApprovalManager,
    ApprovalPolicy,
    ApprovalRequest,
    describe_rules,
    may_need_approval,
    one_time_fields,
    route_requirement,
    rule_for,
    snapshot,
)
from noust.core.caller_policy import NEVER, PROPOSE
from noust.core.callers import ASSISTANT, PERSON, caller_of
from noust.core.compliance import explain
from noust.core.server_text import Param, ServerText, say
from noust.core.teams import current_scope, scoped_store
from noust.web.api import caller_gate
from noust.web.api.deps import NoustErrorRoute, ensure_elevated, require_elevated
from noust.web.api.openapi import api_routes, route_requires_elevation
from noust.web.auth import FLEET_ACTOR_PATTERN, get_client_ip, is_fleet, require_auth
from noust.web.permissions import Permission
from noust.web.permissions.enforce import has_permission, required_permissions, route_template
from noust.web.permissions.principal import actor_of, principal_of
from noust.web.permissions.registry import NODE_PROXY_TEMPLATE, match_path, proxied_path
from noust.web.permissions.team_roles import instance_permissions_of, permissions_at

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


class ApprovalClientInfo(BaseModel):
    """
    What the client of a token said of itself, shown as what it said.

    Attributes:
        name: The name the client gave itself (the ``clientInfo`` of MCP's handshake, or the first
            product of its ``User-Agent``).
        version: Its version, or empty.
        source: ``client_info`` or ``user_agent``.
        label: The name and the version, as one line.
        declared: Always true: the server cannot know it, only that the client said it.
        first_seen: When this client was first seen with the token, ISO 8601.
        last_seen: When it was last seen.
    """

    name: str
    version: str = ""
    source: str = "user_agent"
    label: str = ""
    declared: bool = True
    first_seen: str | None = None
    last_seen: str | None = None


class ApprovalCredentialInfo(BaseModel):
    """
    The token that made a call the policy held.

    Attributes:
        id: The token's record, which ``/api/auth/tokens/{id}/activity`` reads; None when the token is
            gone from the list.
        name: The token's name.
        owner: The account it acts for, by username; None for a token nobody owns.
    """

    id: int | None = None
    name: str
    owner: str | None = None


class ApprovalInfo(BaseModel):
    """
    One request for a second person's approval.

    Attributes:
        id: Its id; the value of ``X-Noust-Approval``.
        action: ``root_equivalent``, ``fleet.node.add``, ``fleet.node.remove``,
            ``db.query.write``, ``db.query.analyze``, ``db.rows.write``,
            ``apps.local_source``, ``user.role_change``, ``user.create``,
            ``user.invite``, ``access.grant``, ``team.member``,
            ``access.role_widen`` or ``auth.source_roles``.
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
        origin: What made it wait: ``rule`` (the second-person rule), ``policy`` (the approval policy for
            a token or the built-in assistant) or ``both``. A request of the policy can be decided by
            a person who holds what the call needs, the token's owner included; one the rule holds
            asks for another person.
        caller: The class of caller that made the call: ``token`` or ``assistant``; None when a
            person asked.
        environment: ``production`` or ``other``: where the resource the call names lives, read by the
            server and never from what the caller sent; None when a person asked.
        domain: The application the call names, when it names one.
        needs: The permissions the call needs, which a person holds to decide a request of the policy.
        client: What the token's client said of itself, labelled as what it said; None when nobody was
            seen.
        credential: The token that made the call, when a token did.
        owned: Whether the caller is the account the token acts for.
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
    origin: str = ORIGIN_RULE
    caller: str | None = None
    environment: str | None = None
    domain: str | None = None
    needs: list[str] = Field(default_factory=list)
    client: ApprovalClientInfo | None = None
    credential: ApprovalCredentialInfo | None = None
    owned: bool = False


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


def approval_actor(
    session: dict[str, Any], client_ip: str | None = None, *, domain: str | None = None
) -> ApprovalActor:
    """
    Name the principal behind a request as an approval sees it.

    Args:
        session: The authenticated payload.
        client_ip: Where it came from.
        domain: The application a request is about, to read what the principal holds there (the
            request the approval policy made is decided by a person who holds what its call needs);
            what it holds on the server itself when omitted.

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
        permissions=_held_for(session, domain),
    )


def _held_for(session: dict[str, Any], domain: str | None) -> frozenset[str]:
    """
    Args:
        session: The authenticated payload.
        domain: The application a request is about, or None for the server itself.

    Returns:
        What the principal holds there: its permissions in the team, project and environment of the
        application when it sees it, only what it holds on the server when the request names none, and
        nothing for an application it does not see.
    """
    if domain is None:
        return instance_permissions_of(session)
    placement = scoped_store().placement_of_domain(domain)
    if placement is None:
        return frozenset() if current_scope().restricted else instance_permissions_of(session)
    return permissions_at(
        session, placement.team_id, placement.project_id, placement.environment_id
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
        detail=_refusal(
            "approval_required",
            say("approvals.central_missing"),
            explain.why_on("approvals").said(),
        ),
    )


def _refusal(
    error: str,
    said: ServerText,
    why: ServerText | None,
    *,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Make the body of an answer that asks something of a person, in the API's error contract.

    The English ``detail`` is what the sentence says followed by which control asks and how to
    stop it, as it always was; ``coded`` and ``notes_coded`` are the same sentences as codes and
    values, for a console to say in its own language.

    Args:
        error: The answer's machine-readable code.
        said: What the call needs (a sentence of the catalog); its fix is the ``hint``.
        why: Which control asks and how to stop it, from
            :meth:`~noust.core.compliance.explain.WhyOn.said`; None when the control is off.
        fields: The answer's own fields, when it has any.

    Returns:
        The ``detail`` of the HTTP exception, which the error handler turns into the body.
    """
    detail = said.text if why is None else f"{said.text} {why.text} {why.hint}"
    return {
        "error": error,
        "detail": detail,
        "hint": said.hint,
        "fields": fields,
        "coded": said.to_dict(),
        "notes_coded": [] if why is None else [why.to_dict()],
    }


def asked_sentence(approval: Any, policy: ApprovalPolicy) -> ServerText:
    """
    Say that a call became a request for a second person's approval, and what to do next.

    Args:
        approval: The request that was made (or found), with its ``id`` and ``state``.
        policy: The approval policy in force.

    Returns:
        The sentence: waiting for somebody to decide it, or already approved.
    """
    values: dict[str, Param] = {
        "request": approval.id,
        "header": APPROVAL_HEADER,
        "minutes": int(policy.execute_minutes),
    }
    if approval.state == "approved":
        return say("approvals.asked_approved", **values)
    if getattr(approval, "origin", ORIGIN_RULE) == ORIGIN_POLICY:
        return caller_gate.held_sentence(
            str(approval.caller),
            str(approval.environment),
            (approval.needs or ("",))[0],
            request=approval.id,
            header=APPROVAL_HEADER,
            minutes=int(policy.execute_minutes),
        )
    return say("approvals.asked_waiting", approvers=", ".join(policy.approvers), **values)


async def require_approval(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> None:
    """
    Hold every call that needs a second person to that person's decision.

    Installed on the API router after ``require_auth`` (FastAPI resolves that
    one once per request, so this adds no second authentication). Two things
    hold a call here, and the guard is where both are decided (rule 4):

    - the four-eyes rule (:func:`~noust.core.accounts.approvals.rule_for`), when
      approvals are on, for whoever makes the call;
    - the approval policy for a caller that is not a person
      (:mod:`noust.core.caller_policy`): a token or the built-in assistant whose
      cell for what the call changes, in the environment of its resource, says
      ``propose`` (the call waits for a person, with the same flow) or ``never``
      (it is refused, saying which policy and who changes it). The MCP calls
      the API with the caller's own token and comes through here too.

    Args:
        request: The call.
        session: The authenticated payload; empty for a public route.

    Raises:
        HTTPException: 202 ``approval_required`` naming the request it
            created; 400 ``approval_reason_required`` under the ENS profile
            without ``X-Noust-Reason``; 403 ``approval_never`` or
            ``approval_floor`` for what the policy never allows a token or the
            assistant; 403 ``elevation_required`` first when the route asks
            for sudo mode; ``approval_*`` (403, 404, 409) for an
            ``X-Noust-Approval`` that does not allow this call.
    """
    method = request.method.upper()
    if not session:
        return
    caller = caller_of(session)
    if caller != PERSON:
        caller_gate.note_client(session, request)
    if method in _SAFE_METHODS:
        if caller == ASSISTANT:
            await caller_gate.judge_read(request, session)
        return
    template = route_template(request)
    if template is None:
        return
    needed = await required_permissions(request) or []
    rule_template = template
    proxied = template == NODE_PROXY_TEMPLATE
    if proxied:
        inner = proxied_path(request.url.path)
        matched = match_path(method, inner) if inner else None
        rule_template = matched[0] if matched else template
    covered = may_need_approval(method, rule_template, needed)
    manager: ApprovalManager | None = None
    policy: ApprovalPolicy | None = None
    raw: bytes | None = None
    rule = None
    if covered:
        manager = approvals()
        policy = manager.policy()
        if policy.enabled or caller != PERSON:
            # Read only now, for a call a rule may cover: every such route takes JSON, which FastAPI
            # has read already and Starlette keeps on the request.
            raw = await request.body()
            rule = rule_for(method, rule_template, needed, _json_body(raw), request.path_params)
    elif caller == PERSON:
        return
    four_eyes = rule if policy is not None and policy.enabled else None
    judgement: caller_gate.Judgement | None = None
    held = False
    if caller != PERSON:
        requirement = route_requirement(method, rule_template, needed)
        judgement = await caller_gate.judge(
            request,
            session,
            method=method,
            template=rule_template,
            needed=needed,
            elevation=_route_needs_elevation(request, method, template),
            approval_when=requirement["when"] if requirement else None,
            four_eyes_action=rule.action if rule is not None else None,
            proxied=proxied,
        )
        if judgement.verdict.value == NEVER:
            caller_gate.refuse(request, session, judgement)
        held = judgement.verdict.value == PROPOSE
    if four_eyes is None and not held:
        return
    if manager is None or policy is None:
        manager = approvals()
        policy = manager.policy()
    if raw is None:
        raw = await request.body()
    if is_fleet(session):
        if four_eyes is not None:
            _vouched_by_central(request, session, four_eyes.action)
        return

    client_ip = get_client_ip(request)
    digest, parameters = snapshot(
        method,
        request.url.path,
        request.query_params.multi_items(),
        raw,
        omit=one_time_fields(method, rule_template),
    )
    requester = approval_actor(session, client_ip)
    action = (four_eyes or POLICY_RULE).action
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
                details={"action": action, "reason": exc.code, "approval": presented[:16]},
            )
            raise _http(exc) from exc
        request.state.noust_approval = approval
        return

    if _route_needs_elevation(request, method, template):
        ensure_elevated(request, session)
    reason = _reason(request)
    if four_eyes is not None and policy.reason_required and not reason:
        raise HTTPException(
            status_code=400,
            detail=_refusal(
                "approval_reason_required",
                say("approvals.reason_required", header=REASON_HEADER),
                explain.why_on("change_reasons").said(),
            ),
        )
    origin = ORIGIN_BOTH if (four_eyes is not None and held) else ORIGIN_RULE
    if four_eyes is None:
        origin = ORIGIN_POLICY
    context: dict[str, Any] = {}
    if judgement is not None:
        context = {
            "caller": judgement.verdict.caller,
            "environment": judgement.verdict.environment,
            "domain": judgement.domain,
            "needs": judgement.needs,
            "client": caller_gate.current_client(session),
        }
    try:
        approval, _created = manager.request(
            four_eyes or POLICY_RULE,
            method=method,
            path=request.url.path,
            parameters=parameters,
            fingerprint=digest,
            reason=reason,
            requester=requester,
            origin=origin,
            **context,
        )
    except ApprovalError as exc:
        raise _http(exc) from exc
    why = explain.why_on("approvals").said() if four_eyes is not None else None
    raise HTTPException(
        status_code=202,
        detail=_refusal(
            "approval_required",
            asked_sentence(approval, policy),
            why,
            fields={"approval": str(approval.id), "state": approval.state},
        ),
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


def _is_owner(request: ApprovalRequest, viewer: ApprovalActor) -> bool:
    """
    Args:
        request: A request.
        viewer: Who is looking.

    Returns:
        Whether the viewer is the account the token that asked acts for.
    """
    owner = request.requester.account
    return request.requester.kind == "token" and owner is not None and owner == viewer.behind


def can_see(request: ApprovalRequest, session: dict[str, Any], manager: ApprovalManager) -> bool:
    """
    Say whether the caller may read a request.

    Everybody reads their own, the owner of a token reads what it asked, and whoever reads the audit
    trail or decides reads all of them. A request the approval policy made is also read by a person who
    holds what its call needs and sees the application it names, through the scoped store: a team's
    administrator reads what a token of their team asked for, and never another team's.

    Args:
        request: The request.
        session: The caller.
        manager: The approval manager.

    Returns:
        True when the caller may see it.
    """
    viewer = approval_actor(session)
    if request.requester.same_principal(viewer) or _sees_all(session, manager):
        return True
    if _is_owner(request, viewer):
        return True
    if request.origin == ORIGIN_RULE or not request.needs:
        return False
    held = approval_actor(session, domain=request.domain).permissions or frozenset()
    return all(permission in held for permission in request.needs)


def can_decide(request: ApprovalRequest, session: dict[str, Any], manager: ApprovalManager) -> bool:
    """
    Args:
        request: The request.
        session: The caller.
        manager: The approval manager.

    Returns:
        Whether the caller may approve or reject it now: it waits for a decision, the caller may read it
        and the rules of who decides it (:meth:`ApprovalManager.eligibility`) allow the caller.
    """
    if request.state != "requested" or not can_see(request, session, manager):
        return False
    return manager.eligibility(request, approval_actor(session, domain=request.domain)) is None


def _credential(request: ApprovalRequest) -> ApprovalCredentialInfo | None:
    """
    Args:
        request: A request.

    Returns:
        The token that made it, with its record and the account it acts for; None when a person did.
    """
    if request.requester.kind != "token":
        return None
    from noust.web.server import get_token_manager

    manager = get_token_manager()
    record = manager.sessions.get_api_token_by_name(request.requester.id or "")
    owner: str | None = None
    if request.requester.account is not None and request.requester.account.isdigit():
        account = manager.accounts.get(int(request.requester.account))
        owner = account.username if account is not None else None
    return ApprovalCredentialInfo(
        id=int(record["id"]) if record is not None else None,
        name=request.requester.id or "",
        owner=owner,
    )


def _info(
    request: ApprovalRequest, session: dict[str, Any], manager: ApprovalManager
) -> ApprovalInfo:
    """
    Args:
        request: A request.
        session: Who is looking.
        manager: The approval manager, to judge whether the viewer may decide.

    Returns:
        Its description for the viewer.
    """
    viewer = approval_actor(session)
    data = request.to_dict()
    data["mine"] = request.requester.same_principal(viewer)
    data["can_decide"] = can_decide(request, session, manager)
    data["owned"] = _is_owner(request, viewer)
    data["credential"] = _credential(request)
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
        The request and the caller as it decides it (with what it holds where the call lands), when the
        caller may see it.

    Raises:
        HTTPException: 404, the same for a request that does not exist and
            one the caller may not see.
    """
    try:
        request = manager.get(approval_id)
    except ApprovalError as exc:
        raise _http(exc) from exc
    if not can_see(request, session, manager):
        raise HTTPException(status_code=404, detail=f"No approval request with id {approval_id}")
    return request, approval_actor(session, domain=request.domain)


@router.get("", response_model=ApprovalListResponse)
def list_approvals(
    session: dict[str, Any] = Depends(require_auth),
    state: str | None = Query(default=None, description="Only requests in this state"),
    mine: bool = Query(default=False, description="Only the caller's own requests"),
    caller: str | None = Query(
        default=None, description="Only calls of this class of caller: `token` or `assistant`"
    ),
    origin: str | None = Query(
        default=None,
        description="Only those that wait for this reason: `rule`, `policy` or `both`",
    ),
) -> ApprovalListResponse:
    """
    List the requests the caller may see, newest first.

    Deciders and auditors see every request; the owner of a token sees what it asked; a person who holds
    what a call the policy held needs sees it; everybody else sees their own.

    Args:
        session: The caller.
        state: Only this state: ``requested``, ``approved``, ``rejected``,
            ``expired`` or ``executed``.
        mine: Only the caller's own.
        caller: Only the calls of this class of caller.
        origin: Only those that wait for this reason.

    Returns:
        The requests.

    Raises:
        HTTPException: 400 for an unknown state, class or origin.
    """
    if state is not None and state not in STATES:
        raise HTTPException(
            status_code=400, detail=f"Unknown state {state!r}; use one of {', '.join(STATES)}"
        )
    if caller is not None and caller not in ("token", "assistant"):
        raise HTTPException(
            status_code=400, detail=f"Unknown class of caller {caller!r}; use token or assistant"
        )
    if origin is not None and origin not in (ORIGIN_RULE, ORIGIN_POLICY, ORIGIN_BOTH):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown origin {origin!r}; use {ORIGIN_RULE}, {ORIGIN_POLICY} or {ORIGIN_BOTH}",
        )
    manager = approvals()
    viewer = approval_actor(session)
    found = manager.list(
        state=state, requester=viewer if mine else None, caller=caller, origin=origin
    )
    if not mine:
        found = [item for item in found if can_see(item, session, manager)]
    return ApprovalListResponse(
        approvals=[_info(item, session, manager) for item in found],
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
    request, _viewer = _visible(approval_id, session, manager)
    return _info(request, session, manager)


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
    return _info(decided, session, manager)


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
