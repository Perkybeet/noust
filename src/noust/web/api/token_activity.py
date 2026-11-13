# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/auth/tokens/{token_id}/activity``: what a credential did, and leaving it as it was (item 185).

A credential is any token (of the API, of MCP, of a central, of the built-in assistant); an intervention is
what it did without a silence of thirty minutes. The reading is :class:`~noust.core.credential_activity.CredentialActivity`'s,
from the audit trail already kept, and what a person may see of it is decided here, with the scoped store: the
owner of a token, whoever governs accounts and whoever reads the audit trail see a token's activity; a person
of a team sees only what names an application of their team, and what names none is the instance's.

Going back is proposed (``GET .../undo``) and then asked for (``POST .../undo``): a job, in sudo mode, for the
applications the person chose, each by the way back Noust already has. Nothing here puts anything back itself.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.credential_activity import (
    DEFAULT_DAYS,
    MAX_DAYS,
    CredentialActivity,
    CredentialRef,
    Intervention,
    assistant_tokens,
    parse_moment,
    reference_of,
)
from noust.core.credential_clients import ClientSeen, CredentialClients
from noust.core.teams import current_scope, scoped_store
from noust.deployers.credential_undo import STATUS_CONFIRM, STATUS_READY, Undoer, UndoPlan
from noust.web.api.approvals import ApprovalClientInfo
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.jobs import JobType, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.enforce import has_permission
from noust.web.permissions.team_roles import permissions_at
from noust.web.server import get_token_manager
from noust.web.undo_jobs import is_read_tool, undo_credential_job

router = APIRouter(route_class=NoustErrorRoute)

#: The most applications one request may ask to put back.
MAX_APPS = 200


class ActivityCredential(BaseModel):
    """
    The token whose activity this is.

    Attributes:
        id: Its record: that of the token itself, and for the assistant that of its newest token (any of
            its tokens opens the same page).
        name: Its name, which is on every audit line it made; ``assistant:<person>`` for the assistant.
        kind: ``token``, ``assistant`` (the built-in assistant, by whoever it works for) or ``fleet`` (a
            central's).
        owner: The account it acts for, by username: the person behind the assistant.
        revoked: Whether it was revoked (every one of its tokens, for the assistant).
        tokens: How many tokens it is made of: one, except for the built-in assistant, which is ONE
            credential for each person whatever number of tokens it used (each conversation has its own).
        clients: What its clients said of themselves, newest first, labelled as what they said; none for
            the assistant, whose client is Noust itself.
    """

    id: int
    name: str
    kind: str
    owner: str | None = None
    revoked: bool = False
    tokens: int = 1
    clients: list[ApprovalClientInfo] = Field(default_factory=list)


class ActivityDeployment(BaseModel):
    """
    A deployment a credential's call started.

    Attributes:
        id: The deployment.
        domain: The application.
        status: ``success``, ``failed``, ``rolled_back`` or still running.
        commit: Its commit.
        release: Its release.
        started_at: When it started.
        finished_at: When it ended.
        schema_changed: A hook or a migration changed the database's schema.
        job: The job that ran it.
    """

    id: int
    domain: str
    status: str
    commit: str | None = None
    release: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    schema_changed: bool = False
    job: str | None = None


class ActivityEntry(BaseModel):
    """
    One thing the credential did.

    Attributes:
        at: When, ISO 8601.
        seq: The sequence number of its first event in the chained audit trail.
        credential: The token that did it: for the assistant, the token of its conversation.
        via: ``api``, ``mcp``, ``assistant`` or ``fleet``.
        kind: ``change``, ``tool``, ``read``, ``proposal`` or ``event``.
        method: The HTTP method, for a call of the API.
        path: The route called.
        tool: The tool of MCP.
        domain: The application it names.
        result: ``ok``, ``proposed`` (a person had to approve it), ``refused`` or ``failed``.
        status: The HTTP status, when the audit line carries it.
        approval: The approval request, when the call waited for a person or ran with one.
        operator: The person a central acted for, for a fleet token.
        events: The names of what else the same request recorded.
        deployment: The deployment it started.
    """

    at: str
    seq: int | None = None
    credential: str = ""
    via: str
    kind: str
    method: str | None = None
    path: str | None = None
    tool: str | None = None
    domain: str | None = None
    result: str
    status: int | None = None
    approval: int | None = None
    operator: str | None = None
    events: list[str] = Field(default_factory=list)
    deployment: ActivityDeployment | None = None


class InterventionInfo(BaseModel):
    """
    What a credential did in a row.

    Attributes:
        n: Its number, which names it in the other routes.
        started_at: When the first thing happened.
        ended_at: When the last did.
        ongoing: It did something less than thirty minutes ago, so it may go on.
        calls: How many things it did.
        changes: Of those, how many changed something and ran.
        proposed: How many waited for a person.
        refused: How many were refused.
        failed: How many failed.
        apps: The applications it changed, in the order it met them.
        deployments: How many deployments it started.
        client: The client it said it was while this went on, when known, as what it said.
        conversation: For the assistant, the token of the conversation this was; None for any other
            credential. An intervention of the assistant is a conversation, or a stretch of one without
            thirty minutes of silence.
    """

    n: int
    started_at: str
    ended_at: str
    ongoing: bool
    calls: int
    changes: int
    proposed: int
    refused: int
    failed: int
    apps: list[str]
    deployments: int
    client: ApprovalClientInfo | None = None
    conversation: str | None = None


class ActivityList(BaseModel):
    """
    The interventions of a credential.

    Attributes:
        credential: The token.
        interventions: Newest first.
        days: How far back it was read.
        complete: False when the reading stopped at its cap before reaching ``days``.
    """

    credential: ActivityCredential
    interventions: list[InterventionInfo]
    days: int
    complete: bool


class UndoSummary(BaseModel):
    """
    Whether something of an intervention can be put back.

    Attributes:
        available: Something can.
        items: How many applications the plan has.
        irreversible: How many things it did have no way back here.
    """

    available: bool
    items: int
    irreversible: int


class ActivityDetail(BaseModel):
    """
    One intervention, with its timeline.

    Attributes:
        credential: The token.
        intervention: The intervention.
        timeline: What it did, oldest first.
        deployments: The deployments it started.
        clients: What the token's clients said of themselves.
        undo: Whether it can be put back.
    """

    credential: ActivityCredential
    intervention: InterventionInfo
    timeline: list[ActivityEntry]
    deployments: list[ActivityDeployment]
    clients: list[ApprovalClientInfo]
    undo: UndoSummary


class UndoItemInfo(BaseModel):
    """
    One application of the plan.

    Attributes:
        kind: ``rollback``.
        domain: The application.
        deployments: What the credential deployed there.
        status: ``ready``, ``needs_confirmation`` (going back passes a deployment that changed the
            database's schema, which Noust puts code back past and never the database), ``already`` or
            ``blocked``.
        reason: Why it is blocked: ``app_gone``, ``failed_only``, ``nothing_before``, ``superseded`` or
            ``unavailable``.
        detail: The reason in a sentence, in English.
        back_to: The deployment it would go back to.
        back_to_commit: Its commit.
        back_to_release: Its release.
        back_to_at: When it started.
        schema_changed: The deployments that changed the schema and that going back passes.
        superseded_by: The deployments of somebody else on top of the credential's.
    """

    kind: str
    domain: str
    deployments: list[int]
    status: str
    reason: str | None = None
    detail: str | None = None
    back_to: int | None = None
    back_to_commit: str | None = None
    back_to_release: str | None = None
    back_to_at: str | None = None
    schema_changed: list[int] = Field(default_factory=list)
    superseded_by: list[int] = Field(default_factory=list)


class IrreversibleInfo(BaseModel):
    """
    Something the credential did that cannot be undone from here.

    Attributes:
        at: When.
        method: The method of the call.
        path: The route called.
        domain: The application it named.
        why: ``deleted``, ``database``, ``restore``, ``variables``, ``service_state`` or ``no_way_back``.
        detail: The reason in a sentence, in English.
    """

    at: str
    method: str | None = None
    path: str | None = None
    domain: str | None = None
    why: str
    detail: str


class UndoPlanInfo(BaseModel):
    """
    What would put an intervention back.

    Attributes:
        credential: The token's name.
        intervention: Its number.
        available: Something can be put back.
        items: An item for each application it deployed.
        irreversible: What it did that cannot be undone from here, with why.
    """

    credential: str
    intervention: int
    available: bool
    items: list[UndoItemInfo]
    irreversible: list[IrreversibleInfo]


class UndoRequest(BaseModel):
    """
    Asking to put an intervention back.

    Attributes:
        apps: The applications to put back; every one that can when omitted.
        schema_changed_ok: The applications for which going back past a deployment that changed the
            database's schema is confirmed: Noust puts the code back, never the database.
    """

    apps: list[str] | None = Field(default=None, max_length=MAX_APPS)
    schema_changed_ok: list[str] = Field(default_factory=list, max_length=MAX_APPS)


def _record(token_id: int, session: dict[str, Any]) -> dict[str, Any]:
    """
    Find the token, when the caller may read what it did.

    Args:
        token_id: The token's record.
        session: The caller.

    Returns:
        The record.

    Raises:
        HTTPException: 404, the same for a token that does not exist and one the caller may not read.
    """
    record = get_token_manager().get_api_token(token_id)
    if record is not None:
        if has_permission(session, Permission.ACCOUNTS_MANAGE) or has_permission(
            session, Permission.AUDIT_READ
        ):
            return record
        owner = session.get("account_id") or session.get("owner_account_id")
        if owner is not None and record.get("owner_account_id") == int(owner):
            return record
    raise HTTPException(status_code=404, detail=f"No API token with id {token_id}")


def _visible() -> Any:
    """
    Returns:
        What an application must pass to be seen by the caller: None for whoever sees every team, else a
        function of the scoped store. An entry that names no application is shown only to the first.
    """
    scope = current_scope()
    if not scope.restricted:
        return None
    scoped = scoped_store()
    seen = scoped.visible_domains() or set()
    return lambda domain: domain is not None and domain in seen


def _kind(record: dict[str, Any]) -> str:
    """
    Args:
        record: A token's record.

    Returns:
        ``assistant`` for the built-in assistant's own token, ``fleet`` for a central's, else ``token``.
    """
    if record.get("caller") == "assistant":
        return "assistant"
    return "fleet" if record.get("scope") == "fleet" else "token"


def _client(item: ClientSeen) -> ApprovalClientInfo:
    """
    Args:
        item: A client a credential was seen with.

    Returns:
        How the API shows it.
    """
    return ApprovalClientInfo(**item.to_dict())


def _owner(record: dict[str, Any]) -> str | None:
    """
    Args:
        record: A token's record.

    Returns:
        The username of the account it acts for.
    """
    if record.get("owner_account_id") is None:
        return None
    account = get_token_manager().accounts.get(int(record["owner_account_id"]))
    return account.username if account is not None else None


def _siblings(record: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Args:
        record: A token's record.

    Returns:
        The records of the tokens of the account it acts for, to look the assistant's other tokens among.
    """
    owner = record.get("owner_account_id")
    return get_token_manager().list_api_tokens(int(owner)) if owner is not None else [record]


def _assistant_tokens(record: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Args:
        record: The record of a token of the built-in assistant.

    Returns:
        Every token of the assistant that acts for the same person: the one credential they make together.
    """
    return [dict(item) for item in assistant_tokens(record, _siblings(record))]


def _ref(record: dict[str, Any]) -> CredentialRef:
    """
    Args:
        record: A token's record.

    Returns:
        What a reading of activity follows: the token, or for the assistant every token it used for the
        person it works for, an intervention being a conversation.
    """
    return reference_of(record, _siblings(record), _owner(record))


def _credential(record: dict[str, Any]) -> ActivityCredential:
    """
    Args:
        record: A token's record.

    Returns:
        The credential as an activity page names it: the token with the clients it was seen with, or the
        assistant of a person, made of all its tokens.
    """
    if _kind(record) == "assistant":
        tokens = _assistant_tokens(record)
        return ActivityCredential(
            id=max(int(item["id"]) for item in tokens),
            name=_ref(record).label,
            kind="assistant",
            owner=_owner(record),
            revoked=all(item.get("revoked_at") is not None for item in tokens),
            tokens=len(tokens),
        )
    return ActivityCredential(
        id=int(record["id"]),
        name=str(record["name"]),
        kind=_kind(record),
        owner=_owner(record),
        revoked=record.get("revoked_at") is not None,
        clients=[_client(item) for item in CredentialClients().seen(str(record["name"]))],
    )


def _clients(record: dict[str, Any]) -> list[ClientSeen]:
    """
    Args:
        record: A token's record.

    Returns:
        What its client said of itself; nothing for the assistant, whose client is Noust itself.
    """
    if _kind(record) == "assistant":
        return []
    return CredentialClients().seen(str(record["name"]), limit=20)


def _client_during(
    clients: list[ClientSeen], intervention: Intervention
) -> ApprovalClientInfo | None:
    """
    Args:
        clients: What the token's clients said of themselves, newest first.
        intervention: What it did.

    Returns:
        The client seen while it went on (what the handshake said when there was one), as what it said;
        None when none was seen then.
    """
    start = parse_moment(intervention.started_at)
    end = parse_moment(intervention.ended_at)
    if start is None or end is None:
        return None
    margin = timedelta(minutes=10)
    during = [
        c
        for c in clients
        if (first := parse_moment(c.first_seen)) is not None
        and (last := parse_moment(c.last_seen)) is not None
        and first <= end + margin
        and last >= start - margin
    ]
    during.sort(key=lambda c: c.source != "client_info")
    return _client(during[0]) if during else None


def _info(intervention: Intervention, clients: list[ClientSeen]) -> InterventionInfo:
    """
    Args:
        intervention: What a credential did in a row.
        clients: What the token's clients said of themselves.

    Returns:
        How the API shows it.
    """
    return InterventionInfo(**intervention.to_dict(), client=_client_during(clients, intervention))


def _activity() -> CredentialActivity:
    """
    Returns:
        The reader of what a credential did, over the server's audit trail and store.
    """
    return CredentialActivity()


@router.get("/{token_id}/activity", response_model=ActivityList)
def list_activity(
    token_id: int,
    session: dict[str, Any] = Depends(get_current_session),
    days: int = Query(
        default=DEFAULT_DAYS, ge=1, le=MAX_DAYS, description="How many days back to read"
    ),
    limit: int = Query(default=50, ge=1, le=500, description="Most interventions returned"),
) -> ActivityList:
    """
    List what a credential did, as interventions, newest first.

    An intervention is what it did without a silence of more than thirty minutes between one thing and the
    next. A person of a team sees only what names an application of their team.

    Args:
        token_id: The token's record, as listed by ``GET /api/auth/tokens``.
        session: The caller: the token's owner, whoever governs accounts or reads the audit trail.
        days: How far back to read.
        limit: The most interventions returned.

    Returns:
        The interventions, each with what it did in numbers, the applications it changed, the deployments
        it started and the client it said it was.

    Raises:
        HTTPException: 404 for a token the caller may not read.
    """
    record = _record(token_id, session)
    found, complete = _activity().interventions(
        _ref(record), days=days, visible=_visible(), is_read_tool=is_read_tool
    )
    clients = _clients(record)
    return ActivityList(
        credential=_credential(record),
        interventions=[_info(item, clients) for item in found[:limit]],
        days=days,
        complete=complete,
    )


def _intervention(record: dict[str, Any], n: int) -> Intervention:
    """
    Args:
        record: The token's record.
        n: The intervention's number.

    Returns:
        The intervention, with only what the caller sees.

    Raises:
        HTTPException: 404 when the credential has none of that number that the caller sees.
    """
    found = _activity().intervention(_ref(record), n, visible=_visible(), is_read_tool=is_read_tool)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No intervention {n} of this token")
    return found


def _plan(record: dict[str, Any], found: Intervention) -> UndoPlan:
    """
    Args:
        record: The token's record.
        found: The intervention.

    Returns:
        What would put it back.
    """
    return Undoer().plan(_ref(record), found)


@router.get("/{token_id}/activity/{n}", response_model=ActivityDetail)
def get_activity(
    token_id: int, n: int, session: dict[str, Any] = Depends(get_current_session)
) -> ActivityDetail:
    """
    Read one intervention: the timeline of what the credential did.

    Args:
        token_id: The token's record.
        n: The intervention's number, from the list.
        session: The caller.

    Returns:
        The intervention with its timeline (the action, the resource, how it ended, the deployment it
        started and the client it said it was), and whether it can be put back.

    Raises:
        HTTPException: 404 for a token the caller may not read, or an intervention it does not have.
    """
    record = _record(token_id, session)
    found = _intervention(record, n)
    plan = _plan(record, found)
    clients = _clients(record)
    return ActivityDetail(
        credential=_credential(record),
        intervention=_info(found, clients),
        timeline=[ActivityEntry(**entry.to_dict()) for entry in found.entries],
        deployments=[ActivityDeployment(**d) for d in _activity().deployments(found)],
        clients=[_client(item) for item in clients],
        undo=UndoSummary(
            available=plan.available, items=len(plan.items), irreversible=len(plan.irreversible)
        ),
    )


@router.get("/{token_id}/activity/{n}/undo", response_model=UndoPlanInfo)
def get_undo_plan(
    token_id: int, n: int, session: dict[str, Any] = Depends(get_current_session)
) -> UndoPlanInfo:
    """
    Propose how to leave an intervention as it was. Changes nothing.

    For each application the credential deployed, the way back Noust already has, to the version before the
    first thing it deployed there, or why there is none; and what it did that has no way back here, with why.

    Args:
        token_id: The token's record.
        n: The intervention's number.
        session: The caller.

    Returns:
        The plan.

    Raises:
        HTTPException: 404 for a token the caller may not read, or an intervention it does not have.
    """
    record = _record(token_id, session)
    return UndoPlanInfo(**_plan(record, _intervention(record, n)).to_dict())


@router.post("/{token_id}/activity/{n}/undo", response_model=JobAcceptedResponse, status_code=202)
def post_undo(
    token_id: int,
    n: int,
    body: UndoRequest | None = None,
    session: dict[str, Any] = Depends(require_elevated),
) -> JobAcceptedResponse:
    """
    Leave an intervention as it was: a job, in sudo mode, for the applications chosen.

    Each application goes back by the way back Noust already has, behind its health gate. A going back
    that passes a deployment that changed the database's schema is refused with 409 ``schema_changed``
    unless the body confirms it for that application: Noust puts code back, never a database. The job says
    what went back and what did not, and what the credential did that has no way back here.

    Args:
        token_id: The token's record.
        n: The intervention's number.
        body: The applications to put back and the ones whose schema change is confirmed.
        session: The caller, in sudo mode, holding ``apps.deploy`` in each application.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 for a token or an intervention the caller may not read; 403 for an application
            the caller may not deploy; 409 ``undo_unavailable`` when nothing asked can go back, and
            ``schema_changed`` when a confirmation is missing.
    """
    request = body or UndoRequest()
    record = _record(token_id, session)
    found = _intervention(record, n)
    plan = _plan(record, found)
    candidates = {i.domain: i for i in plan.items if i.status in (STATUS_READY, STATUS_CONFIRM)}
    asked = list(dict.fromkeys(request.apps)) if request.apps is not None else list(candidates)
    chosen = [domain for domain in asked if domain in candidates]
    if not chosen:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "undo_unavailable",
                "detail": "None of the applications asked for can be put back.",
                "hint": "GET the plan of this intervention to see why each one cannot.",
            },
        )
    scope = current_scope()
    if scope.restricted:
        scoped = scoped_store()
        for domain in chosen:
            place = scoped.placement_of_domain(domain)
            held = (
                permissions_at(session, place.team_id, place.project_id, place.environment_id)
                if place is not None
                else frozenset()
            )
            if Permission.APPS_DEPLOY not in held:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "permission_denied",
                        "detail": f"This needs the '{Permission.APPS_DEPLOY}' permission on {domain}",
                        "hint": "Ask an administrator of its team.",
                        "fields": {"apps": domain},
                    },
                )
    unconfirmed = [
        domain
        for domain in chosen
        if candidates[domain].status == STATUS_CONFIRM and domain not in request.schema_changed_ok
    ]
    if unconfirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "schema_changed",
                "detail": (
                    "Going back passes a deployment that changed the database's schema, in "
                    + ", ".join(unconfirmed)
                ),
                "hint": (
                    "Noust puts the code back, never the database: confirm it for those applications "
                    "(schema_changed_ok) or leave them out."
                ),
                "fields": {"apps": ", ".join(unconfirmed)},
            },
        )
    visible = _visible()
    seen = None if visible is None else sorted(scoped_store().visible_domains() or set())
    ref = _ref(record)
    job = get_job_manager().create_job(
        job_type=JobType.ROLLBACK,
        name=f"Put back what {ref.label} did",
        description=f"Leaving intervention {n} of {ref.label} as it was",
        func=undo_credential_job,
        kwargs={
            "credential": ref.label,
            "intervention": n,
            "domains": chosen,
            "confirmed": [d for d in chosen if d in request.schema_changed_ok],
            "visible_domains": seen,
            "names": list(ref.names),
            "by_conversation": ref.by_conversation,
        },
        metadata={"credential": ref.label, "intervention": n, "domains": chosen},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Putting back {len(chosen)} application(s) queued",
        job=job.to_dict(),
    )


# ------------------------------------------------------------------ the list of credentials


class CredentialSummary(BaseModel):
    """
    One credential of the list.

    Attributes:
        kind: ``token``, ``assistant`` or ``fleet``.
        id: The token's record; for the assistant that of its newest token. ``/api/auth/tokens/{id}/activity``
            reads what it did.
        name: The token's name; ``assistant:<person>`` for the assistant.
        owner: The account it acts for, by username: the person behind the assistant.
        scope: The token's scope; None for the assistant.
        created_at: When it was issued (its first token, for the assistant), UNIX seconds.
        expires_at: When it expires (the last of its tokens, for the assistant), or None.
        last_used_at: When it last authenticated a request, or None.
        revoked: Whether it was revoked (every one of its tokens, for the assistant).
        tokens: How many tokens it is made of: one, except for the assistant.
    """

    kind: str
    id: int
    name: str
    owner: str | None = None
    scope: str | None = None
    created_at: float
    expires_at: float | None = None
    last_used_at: float | None = None
    revoked: bool = False
    tokens: int = 1


class CredentialList(BaseModel):
    """
    The credentials the caller may read the activity of.

    Attributes:
        credentials: Newest first. The built-in assistant is one entry for each person, not one for each
            conversation.
    """

    credentials: list[CredentialSummary]


credentials_router = APIRouter(route_class=NoustErrorRoute)


@credentials_router.get("", response_model=CredentialList)
def list_credentials(session: dict[str, Any] = Depends(get_current_session)) -> CredentialList:
    """
    List the credentials whose activity the caller may read, the assistant as one for each person.

    Every token is a credential, but the built-in assistant has a token for each conversation, and a few
    hundred of them, each lasting hours, are not a list anybody reads: they are one credential, "the
    assistant, on behalf of" a person, whose interventions are its conversations. Whoever governs accounts or
    reads the audit trail sees every credential; anybody else, their own.

    Args:
        session: The caller.

    Returns:
        The credentials, newest first.
    """
    sees_all = has_permission(session, Permission.ACCOUNTS_MANAGE) or has_permission(
        session, Permission.AUDIT_READ
    )
    owner = session.get("account_id") or session.get("owner_account_id")
    records = get_token_manager().list_api_tokens(None if sees_all or owner is None else int(owner))
    found: list[CredentialSummary] = []
    assistants: dict[Any, list[dict[str, Any]]] = {}
    for record in records:
        if _kind(record) == "assistant":
            assistants.setdefault(record.get("owner_account_id"), []).append(record)
            continue
        found.append(
            CredentialSummary(
                kind=_kind(record),
                id=int(record["id"]),
                name=str(record["name"]),
                owner=_owner(record),
                scope=str(record.get("scope") or ""),
                created_at=float(record["created_at"]),
                expires_at=record.get("expires_at"),
                last_used_at=record.get("last_used_at"),
                revoked=record.get("revoked_at") is not None,
            )
        )
    for tokens in assistants.values():
        newest = max(tokens, key=lambda item: int(item["id"]))
        used = [item["last_used_at"] for item in tokens if item.get("last_used_at") is not None]
        expiries = [item["expires_at"] for item in tokens if item.get("expires_at") is not None]
        found.append(
            CredentialSummary(
                kind="assistant",
                id=int(newest["id"]),
                name=_ref(newest).label,
                owner=_owner(newest),
                created_at=min(float(item["created_at"]) for item in tokens),
                expires_at=max(expiries) if expiries else None,
                last_used_at=max(used) if used else None,
                revoked=all(item.get("revoked_at") is not None for item in tokens),
                tokens=len(tokens),
            )
        )
    found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return CredentialList(credentials=found)
