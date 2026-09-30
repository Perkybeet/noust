# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Four-eyes approvals (ENS G08): a second person decides before a root-equivalent change runs.

ENS op.acc.3 asks that critical tasks need two people, so that one authorised
person cannot abuse their rights alone. Sudo mode is the same person again;
``admin`` is root-equivalent. So, under the ENS profile or when the operator
turns it on (``approval.enabled``), the actions of :func:`rule_for` - raw
units, cron commands, backup hooks, raw site configuration, SQL that writes
(row edits and ``EXPLAIN ANALYZE`` included), deploying from a directory of
this server, adding and removing nodes, and role grants (a role change, a new
account, a recovery invitation) - become requests another person decides.

The mechanism is a guard at the chokepoint, like sudo mode
(:func:`noust.web.api.approvals.require_approval`, on every API route): the
first call is answered 202 with a request that snapshots it; a decider
approves or rejects it; the requester sends the same call again naming the
request, and it runs, once. Everything about a request is decided here, so the
console and ``noust approval`` share one implementation:

- **Who decides.** By default only ``security``. With ``approval.approvers:
  [security, admin]`` an ``admin`` may approve infrastructure, never a role
  change. Never the requester (nor the owner of the token that asked), never
  another account of the same person (``person_ref``), never a token, the
  master token or a central. Root at the
  terminal decides too: the CLI is the emergency channel, not held to roles,
  and on record with the operating system identity.
- **What is approved.** The exact call: method, path, query and body, by
  their SHA-256 (:func:`snapshot`). A display copy of the parameters, with
  secret-named values redacted, is what the decider reads.
- **For how long.** A request waits ``approval.request_hours`` (24) for a
  decision; an approval allows the call for ``approval.execute_minutes`` (30),
  once, and only to its requester.
- **On record.** ``approval.request``, ``approval.approve``,
  ``approval.reject``, ``approval.use`` (the requester, with the approver in
  its details) and ``approval.expire``.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from noust.core.accounts.model import ROLE_ADMIN, ROLE_SECURITY
from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.exceptions import SecurityError

if TYPE_CHECKING:
    from noust.core.audit import Actor
    from noust.core.store import NoustStore

KIND_INFRASTRUCTURE = "infrastructure"
KIND_ROLE_CHANGE = "role_change"

STATE_REQUESTED = "requested"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_EXPIRED = "expired"
STATE_EXECUTED = "executed"
STATES: tuple[str, ...] = (
    STATE_REQUESTED,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_EXPIRED,
    STATE_EXECUTED,
)

#: Roles ``approval.approvers`` may name; anything else is ignored.
DECIDING_ROLES = (ROLE_SECURITY, ROLE_ADMIN)

DEFAULT_REQUEST_HOURS = 24.0
DEFAULT_EXECUTE_MINUTES = 30.0
#: Requests one principal may have waiting at once, so nobody floods the deciders.
MAX_PENDING_PER_REQUESTER = 20
#: Longest reason and comment kept.
MAX_TEXT = 500
#: Largest display copy of a call's parameters; a bigger body is shown by size
#: and digest. The fingerprint always covers the whole body.
MAX_SNAPSHOT_BYTES = 64 * 1024

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class ApprovalError(SecurityError):
    """
    A request cannot be decided or used as asked.

    Attributes:
        code: What the console branches on: ``approval_pending``,
            ``approval_rejected``, ``approval_expired``, ``approval_used``,
            ``approval_mismatch``, ``approval_limit`` or ``approval_not_found``.
        status: The HTTP status that fits.
    """

    def __init__(self, code: str, message: str, details: str = "", *, status: int = 409) -> None:
        """
        Args:
            code: The machine-readable code.
            message: What is wrong.
            details: What to do.
            status: The HTTP status that fits.
        """
        super().__init__(message, details=details)
        self.code = code
        self.status = status


class ApprovalNotFound(ApprovalError):
    """No request has that id."""

    def __init__(self, approval_id: int) -> None:
        """
        Args:
            approval_id: The id asked for.
        """
        super().__init__(
            "approval_not_found",
            f"No approval request with id {approval_id}",
            "List them with GET /api/approvals or 'noust approval list'.",
            status=404,
        )


class ApprovalDenied(NoustPermissionError):
    """
    This principal may not decide or use this request.

    Attributes:
        code: ``approval_denied``.
        status: 403.
    """

    code = "approval_denied"
    status = 403


# -------------------------------------------------------------------- policy


@dataclass(frozen=True)
class ApprovalPolicy:
    """
    Whether approvals apply, and who decides.

    Attributes:
        enabled: Whether the actions of :func:`rule_for` need an approval.
        approvers: The roles that decide: ``security``, and ``admin`` for
            infrastructure only when named.
        request_hours: How long a request waits for a decision.
        execute_minutes: How long an approval allows its call.
        reason_required: Whether a request must say why (the ENS profile).
    """

    enabled: bool = False
    approvers: tuple[str, ...] = (ROLE_SECURITY,)
    request_hours: float = DEFAULT_REQUEST_HOURS
    execute_minutes: float = DEFAULT_EXECUTE_MINUTES
    reason_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The policy as the API shows it.
        """
        return {
            "enabled": self.enabled,
            "approvers": list(self.approvers),
            "request_hours": self.request_hours,
            "execute_minutes": self.execute_minutes,
            "reason_required": self.reason_required,
        }


def _positive(value: Any, default: float, ceiling: float) -> float:
    """
    Args:
        value: A configured number, or garbage.
        default: What to use for garbage.
        ceiling: The most it may be.

    Returns:
        The number, capped, or the default.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(number, ceiling) if number > 0 else default


def build_approval_policy(settings: Mapping[str, Any]) -> ApprovalPolicy:
    """
    Turn configuration values into the policy.

    Args:
        settings: ``profile`` (``security.profile``) and the ``approval``
            section's ``enabled``, ``approvers``, ``request_hours`` and
            ``execute_minutes``.

    Returns:
        The policy: on under the ENS profile whatever ``enabled`` says, with
        ``security`` always among the deciders.
    """
    from noust.core.accounts.policy import PROFILE_ENS_MEDIUM, normalise_profile

    ens = normalise_profile(settings.get("profile")) == PROFILE_ENS_MEDIUM
    raw = settings.get("approvers")
    named = [str(item).strip().lower() for item in raw] if isinstance(raw, (list, tuple)) else []
    approvers = tuple(role for role in DECIDING_ROLES if role == ROLE_SECURITY or role in named)
    return ApprovalPolicy(
        enabled=ens or bool(settings.get("enabled")),
        approvers=approvers,
        request_hours=_positive(settings.get("request_hours"), DEFAULT_REQUEST_HOURS, 24 * 14),
        execute_minutes=_positive(
            settings.get("execute_minutes"), DEFAULT_EXECUTE_MINUTES, 24 * 60
        ),
        reason_required=ens,
    )


def load_approval_policy() -> ApprovalPolicy:
    """
    Read the policy in force, on every use, so a change needs no restart.

    Returns:
        The policy.
    """
    from noust.core.config import Config

    config = Config()
    return build_approval_policy(
        {
            "profile": config.get("security.profile", "standard"),
            "enabled": config.get("approval.enabled", False),
            "approvers": config.get("approval.approvers"),
            "request_hours": config.get("approval.request_hours"),
            "execute_minutes": config.get("approval.execute_minutes"),
        }
    )


# --------------------------------------------------------------------- rules


@dataclass(frozen=True)
class ApprovalRule:
    """
    One action that needs a second person.

    Attributes:
        action: Its stable name, such as ``fleet.node.add``.
        kind: ``infrastructure`` or ``role_change``: an admin may be allowed
            to approve the first, never the second.
        description: One sentence for the decider.
    """

    action: str
    kind: str
    description: str


#: Every route whose permission is ``root_equivalent`` (a command, a unit,
#: a hook or a configuration that runs as root), except those below.
ROOT_EQUIVALENT_RULE = ApprovalRule(
    "root_equivalent",
    KIND_INFRASTRUCTURE,
    "A change equivalent to root: a raw unit, a cron command, a backup hook or a raw site "
    "configuration.",
)

#: Routes that need one by what they are.
ROUTE_RULES: dict[tuple[str, str], ApprovalRule] = {
    ("POST", "/api/nodes"): ApprovalRule(
        "fleet.node.add", KIND_INFRASTRUCTURE, "Adding a server to this central."
    ),
    ("DELETE", "/api/nodes/{node}"): ApprovalRule(
        "fleet.node.remove", KIND_INFRASTRUCTURE, "Removing a server from this central."
    ),
    # A row edit is a write through the console's own database session, the
    # same power as SQL that writes.
    ("POST", "/api/databases/databases/{engine}/{name}/rows"): ApprovalRule(
        "db.rows.write", KIND_INFRASTRUCTURE, "Inserting a row into a database."
    ),
    ("PATCH", "/api/databases/databases/{engine}/{name}/rows"): ApprovalRule(
        "db.rows.write", KIND_INFRASTRUCTURE, "Changing a row of a database."
    ),
    ("POST", "/api/databases/databases/{engine}/{name}/rows/delete"): ApprovalRule(
        "db.rows.write", KIND_INFRASTRUCTURE, "Deleting rows of a database."
    ),
}

#: ``root_equivalent`` routes that change nothing: checking a candidate unit.
EXEMPT_ROUTES = frozenset({("POST", "/api/services/verify")})


def _writes_sql(body: Any, _params: Mapping[str, str]) -> bool:
    return isinstance(body, Mapping) and body.get("mode") == "write"


def _local_source(body: Any, _params: Mapping[str, str]) -> bool:
    if not isinstance(body, Mapping) or not isinstance(body.get("source"), str):
        return False
    from noust.validators.source import is_local_path

    return is_local_path(body["source"])


def _analyzes(body: Any, _params: Mapping[str, str]) -> bool:
    # EXPLAIN ANALYZE executes the statement it explains, writes included.
    return isinstance(body, Mapping) and body.get("analyze") is True


def _accounts_exist(_body: Any, _params: Mapping[str, str]) -> bool:
    # The first account is made with the master token before anyone exists
    # who could decide; from the second on, a new account (any role) and a
    # recovery (which hands an existing account, role and all, to whoever
    # holds the code) are role grants another person decides.
    from noust.core.accounts.manager import AccountManager

    return AccountManager().any_exist()


def _changes_role(body: Any, params: Mapping[str, str]) -> bool:
    if not isinstance(body, Mapping) or not isinstance(body.get("role"), str):
        return False
    from noust.core.accounts.manager import AccountManager

    account = AccountManager().find(str(params.get("username") or ""))
    return account is not None and account.role != body["role"].strip().lower()


#: Routes that need one by what their body asks.
BODY_RULES: dict[tuple[str, str], tuple[ApprovalRule, Callable[[Any, Mapping[str, str]], bool]]] = {
    ("POST", "/api/databases/query"): (
        ApprovalRule("db.query.write", KIND_INFRASTRUCTURE, "A SQL statement that may write."),
        _writes_sql,
    ),
    ("POST", "/api/apps"): (
        ApprovalRule(
            "apps.local_source",
            KIND_INFRASTRUCTURE,
            "Deploying an application from a directory of this server.",
        ),
        _local_source,
    ),
    ("POST", "/api/databases/query/explain"): (
        ApprovalRule(
            "db.query.analyze",
            KIND_INFRASTRUCTURE,
            "EXPLAIN ANALYZE, which runs the statement it explains.",
        ),
        _analyzes,
    ),
    ("PATCH", "/api/auth/accounts/{username}"): (
        ApprovalRule("user.role_change", KIND_ROLE_CHANGE, "Changing an account's role."),
        _changes_role,
    ),
    ("POST", "/api/auth/accounts"): (
        ApprovalRule(
            "user.create", KIND_ROLE_CHANGE, "Creating an account, with the role it is given."
        ),
        _accounts_exist,
    ),
    ("POST", "/api/auth/invitations"): (
        ApprovalRule(
            "user.invite",
            KIND_ROLE_CHANGE,
            "Inviting a person to a new account, or recovering an existing one: whoever "
            "holds the code gets the account and its role.",
        ),
        _accounts_exist,
    ),
}


def may_need_approval(method: str, template: str, permissions: Iterable[str]) -> bool:
    """
    Say, without the body, whether a call could be one :func:`rule_for` names.

    Lets the guard leave every other call alone without reading its body.

    Args:
        method: The HTTP method.
        template: The route's path template.
        permissions: Every permission the call needs.

    Returns:
        True when a rule may apply.
    """
    verb = method.upper()
    if verb in _SAFE_METHODS:
        return False
    key = (verb, template)
    if key in ROUTE_RULES or key in BODY_RULES:
        return True
    return "root_equivalent" in set(permissions) and key not in EXEMPT_ROUTES


def rule_for(
    method: str,
    template: str,
    permissions: Iterable[str],
    body: Any,
    path_params: Mapping[str, str],
) -> ApprovalRule | None:
    """
    Decide whether a call is one a second person has to approve.

    Args:
        method: The HTTP method.
        template: The route's path template (for a call through the node
            proxy, the node's own route).
        permissions: Every permission the call needs.
        body: The parsed JSON body, or None.
        path_params: The route's parameters.

    Returns:
        The rule, or None when the call needs no approval.
    """
    verb = method.upper()
    if verb in _SAFE_METHODS:
        return None
    key = (verb, template)
    if key in ROUTE_RULES:
        return ROUTE_RULES[key]
    if key in BODY_RULES:
        rule, applies = BODY_RULES[key]
        if applies(body, path_params):
            return rule
    if "root_equivalent" in set(permissions) and key not in EXEMPT_ROUTES:
        return ROOT_EQUIVALENT_RULE
    return None


#: When a body rule applies, in words, for the console and the schema.
_CONDITIONS: dict[str, str] = {
    "db.query.write": "mode is 'write'",
    "apps.local_source": "source is a directory of this server",
    "user.role_change": "role differs from the account's",
    "db.query.analyze": "analyze is true",
    "user.create": "an account already exists",
    "user.invite": "an account already exists",
}


def route_requirement(
    method: str, template: str, permissions: Iterable[str]
) -> dict[str, str] | None:
    """
    Say what a route needs approved, before any call: what the schema publishes.

    Args:
        method: The HTTP method.
        template: The route's path template.
        permissions: The permissions the route needs.

    Returns:
        ``action``, ``kind`` and ``when`` (``always``, or the condition on the
        body under which it applies), or None when no rule covers the route.
    """
    if not may_need_approval(method, template, permissions):
        return None
    key = (method.upper(), template)
    if key in ROUTE_RULES:
        rule, when = ROUTE_RULES[key], "always"
    elif key in BODY_RULES:
        rule = BODY_RULES[key][0]
        when = _CONDITIONS.get(rule.action, "depends on the body")
    else:
        rule, when = ROOT_EQUIVALENT_RULE, "always"
    return {"action": rule.action, "kind": rule.kind, "when": when}


def describe_rules() -> builtins.list[dict[str, Any]]:
    """
    Every rule, for the console to say in advance what will ask for approval.

    Returns:
        One entry per rule: the route (or the permission) and when it applies.
    """
    rules: builtins.list[dict[str, Any]] = [
        {
            "method": method,
            "template": template,
            "action": rule.action,
            "kind": rule.kind,
            "description": rule.description,
            "when": "always",
        }
        for (method, template), rule in ROUTE_RULES.items()
    ]
    rules.extend(
        {
            "method": method,
            "template": template,
            "action": rule.action,
            "kind": rule.kind,
            "description": rule.description,
            "when": _CONDITIONS.get(rule.action, "depends on the body"),
        }
        for (method, template), (rule, _check) in BODY_RULES.items()
    )
    rules.append(
        {
            "method": "*",
            "template": "*",
            "action": ROOT_EQUIVALENT_RULE.action,
            "kind": ROOT_EQUIVALENT_RULE.kind,
            "description": ROOT_EQUIVALENT_RULE.description,
            "when": "the route needs the root_equivalent permission",
        }
    )
    return rules


# ------------------------------------------------------------------ snapshot


def _redact(value: Any, depth: int = 0) -> Any:
    """
    Hide the values of secret-named keys, keeping everything else whole.

    Args:
        value: Parsed JSON.
        depth: Nesting, for the bound.

    Returns:
        A copy with secret-named values replaced by the redaction marker.
    """
    from noust.core.audit.sanitize import secret_name
    from noust.core.config import REDACTED

    if depth > 16:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED
            if secret_name(str(key)) and item not in (None, "")
            else _redact(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value]
    return value


def snapshot(
    method: str, path: str, query: Sequence[tuple[str, str]], body: bytes
) -> tuple[str, dict[str, Any]]:
    """
    Fingerprint one call, and describe it for the person who decides.

    Args:
        method: The HTTP method.
        path: The concrete path, such as ``/api/services/shop/config``.
        query: The query string's pairs, in any order.
        body: The body as received.

    Returns:
        The SHA-256 of the canonical call (JSON bodies by content, so key
        order is not a different call; any other body by its bytes), and
        the display copy: method, path, query and body with secret-named
        values redacted, or the body's size and digest when it is not JSON or
        too large to show.
    """
    parsed: Any = None
    is_json = False
    if body.strip():
        try:
            parsed = json.loads(body)
            is_json = True
        except (UnicodeDecodeError, ValueError):
            parsed = None
    canonical_body = (
        json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if is_json
        else hashlib.sha256(body).hexdigest()
    )
    pairs = sorted((str(key), str(value)) for key, value in query)
    material = json.dumps(
        {"method": method.upper(), "path": path, "query": pairs, "body": canonical_body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
    shown: Any = None
    if is_json:
        shown = _redact(parsed)
        if len(json.dumps(shown)) > MAX_SNAPSHOT_BYTES:
            shown = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "shown": False}
    elif body:
        shown = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "shown": False}
    display = {
        "method": method.upper(),
        "path": path,
        "query": [list(pair) for pair in pairs],
        "body": shown,
    }
    return fingerprint, display


# -------------------------------------------------------------------- actors


@dataclass(frozen=True)
class ApprovalActor:
    """
    Who asks, or who decides.

    Attributes:
        kind: ``account``, ``master``, ``token``, ``fleet`` or ``cli``.
        id: The account id, the token's name, the login uid.
        name: The username, ``master``, ``token:<name>``, the login.
        role: The account's role, or the grant of the master token.
        person_ref: The person an account belongs to; for a token, its
            owner's.
        via: How the credential arrived.
        source: Where from.
        account: The account behind it: the account itself, or the owner of
            an API token. A token is its owner when deciding, so nobody
            approves what they asked for through a token of their own.
    """

    kind: str
    id: str | None
    name: str
    role: str | None = None
    person_ref: str | None = None
    via: str | None = None
    source: str | None = None
    account: str | None = None

    @property
    def account_id(self) -> int | None:
        """The account id, for an account."""
        if self.kind != "account" or self.id is None:
            return None
        try:
            return int(self.id)
        except ValueError:
            return None

    @property
    def behind(self) -> str | None:
        """The account behind the principal: its own id, or a token's owner."""
        if self.kind == "account":
            return self.id
        return self.account

    def same_principal(self, other: ApprovalActor) -> bool:
        """
        Args:
            other: Another actor.

        Returns:
            True when both are the same credential or account.
        """
        return (self.kind, self.id) == (other.kind, other.id)

    def audit_actor(self) -> Actor:
        """
        Returns:
            The audit trail's :class:`~noust.core.audit.Actor` for it.
        """
        from noust.core.audit import Actor, ActorKind

        kind = self.kind if self.kind in ("master", "token", "fleet", "cli", "system") else "user"
        return Actor(
            kind=cast(ActorKind, kind),
            id=self.id,
            name=self.name,
            role=self.role,
            via=self.via,
            source=self.source,
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The actor as the API and the audit details show it; no person
            reference, which is the accounts' business.
        """
        return {"kind": self.kind, "id": self.id, "name": self.name, "role": self.role}

    @classmethod
    def from_audit_actor(cls, actor: Actor) -> ApprovalActor:
        """
        Args:
            actor: An audit actor, such as :func:`noust.core.audit.cli_actor`.

        Returns:
            The same identity as an approval actor.
        """
        kind = "account" if actor.kind == "user" else actor.kind
        return cls(
            kind=kind,
            id=actor.id,
            name=actor.name or actor.label,
            role=actor.role,
            via=actor.via,
            source=actor.source,
        )


# ------------------------------------------------------------ notifications


def _moment(timestamp: float | None) -> datetime | None:
    """
    Args:
        timestamp: UNIX seconds, or None.

    Returns:
        The moment, in UTC.
    """
    return None if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc)


def _notify_requested(request: ApprovalRequest) -> None:
    """
    Tell the deciders a request is waiting, through the operator's channels.

    Args:
        request: The request, just created.
    """
    from noust.core.notifications.composers import compose_approval_requested
    from noust.core.notifier import notify_composed

    notify_composed(
        lambda ctx: compose_approval_requested(
            ctx,
            approval_id=request.id,
            action=request.action,
            call=f"{request.method} {request.path}",
            requester=request.requester.name,
            reason=request.reason,
            expires_at=_moment(request.expires_at),
        )
    )


def _notify_decided(request: ApprovalRequest) -> None:
    """
    Tell whoever asked how their request was decided.

    Args:
        request: The request, just approved or rejected.
    """
    from noust.core.notifications.composers import compose_approval_decided
    from noust.core.notifier import notify_composed

    notify_composed(
        lambda ctx: compose_approval_decided(
            ctx,
            approval_id=request.id,
            approved=request.state == STATE_APPROVED,
            action=request.action,
            call=f"{request.method} {request.path}",
            requester=request.requester.name,
            decider=request.decider.name if request.decider else "",
            comment=request.decision_comment,
            use_before=_moment(request.execute_by),
        )
    )


# ------------------------------------------------------------------ requests


@dataclass(frozen=True)
class ApprovalRequest:
    """
    One request, as stored.

    Attributes:
        id: Store id; the value of ``X-Noust-Approval``.
        action: The rule's action.
        kind: ``infrastructure`` or ``role_change``.
        description: What the rule says the action is.
        method: The call's method.
        path: The call's path.
        parameters: The display copy of the call.
        fingerprint: The SHA-256 of the exact call.
        reason: Why the requester asked.
        state: One of :data:`STATES`, as it is now.
        requester: Who asked.
        created_at: When, UNIX seconds.
        expires_at: When it expires undecided.
        decided_at: When it was decided.
        decider: Who decided.
        decision_comment: What the decider said.
        execute_by: Until when an approval allows the call.
        executed_at: When the call ran.
    """

    id: int
    action: str
    kind: str
    method: str
    path: str
    parameters: dict[str, Any]
    fingerprint: str
    reason: str | None
    state: str
    requester: ApprovalActor
    created_at: float
    expires_at: float
    decided_at: float | None
    decider: ApprovalActor | None
    decision_comment: str | None
    execute_by: float | None
    executed_at: float | None

    @property
    def description(self) -> str:
        """The rule's sentence for this action."""
        for rule in (ROOT_EQUIVALENT_RULE, *ROUTE_RULES.values()):
            if rule.action == self.action:
                return rule.description
        for rule, _check in BODY_RULES.values():
            if rule.action == self.action:
                return rule.description
        return self.action

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The request as the API and ``--json`` show it.
        """
        return {
            "id": self.id,
            "action": self.action,
            "kind": self.kind,
            "description": self.description,
            "method": self.method,
            "path": self.path,
            "parameters": self.parameters,
            "fingerprint": self.fingerprint,
            "reason": self.reason,
            "state": self.state,
            "requester": self.requester.to_dict(),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "decider": self.decider.to_dict() if self.decider else None,
            "decision_comment": self.decision_comment,
            "execute_by": self.execute_by,
            "executed_at": self.executed_at,
        }


def _request(row: sqlite3.Row) -> ApprovalRequest:
    """
    Args:
        row: A row of ``approval_requests``.

    Returns:
        The request.
    """
    try:
        parameters = json.loads(row["parameters"] or "{}")
    except ValueError:
        parameters = {}
    decider = (
        ApprovalActor(
            kind=str(row["decider_kind"]),
            id=row["decider_id"],
            name=str(row["decider_name"] or ""),
            role=row["decider_role"],
        )
        if row["decider_kind"]
        else None
    )
    return ApprovalRequest(
        id=int(row["id"]),
        action=str(row["action"]),
        kind=str(row["kind"]),
        method=str(row["method"]),
        path=str(row["path"]),
        parameters=parameters if isinstance(parameters, dict) else {},
        fingerprint=str(row["fingerprint"]),
        reason=row["reason"],
        state=str(row["state"]),
        requester=ApprovalActor(
            kind=str(row["requester_kind"]),
            id=row["requester_id"],
            name=str(row["requester_name"]),
            role=row["requester_role"],
            person_ref=row["requester_person"],
            account=row["requester_account"],
        ),
        created_at=float(row["created_at"]),
        expires_at=float(row["expires_at"]),
        decided_at=row["decided_at"],
        decider=decider,
        decision_comment=row["decision_comment"],
        execute_by=row["execute_by"],
        executed_at=row["executed_at"],
    )


def _text(value: str | None) -> str | None:
    """
    Args:
        value: A reason or a comment as typed.

    Returns:
        It without control characters, cut at :data:`MAX_TEXT`; None when empty.
    """
    cleaned = "".join(char for char in (value or "") if char.isprintable()).strip()
    return cleaned[:MAX_TEXT] or None


class ApprovalManager:
    """
    Request, decide and use four-eyes approvals.

    Args:
        store: The store; the process-wide one by default.
        policy: The policy; read from the configuration on every use by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        *,
        policy: ApprovalPolicy | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock or time.time

    @property
    def store(self) -> NoustStore:
        """The store the requests live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def policy(self) -> ApprovalPolicy:
        """
        Returns:
            The policy in force.
        """
        return self._policy or load_approval_policy()

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> builtins.list[sqlite3.Row]:
        """
        Args:
            sql: A SELECT.
            params: Its parameters.

        Returns:
            The rows.
        """
        return builtins.list(self.store._get_connection().execute(sql, params).fetchall())

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Cursor]:
        """
        Yields:
            A cursor inside one store transaction.
        """
        with self.store._transaction() as cursor:
            yield cursor

    def _record(
        self,
        event: str,
        actor: ApprovalActor,
        request: ApprovalRequest,
        outcome: str = "ok",
        **details: Any,
    ) -> None:
        """
        Put a step of a request on the audit trail.

        Args:
            event: The catalog name.
            actor: Who took the step.
            request: The request.
            outcome: How it ended.
            **details: More context.
        """
        from noust.core.audit import record

        record(
            event,
            actor=actor.audit_actor(),
            target=f"approval:{request.id}",
            outcome=outcome,
            details={
                "approval_id": request.id,
                "action": request.action,
                "method": request.method,
                "path": request.path,
                "fingerprint": request.fingerprint,
                **details,
            },
        )

    # ------------------------------------------------------------- reading

    def get(self, approval_id: int) -> ApprovalRequest:
        """
        Args:
            approval_id: The request's id.

        Returns:
            The request, with its state as it is now.

        Raises:
            ApprovalNotFound: When there is none.
        """
        self.expire_due()
        rows = self._rows("SELECT * FROM approval_requests WHERE id = ?", (int(approval_id),))
        if not rows:
            raise ApprovalNotFound(int(approval_id))
        return _request(rows[0])

    def list(
        self, *, state: str | None = None, requester: ApprovalActor | None = None, limit: int = 200
    ) -> builtins.list[ApprovalRequest]:
        """
        List requests, newest first.

        Args:
            state: Only those in this state.
            requester: Only this principal's.
            limit: At most this many.

        Returns:
            The requests.
        """
        self.expire_due()
        clauses: builtins.list[str] = []
        params: builtins.list[Any] = []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if requester is not None:
            clauses.append("requester_kind = ? AND requester_id IS ?")
            params.extend([requester.kind, requester.id])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(
            f"SELECT * FROM approval_requests {where} ORDER BY id DESC LIMIT ?",  # noqa: S608 - fixed clauses
            (*params, max(1, min(int(limit), 1000))),
        )
        return [_request(row) for row in rows]

    def pending_count(self) -> int:
        """
        Returns:
            How many requests wait for a decision.
        """
        self.expire_due()
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM approval_requests WHERE state = ?", (STATE_REQUESTED,)
        )
        return int(rows[0]["n"])

    # ------------------------------------------------------------ asking

    def request(
        self,
        rule: ApprovalRule,
        *,
        method: str,
        path: str,
        parameters: Mapping[str, Any],
        fingerprint: str,
        reason: str | None,
        requester: ApprovalActor,
    ) -> tuple[ApprovalRequest, bool]:
        """
        Ask for an approval of one call, or find the one already asked for.

        Args:
            rule: What the call is.
            method: Its method.
            path: Its path.
            parameters: Its display copy (:func:`snapshot`).
            fingerprint: Its fingerprint (:func:`snapshot`).
            reason: Why.
            requester: Who asks.

        Returns:
            The request, and whether it was created now (False when the same
            principal already had one open for the same call).

        Raises:
            ApprovalError: ``approval_limit`` when the requester has too many
                waiting.
        """
        self.expire_due()
        existing = self._rows(
            "SELECT * FROM approval_requests WHERE requester_kind = ? AND requester_id IS ? "
            "AND fingerprint = ? AND state IN (?, ?) ORDER BY id DESC LIMIT 1",
            (requester.kind, requester.id, fingerprint, STATE_REQUESTED, STATE_APPROVED),
        )
        if existing:
            return _request(existing[0]), False
        waiting = self._rows(
            "SELECT COUNT(*) AS n FROM approval_requests WHERE requester_kind = ? "
            "AND requester_id IS ? AND state = ?",
            (requester.kind, requester.id, STATE_REQUESTED),
        )
        if int(waiting[0]["n"]) >= MAX_PENDING_PER_REQUESTER:
            raise ApprovalError(
                "approval_limit",
                f"You already have {MAX_PENDING_PER_REQUESTER} requests waiting for approval",
                "Wait for them to be decided, or let them expire, before asking for more.",
                status=429,
            )
        now = self.now()
        policy = self.policy()
        with self._write() as cursor:
            cursor.execute(
                "INSERT INTO approval_requests (action, kind, method, path, parameters, "
                "fingerprint, reason, state, requester_kind, requester_id, requester_name, "
                "requester_role, requester_person, requester_account, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rule.action,
                    rule.kind,
                    method.upper(),
                    path,
                    json.dumps(dict(parameters)),
                    fingerprint,
                    _text(reason),
                    STATE_REQUESTED,
                    requester.kind,
                    requester.id,
                    requester.name,
                    requester.role,
                    requester.person_ref,
                    requester.behind,
                    now,
                    now + policy.request_hours * 3600,
                ),
            )
            approval_id = int(cursor.lastrowid or 0)
        created = self.get(approval_id)
        self._record("approval.request", requester, created, reason=created.reason or "")
        _notify_requested(created)
        return created, True

    # ---------------------------------------------------------- deciding

    def eligibility(self, request: ApprovalRequest, decider: ApprovalActor) -> str | None:
        """
        Say why a principal may not decide a request, if it may not.

        Args:
            request: The request.
            decider: Who wants to decide it.

        Returns:
            None when it may; otherwise the sentence to refuse with.
        """
        if decider.kind == "cli":
            return None
        if decider.kind != "account":
            return (
                "Only a person with an account decides approvals; tokens, the master token "
                "and centrals never do."
            )
        if decider.same_principal(request.requester) or (
            decider.behind is not None and decider.behind == request.requester.behind
        ):
            return "Nobody decides their own request; another person has to."
        if (
            decider.person_ref
            and request.requester.person_ref
            and decider.person_ref == request.requester.person_ref
        ):
            return (
                "This account belongs to the same person as the requester; another person has "
                "to decide."
            )
        approvers = self.policy().approvers
        if decider.role == ROLE_SECURITY and ROLE_SECURITY in approvers:
            return None
        if decider.role == ROLE_ADMIN and ROLE_ADMIN in approvers:
            if request.kind == KIND_INFRASTRUCTURE:
                return None
            return "An admin never approves a role change; a security officer has to."
        allowed = " or ".join(approvers)
        return f"Only a {allowed} account decides this request."

    def _decide(
        self,
        approval_id: int,
        decider: ApprovalActor,
        comment: str | None,
        *,
        approve: bool,
    ) -> ApprovalRequest:
        """
        Approve or reject a request.

        Args:
            approval_id: The request.
            decider: Who decides.
            comment: What they say.
            approve: Approve rather than reject.

        Returns:
            The request after the decision.

        Raises:
            ApprovalNotFound: When there is no such request.
            ApprovalDenied: When the decider may not decide it.
            ApprovalError: When it is no longer open to this decision.
        """
        request = self.get(approval_id)
        refusal = self.eligibility(request, decider)
        if refusal is not None:
            raise ApprovalDenied(refusal, details="Ask a security officer to decide it.")
        open_states = (STATE_REQUESTED,) if approve else (STATE_REQUESTED, STATE_APPROVED)
        if request.state not in open_states:
            raise _closed(request)
        now = self.now()
        state = STATE_APPROVED if approve else STATE_REJECTED
        execute_by = now + self.policy().execute_minutes * 60 if approve else None
        with self._write() as cursor:
            cursor.execute(
                "UPDATE approval_requests SET state = ?, decided_at = ?, decider_kind = ?, "
                "decider_id = ?, decider_name = ?, decider_role = ?, decision_comment = ?, "
                "execute_by = ? WHERE id = ? AND state IN (?, ?)",
                (
                    state,
                    now,
                    decider.kind,
                    decider.id,
                    decider.name,
                    decider.role,
                    _text(comment),
                    execute_by,
                    request.id,
                    # Two placeholders, whichever states are open.
                    open_states[0],
                    open_states[-1],
                ),
            )
            if cursor.rowcount != 1:
                raise ApprovalError(
                    "approval_changed",
                    "The request changed while you were deciding it",
                    "Reload it.",
                )
        decided = self.get(request.id)
        self._record(
            "approval.approve" if approve else "approval.reject",
            decider,
            decided,
            requester=decided.requester.to_dict(),
            comment=decided.decision_comment or "",
        )
        _notify_decided(decided)
        return decided

    def approve(
        self, approval_id: int, decider: ApprovalActor, comment: str | None = None
    ) -> ApprovalRequest:
        """
        Approve a request: its requester may make the call once, for a while.

        Args:
            approval_id: The request.
            decider: Who approves.
            comment: What they say.

        Returns:
            The request, approved.

        Raises:
            ApprovalNotFound: When there is no such request.
            ApprovalDenied: When the decider may not decide it.
            ApprovalError: When it is not waiting for a decision.
        """
        return self._decide(approval_id, decider, comment, approve=True)

    def reject(
        self, approval_id: int, decider: ApprovalActor, comment: str | None = None
    ) -> ApprovalRequest:
        """
        Reject a request, or withdraw an approval not used yet.

        Args:
            approval_id: The request.
            decider: Who rejects.
            comment: Why.

        Returns:
            The request, rejected.

        Raises:
            ApprovalNotFound: When there is no such request.
            ApprovalDenied: When the decider may not decide it.
            ApprovalError: When it was already used, rejected or expired.
        """
        return self._decide(approval_id, decider, comment, approve=False)

    # ------------------------------------------------------------- using

    def consume(
        self, approval_id: int, requester: ApprovalActor, fingerprint: str
    ) -> ApprovalRequest:
        """
        Use an approval for the call it approved, once.

        Args:
            approval_id: The request named by ``X-Noust-Approval``.
            requester: Who makes the call now.
            fingerprint: The call's fingerprint (:func:`snapshot`).

        Returns:
            The request, executed.

        Raises:
            ApprovalNotFound: When there is no such request.
            ApprovalDenied: When it is somebody else's request.
            ApprovalError: ``approval_pending``, ``approval_rejected``,
                ``approval_expired``, ``approval_used`` or
                ``approval_mismatch`` (another call than the one approved).
        """
        request = self.get(approval_id)
        if not request.requester.same_principal(requester):
            raise ApprovalDenied(
                f"Approval request {request.id} is somebody else's",
                details="Ask for an approval of your own call.",
            )
        if request.state != STATE_APPROVED:
            raise _closed(request)
        if request.fingerprint != fingerprint:
            raise ApprovalError(
                "approval_mismatch",
                "This call is not the one that was approved",
                "Send exactly the approved method, path, query and body, or ask for a new approval.",
            )
        now = self.now()
        with self._write() as cursor:
            cursor.execute(
                "UPDATE approval_requests SET state = ?, executed_at = ? WHERE id = ? AND state = ?",
                (STATE_EXECUTED, now, request.id, STATE_APPROVED),
            )
            if cursor.rowcount != 1:
                raise ApprovalError(
                    "approval_used", "This approval was already used", "Ask for a new one."
                )
        executed = self.get(request.id)
        self._record(
            "approval.use",
            requester,
            executed,
            approved_by=executed.decider.to_dict() if executed.decider else None,
            approved_at=executed.decided_at,
        )
        return executed

    def expire_due(self) -> builtins.list[ApprovalRequest]:
        """
        Expire requests nobody decided in time and approvals nobody used.

        Returns:
            The requests that expired now; each is put on record.
        """
        now = self.now()
        rows = self._rows(
            "SELECT * FROM approval_requests WHERE (state = ? AND expires_at < ?) "
            "OR (state = ? AND execute_by < ?)",
            (STATE_REQUESTED, now, STATE_APPROVED, now),
        )
        if not rows:
            return []
        expired = []
        with self._write() as cursor:
            for row in rows:
                cursor.execute(
                    "UPDATE approval_requests SET state = ? WHERE id = ? AND state = ?",
                    (STATE_EXPIRED, int(row["id"]), row["state"]),
                )
                if cursor.rowcount == 1:
                    expired.append((_request(row), str(row["state"])))
        from noust.core.audit import Actor

        system = ApprovalActor.from_audit_actor(Actor(kind="system", name="approvals"))
        results = []
        for request, previous in expired:
            self._record("approval.expire", system, request, was=previous)
            results.append(request)
        return results


def _closed(request: ApprovalRequest) -> ApprovalError:
    """
    Args:
        request: A request not in the state the caller needed.

    Returns:
        The error that names its state.
    """
    messages = {
        STATE_REQUESTED: (
            "approval_pending",
            f"Approval request {request.id} is still waiting for a decision",
            "Try again once someone approved it.",
        ),
        STATE_APPROVED: (
            "approval_approved",
            f"Approval request {request.id} is already approved",
            f"The requester can run it now with X-Noust-Approval: {request.id}.",
        ),
        STATE_REJECTED: (
            "approval_rejected",
            f"Approval request {request.id} was rejected",
            request.decision_comment or "Ask for a new approval if it is still needed.",
        ),
        STATE_EXPIRED: (
            "approval_expired",
            f"Approval request {request.id} expired",
            "Ask for a new approval.",
        ),
        STATE_EXECUTED: (
            "approval_used",
            f"Approval request {request.id} was already used",
            "An approval allows its call once; ask for a new one.",
        ),
    }
    code, message, details = messages.get(
        request.state, ("approval_closed", f"Approval request {request.id} is closed", "")
    )
    return ApprovalError(code, message, details)
