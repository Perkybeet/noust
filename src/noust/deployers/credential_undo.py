# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"Leave it as it was": what a credential did, put back, for what Noust can put back (item 185).

An intervention (:mod:`noust.core.credential_activity`) may have deployed or updated applications. Going
back is not a new mechanism: it is the way back Noust already has for each of them
(:func:`noust.deployers.lifecycle.rollback_to_deployment`, with its health gate, its release or its commit,
and its confirmation when a deployment changed the database's schema), asked for every application the
credential touched, to the version that served before the first thing it deployed there.

:func:`plan` only proposes. For each application it says what it would go back to, or why it cannot, in a
closed list of reasons:

- ``app_gone``: the application no longer exists.
- ``failed_only``: everything the credential deployed there failed its checks, and what served before still
  serves.
- ``nothing_before``: what it deployed is the application's first deployment, or the one before is not in the
  history any more; deleting the application is not going back.
- ``already``: it is already as it was.
- ``superseded``: somebody else deployed after it. Going back to before the credential would also take their
  work away, so Noust does not offer it; roll back to the deployment you want from the application's history.
- ``unavailable``: the version before cannot be gone back to (its release was pruned, it never served), with
  the reason Noust gives.

What went with the credential that has no way back written here is listed with its reason, never silently
left out: a deleted application and a restored backup do not come back, Noust puts the code back and never a
database, a variable's earlier value is not kept, and a service stopped or started may have been in the
other state before, which the audit trail does not say.

:func:`apply` is the work, asked for in sudo mode and run as a job: it plans again at that moment, so what
changed since is judged as it is now, goes through the list, and says what went back and what did not. Run
twice in a row, the second finds everything ``already``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.credential_activity import (
    RESULT_OK,
    CredentialActivity,
    CredentialRef,
    Entry,
    Intervention,
)
from noust.core.exceptions import NoustError
from noust.core.store import DeploymentRecord, NoustStore, get_store

logger = logging.getLogger(__name__)

STATUS_READY = "ready"
STATUS_CONFIRM = "needs_confirmation"
STATUS_ALREADY = "already"
STATUS_BLOCKED = "blocked"

REASON_APP_GONE = "app_gone"
REASON_FAILED_ONLY = "failed_only"
REASON_NOTHING_BEFORE = "nothing_before"
REASON_SUPERSEDED = "superseded"
REASON_UNAVAILABLE = "unavailable"
#: What an application can be blocked by.
REASONS: tuple[str, ...] = (
    REASON_APP_GONE,
    REASON_FAILED_ONLY,
    REASON_NOTHING_BEFORE,
    REASON_SUPERSEDED,
    REASON_UNAVAILABLE,
)

#: Deployments that served something.
_SERVED = frozenset({"success", "rolled_back"})

#: What cannot be undone here: ``(methods, route pattern, code, sentence)``. The first that matches wins.
_IRREVERSIBLE: tuple[tuple[frozenset[str], str, str, str], ...] = (
    (
        frozenset({"DELETE"}),
        r"/api/apps/[^/]+",
        "deleted",
        "A deleted application does not come back from here. Restore it from a backup.",
    ),
    (
        frozenset(),
        r"/api/(?:databases|apps/[^/]+/database)(?:/.*)?",
        "database",
        "Noust puts the code back, never the data: a database does not go back from here.",
    ),
    (
        frozenset({"POST"}),
        r"/api/(?:backups|volume-backups|server/restore)/.*restore.*|/api/apps/[^/]+/restore",
        "restore",
        "A restore replaced what was there; it cannot be un-restored from here.",
    ),
    (
        frozenset({"DELETE"}),
        r"/api/.*",
        "deleted",
        "What was deleted does not come back from here.",
    ),
    (
        frozenset({"PUT", "PATCH", "POST"}),
        r"/api/apps/[^/]+/(?:env|variables)(?:/.*)?|/api/variables(?:/.*)?",
        "variables",
        "The earlier value of a variable is not kept: set it again from where it came.",
    ),
    (
        frozenset({"POST"}),
        r"/api/apps/[^/]+/(?:start|stop)",
        "service_state",
        "Noust does not know whether the application was running before: start or stop it again by hand.",
    ),
)

#: Calls that change nothing that needs undoing: a restart leaves the application running as it was.
_NOTHING_TO_UNDO = re.compile(
    r"/api/apps/[^/]+/(?:restart|logs)|/api/.*/(?:test|plan|preview|verify|diagnose)"
)
#: Calls that are a deployment: they are undone through their deployment, not listed.
_DEPLOYMENT = re.compile(
    r"/api/apps/[^/]+/(?:update|deploy|rollback|deployments/\d+/(?:rollback|rebuild))|/api/apps"
)

Availability = Callable[[Sequence[DeploymentRecord]], dict[int, str | None]]
SchemaBetween = Callable[[Sequence[DeploymentRecord]], dict[int, list[int]]]
Rollback = Callable[..., Any]
Audit = Callable[..., None]


@dataclass
class UndoItem:
    """
    One application of the plan.

    Attributes:
        domain: The application.
        deployments: What the credential deployed there, oldest first.
        status: ``ready``, ``needs_confirmation`` (a deployment changed the database's schema: going back
            passes it, and Noust puts code back and never a database), ``already`` or ``blocked``.
        reason: Why it is blocked: one of :data:`REASONS`.
        detail: The reason in a sentence, in English.
        back_to: The deployment it would go back to.
        back_to_commit: Its commit.
        back_to_release: Its release.
        back_to_at: When it started.
        schema_changed: The deployments that changed the schema and that going back passes.
        superseded_by: The deployments of somebody else on top of the credential's.
    """

    domain: str
    deployments: list[int]
    status: str
    reason: str | None = None
    detail: str | None = None
    back_to: int | None = None
    back_to_commit: str | None = None
    back_to_release: str | None = None
    back_to_at: str | None = None
    schema_changed: list[int] = field(default_factory=list)
    superseded_by: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The item as the API shows it.
        """
        return {
            "kind": "rollback",
            "domain": self.domain,
            "deployments": list(self.deployments),
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "back_to": self.back_to,
            "back_to_commit": self.back_to_commit,
            "back_to_release": self.back_to_release,
            "back_to_at": self.back_to_at,
            "schema_changed": list(self.schema_changed),
            "superseded_by": list(self.superseded_by),
        }


@dataclass
class Irreversible:
    """
    Something the credential did that cannot be undone from here.

    Attributes:
        at: When.
        method: The method of the call.
        path: The route called.
        domain: The application it named.
        why: A code: ``deleted``, ``database``, ``restore``, ``variables``, ``service_state`` or
            ``no_way_back``.
        detail: The reason in a sentence, in English.
    """

    at: str
    method: str | None
    path: str | None
    domain: str | None
    why: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            It as the API shows it.
        """
        return {
            "at": self.at,
            "method": self.method,
            "path": self.path,
            "domain": self.domain,
            "why": self.why,
            "detail": self.detail,
        }


@dataclass
class UndoPlan:
    """
    What would put an intervention back.

    Attributes:
        credential: The token's name.
        intervention: Its number.
        items: One for each application it deployed.
        irreversible: What it did that cannot be undone from here.
    """

    credential: str
    intervention: int
    items: list[UndoItem]
    irreversible: list[Irreversible]

    @property
    def available(self) -> bool:
        """Whether something can be put back."""
        return any(item.status in (STATUS_READY, STATUS_CONFIRM) for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as the API shows it.
        """
        return {
            "credential": self.credential,
            "intervention": self.intervention,
            "available": self.available,
            "items": [item.to_dict() for item in self.items],
            "irreversible": [item.to_dict() for item in self.irreversible],
        }


@dataclass
class UndoResult:
    """
    What applying a plan did.

    Attributes:
        reverted: The applications that went back, with what they went back to.
        not_reverted: Those that did not, each with why.
        irreversible: What the credential did that cannot be undone from here, as the plan said it.
    """

    reverted: list[dict[str, Any]] = field(default_factory=list)
    not_reverted: list[dict[str, Any]] = field(default_factory=list)
    irreversible: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        """``undone`` when everything asked went back, ``partly`` when some did, ``nothing`` otherwise."""
        if self.reverted and not self.not_reverted:
            return "undone"
        return "partly" if self.reverted else "nothing"

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as the job reports it.
        """
        return {
            "status": self.status,
            "reverted": self.reverted,
            "not_reverted": self.not_reverted,
            "irreversible": self.irreversible,
        }


def classify_entry(entry: Entry) -> tuple[str, str] | None:
    """
    Say why a call of a credential cannot be undone here.

    Args:
        entry: What it did.

    Returns:
        ``(code, sentence)``; None for what needs no undoing (a restart, a plan) or is undone through its
        deployment.
    """
    path = entry.path or ""
    method = (entry.method or "").upper()
    if _NOTHING_TO_UNDO.fullmatch(path) or _DEPLOYMENT.fullmatch(path):
        return None
    for methods, pattern, code, sentence in _IRREVERSIBLE:
        if (not methods or method in methods) and re.fullmatch(pattern, path):
            return code, sentence
    return "no_way_back", "No way back is written for this change."


class Undoer:
    """
    Plan and apply "leave it as it was".

    Args:
        activity: The reader of what a credential did; the server's by default.
        store: The store; the process-wide one by default.
        availability: Whether a deployment can be gone back to (:func:`~noust.deployers.lifecycle.rollback_availability`).
        schema_between: Which later deployments changed the schema
            (:func:`~noust.deployers.lifecycle.schema_changed_between`).
        rollback: How an application goes back (:func:`~noust.deployers.lifecycle.rollback_to_deployment`).
        audit: How what was done is put on record (:func:`noust.core.audit.record`).
    """

    def __init__(
        self,
        activity: CredentialActivity | None = None,
        store: NoustStore | None = None,
        *,
        availability: Availability | None = None,
        schema_between: SchemaBetween | None = None,
        rollback: Rollback | None = None,
        audit: Audit | None = None,
    ) -> None:
        self._audit = audit
        self._activity = activity
        self._store = store
        self._availability = availability
        self._schema_between = schema_between
        self._rollback = rollback

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store if self._store is not None else get_store()

    @property
    def activity(self) -> CredentialActivity:
        """The reader of what a credential did."""
        return (
            self._activity if self._activity is not None else CredentialActivity(store=self.store)
        )

    def _availability_of(self, records: Sequence[DeploymentRecord]) -> dict[int, str | None]:
        if self._availability is not None:
            return self._availability(records)
        from noust.deployers.lifecycle import rollback_availability

        return rollback_availability(records)

    def _schema_between_of(self, records: Sequence[DeploymentRecord]) -> dict[int, list[int]]:
        if self._schema_between is not None:
            return self._schema_between(records)
        from noust.deployers.lifecycle import schema_changed_between

        return schema_changed_between(records)

    # -------------------------------------------------------------------- plan

    def plan(self, credential: CredentialRef | str, intervention: Intervention) -> UndoPlan:
        """
        Propose how to put an intervention back. Changes nothing.

        Args:
            credential: The token's name, or the reference of a credential made of several.
            intervention: What it did, read with its deployments (and with only what the caller sees).

        Returns:
            The plan: an item for each application it deployed, and what cannot be undone here.
        """
        by_app: dict[str, list[dict[str, Any]]] = {}
        for entry in intervention.entries:
            if entry.deployment is not None:
                by_app.setdefault(str(entry.deployment["domain"]), []).append(entry.deployment)
        items = [self._item(domain, deployments) for domain, deployments in by_app.items()]
        irreversible = []
        for entry in intervention.entries:
            if not entry.changes or entry.result != RESULT_OK or entry.deployment is not None:
                continue
            found = classify_entry(entry)
            if found is not None:
                irreversible.append(
                    Irreversible(entry.at, entry.method, entry.path, entry.domain, *found)
                )
        return UndoPlan(CredentialRef.of(credential).label, intervention.n, items, irreversible)

    def _item(self, domain: str, made: list[dict[str, Any]]) -> UndoItem:
        """
        Args:
            domain: An application the credential deployed.
            made: What it deployed there, oldest first.

        Returns:
            What to do about it.
        """
        ids = sorted({int(d["id"]) for d in made})
        item = UndoItem(domain=domain, deployments=ids, status=STATUS_BLOCKED)
        store = self.store
        if store.get_app(domain) is None:
            return _blocked(item, REASON_APP_GONE, f"{domain} no longer exists.")
        served = [d for d in made if d["status"] == "success"]
        if not served:
            return _blocked(
                item,
                REASON_FAILED_ONLY,
                "Everything it deployed there failed its checks: what served before is still serving.",
            )
        history = store.list_deployments(domain, limit=200)
        first, last = ids[0], ids[-1]
        target = next(
            (r for r in history if r.id is not None and r.id < first and r.status in _SERVED), None
        )
        if target is None or target.id is None:
            return _blocked(
                item,
                REASON_NOTHING_BEFORE,
                "What it deployed is the first deployment of the application, or the one before is no "
                "longer in the history: there is nothing to go back to.",
            )
        item.back_to = target.id
        item.back_to_commit = target.git_commit
        item.back_to_release = target.release_id
        item.back_to_at = target.started_at
        current = next((r for r in history if r.status == "success"), None)
        if (
            current is not None
            and current.id is not None
            and current.id not in ids
            and (
                (target.release_id and current.release_id == target.release_id)
                or (target.git_commit and current.git_commit == target.git_commit)
            )
        ):
            item.status = STATUS_ALREADY
            item.detail = "It is already as it was before."
            return item
        later = [
            r.id
            for r in history
            if r.id is not None and r.id > last and r.id not in ids and r.status in _SERVED
        ]
        if later:
            item.superseded_by = sorted(later)
            return _blocked(
                item,
                REASON_SUPERSEDED,
                "Somebody else deployed after it: going back to before it would take their work away. "
                "Roll back to the deployment you want from the application's history.",
                keep=True,
            )
        reason = self._availability_of([target]).get(target.id)
        if reason is not None:
            return _blocked(item, REASON_UNAVAILABLE, reason, keep=True)
        item.schema_changed = list(self._schema_between_of([target]).get(target.id, []))
        item.status = STATUS_CONFIRM if item.schema_changed else STATUS_READY
        return item

    # ------------------------------------------------------------------- apply

    def apply(
        self,
        credential: CredentialRef | str,
        n: int,
        *,
        domains: Collection[str],
        confirmed: Collection[str] = (),
        visible: Callable[[str | None], bool] | None = None,
        on_step: Callable[[UndoItem], None] | None = None,
        is_read_tool: Callable[[str], bool] = lambda _tool: False,
    ) -> UndoResult:
        """
        Put an intervention back, for the applications asked, as the plan is now.

        The plan is made again here, at the moment of doing it, so a change since the person looked is
        judged as it is: what is ``already`` is left, what somebody deployed on top is ``superseded``.

        Args:
            credential: The token's name, or the reference of a credential made of several.
            n: The intervention's number.
            domains: The applications to put back.
            confirmed: The applications for which the person confirmed going back past a deployment that
                changed the database's schema.
            visible: Whether the caller sees an application.
            on_step: Told which application is being put back, for the job's progress.
            is_read_tool: Whether a tool of MCP only reads.

        Returns:
            What went back and what did not, each with why.
        """
        found = self.activity.intervention(
            credential, n, visible=visible, is_read_tool=is_read_tool
        )
        result = UndoResult()
        if found is None:
            result.not_reverted.append(
                {
                    "domain": None,
                    "reason": "intervention_gone",
                    "detail": "That intervention is not in the audit trail any more.",
                }
            )
            return result
        plan = self.plan(credential, found)
        result.irreversible = [item.to_dict() for item in plan.irreversible]
        wanted = set(domains)
        for item in plan.items:
            if item.domain not in wanted:
                continue
            if item.status == STATUS_ALREADY:
                result.not_reverted.append(_report(item, "already", item.detail or ""))
                continue
            if item.status == STATUS_BLOCKED:
                result.not_reverted.append(
                    _report(item, item.reason or "blocked", item.detail or "")
                )
                continue
            if item.status == STATUS_CONFIRM and item.domain not in set(confirmed):
                result.not_reverted.append(
                    _report(
                        item,
                        "schema_changed",
                        "Going back passes a deployment that changed the database's schema, and was "
                        "not confirmed: Noust puts code back, never the database.",
                    )
                )
                continue
            if on_step is not None:
                on_step(item)
            try:
                outcome = self._roll_back(item)
            except NoustError as exc:
                # The error boundary of one application: the others still go back, and the person is told
                # which did not, with Noust's own words.
                logger.warning("Could not put %s back: %s", item.domain, exc.message)
                result.not_reverted.append(_report(item, "failed", exc.message, exc.details))
                continue
            result.reverted.append(
                {
                    "domain": item.domain,
                    "back_to": item.back_to,
                    "release": getattr(outcome, "release_id", None),
                    "history": getattr(outcome, "history_id", None),
                }
            )
        self._record(CredentialRef.of(credential).label, n, result)
        return result

    def _record(self, credential: str, n: int, result: UndoResult) -> None:
        """
        Put on record what applying a plan did.

        Args:
            credential: What the credential is called.
            n: The intervention's number.
            result: What went back and what did not.
        """
        record = self._audit
        if record is None:
            from noust.core.audit import record as default_record

            record = default_record
        record(
            "auth.token.undo",
            target=f"token:{credential}",
            outcome="ok" if result.status == "undone" else "failure",
            details={
                "intervention": n,
                "status": result.status,
                "reverted": [item["domain"] for item in result.reverted],
                "not_reverted": [
                    {"domain": item.get("domain"), "reason": item["reason"]}
                    for item in result.not_reverted
                ],
            },
        )

    def _roll_back(self, item: UndoItem) -> Any:
        """
        Args:
            item: An application of the plan that can go back.

        Returns:
            What the rollback returned.
        """
        confirmed = item.status == STATUS_CONFIRM
        if self._rollback is not None:
            return self._rollback(item.domain, item.back_to, schema_changed_ok=confirmed)
        from noust.core.store import DeploymentTrigger
        from noust.deployers.lifecycle import rollback_to_deployment

        if item.back_to is None:
            raise ValueError(
                "an item that can go back names what it goes back to"
            )  # a defect of the plan
        return rollback_to_deployment(
            item.domain,
            item.back_to,
            trigger=DeploymentTrigger.PANEL.value,
            schema_changed_ok=confirmed,
        )


def _blocked(item: UndoItem, reason: str, detail: str, *, keep: bool = False) -> UndoItem:
    """
    Args:
        item: An item of the plan.
        reason: One of :data:`REASONS`.
        detail: The reason in a sentence.
        keep: Keep what it would have gone back to, which the reason is about.

    Returns:
        The item, blocked.
    """
    item.status = STATUS_BLOCKED
    item.reason = reason
    item.detail = detail
    if not keep:
        item.back_to = item.back_to_commit = item.back_to_release = item.back_to_at = None
    return item


def _report(item: UndoItem, reason: str, detail: str, hint: str = "") -> dict[str, Any]:
    """
    Args:
        item: An item of the plan.
        reason: A code.
        detail: The reason in a sentence.
        hint: What to do, when Noust said.

    Returns:
        What the job says of an application that did not go back.
    """
    return {"domain": item.domain, "reason": reason, "detail": detail, "hint": hint or None}


__all__ = [
    "REASONS",
    "STATUS_ALREADY",
    "STATUS_BLOCKED",
    "STATUS_CONFIRM",
    "STATUS_READY",
    "Irreversible",
    "UndoItem",
    "UndoPlan",
    "UndoResult",
    "Undoer",
    "classify_entry",
]
